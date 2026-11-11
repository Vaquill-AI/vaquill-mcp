"""Entry point for the hosted Indian-tribunals MCP server.

Serves the six tribunal tools over Streamable HTTP at the ROOT of its own
origin:

    https://<tribunals host>/mcp             bearer `vq_key_...` or OAuth
    https://<tribunals host>/s/{api_key}     key in the path, for authless clients

This is `regulatory_main` for a different corpus, and every structural choice
here is made for the reason recorded there: its own origin because RFC 9728
discovery is served from an origin's `/.well-known/*` and a FastMCP app under a
Mount prefix advertises the wrong `resource`; separate from `remote_main._MOUNTS`
so a third corpus does not land behind the same process, tool budget and failure
domain; and TWO servers rather than one, because FastMCP reads `self.auth` when
it builds each HTTP app and `RequireAuthMiddleware` 401s a request carrying no
Authorization header, which is exactly what a `/s/{api_key}` caller sends.

WHY A SEPARATE ORIGIN FROM THE REGULATORY ONE
=============================================

One connection should see one corpus. These two are easy to confuse in a way
that produces a wrong answer rather than a missing one: the regulatory corpus
holds what a regulator ISSUED, and this one holds matters BEFORE tribunals,
regulators and boards. A model with both catalogues on one connection can answer
a question about an order with a circular, or report that a tribunal has not
considered something because it searched the wrong corpus. `instructions.py`
gives each origin its own corpus sentence (`TRIB` here), which only works when
the origins are distinct.

Environment variables (a subset of `remote_main`'s, with the same meaning):
    HOST                     Listen address (default: 0.0.0.0)
    PORT                     Listen port (default: 8000)
    VAQUILL_BASE_URL         API base URL (default: https://api.vaquill.ai)
    VAQUILL_TIMEOUT          Request timeout in seconds (default: 120)
    VAQUILL_PUBLIC_URL       This server's public URL, for OAuth metadata
    VAQUILL_INTERNAL_SECRET  Shared secret for connector-key resolution
    VAQUILL_OAUTH_*          Authorization server details; see oauth.py
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator

import httpx2
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from vaquill_mcp.log_redaction import install_key_redaction

logger = logging.getLogger(__name__)


def build_app() -> Starlette:
    """Compose the ASGI app for the tribunals origin."""
    from starlette.middleware import Middleware

    from vaquill_mcp import __version__
    from vaquill_mcp.brand_assets import asset_routes
    from vaquill_mcp.client_identity import make_client_stamp
    from vaquill_mcp.config import get_base_url, get_timeout
    from vaquill_mcp.oauth import (
        BrandSkinMiddleware,
        build_auth_provider,
        build_connector_key_resolver,
    )
    from vaquill_mcp.registration_guard import RegistrationRateLimit
    from vaquill_mcp.remote import _PerRequestBearerAuth
    from vaquill_mcp.tribunals import create_tribunals_server, fetch_tribunals_costs

    auth = build_auth_provider()
    resolver = build_connector_key_resolver()
    if auth is None:
        logger.info("OAuth is not configured; /mcp accepts vq_key_ bearer tokens only")

    base_url = get_base_url()
    # ONE client for both servers, owned and closed HERE: `create_tribunals_server`
    # builds none when it is handed one. The caller's key is read per request by
    # `_PerRequestBearerAuth` and is never held on the client, which serves
    # everybody. The OAuth token is never forwarded; it is resolved to a
    # `vq_key_` first (see `remote._PerRequestBearerAuth`).
    user_agent = f"vaquill-mcp-remote/{__version__}"
    client = httpx2.AsyncClient(
        base_url=base_url,
        auth=_PerRequestBearerAuth(resolver),
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        event_hooks={"request": [make_client_stamp(user_agent)]},
        timeout=httpx2.Timeout(get_timeout(), connect=10.0),
    )
    costs = fetch_tribunals_costs(base_url)

    # Both built from the same function, so the two URL shapes cannot present
    # different catalogues.
    key_server = create_tribunals_server(client, costs=costs)
    mcp_server = create_tribunals_server(client, auth=auth, costs=costs)

    # Each app's `path` is relative to its mount. `/mcp` is a ROUTE inside the
    # app mounted at the root, never a Mount prefix of its own: mounting at
    # "/mcp" with an inner "/" makes the bare "/mcp" a 307 to "/mcp/", and a
    # redirect on this path is how the Authorization header gets dropped.
    key_app = key_server.http_app(path="/{api_key}", transport="streamable-http")
    # The brand skin and the registration limiter go on the `/mcp` app only:
    # it is the one that serves the consent screen and the OAuth routes.
    mcp_app = mcp_server.http_app(
        path="/mcp",
        transport="streamable-http",
        middleware=[Middleware(RegistrationRateLimit), Middleware(BrandSkinMiddleware)],
    )
    sub_apps = (key_app, mcp_app)

    async def health(_request):
        """Health endpoint for Docker/load balancer probes."""
        return JSONResponse(
            {
                "status": "ok",
                "service": "vaquill-mcp",
                "version": __version__,
                "corpus": "tribunals",
            }
        )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        """Run both mounted apps' lifespans, then close the shared client.

        Starlette does NOT start a mounted app's lifespan for it. The client is
        pushed FIRST so it is closed LAST, after every app that could still be
        using it has unwound.
        """
        async with contextlib.AsyncExitStack() as stack:
            stack.push_async_callback(client.aclose)
            for app in sub_apps:
                await stack.enter_async_context(app.lifespan(app))
            yield

    # Order is load-bearing: Mount("/") matches everything and Starlette hands a
    # request to the FIRST matching Mount without falling through. So the probe,
    # the icon routes and "/s" all precede the root catch-all, or `/s/{key}`
    # would reach the app that only knows `/mcp` and come back a bare 404.
    return Starlette(
        routes=[
            Route("/health", health),
            *asset_routes(),
            Mount("/s", app=key_app),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    """Start the tribunals MCP server on Streamable HTTP transport."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # AFTER `basicConfig`, and before anything is served: `/s/{api_key}` puts a
    # customer's key in the URL and uvicorn's access log prints the URL. This
    # also holds the httpx2/httpcore2 loggers at WARNING (their INFO line is the
    # full upstream URL, `?q=` included).
    install_key_redaction()

    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    logger.info(
        "Tribunals MCP serving /mcp and /s/{api_key} on %s:%s",
        host,
        port,
    )
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
