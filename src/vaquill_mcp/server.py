"""Vaquill MCP Server - Legal research tools powered by 20M+ court judgments.

Uses FastMCP with an OpenAPIProvider to auto-generate tools from the Vaquill
OpenAPI spec, with custom tool names and descriptions optimized for LLM agents.

The OpenAPI spec is fetched from the live API at startup, so tools automatically
reflect any API changes without a package update.
"""

import contextlib
import logging
import time
from collections.abc import AsyncIterator

import httpx
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import (
    MCPType,
    OpenAPIProvider,
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
    RouteMap,
)
from fastmcp.utilities.openapi.models import HTTPRoute

from vaquill_mcp import __version__
from vaquill_mcp.config import get_api_key, get_base_url, get_timeout
from vaquill_mcp.descriptions import TOOL_DESCRIPTIONS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAPI operationId -> desired MCP tool name
# ---------------------------------------------------------------------------
# These map the auto-generated operationIds from FastAPI (which include the
# full path) to clean, LLM-friendly tool names.

_MCP_NAMES: dict[str, str] = {
    "ask_legal_question_api_v1_ask_post": "ask_legal_question",
    "external_search_api_v1_research_search_post": "search_legal_cases",
    "bot_search_api_v1_research_quick_post": "quick_search",
    "resolve_citation_api_v1_citations_resolve_get": "resolve_citation",
    "search_cases_api_v1_citations_cases_search_get": "search_cases_by_citation",
    "lookup_case_api_v1_citations_cases_lookup_get": "lookup_case",
    "get_citation_network_api_v1_citations_cases_network_get": "get_citation_network",
    "get_pricing_api_v1_api_credits_pricing_get": "get_pricing",
    # Indian Acts & Legislation
    "search_acts_api_v1_acts_search_post": "search_legislation",
    "list_acts_api_v1_acts_list_get": "list_legislation",
    "get_act_text_api_v1_acts__act_id__text_get": "get_act_text",
    "get_act_amendments_api_v1_acts__act_id__amendments_get": "get_amendments",
    # US Statutes (USC + CFR + 50 state legislation via /ask)
    "search_statutes_api_v1_statutes_search_post": "search_us_statutes",
    "get_section_api_v1_statutes_section__act_id__get": "get_us_statute_section",
    "get_section_body_api_v1_statutes_section__act_id__body_get": "get_us_statute_section_text",
}

# ---------------------------------------------------------------------------
# Route exclusions
# ---------------------------------------------------------------------------
# The /ask/stream endpoint uses SSE streaming which MCP tools cannot support.
# Exclude it so only the synchronous /ask endpoint becomes a tool.

_ROUTE_MAPS: list[RouteMap] = [
    RouteMap(pattern=r".*/ask/stream$", mcp_type=MCPType.EXCLUDE),
]


# ---------------------------------------------------------------------------
# Credit-cost injection
# ---------------------------------------------------------------------------
# Credit costs are NEVER hardcoded in descriptions.py. Instead they are fetched
# once at startup from the live API (the same CREDIT_PRICING source of truth the
# billing system uses) and appended to each tool description. This makes it
# structurally impossible for the advertised cost to drift from the real charge.
#
# Each MCP tool maps to a pricing `endpoint` (as returned by
# /api/v1/api-credits/pricing/all). Some endpoints serve both US and India at
# different prices (e.g. /research/search, /citations/network); for those we
# pin the region the tool is documented against so the injected cost matches
# what the tool actually does. `None` means the endpoint is single-region.
_TOOL_COST_ENDPOINTS: dict[str, tuple[str, str | None]] = {
    "ask_legal_question": ("/ask", None),
    "search_legal_cases": ("/research/search", "IN"),
    "quick_search": ("/research/quick", "IN"),
    "resolve_citation": ("/citations/resolve", "IN"),
    "search_cases_by_citation": ("/citations/cases/search", "IN"),
    "lookup_case": ("/citations/cases/lookup", "IN"),
    "get_citation_network": ("/citations/network", "IN"),
    "search_legislation": ("/acts/search", None),
    "get_act_text": ("/acts/{actId}/text", None),
    "get_amendments": ("/acts/{actId}/amendments", None),
    "list_legislation": ("/acts/list", None),
    "search_us_statutes": ("/statutes/search", None),
    "get_us_statute_section": ("/statutes/section", None),
    "get_us_statute_section_text": ("/statutes/section/body", None),
    # get_pricing is free — deliberately absent so no cost line is appended.
}


