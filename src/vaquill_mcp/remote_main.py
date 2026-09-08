"""Entry point for the remote Vaquill MCP server.

Serves BOTH jurisdictions from one process, over Streamable HTTP, at TWO URL
shapes each:

    https://mcp.vaquill.ai/mcp                US primary law
    https://mcp.vaquill.ai/in/mcp             Indian legislation
    https://mcp.vaquill.ai/s/{api_key}        US primary law, key in the path
    https://mcp.vaquill.ai/in/s/{api_key}     Indian legislation, key in the path

The `/mcp` pair takes the key as `Authorization: Bearer`. The `/s/{api_key}`
pair takes it in the path, for the Claude.ai authless integration that cannot
send a header, and stays live because customers already use those URLs.

WHY `/mcp` EXISTS AS WELL
=========================

Two reasons, and the first is not obvious enough to leave undocumented.

1. claude.ai will not talk to an MCP endpoint on any other path. It completes
   the OAuth token exchange with a clean 200, then silently never sends the MCP
   request and loops into re-registration (anthropics/claude-ai-mcp#423). Claude
   Code and ChatGPT work on any path, so the failure looks like an auth bug for
   days. `/s/{api_key}` is exactly the shape that triggers it.
2. A key in a URL is a documented defect, not a preference. Anthropic's
   connector authentication guidance calls tokens in a connector URL "not
   recommended" because URLs are recorded in server logs, proxies and browsing
   history, and the MCP specification prohibits them outright.

`/mcp` works with or without OAuth. A `vq_key_` sent as a bearer token
authenticates there exactly as it does at `/s/_`, and continues to once OAuth is
switched on: the two credential shapes are composed with `MultiAuth`, not traded
for one another. Nothing that works today stops working when OAuth arrives.

WHY TWO APPS PER JURISDICTION RATHER THAN ONE WITH A FILTER
===========================================================

Each app derives its whole catalogue from one jurisdiction's OpenAPI document,
and the documents are disjoint. A caller reaching `/s/{key}` gets US tools
because that app was built from the US document; there is no per-request
jurisdiction check to get wrong, and no way for an India tool to appear in a US
listing. The mount path selects an app, it does not filter one.

Each URL shape gets its own `create_remote_server()` instance, because auth is
attached per server: `/mcp` may carry an OAuth provider and `/s/{api_key}` must
not, or `RequireAuthMiddleware` would 401 the header-less requests that URL
exists to serve. Both instances derive from the SAME OpenAPI document, so the
two shapes cannot present different catalogues.

NO JURISDICTION CONFIGURATION
=============================

Jurisdiction is passed as an argument, so there is nothing to set and nothing
to set wrongly. `VAQUILL_JURISDICTION` still applies to the STDIO server, where
one process really does serve one user.

OAuth IS configuration, and is OFF unless configured. Absent the
`VAQUILL_OAUTH_*` group the server behaves exactly as it did before OAuth
existed, so enabling it is a config change rather than a release. A PARTIAL
group raises at startup instead of serving a half-built discovery document,
which Claude caches globally by URL for about five minutes across all users.

Environment variables:
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

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
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
    from starlette.middleware import Middleware

    from vaquill_mcp.oauth import (
        BrandSkinMiddleware,
        build_auth_provider,
        build_connector_key_resolver,
    )
    from vaquill_mcp.remote import create_remote_server

    auth = build_auth_provider()
    resolver = build_connector_key_resolver()
    if auth is None:
        logger.info("OAuth is not configured; /mcp accepts vq_key_ bearer tokens only")

    sub_apps = []
    routes = []
    for jurisdiction, prefix in _MOUNTS:
        # TWO SERVERS PER JURISDICTION, not one serving two apps, and the reason
        # is auth rather than tidiness. FastMCP reads `self.auth` when it builds
        # each HTTP app, so a provider on one instance reaches every mount built
        # from it -- and `RequireAuthMiddleware` 401s a request that carries no
        # Authorization header, which is precisely what a `/s/{api_key}` caller
        # sends. Sharing an instance would 401 every existing customer URL the
        # moment OAuth was switched on.
        #
        # The cost is one extra OpenAPI fetch and one extra catalogue per
        # jurisdiction at boot. Both apps still derive from the SAME document,
        # so the two URL shapes cannot present different tool sets.
        # OAuth goes ONLY on the jurisdiction mounted at the origin, and that
        # is a structural constraint rather than a preference. RFC 9728
        # discovery is served from the origin's `/.well-known/*`, but a
        # FastMCP app knows only its own `path` and nothing about the Mount
        # above it, so the app under `/in` advertises `resource:
        # https://mcp.vaquill.ai/mcp` (the wrong URL) and serves its document
        # at `/in/.well-known/oauth-protected-resource/mcp` (the wrong
        # location, and `/.well-known/oauth-protected-resource/in/mcp` 404s).
        # Both were MEASURED. Anthropic requires the `resource` field to match
        # the URL the user typed, path included, so an India OAuth connect
        # would fail discovery in a way that reads as "couldn't reach the MCP
        # server".
        #
        # India therefore keeps working exactly as it does today, on a
        # `vq_key_` bearer or the path form. Giving it OAuth needs its own
        # origin (a subdomain) or the well-known routes hoisted to the parent,
        # and neither is in scope for making the US plugin work in Cowork.
        mount_auth = auth if prefix == "" else None
        key_server = create_remote_server(jurisdiction, resolver=resolver)
        mcp_server = create_remote_server(
            jurisdiction, auth=mount_auth, resolver=resolver
        )

        # Each app's `path` is relative to its mount, and the two must not share
        # a mount prefix -- Starlette hands a request to the FIRST Mount whose
        # prefix matches and never falls through to a later one, so a second app
        # mounted alongside would simply be unreachable.
        key_app = key_server.http_app(path="/{api_key}", transport="streamable-http")
        # The brand skin goes on the `/mcp` app only, because only that app
        # serves HTML: FastMCP renders the OAuth consent screen there and
        # exposes no way to style it. The middleware passes everything that
        # is not text/html straight through, so the MCP transport itself is
        # untouched.
        mcp_app = mcp_server.http_app(
            path="/mcp",
            transport="streamable-http",
            middleware=[Middleware(BrandSkinMiddleware)],
        )
        sub_apps.extend((key_app, mcp_app))

        # `/mcp` is a ROUTE inside the mounted app, never a Mount prefix of its
        # own. Mounting at "/mcp" with an inner "/" route makes a request to the
        # bare "/mcp" miss and come back as a 307 to "/mcp/" (measured), and a
        # redirect on this path is how the Authorization header gets dropped.
        #
        # Mount("") is not valid, so the root jurisdiction mounts at "/".
        routes.append(Mount(f"{prefix}/s", app=key_app))
        routes.append(Mount(prefix or "/", app=mcp_app))

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

    async def openai_apps_challenge(_request):
        """Domain-verification token for the OpenAI plugin/app directory.

        OpenAI proves you control the MCP host before it will publish a plugin,
        by fetching an exact token from this path on the MCP host name (or a
        parent). Served from an environment variable rather than a committed
        file because the token is issued per submission and rotates: a redeploy
        must be able to answer a NEW challenge without a code change.

        404 while unset, which is the honest answer, and identical to the state
        before this route existed. Returned as text/plain because the value is a
        bare token and not JSON.
        """
        token = os.environ.get("OPENAI_APPS_CHALLENGE_TOKEN", "").strip()
        if not token:
            return PlainTextResponse("Not Found", status_code=404)
        return PlainTextResponse(token)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        """Run every mounted app's lifespan.

        Starlette does NOT start a mounted app's lifespan for it. Without this
        each FastMCP app's httpx client would never be created, and every tool
        call would fail on a closed session. `AsyncExitStack` also guarantees
        the ones that did start are unwound if a later one raises.

        Four apps, four servers: each URL shape owns its own `FastMCP` and its
        own httpx client, so every one of them needs entering here.
        """
        async with contextlib.AsyncExitStack() as stack:
            for app in sub_apps:
                await stack.enter_async_context(app.lifespan(app))
            yield

    # Health first, then "/in/s", "/in", "/s", and the root catch-all last.
    # Order is load-bearing in both directions: Mount("/") matches everything,
    # so anything it must not swallow has to be routed before it, and Mount
    # matches on PREFIX, so "/in/s" has to precede "/in" or every path-key call
    # to India lands in the app that only knows "/mcp".
    return Starlette(
        routes=[
            Route("/health", health),
            Route(
                "/.well-known/openai-apps-challenge",
                openai_apps_challenge,
            ),
            *routes,
        ],
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
        ", ".join(f"{j} at {p}/mcp and {p}/s/{{api_key}}" for j, p in _MOUNTS),
        host,
        port,
    )
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
