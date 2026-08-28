"""Vaquill MCP Server - Legal research tools powered by 20M+ court judgments.

Uses FastMCP with an OpenAPIProvider to auto-generate tools from the Vaquill
OpenAPI spec, with custom tool names and descriptions optimized for LLM agents.

The OpenAPI spec is fetched from the live API at startup, so tools automatically
reflect any API changes without a package update.
"""

import contextlib
import logging
import re
import time
from collections import Counter
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

# Tool names are AUTO-DERIVED from the live OpenAPI (see _derive_mcp_names), so
# new/renamed/re-prefixed endpoints need no change here. This map is only the
# handful of SEMANTIC renames where the raw handler-function name is not the
# clearest tool name. Keyed on the FUNCTION NAME (the part of FastAPI's
# operationId before `_api_v1_`) so it survives path/prefix changes such as the
# `/us` country-prefix migration.
#
# Entries for retired surfaces were removed on 2026-08-20: `/acts/*` went with
# the India-market exit (router unmounted) and `/citations/cases/search` no
# longer exists. An override for a route the spec no longer carries is inert
# but misleading, and it makes the description drift-guard pass vacuously.
_FUNC_OVERRIDES: dict[str, str] = {
    "external_search": "search_legal_cases",
    "bot_search": "quick_search",
    "search_statutes": "search_us_statutes",
    "get_section": "get_us_statute_section",
    "get_section_body": "get_us_statute_section_text",
}


def _derive_mcp_names(spec: dict) -> dict[str, str]:
    """Map each operationId to a clean tool name, derived from the OpenAPI.

    FastAPI auto-generates operationIds as ``{func}_api_v1_{path}_{method}``, so
    the text before ``_api_v1_`` is the clean handler-function name -- that is
    the default tool name for every endpoint (turning the ugly
    ``list_statutes_coverage_api_v1_statutes_coverage_get`` into
    ``list_statutes_coverage``). ``_FUNC_OVERRIDES`` refines a few, and genuine
    cross-router name collisions (e.g. ``resolve_citation`` under both
    ``/citations`` and ``/statutes``) are disambiguated by the resource segment.
    """
    entries: list[tuple[str, str, str]] = []  # (operation_id, resource, base_name)
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        segs = [s for s in path.strip("/").split("/") if s not in ("api", "v1", "us") and "{" not in s]
        resource = segs[0] if segs else ""
        for op in item.values():
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if not op_id or "_api_v1_" not in op_id:
                continue
            func = op_id.split("_api_v1_", 1)[0]
            entries.append((op_id, resource, _FUNC_OVERRIDES.get(func, func)))
    counts = Counter(base for _, _, base in entries)
    names: dict[str, str] = {}
    for op_id, resource, base in entries:
        if counts[base] > 1 and resource and not base.startswith(f"{resource}_"):
            base = f"{resource}_{base}"
        names[op_id] = base
    return names

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
# The tool -> pricing-endpoint link is DERIVED from the OpenAPI path, never
# hand-maintained. The pricing matrix names an endpoint as the routable path
# with the version prefix and every path parameter removed, so the mapping is a
# pure function of the spec (`_pricing_endpoint_for_route`) and a tool cannot
# drift away from its price by being renamed, re-prefixed or moved.
#
# It used to be a hand-keyed dict, and that is exactly how this broke: the
# 2026-08 `/us` country-prefix migration moved every statutes price from
# `/statutes/search` to `/us/statutes/search`, the dict still said the old
# spelling, and `_build_tool_costs` silently matched nothing. Combined with the
# India-market exit retiring the ask/acts/research/citations tools the dict was
# mostly built around, the result was that EVERY tool shipped with no cost line
# at all -- a "cannot drift" mechanism that had quietly stopped running.
#
# `descriptions.py` still may not state a credit number; see the drift guard in
# tests/test_server.py.

_PATH_PARAM_RE = re.compile(r"/\{[^}]+\}")
_VERSION_PREFIX_RE = re.compile(r"^/api/v\d+")


def _pricing_endpoint_for_route(path: str) -> str:
    """The pricing-matrix `endpoint` string for an OpenAPI path.

    `/api/v1/us/statutes/section/{act_id}/body` -> `/us/statutes/section/body`.

    This mirrors, deliberately, the same normalization the web console applies
    in `frontend/src/lib/playground/cost-endpoint.ts`. Both sides join prices to
    routes this way; keep them in step.
    """
    return _PATH_PARAM_RE.sub("", _VERSION_PREFIX_RE.sub("", path)).rstrip("/")


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
    e.g. 'Cost: 2 credits (1-20 results), 4 credits (21-50 results).'

    A priced-at-zero endpoint renders 'Free.' rather than 'Cost: 0 credits.'
    Several endpoints are deliberately free (coverage discovery, and every
    law-change alert route except the diff), and an agent choosing between
    tools benefits more from knowing one is free than from parsing a zero.
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
        if credits == "0":
            return "Free."
        return f"Cost: {credits} {_credit_noun(credits)}."
    rendered = ", ".join(
        f"{credits} {_credit_noun(credits)} ({label})"
        if label
        else f"{credits} {_credit_noun(credits)}"
        for credits, label in tiers
    )
    return f"Cost: {rendered}."


def _build_tool_costs(spec: dict, cost_entries: list[dict]) -> dict[str, str]:
    """Map each MCP tool name to its injected 'Cost: ...' sentence.

    ``cost_entries`` is the ``costs`` array from /api/v1/api-credits/pricing/all.
    Both the tool name and the pricing endpoint are derived from the same
    OpenAPI operation, so they cannot fall out of step with each other.

    A tool whose route has no pricing row (``get_pricing``) or whose fetch
    failed is omitted, so it carries no cost line rather than a wrong one.
    """
    by_endpoint: dict[str, list[dict]] = {}
    for entry in cost_entries:
        endpoint = entry.get("endpoint")
        if endpoint:
            by_endpoint.setdefault(endpoint, []).append(entry)

    names = _derive_mcp_names(spec)
    tool_costs: dict[str, str] = {}
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        endpoint = _pricing_endpoint_for_route(path)
        matches = by_endpoint.get(endpoint)
        if not matches:
            continue
        line = _format_cost(matches)
        if not line:
            continue
        for op in item.values():
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if not op_id:
                continue
            # An explicit hand-set operation_id is not in `names` (it has no
            # `_api_v1_` marker); FastMCP uses it verbatim as the tool name.
            tool_costs[names.get(op_id, op_id)] = line
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
    tool_costs = _build_tool_costs(openapi_spec, _fetch_full_costs(base_url, api_key))

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
        mcp_names=_derive_mcp_names(openapi_spec),
        route_maps=_ROUTE_MAPS,
        mcp_component_fn=_make_customize_component(tool_costs),
        # Disable output validation — the live API is the source of truth.
        # Some fields (e.g., citation network treatmentType) can be null in
        # practice even though the OpenAPI enum doesn't declare it nullable.
        validate_output=False,
    )
    mcp.add_provider(provider)

    return mcp
