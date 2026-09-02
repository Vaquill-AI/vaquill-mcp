"""Entry point for the remote Vaquill MCP server.

Serves BOTH jurisdictions from one process, over Streamable HTTP:

    https://mcp.vaquill.ai/s/{api_key}        US primary law
    https://mcp.vaquill.ai/in/s/{api_key}     Indian legislation

Users paste one of those into Claude.ai as an authless MCP integration. The key
sits in the path because that integration cannot send an Authorization header.

WHY TWO APPS RATHER THAN ONE WITH A FILTER
==========================================

Each app derives its whole catalogue from one jurisdiction's OpenAPI document,
and the documents are disjoint. A caller reaching `/s/{key}` gets US tools
because that app was built from the US document; there is no per-request
jurisdiction check to get wrong, and no way for an India tool to appear in a US
listing. The mount path selects an app, it does not filter one.

NO CONFIGURATION
================

The pair needs no environment variables beyond the ones that already existed.
Jurisdiction is passed as an argument, so there is nothing to set and nothing
to set wrongly. `VAQUILL_JURISDICTION` still applies to the STDIO server, where
one process really does serve one user.

Environment variables:
    HOST            Listen address (default: 0.0.0.0)
    PORT            Listen port (default: 8000)
    VAQUILL_BASE_URL  API base URL (default: https://api.vaquill.ai)
    VAQUILL_TIMEOUT   Request timeout in seconds (default: 120)
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

logger = logging.getLogger(__name__)

# Mount prefix per jurisdiction. US is at the root so the URL customers already
# use keeps working unchanged; India is additive.
_MOUNTS: tuple[tuple[str, str], ...] = (
    ("IN", "/in"),
    ("US", ""),
)


def build_app() -> Starlette:
    """Compose one ASGI app serving every jurisdiction."""
    from vaquill_mcp import __version__
    from vaquill_mcp.remote import create_remote_server

    sub_apps = []
    routes = []
    for jurisdiction, prefix in _MOUNTS:
        server = create_remote_server(jurisdiction)
        # `path` is relative to the mount, so both are "/s/{api_key}" and the
        # prefix does the separating.
        app = server.http_app(path="/s/{api_key}", transport="streamable-http")
        sub_apps.append(app)
        # Mount("") is not valid, so the root jurisdiction mounts at "/".
        routes.append(Mount(prefix or "/", app=app))

    async def health(_request):
        """Health endpoint for Docker/load balancer probes.

        Declared on the PARENT rather than relying on a mounted app's own
        `/health`: the US app mounts at "/" and would answer, but that couples
        the probe to which jurisdiction happens to sit at the root.
        """
        return JSONResponse(
            {
                "status": "ok",
                "service": "vaquill-mcp",
                "version": __version__,
                "jurisdictions": [j for j, _ in _MOUNTS],
            }
        )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        """Run every mounted app's lifespan.

        Starlette does NOT start a mounted app's lifespan for it. Without this
        each FastMCP app's httpx client would never be created, and every tool
        call would fail on a closed session. `AsyncExitStack` also guarantees
        the ones that did start are unwound if a later one raises.
        """
        async with contextlib.AsyncExitStack() as stack:
            for app in sub_apps:
                await stack.enter_async_context(app.lifespan(app))
            yield

    # Health first, then the /in prefix, then the root catch-all. Order is
    # load-bearing: Mount("/") matches everything, so anything it must not
    # swallow has to be routed before it.
    return Starlette(
        routes=[Route("/health", health), *routes],
        lifespan=lifespan,
    )


def main() -> None:
    """Start the remote MCP server on Streamable HTTP transport."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    logger.info(
        "Remote MCP serving %s on %s:%s",
        ", ".join(f"{j} at {p or '/'}/s/{{api_key}}" for j, p in _MOUNTS),
        host,
        port,
    )
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