def _fmt_credits(credits: float) -> str:
    """Render a credit count without a trailing '.0' for whole numbers."""
    return str(int(credits)) if float(credits).is_integer() else str(credits)


def _credit_noun(credits_str: str) -> str:
    """'credit' for exactly one, 'credits' otherwise."""
    return "credit" if credits_str == "1" else "credits"


def _short_label(operation: str) -> str:
    """Extract the disambiguating tier label from a pricing operation name.

    'Ask (Standard)' -> 'standard'; 'US Case Law Search (21-50 results)' ->
    '21-50 results'; 'US Statutes Search' -> '' (no tier to disambiguate).
    """
    start = operation.rfind("(")
    end = operation.rfind(")")
    if start != -1 and end > start:
        return operation[start + 1 : end].strip().lower()
    return ""


def _format_cost(entries: list[dict]) -> str:
    """Build a 'Cost: ...' sentence from the pricing entries for one tool.

    Single price -> 'Cost: 4 credits.' Multiple tiers -> each tier labelled,
    e.g. 'Cost: 15 credits (standard), 30 credits (deep).'
    """
    tiers: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        credits = _fmt_credits(entry["credits"])
        label = _short_label(entry.get("operation", ""))
        key = (credits, label)
        if key in seen:
            continue
        seen.add(key)
        tiers.append((credits, label))

    if not tiers:
        return ""
    if len(tiers) == 1:
        credits = tiers[0][0]
        return f"Cost: {credits} {_credit_noun(credits)}."
    rendered = ", ".join(
        f"{credits} {_credit_noun(credits)} ({label})"
        if label
        else f"{credits} {_credit_noun(credits)}"
        for credits, label in tiers
    )
    return f"Cost: {rendered}."


def _build_tool_costs(cost_entries: list[dict]) -> dict[str, str]:
    """Map each MCP tool name to its injected 'Cost: ...' sentence.

    ``cost_entries`` is the ``costs`` array from /api/v1/api-credits/pricing/all.
    Tools with no matching endpoint (or when the fetch failed) are simply
    omitted, so no cost line is appended rather than a wrong one.
    """
    tool_costs: dict[str, str] = {}
    for tool_name, (endpoint, region) in _TOOL_COST_ENDPOINTS.items():
        matches = [
            e
            for e in cost_entries
            if e.get("endpoint") == endpoint
            and (region is None or region in e.get("regions", []))
        ]
        line = _format_cost(matches)
        if line:
            tool_costs[tool_name] = line
    return tool_costs


def _fetch_full_costs(base_url: str, api_key: str) -> list[dict]:
    """Fetch the full per-endpoint credit-cost matrix from the live API.

    Uses the authenticated /pricing/all endpoint (research:read scope) so the
    hidden ask/research/citations/acts prices are included. Best-effort: any
    failure returns an empty list, and tools then carry no cost line rather
    than a stale one.
    """
    url = f"{base_url}/api/v1/api-credits/pricing/all"
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json().get("costs", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Could not fetch credit pricing from %s; tool descriptions will "
            "omit cost lines this session: %s",
            url,
            exc,
        )
        return []


# ---------------------------------------------------------------------------
# Component customization
# ---------------------------------------------------------------------------


