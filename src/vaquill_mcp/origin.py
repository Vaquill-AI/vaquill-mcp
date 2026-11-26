"""One ASGI app shape for every single-corpus origin, and the registry of them.

WHY THIS MODULE EXISTS
======================

`regulatory_main` and `tribunals_main` were the same 180 lines twice. Measured
on 2026-11-26, with corpus names normalised away, they differed by 67 lines and
almost all of that was docstring: the three things that actually varied were the
server factory, the cost fetcher and one health string. Vaquill is adding
Australia, Canada and further jurisdictions, each needing its own origin for the
reason below, so that copy was about to be made five more times. Five copies of
a `build_app` is five places for the mount order to drift, and the mount order
is behaviour (see `_routes`).

So: the shape lives here ONCE, and an origin is a ROW in `ORIGINS` rather than
a module. Adding a jurisdiction is a registry row, a spec path in
`config._SPEC_PATHS`, a corpus sentence in `instructions.py`, a DNS record and a
deployment. No new Python.

WHY EVERY ORIGIN IS ITS OWN HOST NAME
=====================================

OAuth only works at an origin root. RFC 9728 discovery is served from the
origin's `/.well-known/*`, but a FastMCP app knows only its own `path` and
nothing about a Mount above it, so an app under a prefix advertises the WRONG
`resource` URL and serves its document at the WRONG location. Both were
MEASURED, and the measurement is recorded in `remote_main.build_app`
(`mount_auth = auth if prefix == "" else None`). Anthropic requires `resource`
to match the URL the user typed, path included, so an OAuth connect to a
path-mounted corpus fails discovery in a way that reads to the user as
"couldn't reach the MCP server".

Hence: one corpus, one origin, mounted at "/", and the problem does not arise.

WHAT THIS MODULE DELIBERATELY DOES NOT TOUCH
============================================

`remote_main` is NOT built from this registry and must not be. It is the only
app serving two jurisdictions from one process; it is the live US connector
listed in Anthropic's directory; and its route table, OAuth routes and tool
count are pinned by golden-file tests. Folding it in here would put the one
surface customers already depend on behind a refactor that buys nothing, since
it is one app rather than five. It keeps its own module, unchanged.

THE EDGE IS PART OF "A DNS RECORD", AND IT BITES SILENTLY
=========================================================

A new origin needs its hostname to pass Cloudflare before it serves anything.
MCP clients are bots by every heuristic Super Bot Fight Mode applies: no browser
fingerprint, a JSON POST, no cookies. A host not covered by the zone's MCP skip
rule therefore gets HTTP 403 and a Cloudflare interstitial AT THE EDGE, while
the container behind it is perfectly healthy. The symptom reads like a broken
build or a bad Dokploy config, and the deployment logs show nothing at all,
because the request never arrived.

That rule used to be a hand-maintained host list and it was forgotten every
single time: reg-mcp on 2026-11-11, tribunals-mcp on 2026-11-12, in-mcp on
2026-11-26, three for three. On 2026-11-26 it was changed to
`(http.host eq "mcp.vaquill.ai") or (ends_with(http.host, "-mcp.vaquill.ai"))`,
so any origin named `<slug>-mcp.vaquill.ai` is covered on the day its DNS record
exists and NOTHING on the Cloudflare side needs doing. Verified by canary the
same day: `au-mcp.vaquill.ai`, which exists in neither DNS nor Dokploy, answers
530 (no origin) rather than 403 (blocked), proving the request now reaches past
the bot check.

So the naming convention is load-bearing infrastructure, not cosmetics. Name a
new origin `<slug>-mcp.vaquill.ai`. A host spelled any other way
(`mcp-au.vaquill.ai`, `au.mcp.vaquill.ai`) is NOT covered, will 403, and will
cost somebody the afternoon this paragraph exists to save. If you must use
another spelling, edit the zone rule FIRST and extend its description there, in
the same change.

Note what that rule does and does not do, because widening it is the tempting
wrong fix: it skips bot checks ONLY. WAF, rate limiting and the per-call API key
every server enforces are all still in force. A 403 that survives this rule is
a different rule, so read the live ruleset rather than assuming.

ONE DEPLOYMENT PER ORIGIN
=========================

OAuth is configured by `VAQUILL_PUBLIC_URL`, and that is THIS origin, so each
deployment's `VAQUILL_OAUTH_*` group names its own host and the upstream
authorization server must list that host's callback. Give each its own
`VAQUILL_OAUTH_JWT_SIGNING_KEY` and its own Redis database, so two deployments
never share OAuth state. Absent the group an origin serves bearer keys only,
exactly as `remote_main` does.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx2
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from vaquill_mcp.log_redaction import install_key_redaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Origin:
    """One deployable single-corpus origin.

    `slug` is what `VAQUILL_ORIGIN` names and what `/health` reports as
    `corpus`; it is part of the deployment's contract, so renaming one is a
    breaking change to a probe and to the golden files.

    `factory` builds the FastMCP server and `costs` fetches its price lines.
    Both are imported lazily, by name, so importing this registry does not drag
    every corpus module (and its OpenAPI fetch) into every process.
    """

    slug: str
    description: str
    #: Set for a WHOLE-JURISDICTION origin (every tool the API publishes for that
    #: country). Mutually exclusive with `module`/`factory`: such an origin is
    #: built by `remote.create_remote_server`, which derives its catalogue from
    #: the jurisdiction's OpenAPI document and owns its own client, so there is
    #: no hand-declared tool set and no separate cost fetcher to name.
    jurisdiction: str | None = None
    #: Set for a SINGLE-CORPUS origin whose tools are declared by hand, because
    #: the backend publishes no document for that corpus yet.
    module: str | None = None
    factory: str | None = None
    costs: str | None = None

    def __post_init__(self) -> None:
        corpus = bool(self.module and self.factory and self.costs)
        if bool(self.jurisdiction) == corpus:
            raise ValueError(
                f"origin {self.slug!r} must set EITHER jurisdiction OR "
                "module+factory+costs, never both and never neither"
            )


# Every origin Vaquill deploys from this package, keyed by `VAQUILL_ORIGIN`.
#
# `remote_main` (mcp.vaquill.ai, US at the root plus India on /in) is NOT here,
# for the reason in this module's docstring.
#
# Adding a jurisdiction: add the spec path to `config._SPEC_PATHS`, the corpus
# sentence to `instructions.py`, and a row here. The generic entry point and the
# Dockerfile need no edit.
ORIGINS: dict[str, Origin] = {
    # Whole-jurisdiction origins. The catalogue comes from the jurisdiction's
    # OpenAPI document, so adding a country is this row plus a spec path in
    # `config._SPEC_PATHS` and a corpus sentence in `instructions.py`.
    "india": Origin(
        slug="india",
        jurisdiction="IN",
        # NOT "regulatory publications": those left this origin on 2026-11-26
        # for `reg-mcp`, and this string is what `instructions.py` and the
        # connector directory tell a user the server covers.
        description="Indian legislation, case law and tribunals",
    ),
    # Single-corpus origins, tools declared by hand because no document
    # describes them yet. See `regulatory.py`'s module docstring.
    "regulatory": Origin(
        slug="regulatory",
        module="vaquill_mcp.regulatory",
        factory="create_regulatory_server",
        costs="fetch_regulatory_costs",
        description="Indian regulatory publications",
    ),
    "tribunals": Origin(
        slug="tribunals",
        module="vaquill_mcp.tribunals",
        factory="create_tribunals_server",
        costs="fetch_tribunals_costs",
        description="Indian tribunal matters",
    ),
}


def get_origin(slug: str | None = None) -> Origin:
    """The origin this process serves, from `VAQUILL_ORIGIN` when not given.

    Raises on an unknown slug rather than falling back to a default. A process
    that cannot tell which corpus it serves would serve the WRONG corpus under
    the right host name, and `instructions.py` would then describe a catalogue
    the tools do not match.
    """
    raw = (slug if slug is not None else os.environ.get("VAQUILL_ORIGIN", "")).strip().lower()
    if not raw:
        known = ", ".join(sorted(ORIGINS))
        raise ValueError(f"VAQUILL_ORIGIN must be set to one of: {known}")
    if raw not in ORIGINS:
        known = ", ".join(sorted(ORIGINS))
        raise ValueError(f"VAQUILL_ORIGIN must be one of: {known}. Got: {raw!r}")
    return ORIGINS[raw]


def _resolve(origin: Origin) -> tuple[Callable[..., Any], Callable[[str], dict[str, str]]]:
    """Import one single-corpus origin's factory and cost fetcher."""
    import importlib

    if origin.module is None:  # pragma: no cover - guarded by __post_init__
        raise ValueError(f"origin {origin.slug!r} is a jurisdiction origin, not a corpus one")
    module = importlib.import_module(origin.module)
    return getattr(module, origin.factory), getattr(module, origin.costs)


