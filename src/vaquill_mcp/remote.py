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

THE CATALOGUE IS DERIVED, NOT DECLARED
======================================

Every tool comes from the published OpenAPI document, through the same
`OpenAPIProvider` the stdio server uses. This module contains no tool
definitions at all, and that is the point.

It used to declare 28 tools by hand, and hand-maintenance failed twice in a
month. It shipped 9 of 28 while `server.json` and the README pointed every
Claude.ai, Cursor and ChatGPT user at it (fixed 2026-08-31). Then five of the
remaining tools -- `search_legal_cases`, `quick_search`, `resolve_citation`,
`lookup_case`, `get_citation_network` -- kept calling `/research/*` and
`/citations/*` for a month after those routers were deleted, and every one of
them 404'd. Nothing failed in CI either time, because a hand-written catalogue
has nothing to be compared against.

Deriving removes the class rather than guarding it: a retired endpoint leaves
the document, so its tool cannot survive; a new endpoint appears without a
release.

ONE APP SERVES ONE JURISDICTION
===============================

Each server is built for exactly one jurisdiction and derives every tool from
that jurisdiction's OpenAPI document. The two documents are disjoint by
construction (see the backend's `test_jurisdiction_openapi_separation.py`), so
the US app cannot expose an Indian tool: there is no India path in the spec it
read. Nothing filters anything, which is why nothing can be filtered wrongly.

`remote_main` mounts both apps in one process, at `/s/{api_key}` and
`/in/s/{api_key}`. That is a routing decision, not an isolation one: a caller
reaches one app or the other, and each app's catalogue was fixed when it was
built. The jurisdiction is a PARAMETER here, never an environment variable, so
running the pair needs no configuration at all.

Note: This module uses a module-level httpx client (`_client`) managed
by the FastMCP lifespan. This is safe because the server runs as a
single uvicorn process (no multi-worker). The orchestrator (Docker/K8s)
handles horizontal scaling.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Generator
from typing import Any

# httpx2, not httpx2. fastmcp 4 deprecated passing an `httpx2.AsyncClient` to
# `OpenAPIProvider` ("temporarily accepted via duck typing... will be rejected in
# a future release") and ships httpx2 as a hard dependency. httpx2 is a drop-in
# fork with the same public API, so this is an import swap, not a rewrite.
import httpx2
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.providers.openapi import OpenAPIProvider

from vaquill_mcp import __version__
from vaquill_mcp.aliases import register_aliases
from vaquill_mcp.config import _SPEC_PATHS, get_base_url, get_timeout
from vaquill_mcp.ordering import DeterministicToolOrder
from vaquill_mcp.prompts import register_prompts
from vaquill_mcp.resources import register_resources
from vaquill_mcp.server import (
    _ROUTE_MAPS,
    _build_tool_costs,
    _derive_mcp_names,
    _fetch_openapi_spec,
    _fetch_public_costs,
    _make_customize_component,
    published_tool_names,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-request authentication
# ---------------------------------------------------------------------------


def _get_api_key() -> str:
    """Extract API key from Bearer header (preferred) or URL path ``/s/{api_key}``.

    Order:
        1. ``Authorization: Bearer <key>`` header
        2. URL path parameter ``/s/{api_key}`` (simple paste for Claude.ai)
    """
    try:
        request = get_http_request()
    except Exception:
        raise ValueError(
            "Cannot extract API key -- not running in HTTP context. "
            "The remote server requires Streamable HTTP transport."
        ) from None

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        if token:
            return token

    api_key: str = request.path_params.get("api_key", "")
    if api_key and api_key != "_":
        return api_key

    raise ValueError(
        "Missing API key. Provide via Authorization: Bearer header "
        "or URL path /s/{your_api_key}"
    )


class _PerRequestBearerAuth(httpx2.Auth):
    """Attach the CALLER's API key to each outgoing request.

    The stdio server can put a fixed Authorization header on its client because
    one process serves one user. This one serves everybody, so the key has to be
    read at request time from the HTTP context and can never be cached on the
    client. Doing it as an `httpx2.Auth` rather than at the call site is what
    lets `OpenAPIProvider` own the requests: the provider builds and sends them,
    and the key still arrives.
    """

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, None, None]:
        request.headers["Authorization"] = f"Bearer {_get_api_key()}"
        yield request


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def create_remote_server(jurisdiction: str = "US") -> FastMCP:
    """Build the remote server for ONE jurisdiction.

    Mirrors `server.create_server()` deliberately. The two entry points differ
    only in how the API key reaches the request; everything that decides WHICH
    tools exist is shared, so the hosted and stdio catalogues cannot diverge.

    `jurisdiction` is an argument rather than an environment read on purpose.
    The hosted deployment serves BOTH, so a process-wide env var could only
    describe one of them, and deploying the pair would need configuration that
    can be set wrong. The stdio server still reads `VAQUILL_JURISDICTION`,
    because there one process genuinely does serve one user.
    """
    base_url = get_base_url()
    timeout = get_timeout()
    if jurisdiction not in _SPEC_PATHS:
        raise ValueError(
            f"jurisdiction must be one of {sorted(_SPEC_PATHS)}, got {jurisdiction!r}"
        )

    openapi_spec = _fetch_openapi_spec(base_url, jurisdiction)
    # No API key at startup, so the public matrix is used. See
    # `_fetch_public_costs` for why that is complete rather than a compromise.
    tool_costs = _build_tool_costs(openapi_spec, _fetch_public_costs(base_url))

    # ONE client, built here because the provider needs it at construction
    # time, and closed by the lifespan below. An earlier draft built a second
    # one inside the lifespan, which left two alive and closed only one.
    client = httpx2.AsyncClient(
        base_url=base_url,
        auth=_PerRequestBearerAuth(),
        headers={
            "User-Agent": f"vaquill-mcp-remote/{__version__}",
            "Accept": "application/json",
        },
        timeout=httpx2.Timeout(timeout, connect=10.0),
    )

    @contextlib.asynccontextmanager
    async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
        """Close the shared httpx client on shutdown."""
        logger.info(
            "Remote MCP server started (base_url=%s, jurisdiction=%s)",
            base_url,
            jurisdiction,
        )
        try:
            yield
        finally:
            await client.aclose()
            logger.info("Remote MCP server stopped")

    name = (
        "Vaquill Legal Research"
        if jurisdiction == "US"
        else f"Vaquill Legal Research ({jurisdiction})"
    )
    server = FastMCP(name, lifespan=_lifespan)

    @server.custom_route("/health", methods=["GET"])
    async def health_check(_request: Any) -> Any:
        """Health endpoint for Docker/load balancer probes."""
        from starlette.responses import JSONResponse

        return JSONResponse(
            {
                "status": "ok",
                "service": "vaquill-mcp",
                "version": __version__,
                "jurisdiction": jurisdiction,
            }
        )

    server.add_provider(
        OpenAPIProvider(
            openapi_spec=openapi_spec,
            client=client,
            mcp_names=_derive_mcp_names(openapi_spec),
            route_maps=_ROUTE_MAPS,
            mcp_component_fn=_make_customize_component(tool_costs),
            # Same reason as the stdio server: the live API is the source of
            # truth and some fields are nullable in practice but not declared.
            validate_output=False,
        )
    )

    # Both mirror the stdio server deliberately, for the same reason the
    # provider config does: the hosted and stdio catalogues must not diverge.
    server.add_middleware(DeterministicToolOrder())
    register_aliases(
        server, client, jurisdiction, base_url, published_tool_names(openapi_spec)
    )
    register_resources(server, client, jurisdiction)
    register_prompts(server, jurisdiction)
    return server
