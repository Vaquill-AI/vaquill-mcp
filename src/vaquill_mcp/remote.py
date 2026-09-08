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
from collections.abc import AsyncGenerator, AsyncIterator, Generator
from typing import Any

# httpx2, not httpx2. fastmcp 4 deprecated passing an `httpx2.AsyncClient` to
# `OpenAPIProvider` ("temporarily accepted via duck typing... will be rejected in
# a future release") and ships httpx2 as a hard dependency. httpx2 is a drop-in
# fork with the same public API, so this is an import swap, not a rewrite.
import httpx2
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.providers.openapi import OpenAPIProvider
from mcp.types import Icon

from vaquill_mcp import __version__
from vaquill_mcp.aliases import register_aliases
from vaquill_mcp.config import _SPEC_PATHS, get_base_url, get_timeout
from vaquill_mcp.oauth import ConnectorKeyResolver
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


def _oauth_subject() -> str | None:
    """The verified OAuth subject for this request, or None.

    None covers three distinct cases that all mean "no OAuth identity here":
    the mount has no auth provider (`/s/{api_key}`), the credential was a raw
    `vq_key_` (`RawApiKeyVerifier` sets no subject, deliberately), or there is
    no HTTP context at all. Every one of them falls through to the header and
    path below, which is the pre-OAuth behaviour unchanged.
    """
    try:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
    except Exception:
        return None
    if token is None or token.token.startswith("vq_key_"):
        return None
    return token.subject


def _get_api_key() -> str:
    """Extract API key from Bearer header (preferred) or URL path ``/s/{api_key}``.

    Order:
        1. ``Authorization: Bearer <key>`` header
        2. URL path parameter ``/s/{api_key}`` (simple paste for Claude.ai)

    An OAuth caller has NEITHER, and is handled one level up in
    `_PerRequestBearerAuth.async_auth_flow`, because resolving an OAuth subject
    to a key is an awaitable round trip and this function is synchronous.
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

    def __init__(self, resolver: ConnectorKeyResolver | None = None) -> None:
        self._resolver = resolver

    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, None, None]:
        request.headers["Authorization"] = f"Bearer {_get_api_key()}"
        yield request

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        """The OAuth-aware path, which is the one production actually takes.

        Overridden rather than left to the base class, which defers to the
        synchronous `auth_flow` in a way that cannot await anything: resolving
        an OAuth subject to a `vq_key_` is an HTTP call to the backend.

        The OAuth token itself is NEVER forwarded. The MCP specification says
        plainly that "the MCP server MUST NOT pass through the token it received
        from the MCP client", and resolving to our own key server-side is both
        the compliant shape and the one that leaves metering, credits, rate
        limits and refunds seeing exactly the credential they were built for.
        """
        subject = _oauth_subject()
        if subject and self._resolver is not None:
            key = await self._resolver.resolve(subject)
        else:
            key = _get_api_key()
        request.headers["Authorization"] = f"Bearer {key}"
        yield request


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def create_remote_server(
    jurisdiction: str = "US",
    *,
    auth: object | None = None,
    resolver: ConnectorKeyResolver | None = None,
) -> FastMCP:
    """Build the remote server for ONE jurisdiction.

    Mirrors `server.create_server()` deliberately. The two entry points differ
    only in how the API key reaches the request; everything that decides WHICH
    tools exist is shared, so the hosted and stdio catalogues cannot diverge.

    `jurisdiction` is an argument rather than an environment read on purpose.
    The hosted deployment serves BOTH, so a process-wide env var could only
    describe one of them, and deploying the pair would need configuration that
    can be set wrong. The stdio server still reads `VAQUILL_JURISDICTION`,
    because there one process genuinely does serve one user.

    `auth` is per-SERVER because FastMCP reads `self.auth` when it builds each
    HTTP app, so a provider set here reaches every mount built from this
    instance. The OAuth mount and the path-key mount therefore cannot share one
    instance: `RequireAuthMiddleware` 401s a request carrying no Authorization
    header at all, which is exactly what a `/s/{api_key}` caller sends, so
    enabling OAuth on a shared instance would 401 every existing customer URL.
    `remote_main` builds one server per (jurisdiction, mount) for that reason.
    """
    base_url = get_base_url()
    timeout = get_timeout()
    if jurisdiction not in _SPEC_PATHS:
        raise ValueError(
            f"jurisdiction must be one of {sorted(_SPEC_PATHS)}, got {jurisdiction!r}"
        )

    openapi_spec = _fetch_openapi_spec(base_url, jurisdiction)
    # No API key at startup, so the public matrix is used. See
    # `_fetch_public_costs` for why that is complete rather than a compromise,
    # and why the jurisdiction has to be passed through to it explicitly.
    tool_costs = _build_tool_costs(openapi_spec, _fetch_public_costs(base_url, jurisdiction))

    # ONE client, built here because the provider needs it at construction
    # time, and closed by the lifespan below. An earlier draft built a second
    # one inside the lifespan, which left two alive and closed only one.
    client = httpx2.AsyncClient(
        base_url=base_url,
        auth=_PerRequestBearerAuth(resolver),
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
    # `icons` and `website_url` are not decoration. FastMCP's OAuth consent
    # screen reads them off this object (`fastmcp.icons[0].src`,
    # `fastmcp.website_url`) to render the page a user sees mid-sign-in, and
    # without them it shows FastMCP's own logo and no link. That screen is the
    # one naming the ACTUAL calling client, so it is the one a user checks
    # before granting access to their account, and a generic vendor logo there
    # invites exactly the "is this real?" hesitation it exists to resolve.
    server = FastMCP(
        name,
        lifespan=_lifespan,
        auth=auth,
        icons=[
            Icon(
                src="https://www.vaquill.ai/brand/lockup/vaquill-lockup-color-512w.png",
                mime_type="image/png",
            )
        ],
        website_url="https://www.vaquill.ai",
    )

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
