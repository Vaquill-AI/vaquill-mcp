"""Entry point for the hosted Indian-regulatory MCP server.

Serves the five regulatory tools over Streamable HTTP at the ROOT of its own
origin:

    https://<regulatory host>/mcp             bearer `vq_key_...` or OAuth
    https://<regulatory host>/s/{api_key}     key in the path, for authless clients

WHY ITS OWN ORIGIN, AND NOT A PATH ON mcp.vaquill.ai
====================================================

OAuth only works at an origin root. RFC 9728 discovery is served from the
origin's `/.well-known/*`, but a FastMCP app knows only its own `path` and
nothing about a Mount above it, so an app under a prefix advertises the WRONG
`resource` URL and serves its document at the WRONG location (measured, and
recorded in `remote_main.build_app`: `mount_auth = auth if prefix == "" else
None`). Anthropic requires `resource` to match the URL the user typed, path
included. This corpus has to be connectable from Harvey and Claude over OAuth,
so it gets a subdomain, mounted at "/", and the problem does not arise.

This module is SEPARATE from `remote_main` on purpose. `remote_main._MOUNTS`
drives the live US and India connectors, which exact tool-count tests protect;
a third entry there would put a different corpus behind the same process, the
same tool budget and the same failure domain. A separate process also lets this
one be deployed, scaled and rolled back alone.

ONE DEPLOYMENT PER ORIGIN
=========================

OAuth is configured by `VAQUILL_PUBLIC_URL`, and that is THIS origin, so the
`VAQUILL_OAUTH_*` group on this deployment is its own: it names this host, and
the upstream authorization server must list this host's callback. Absent the
group the server behaves as bearer-only, exactly as `remote_main` does.

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
    """Compose the ASGI app for the regulatory origin."""
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
    from vaquill_mcp.regulatory import create_regulatory_server, fetch_regulatory_costs
    from vaquill_mcp.remote import _PerRequestBearerAuth

    auth = build_auth_provider()
    resolver = build_connector_key_resolver()
    if auth is None:
        logger.info("OAuth is not configured; /mcp accepts vq_key_ bearer tokens only")

    base_url = get_base_url()
    # ONE client for both servers, owned and closed HERE: `create_regulatory_server`
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
    costs = fetch_regulatory_costs(base_url)

    # TWO SERVERS, not one serving two apps, for the reason `remote_main` gives:
    # FastMCP reads `self.auth` when it builds each HTTP app, and
    # `RequireAuthMiddleware` 401s a request with no Authorization header, which
    # is exactly what a `/s/{api_key}` caller sends. Sharing an instance would
    # 401 every path-key URL the moment OAuth was switched on. Both are built
    # from the same function, so the two URL shapes cannot present different
    # catalogues.
    key_server = create_regulatory_server(client, costs=costs)
    mcp_server = create_regulatory_server(client, auth=auth, costs=costs)

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
                "corpus": "regulatory",
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
    """Start the regulatory MCP server on Streamable HTTP transport."""
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
        "Regulatory MCP serving /mcp and /s/{api_key} on %s:%s",
        host,
        port,
    )
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
