"""Remote Vaquill MCP server for Claude.ai web integration.

Serves over Streamable HTTP with per-user API key authentication
via URL path: https://mcp.vaquill.ai/s/{api_key}

Users paste this URL into Claude.ai as an authless MCP integration.
The API key is extracted from the URL path on each request and used
for all Vaquill API calls, mapping credits 1:1 to the user's account.

Architecture:
    Claude.ai  --POST-->  /s/{api_key}  -->  FastMCP handler
                                                    |
                                             tool called
                                                    |
                                         get_http_request()
                                         -> path_params["api_key"]
                                                    |
                                         httpx -> api.vaquill.ai
                                         (Bearer {api_key})

Note: This module uses a module-level httpx client (`_client`) managed
by the FastMCP lifespan. This is safe because the server runs as a
single uvicorn process (no multi-worker). The orchestrator (Docker/K8s)
handles horizontal scaling.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

from vaquill_mcp import __version__
from vaquill_mcp.descriptions import TOOL_DESCRIPTIONS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_BASE_URL = os.environ.get("VAQUILL_BASE_URL", "https://api.vaquill.ai").rstrip("/")
_TIMEOUT = float(os.environ.get("VAQUILL_TIMEOUT", "120"))

# Shared httpx client -- created in lifespan, auth injected per-request.
# Single-process only; see module docstring.
_client: httpx.AsyncClient | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_api_key() -> str:
    """Extract API key from the Streamable HTTP URL path ``/s/{api_key}``."""
    try:
        request = get_http_request()
    except RuntimeError:
        raise ValueError(
            "Cannot extract API key -- not running in HTTP context. "
            "The remote server requires Streamable HTTP transport."
        ) from None

    api_key: str = request.path_params.get("api_key", "")
    if not api_key:
        raise ValueError("Missing API key in URL path. Expected /s/{your_api_key}")
    return api_key


async def _call_api(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """Make an authenticated request to the Vaquill API.

    Injects the per-request API key as a Bearer token.
    Returns the JSON response, or an error dict on failure.
    """
    if _client is None:
        return {"error": "Server not ready -- httpx client not initialized."}

    try:
        api_key = _get_api_key()
    except ValueError as exc:
        return {"error": str(exc)}

    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = await _client.request(method, path, headers=headers, **kwargs)
        response.raise_for_status()
        try:
            return response.json()
        except (ValueError, Exception):
            return {"error": "API returned a non-JSON response. Please retry."}
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        try:
            body = exc.response.json()
            msg = body.get("error", {}).get("message", str(exc))
        except Exception:
            msg = str(exc)

        if status == 401:
            return {"error": "Invalid API key. Check your key at https://www.vaquill.ai/settings"}
        if status == 402:
            return {"error": f"Insufficient credits. {msg}"}
        if status == 429:
            return {"error": "Rate limited. Please wait and try again."}
        return {"error": f"API error ({status}): {msg}"}
    except httpx.TimeoutException:
        return {"error": "Request timed out. Try 'standard' mode or a simpler query."}
    except httpx.ConnectError:
        return {"error": "Cannot reach Vaquill API. The service may be temporarily unavailable."}
    except (httpx.DecodingError, httpx.ReadError):
        return {"error": "Received an invalid response from the API. Please retry."}
    except httpx.HTTPError as exc:
        logger.exception("Unexpected httpx error: %s", exc)
        return {"error": "An unexpected error occurred communicating with the API."}


def _build_filters(**kwargs: Any) -> dict[str, Any] | None:
    """Build a camelCase filters dict, omitting ``None`` values."""
    mapping = {
        "court_type": "courtType",
        "court_name": "courtName",
        "year_from": "yearFrom",
        "year_to": "yearTo",
        "country_code": "countryCode",
    }
    filters = {
        camel: kwargs[snake]
        for snake, camel in mapping.items()
        if kwargs.get(snake) is not None
    }
    return filters or None


# ---------------------------------------------------------------------------
# Server + Lifespan
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
    """Manage the shared httpx client lifecycle."""
    global _client
    _client = httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={
            "User-Agent": f"vaquill-mcp-remote/{__version__}",
            "Accept": "application/json",
        },
        timeout=httpx.Timeout(_TIMEOUT, connect=10.0),
    )
    logger.info("Remote MCP server started (base_url=%s)", _BASE_URL)
    try:
        yield
    finally:
        await _client.aclose()
        _client = None
        logger.info("Remote MCP server stopped")


mcp = FastMCP("Vaquill Legal Research", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(description=TOOL_DESCRIPTIONS["ask_legal_question"])
async def ask_legal_question(
    question: str,
    mode: Literal["standard", "deep"] = "standard",
    sources: bool = True,
    max_sources: int = 5,
    chat_history: list[dict[str, str]] | None = None,
    country_code: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "question": question,
        "mode": mode,
        "sources": sources,
        "maxSources": max_sources,
    }
    if chat_history:
        body["chatHistory"] = chat_history
    if country_code:
        body["countryCode"] = country_code
    return await _call_api("POST", "/api/v1/ask", json=body)


@mcp.tool(description=TOOL_DESCRIPTIONS["search_legal_cases"])
async def search_legal_cases(
    query: str,
    court_type: str | None = None,
    court_name: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    country_code: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query, "page": page, "pageSize": page_size}
    filters = _build_filters(
        court_type=court_type,
        court_name=court_name,
        year_from=year_from,
        year_to=year_to,
        country_code=country_code,
    )
    if filters:
        body["filters"] = filters
    return await _call_api("POST", "/api/v1/research/search", json=body)


@mcp.tool(description=TOOL_DESCRIPTIONS["quick_search"])
async def quick_search(
    query: str,
    top_k: int = 3,
    court_type: str | None = None,
    court_name: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    country_code: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query, "topK": top_k}
    filters = _build_filters(
        court_type=court_type,
        court_name=court_name,
        year_from=year_from,
        year_to=year_to,
        country_code=country_code,
    )
    if filters:
        body["filters"] = filters
    return await _call_api("POST", "/api/v1/research/quick", json=body)


@mcp.tool(description=TOOL_DESCRIPTIONS["resolve_citation"])
async def resolve_citation(
    citation: str,
    country_code: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"citation": citation}
    if country_code:
        params["country_code"] = country_code
    return await _call_api("GET", "/api/v1/citations/resolve", params=params)


@mcp.tool(description=TOOL_DESCRIPTIONS["search_cases_by_citation"])
async def search_cases_by_citation(
    query: str,
    limit: int = 10,
    court_code: str | None = None,
    year_start: int | None = None,
    year_end: int | None = None,
    validity_status: str | None = None,
    country_code: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"q": query, "limit": limit}
    if court_code:
        params["court_code"] = court_code
    if year_start is not None:
        params["year_start"] = year_start
    if year_end is not None:
        params["year_end"] = year_end
    if validity_status:
        params["validity_status"] = validity_status
    if country_code:
        params["country_code"] = country_code
    return await _call_api("GET", "/api/v1/citations/cases/search", params=params)


@mcp.tool(description=TOOL_DESCRIPTIONS["lookup_case"])
async def lookup_case(
    citation: str,
    country_code: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"citation": citation}
    if country_code:
        params["country_code"] = country_code
    return await _call_api("GET", "/api/v1/citations/cases/lookup", params=params)


@mcp.tool(description=TOOL_DESCRIPTIONS["get_citation_network"])
async def get_citation_network(
    citation: str,
    direction: Literal["outbound", "inbound", "both"] = "both",
    depth: int = 2,
    limit: int = 50,
    country_code: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "citation": citation,
        "direction": direction,
        "depth": depth,
        "limit": limit,
    }
    if country_code:
        params["country_code"] = country_code
    return await _call_api("GET", "/api/v1/citations/cases/network", params=params)


@mcp.tool(description=TOOL_DESCRIPTIONS["get_pricing"])
async def get_pricing() -> dict[str, Any]:
    return await _call_api("GET", "/api/v1/api-credits/pricing")
