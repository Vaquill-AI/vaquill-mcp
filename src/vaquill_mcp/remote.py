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


# The public corpusType vocabulary, mirroring `PublicCorpusType` in
# app/services/us_corpus/us_statutes_corpus_types.py on the API side. The five
# STATE_SCOPED ones pair with `state`.
#
# This was `Literal["USC", "CFR"]` until 2026-08-28, which schema-rejected every
# state corpus through the hosted server: the 50-state statutes, regulations,
# constitutions and court rules were unreachable here while the tool
# description promised them. The stdio server never had the bug because it
# derives its schema from the OpenAPI document.
_CorpusType = Literal[
    "USC",
    "CFR",
    "STATE",
    "CONSTITUTION",
    "FEDERAL_RULES",
    "STATE_CONSTITUTION",
    "STATE_RULES",
    "EXECUTIVE_ACTION",
    "REGULATION",
    "FEDERAL_REGISTER",
    "AGENCY_GUIDANCE",
    "SENTENCING_GUIDELINES",
    "US_TAX_TREATY",
    "STATE_AGENCY_GUIDANCE",
    "SESSION_LAW",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_api_key() -> str:
    """Extract API key from Bearer header (preferred) or URL path ``/s/{api_key}``.

    Resolution order:
        1. ``Authorization: Bearer <key>`` header (secure, spec-compliant)
        2. URL path parameter ``/s/{api_key}`` (simple paste for Claude.ai)
    """
    try:
        request = get_http_request()
    except RuntimeError:
        raise ValueError(
            "Cannot extract API key -- not running in HTTP context. "
            "The remote server requires Streamable HTTP transport."
        ) from None

    # 1. Bearer token in Authorization header (preferred)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        if token:
            return token

    # 2. URL path parameter (fallback for simple paste)
    api_key: str = request.path_params.get("api_key", "")
    if api_key and api_key != "_":
        return api_key

    raise ValueError(
        "Missing API key. Provide via Authorization: Bearer header "
        "or URL path /s/{your_api_key}"
    )


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
        except Exception:
            # Was `except (ValueError, Exception)`, in which the second arm makes
            # the first dead. Same behaviour, but the intent is now readable.
            return {"error": "API returned a non-JSON response. Please retry."}
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        try:
            body = exc.response.json()
            msg = body.get("error", {}).get("message", str(exc))
        except Exception:
            msg = str(exc)

        if status == 401:
            return {
                "error": "Invalid API key. Check your key at https://www.vaquill.ai/settings"
            }
        if status == 402:
            return {"error": f"Insufficient credits. {msg}"}
        if status == 429:
            return {"error": "Rate limited. Please wait and try again."}
        return {"error": f"API error ({status}): {msg}"}
    except httpx.TimeoutException:
        return {"error": "Request timed out. Try 'standard' mode or a simpler query."}
    except httpx.ConnectError:
        return {
            "error": "Cannot reach Vaquill API. The service may be temporarily unavailable."
        }
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
# Health Check
# ---------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request: Any) -> Any:
    """Health endpoint for Docker/load balancer probes."""
    from starlette.responses import JSONResponse

    return JSONResponse(
        {"status": "ok", "service": "vaquill-mcp", "version": __version__}
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(description=TOOL_DESCRIPTIONS["search_legal_cases"])
async def search_legal_cases(
    query: str,
    court_type: str | None = None,
    court_name: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    country_code: str | None = None,
    page: int = 1,
    page_size: int = 10,
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


@mcp.tool(description=TOOL_DESCRIPTIONS["search_us_statutes"])
async def search_us_statutes(
    query: str,
    corpus_type: _CorpusType | None = None,
    state: str | None = None,
    code: str | None = None,
    title_number: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query, "limit": limit}
    if corpus_type:
        body["corpusType"] = corpus_type
    if state:
        body["state"] = state
    if code:
        body["code"] = code
    if title_number is not None:
        body["titleNumber"] = title_number
    return await _call_api("POST", "/api/v1/us/statutes/search", json=body)


@mcp.tool(description=TOOL_DESCRIPTIONS["get_us_statute_section"])
async def get_us_statute_section(
    act_id: str,
) -> dict[str, Any]:
    return await _call_api("GET", f"/api/v1/us/statutes/section/{act_id}")


@mcp.tool(description=TOOL_DESCRIPTIONS["get_us_statute_section_text"])
async def get_us_statute_section_text(
    act_id: str,
) -> dict[str, Any]:
    return await _call_api("GET", f"/api/v1/us/statutes/section/{act_id}/body")


# ---------------------------------------------------------------------------
# Section intelligence
#
# These nineteen tools existed in `descriptions.py` and on the stdio server but
# were never declared here, so the hosted server published 9 of 28. The stdio
# server derives its catalogue from the OpenAPI document and so cannot drift;
# this module declares tools by hand and did. `test_remote_publishes_every_tool`
# is what stops the two diverging again.
# ---------------------------------------------------------------------------


@mcp.tool(description=TOOL_DESCRIPTIONS["get_sections_batch"])
async def get_sections_batch(act_ids: list[str]) -> dict[str, Any]:
    return await _call_api(
        "POST", "/api/v1/us/statutes/sections", json={"actIds": act_ids}
    )


@mcp.tool(description=TOOL_DESCRIPTIONS["get_section_neighbors"])
async def get_section_neighbors(act_id: str, limit: int = 5) -> dict[str, Any]:
    return await _call_api(
        "GET", f"/api/v1/us/statutes/section/{act_id}/related", params={"limit": limit}
    )


@mcp.tool(description=TOOL_DESCRIPTIONS["get_section_cited_by"])
async def get_section_cited_by(act_id: str, limit: int = 20) -> dict[str, Any]:
    return await _call_api(
        "GET", f"/api/v1/us/statutes/section/{act_id}/cited-by", params={"limit": limit}
    )


@mcp.tool(description=TOOL_DESCRIPTIONS["get_section_definitions"])
async def get_section_definitions(act_id: str) -> dict[str, Any]:
    return await _call_api("GET", f"/api/v1/us/statutes/section/{act_id}/definitions")


@mcp.tool(description=TOOL_DESCRIPTIONS["get_section_cross_state"])
async def get_section_cross_state(act_id: str, limit: int = 10) -> dict[str, Any]:
    return await _call_api(
        "GET",
        f"/api/v1/us/statutes/section/{act_id}/cross-state",
        params={"limit": limit},
    )


@mcp.tool(description=TOOL_DESCRIPTIONS["get_section_changes"])
async def get_section_changes(act_id: str) -> dict[str, Any]:
    return await _call_api("GET", f"/api/v1/us/statutes/section/{act_id}/changes")


@mcp.tool(description=TOOL_DESCRIPTIONS["resolve_statute_citation"])
async def resolve_statute_citation(citation: str) -> dict[str, Any]:
    return await _call_api(
        "GET", "/api/v1/us/statutes/resolve", params={"citation": citation}
    )


# ---------------------------------------------------------------------------
# Corpus discovery (free: authenticated and rate-limited, but no credits)
# ---------------------------------------------------------------------------


@mcp.tool(description=TOOL_DESCRIPTIONS["list_statute_divisions"])
async def list_statute_divisions(
    corpus_type: _CorpusType | None = None,
    state: str | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if corpus_type:
        params["corpusType"] = corpus_type
    if state:
        params["state"] = state
    if code:
        params["code"] = code
    return await _call_api("GET", "/api/v1/us/statutes/divisions", params=params)


@mcp.tool(description=TOOL_DESCRIPTIONS["list_statutes_coverage"])
async def list_statutes_coverage() -> dict[str, Any]:
    return await _call_api("GET", "/api/v1/us/statutes/coverage")


@mcp.tool(description=TOOL_DESCRIPTIONS["list_statutes_laws"])
async def list_statutes_laws(
    state: str | None = None,
    corpus_type: _CorpusType | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if state:
        params["state"] = state
    if corpus_type:
        params["corpusType"] = corpus_type
    return await _call_api("GET", "/api/v1/us/statutes/laws", params=params)


# ---------------------------------------------------------------------------
# Law Change Alerts (boards and watches)
# ---------------------------------------------------------------------------


@mcp.tool(description=TOOL_DESCRIPTIONS["list_boards"])
async def list_boards(
    corpus_type: str | None = None,
    state: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if corpus_type:
        params["corpusType"] = corpus_type
    if state:
        params["state"] = state
    return await _call_api("GET", "/api/v1/boards", params=params)


@mcp.tool(description=TOOL_DESCRIPTIONS["create_watch"])
async def create_watch(
    corpus_type: str,
    channel: Literal["webhook", "email", "both"],
    state: str | None = None,
    webhook_url: str | None = None,
    email_address: str | None = None,
    act_ids: list[str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"corpusType": corpus_type, "channel": channel}
    if state:
        body["state"] = state
    if webhook_url:
        body["webhookUrl"] = webhook_url
    if email_address:
        body["emailAddress"] = email_address
    if act_ids:
        body["actIds"] = act_ids
    return await _call_api("POST", "/api/v1/watches", json=body)


@mcp.tool(description=TOOL_DESCRIPTIONS["list_watches"])
async def list_watches(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    return await _call_api(
        "GET", "/api/v1/watches", params={"limit": limit, "offset": offset}
    )


@mcp.tool(description=TOOL_DESCRIPTIONS["update_watch"])
async def update_watch(
    watch_id: str,
    is_active: bool | None = None,
    webhook_url: str | None = None,
    email_address: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if is_active is not None:
        body["isActive"] = is_active
    if webhook_url:
        body["webhookUrl"] = webhook_url
    if email_address:
        body["emailAddress"] = email_address
    return await _call_api("PATCH", f"/api/v1/watches/{watch_id}", json=body)


@mcp.tool(description=TOOL_DESCRIPTIONS["delete_watch"])
async def delete_watch(watch_id: str) -> dict[str, Any]:
    return await _call_api("DELETE", f"/api/v1/watches/{watch_id}")


@mcp.tool(description=TOOL_DESCRIPTIONS["test_watch"])
async def test_watch(watch_id: str) -> dict[str, Any]:
    return await _call_api("POST", f"/api/v1/watches/{watch_id}/test")


@mcp.tool(description=TOOL_DESCRIPTIONS["list_watch_changes"])
async def list_watch_changes(
    watch_id: str, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    return await _call_api(
        "GET",
        f"/api/v1/watches/{watch_id}/changes",
        params={"limit": limit, "offset": offset},
    )


@mcp.tool(description=TOOL_DESCRIPTIONS["get_watch_change_diff"])
async def get_watch_change_diff(watch_id: str, change_id: str) -> dict[str, Any]:
    return await _call_api(
        "GET", f"/api/v1/watches/{watch_id}/changes/{change_id}/diff"
    )


@mcp.tool(description=TOOL_DESCRIPTIONS["list_watch_deliveries"])
async def list_watch_deliveries(
    watch_id: str, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    return await _call_api(
        "GET",
        f"/api/v1/watches/{watch_id}/deliveries",
        params={"limit": limit, "offset": offset},
    )