def build_app(origin: Origin | None = None) -> Starlette:
    """Compose the ASGI app for one single-corpus origin."""
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

    origin = origin or get_origin()

    auth = build_auth_provider()
    resolver = build_connector_key_resolver()
    if auth is None:
        logger.info("OAuth is not configured; /mcp accepts vq_key_ bearer tokens only")

    if origin.jurisdiction is not None:
        return _jurisdiction_app(origin, auth=auth, resolver=resolver)

    create_server, fetch_costs = _resolve(origin)
    base_url = get_base_url()
    # ONE client for both servers, owned and closed HERE: the factory builds
    # none when it is handed one. The caller's key is read per request by
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
    costs = fetch_costs(base_url)

    # TWO SERVERS, not one serving two apps, for the reason `remote_main` gives:
    # FastMCP reads `self.auth` when it builds each HTTP app, and
    # `RequireAuthMiddleware` 401s a request with no Authorization header, which
    # is exactly what a `/s/{api_key}` caller sends. Sharing an instance would
    # 401 every path-key URL the moment OAuth was switched on. Both are built
    # from the same function, so the two URL shapes cannot present different
    # catalogues.
    key_server = create_server(client, costs=costs)
    mcp_server = create_server(client, auth=auth, costs=costs)

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

    return _assemble(origin, key_app=key_app, mcp_app=mcp_app, closing=client.aclose)