def _make_customize_component(tool_costs: dict[str, str]):
    """Build the FastMCP ``mcp_component_fn`` bound to a live cost map."""

    def _customize_component(
        route: HTTPRoute,
        component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate,
    ) -> None:
        """Rewrite auto-generated descriptions to be concise and LLM-friendly,
        and append the live per-call credit cost.

        The OpenAPI spec descriptions are multi-paragraph markdown with tables,
        code examples, and SSE documentation -- far too verbose for an LLM tool
        description. We replace them with focused 50-100 word descriptions that
        tell the LLM WHEN to use the tool and WHAT it returns, then append the
        credit cost fetched from the live API at startup.

        Note: This callback mutates ``component`` in-place as required by
        FastMCP's ``mcp_component_fn`` contract.
        """
        if component.name in TOOL_DESCRIPTIONS:
            description = TOOL_DESCRIPTIONS[component.name]
            cost_line = tool_costs.get(component.name)
            if cost_line:
                description = f"{description} {cost_line}"
            component.description = description

        # Tag all components for discoverability
        component.tags.add("legal-research")
        component.tags.add("vaquill")

    return _customize_component


# ---------------------------------------------------------------------------
# Spec fetching (with retry)
# ---------------------------------------------------------------------------

_MAX_RETRIES = 2


def _fetch_openapi_spec(base_url: str) -> dict:
    """Fetch the OpenAPI spec from the Vaquill API server.

    Retries up to 2 times with exponential backoff for transient network
    errors (connect failures, timeouts). This is intentionally synchronous
    because it runs during server initialization before the async event
    loop starts.

    Raises:
        httpx.HTTPStatusError: If the API returns a non-2xx status.
        httpx.ConnectError: If the API is unreachable after retries.
        httpx.TimeoutException: If all attempts time out.
        ValueError: If the response is not valid JSON.
    """
    url = f"{base_url}/external/openapi.json"
    last_error: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = httpx.get(url, timeout=15.0)
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                raise ValueError(
                    f"Failed to parse OpenAPI spec from {url} -- "
                    f"expected JSON but got: {response.text[:200]}"
                ) from exc
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                delay = 2**attempt  # 1s, 2s
                logger.warning(
                    "OpenAPI spec fetch failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    delay,
                    exc,
                )
                time.sleep(delay)

    raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_server() -> FastMCP:
    """Create and configure the Vaquill MCP server.

    Reads configuration from environment variables:
    - VAQUILL_API_KEY (required): API key for authentication
    - VAQUILL_BASE_URL (optional): API base URL (default: https://api.vaquill.ai)
    - VAQUILL_TIMEOUT (optional): Request timeout in seconds (default: 120)

    Returns:
        A configured FastMCP server ready to run.

    Raises:
        ValueError: If VAQUILL_API_KEY is not set.
        httpx.HTTPError: If the OpenAPI spec cannot be fetched.
    """
    api_key = get_api_key()
    base_url = get_base_url()
    timeout = get_timeout()

    # Fetch OpenAPI spec from the live API
    openapi_spec = _fetch_openapi_spec(base_url)

    # Fetch the live credit-cost matrix so each tool description carries an
    # accurate, never-drifting cost line (best-effort; empty on failure).
    tool_costs = _build_tool_costs(_fetch_full_costs(base_url, api_key))

    # Create authenticated HTTP client.
    # The auth header is set on the client so ALL requests carry it.
    # The pricing endpoint ignores the extra header (it's unauthenticated).
    # Timeout is generous (120s default) because /ask in deep mode can take 90s.
    client = httpx.AsyncClient(
        base_url=base_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": f"vaquill-mcp/{__version__}",
        },
        timeout=httpx.Timeout(timeout, connect=10.0),
    )

    # Lifespan context manager to cleanly close the httpx client on shutdown.
    @contextlib.asynccontextmanager
    async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await client.aclose()

    # Build MCP server with lifespan for proper resource cleanup,
    # then add the OpenAPI provider for tool generation.
    mcp = FastMCP(name="Vaquill Legal Research", lifespan=_lifespan)

    provider = OpenAPIProvider(
        openapi_spec=openapi_spec,
        client=client,
        mcp_names=_MCP_NAMES,
        route_maps=_ROUTE_MAPS,
        mcp_component_fn=_make_customize_component(tool_costs),
        # Disable output validation — the live API is the source of truth.
        # Some fields (e.g., citation network treatmentType) can be null in
        # practice even though the OpenAPI enum doesn't declare it nullable.
        validate_output=False,
    )
    mcp.add_provider(provider)

    return mcp
