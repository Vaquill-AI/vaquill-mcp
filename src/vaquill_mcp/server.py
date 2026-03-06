"""Vaquill MCP Server - Legal research tools powered by 13M+ court judgments.

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
# Component customization
# ---------------------------------------------------------------------------


def _customize_component(
    route: HTTPRoute,
    component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate,
) -> None:
    """Rewrite auto-generated descriptions to be concise and LLM-friendly.

    The OpenAPI spec descriptions are multi-paragraph markdown with tables,
    code examples, and SSE documentation -- far too verbose for an LLM tool
    description. We replace them with focused 50-100 word descriptions that
    tell the LLM WHEN to use the tool and WHAT it returns.

    Note: This callback mutates ``component`` in-place as required by
    FastMCP's ``mcp_component_fn`` contract.
    """
    if component.name in TOOL_DESCRIPTIONS:
        component.description = TOOL_DESCRIPTIONS[component.name]

    # Tag all components for discoverability
    component.tags.add("legal-research")
    component.tags.add("vaquill")


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
        mcp_component_fn=_customize_component,
        # Disable output validation — the live API is the source of truth.
        # Some fields (e.g., citation network treatmentType) can be null in
        # practice even though the OpenAPI enum doesn't declare it nullable.
        validate_output=False,
    )
    mcp.add_provider(provider)

    return mcp