def _jurisdiction_app(origin: Origin, *, auth: object | None, resolver: object) -> Starlette:
    """The app for a whole-jurisdiction origin.

    `create_remote_server` derives the catalogue from the jurisdiction's OpenAPI
    document and owns its own httpx client, so there is no client to build or
    close here. Otherwise identical to the single-corpus shape, and the mount
    order comes from the same `_assemble`.
    """
    from starlette.middleware import Middleware

    from vaquill_mcp.oauth import BrandSkinMiddleware
    from vaquill_mcp.path_key_deprecation import PathKeyDeprecation
    from vaquill_mcp.registration_guard import RegistrationRateLimit
    from vaquill_mcp.remote import create_remote_server

    # TWO SERVERS for the reason `remote_main` records: FastMCP reads `self.auth`
    # when it builds each HTTP app, and `RequireAuthMiddleware` 401s a request
    # carrying no Authorization header, which is exactly what a `/s/{api_key}`
    # caller sends. Both derive from the SAME document, so the two URL shapes
    # cannot present different tool sets.
    key_server = create_remote_server(origin.jurisdiction, resolver=resolver)
    mcp_server = create_remote_server(origin.jurisdiction, auth=auth, resolver=resolver)

    # The path-key form is deprecated (a credential in the URL): every response
    # carries `Deprecation` and `Link` headers and each use is logged with a key
    # prefix only. It keeps working.
    key_app = key_server.http_app(
        path="/{api_key}",
        transport="streamable-http",
        middleware=[Middleware(PathKeyDeprecation, mount="/s")],
    )
    mcp_app = mcp_server.http_app(
        path="/mcp",
        transport="streamable-http",
        middleware=[Middleware(RegistrationRateLimit), Middleware(BrandSkinMiddleware)],
    )
    return _assemble(origin, key_app=key_app, mcp_app=mcp_app, closing=None)


def _assemble(
    origin: Origin,
    *,
    key_app: Any,
    mcp_app: Any,
    closing: Callable[[], Any] | None,
) -> Starlette:
    """The route table and lifespan every origin shares.

    ONE place, because the mount ORDER is behaviour rather than layout and five
    copies of it would be five chances for it to drift.
    """
    from vaquill_mcp import __version__
    from vaquill_mcp.brand_assets import asset_routes

    sub_apps = (key_app, mcp_app)

    async def health(_request):
        """Health endpoint for Docker/load balancer probes."""
        return JSONResponse(
            {
                "status": "ok",
                "service": "vaquill-mcp",
                "version": __version__,
                "corpus": origin.slug,
            }
        )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        """Run both mounted apps' lifespans, then close any shared client.

        Starlette does NOT start a mounted app's lifespan for it. The client is
        pushed FIRST so it is closed LAST, after every app that could still be
        using it has unwound.
        """
        async with contextlib.AsyncExitStack() as stack:
            if closing is not None:
                stack.push_async_callback(closing)
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
    """Start one single-corpus origin on Streamable HTTP transport."""
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

    origin = get_origin()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    logger.info(
        "%s MCP serving /mcp and /s/{api_key} on %s:%s",
        origin.slug,
        host,
        port,
    )
    uvicorn.run(build_app(origin), host=host, port=port)


if __name__ == "__main__":
    main()
