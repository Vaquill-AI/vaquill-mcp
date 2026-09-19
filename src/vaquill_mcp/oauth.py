"""OAuth for the hosted server, and the token -> `vq_key_` turn behind it.

WHY OAUTH AT ALL
================

A Cowork cloud session runs in an Anthropic container with no shell the user
owns and no user-settable environment, so `"Authorization": "Bearer
${VAQUILL_API_KEY}"` in a plugin's `.mcp.json` is sent LITERALLY and every tool
call 401s behind a connector that reports healthy. That is not a bug to work
around; from Anthropic's Cowork architecture overview: "Connector authorization
tokens never enter the sandbox; connector calls are made on the server side."
The credential was never going to live where an environment variable could reach
it. OAuth is the only path with first-party documentation and a working user
flow.

TWO CREDENTIAL SHAPES ON ONE ENDPOINT
=====================================

`/mcp` must accept BOTH:

* `Bearer vq_key_...`, which works today, is what the README recommends, is what
  the published plugin sends, and is what every existing Claude Code
  registration holds. Turning `/mcp` into an OAuth-only endpoint would break all
  of them on the day OAuth shipped.
* an OAuth access token, for Cowork and the Connectors Directory.

`_VaquillTokenVerifier` is that fork. A `vq_key_` is accepted OPAQUELY and
passed through unchanged, exactly as before: `api.vaquill.ai` remains the only
authority on whether a key is valid, so this introduces no second opinion that
could disagree with it and no extra round trip on the hot path. An OAuth token
is verified properly, by signature, against the upstream JWKS.

WHY THE OAUTH TOKEN IS NOT FORWARDED
====================================

The MCP specification is explicit: "The MCP server MUST NOT pass through the
token it received from the MCP client." So the verified `sub` is resolved,
server-side, to a `vq_key_` the product mints for this connection, through the
backend's internal resolution endpoint. Metering, credits, rate limits and
refund discipline are all untouched, because the API still receives a `vq_key_`
and cannot tell anything changed.

WHY NOT LAZY AUTHENTICATION
===========================

Anthropic documents "lazy" (mixed) auth as the pattern for a server with both
public and protected tools: let anyone connect, list tools and call the public
ones, and return the 401 only when a protected tool is invoked. It reads like
the more generous option, and it does not apply here.

There is no tool on this server a caller could usefully invoke without a
credential. Even the zero-credit ones -- `list_statutes_coverage`,
`get_pricing`, `get_coverage` -- are served by `api.vaquill.ai`, which
authenticates every request and answers 401 without a valid key (measured
2026-09-07 against production). A lazy gate would therefore let a caller
connect, list twenty-five tools, and have every single one of them fail at the
API. That is precisely the "connector reports healthy, nothing works" shape this
whole project exists to remove.

Gating the endpoint also ends the older version of the same problem: before
this, `/mcp` listed all twenty-five tools to a caller with NO credential at all,
so a green connection proved nothing and every verification that stopped at
"it connected" was worthless.

CONFIGURATION, AND WHY IT IS OFF BY DEFAULT
===========================================

Absent `VAQUILL_OAUTH_*`, `build_auth_provider()` returns None and the server
behaves exactly as it does today. OAuth is therefore deployable as a config
change rather than a release, and an environment that has not been given an
authorization server cannot half-enable it.
"""

from __future__ import annotations

import logging
import os
import re
import time

import httpx2
from fastmcp.server.auth import AccessToken, TokenVerifier
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

#: Prefix identifying a Vaquill API key. A token starting with this is a
#: first-party credential and never an OAuth token; the two namespaces cannot
#: collide because we mint the prefix.
_KEY_PREFIX = "vq_key_"

#: How long a resolved connector key is reused before being re-resolved.
#: Bounded, not indefinite: revoking a connection has to take effect without a
#: restart. The backend's own auth cache is 60s, so this is the longer of two
#: bounds and the one that decides how stale a disconnect can be.
_KEY_CACHE_TTL_SECONDS = 300.0

#: `client_id` reported for a raw-key caller. Not a real OAuth client; it exists
#: because `AccessToken` requires the field, and a value that reads as what it
#: is beats an empty string in a log line.
_RAW_KEY_CLIENT_ID = "vaquill-api-key"


def oauth_enabled() -> bool:
    """True when this deployment has been given an authorization server."""
    return bool(os.environ.get("VAQUILL_OAUTH_UPSTREAM_JWKS_URI", "").strip())


class ConnectorKeyResolver:
    """Turns a verified OAuth subject into the `vq_key_` to spend.

    Cached per subject, because this sits on the path of EVERY tool call and the
    answer changes only when a user connects or disconnects. Cache misses cost
    one request to the backend; without the cache every single tool call would
    pay for one.
    """

    def __init__(self, base_url: str, secret: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._timeout = timeout
        self._cache: dict[str, tuple[str, float]] = {}

    def _cached(self, subject: str) -> str | None:
        hit = self._cache.get(subject)
        if not hit:
            return None
        key, expires_at = hit
        if expires_at <= time.monotonic():
            # Drop rather than leave: a subject that stops connecting should not
            # keep its entry alive in a long-running process.
            self._cache.pop(subject, None)
            return None
        return key

    async def resolve(self, subject: str) -> str:
        """The connector key for this subject. Raises on failure."""
        cached = self._cached(subject)
        if cached:
            return cached

        async with httpx2.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/internal/connector-keys/resolve",
                json={"userId": subject, "connector": "mcp"},
                headers={"X-Vaquill-Internal": self._secret},
            )
        if response.status_code != 200:
            # Deliberately does NOT include the response body. This endpoint
            # returns a raw key on success, and an error path that echoed the
            # body would eventually echo one into a log or a tool error shown to
            # the model.
            raise ValueError(
                "Could not resolve a Vaquill API key for this account "
                f"(status {response.status_code}). Sign in again at "
                "https://www.vaquill.ai/developer, or contact support."
            )

        key = (response.json() or {}).get("apiKey", "")
        if not key.startswith(_KEY_PREFIX):
            raise ValueError("Connector key resolution returned an unusable credential")

        self._cache[subject] = (key, time.monotonic() + _KEY_CACHE_TTL_SECONDS)
        return key

    def forget(self, subject: str) -> None:
        """Drop a cached key, so the next call re-resolves it."""
        self._cache.pop(subject, None)


class RawApiKeyVerifier(TokenVerifier):
    """Accept a `vq_key_` as a bearer credential, opaquely.

    NOT validated here, on purpose. `api.vaquill.ai` is the single authority on
    whether a key is live, and asking a second system would add a round trip to
    every request plus a way for the two answers to disagree. An invalid key
    still fails, at the API call, with the API's own message -- exactly as it
    does today.

    This exists so that turning OAuth on does NOT break the credential shape
    that works today: `Bearer vq_key_...` is what the README recommends, what
    the published plugin sends, and what every existing Claude Code
    registration holds. Composed via `MultiAuth` rather than passed to
    `OAuthProxy(token_verifier=...)`, which would never see it: the proxy's
    verifier is called with the UPSTREAM token during its token swap, not with
    the credential the client presented.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith(_KEY_PREFIX):
            return None
        return AccessToken(
            token=token,
            client_id=_RAW_KEY_CLIENT_ID,
            scopes=[],
            subject=None,
        )


# ---------------------------------------------------------------------------
# Durable OAuth state
# ---------------------------------------------------------------------------

# What the proxy keeps in `client_storage` is not a cache. It holds the active
# authorization transactions, the issued authorization codes, the client
# registrations, the JTI mapping from a FastMCP token to its upstream one, and
# the refresh-token metadata. Lose it and every connected user is signed out
# with "Connection has expired"; SHARE it wrongly and OAuth breaks outright,
# because `/authorize` and `/oauth/callback` can land on different replicas and
# the callback cannot find the transaction the first one wrote.
#
# 🔴 The default is a local encrypted FileTreeStore under `settings.home`. That
# is documented by FastMCP as development-only, and on this image it is inside
# the container, so a REDEPLOY SIGNS EVERYONE OUT. Measured 2026-09-08: routine
# deploys for unrelated changes killed a live session twice in one afternoon.
#
# The second half is `jwt_signing_key`. Left unset, FastMCP derives it from
# `upstream_client_secret` via HKDF, and the storage encryption key (and so the
# storage DIRECTORY) is derived from that in turn. Rotating the Supabase client
# secret would therefore orphan the entire store and sign everyone out again,
# silently. An explicit key decouples the two, which is exactly why upstream
# documents the pair as one production setting rather than two.
_STORAGE_ENV = (
    "VAQUILL_OAUTH_REDIS_URL",
    "VAQUILL_OAUTH_JWT_SIGNING_KEY",
    "VAQUILL_OAUTH_STORAGE_ENCRYPTION_KEY",
)


def _durable_storage() -> tuple[object | None, str | None]:
    """Return `(client_storage, jwt_signing_key)` for a production deployment.

    `(None, None)` when none of the three variables is set, which keeps the
    development default: a local encrypted file store, ephemeral on this image.

    Configuring SOME of them raises. A half-configured store is worse than none,
    because it looks configured and still loses sessions on the one event it
    exists to survive.
    """
    values = {name: os.environ.get(name, "").strip() for name in _STORAGE_ENV}
    supplied = {name for name, value in values.items() if value}
    if not supplied:
        logger.warning(
            "OAuth state is stored on local disk inside the container, so a "
            "redeploy will sign every connected user out. Set %s together for "
            "a durable store.",
            ", ".join(_STORAGE_ENV),
        )
        return None, None
    if supplied != set(_STORAGE_ENV):
        raise RuntimeError(
            "OAuth durable storage is half-configured. Set all of "
            f"{', '.join(_STORAGE_ENV)} or none of them; missing: "
            f"{', '.join(sorted(set(_STORAGE_ENV) - supplied))}"
        )

    from cryptography.fernet import Fernet
    from key_value.aio.stores.redis import RedisStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    # Encrypted AT REST, not merely behind Redis auth. The upstream Supabase
    # access and refresh tokens live in these values; anyone who can read the
    # Redis keyspace would otherwise read them in plaintext.
    storage = FernetEncryptionWrapper(
        key_value=RedisStore(url=values["VAQUILL_OAUTH_REDIS_URL"]),
        fernet=Fernet(values["VAQUILL_OAUTH_STORAGE_ENCRYPTION_KEY"]),
    )
    logger.info("OAuth state persisted to Redis; sessions survive a redeploy")
    return storage, values["VAQUILL_OAUTH_JWT_SIGNING_KEY"]


def build_auth_provider():
    """The auth provider for the `/mcp` mounts, or None when unconfigured.

    Returns None absent `VAQUILL_OAUTH_*`, and that is the deployed default:
    the endpoint keeps behaving exactly as it does today, so OAuth arrives as a
    config change rather than a release, and an environment that has not been
    given an authorization server cannot half-enable it.

    WHY `OAuthProxy` RATHER THAN A BARE RESOURCE-SERVER PROVIDER
    ===========================================================

    Three reasons, each measured against the alternative of forwarding the
    upstream's own metadata:

    * CIMD. Claude selects Client ID Metadata Documents only when the metadata
      advertises BOTH `client_id_metadata_document_supported` and `"none"` in
      `token_endpoint_auth_methods_supported`. Miss either and it falls back to
      Dynamic Client Registration silently, with no error. `OAuthProxy` sets
      both together; a forwarding provider advertises whatever the upstream
      happens to say, which for Supabase today is neither.
    * Loopback redirect ports. The proxy registers ONE fixed redirect URI
      upstream and handles the client's ephemeral loopback port itself, which
      is how Claude Code authenticates (RFC 8252 section 7.3). The upstream
      never sees a loopback URI, so an upstream that mishandles them cannot
      break the flow.
    * Audience. The proxy issues its own tokens bound to this resource, which
      is the cross-server replay protection that RFC 8707 resource indicators
      would otherwise provide and Supabase does not support.

    `MultiAuth` then adds the raw-key verifier alongside it. Routes and OAuth
    metadata come from the proxy; the verifier only widens what counts as a
    valid credential.

    `require_authorization_consent=False` because the upstream authorization
    server already shows a consent screen we build and own. Leaving it on asks
    the same user the same question twice in one flow.
    """
    if not oauth_enabled():
        return None

    from fastmcp.server.auth import MultiAuth, OAuthProxy
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    required = {
        "VAQUILL_OAUTH_UPSTREAM_JWKS_URI": os.environ.get(
            "VAQUILL_OAUTH_UPSTREAM_JWKS_URI", ""
        ),
        "VAQUILL_OAUTH_UPSTREAM_ISSUER": os.environ.get(
            "VAQUILL_OAUTH_UPSTREAM_ISSUER", ""
        ),
        "VAQUILL_OAUTH_AUTHORIZE_URL": os.environ.get(
            "VAQUILL_OAUTH_AUTHORIZE_URL", ""
        ),
        "VAQUILL_OAUTH_TOKEN_URL": os.environ.get("VAQUILL_OAUTH_TOKEN_URL", ""),
        "VAQUILL_OAUTH_CLIENT_ID": os.environ.get("VAQUILL_OAUTH_CLIENT_ID", ""),
        "VAQUILL_OAUTH_CLIENT_SECRET": os.environ.get(
            "VAQUILL_OAUTH_CLIENT_SECRET", ""
        ),
        "VAQUILL_PUBLIC_URL": os.environ.get("VAQUILL_PUBLIC_URL", ""),
    }
    missing = sorted(name for name, value in required.items() if not value.strip())
    if missing:
        # Fail LOUDLY at startup rather than serving a half-configured OAuth
        # endpoint. Claude caches a discovery document globally by URL for about
        # five minutes, shared across all users, so a wrong one outlives the
        # misconfiguration that produced it.
        raise ValueError(
            "OAuth is enabled but incompletely configured. Missing: "
            + ", ".join(missing)
        )

    upstream_jwt = JWTVerifier(
        jwks_uri=required["VAQUILL_OAUTH_UPSTREAM_JWKS_URI"],
        issuer=required["VAQUILL_OAUTH_UPSTREAM_ISSUER"],
        algorithm=os.environ.get("VAQUILL_OAUTH_ALGORITHM", "ES256"),
        audience=os.environ.get("VAQUILL_OAUTH_AUDIENCE", "authenticated"),
    )

    client_storage, jwt_signing_key = _durable_storage()

    proxy = OAuthProxy(
        client_storage=client_storage,
        jwt_signing_key=jwt_signing_key,
        upstream_authorization_endpoint=required["VAQUILL_OAUTH_AUTHORIZE_URL"],
        upstream_token_endpoint=required["VAQUILL_OAUTH_TOKEN_URL"],
        upstream_client_id=required["VAQUILL_OAUTH_CLIENT_ID"],
        upstream_client_secret=required["VAQUILL_OAUTH_CLIENT_SECRET"],
        token_verifier=upstream_jwt,
        base_url=required["VAQUILL_PUBLIC_URL"],
        # `offline_access`, and ONLY that, for a measured reason.
        #
        # Claude requests whatever this server advertises in `scopes_supported`,
        # and the proxy forwards it upstream. Advertising nothing means Supabase
        # is asked for no refresh token, so when the upstream access token
        # expires (about an hour) the proxy has nothing to refresh with and the
        # connection dies with no way back except reconnecting by hand. Verified
        # 2026-09-07 that the upstream lists `offline_access` in its own
        # `scopes_supported`, so it will honour the request.
        #
        # `openid` is deliberately NOT requested. It makes Supabase mint an ID
        # token, and Supabase's own docs say ID token generation FAILS on a
        # symmetric HS256 project. We verify the access token against JWKS and
        # never read an ID token, so asking for one would buy a failure mode and
        # nothing else.
        valid_scopes=["offline_access"],
        enable_cimd=True,
        # "remember", NOT False, and not True either.
        #
        # There are two consent screens in this flow and neither is removable.
        # The upstream's is ours to build (Supabase Auth ships no hosted consent
        # UI, so the flow dead-ends without it) but it names the client
        # registered WITH Supabase, which is this proxy's single static OAuth
        # app -- "Vaquill AI MCP" regardless of who is really asking. Only the
        # proxy's screen names the ACTUAL downstream caller, which is what makes
        # it the defence against a confused deputy: a malicious client borrowing
        # our static upstream client_id to obtain a token the user never
        # knowingly granted. The MCP specification requires a proxy to keep a
        # per-user registry of approved client_ids for exactly this reason.
        #
        # "remember" keeps that protection where it matters and drops it where
        # it does not: the screen shows on the FIRST authorization for a given
        # (client_id, redirect_uri) in a browser, and cross-site navigations are
        # still prompted, so an attacker arriving from elsewhere cannot inherit
        # the silent approval. What it removes is the second and subsequent
        # prompt for a user reconnecting a client they have already vouched for.
        #
        # Never False. That is the setting FastMCP warns about, and the warning
        # is right.
        require_authorization_consent="remember",
    )
    return MultiAuth(server=proxy, verifiers=[RawApiKeyVerifier()])


def build_connector_key_resolver() -> ConnectorKeyResolver | None:
    """The OAuth-subject -> `vq_key_` resolver, or None when unconfigured.

    Separate from `build_auth_provider` because the two fail independently and
    should be diagnosable independently: an authorization server with no
    resolution secret authenticates users and then cannot bill them, which is a
    different broken from not authenticating at all.

    Returns None when `VAQUILL_INTERNAL_SECRET` is unset, and an OAuth caller
    then gets the same "Missing API key" error a credential-less caller does.
    That is the honest failure: no secret means no way to resolve a key, and
    inventing one would be worse than saying so.
    """
    secret = os.environ.get("VAQUILL_INTERNAL_SECRET", "").strip()
    if not secret:
        if oauth_enabled():
            logger.warning(
                "OAuth is enabled but VAQUILL_INTERNAL_SECRET is unset: "
                "OAuth callers will authenticate and then fail at the first tool call"
            )
        return None
    from vaquill_mcp.config import get_base_url

    return ConnectorKeyResolver(get_base_url(), secret)


# ---------------------------------------------------------------------------
# Brand skin for the proxy's consent screen
# ---------------------------------------------------------------------------

#: Tokens copied from the product's own `globals.css`, so the screen a user
#: lands on mid-sign-in looks like the product they are signing in to. Values,
#: not variables, because this CSS is injected into a page that has none of the
#: app's stylesheets.
#:
#: WHY THERE IS NO WEBFONT HERE
#: ============================
#:
#: The consent page ships `default-src 'none'` with no `font-src`, so a
#: `@font-face` or a Google Fonts `@import` is blocked by the page's own CSP,
#: and `'none'` covers our own origin too. Loading the product's Fraunces and
#: Inter would mean widening that policy on the one page where a user hands
#: over access to their account, and paying for a third-party font round trip
#: mid-authorization. Both families are named first in the stack anyway, so a
#: visitor who has them installed gets them and everyone else gets the system
#: stack FastMCP already used.
_BRAND_CSS = """
:root {
    /* Inert, and the marker that says this page was skinned. Asserted by the
       tests and readable in devtools; the comments around it do not ship. */
    --vaquill-brand-skin: 1;
}

/* vaquill brand skin, appended after FastMCP's base styles so it wins on
   equal specificity. Every rule here is cosmetic: if FastMCP renames a class
   the rule stops matching and the page renders in its default styling rather
   than breaking. The selectors mirror `fastmcp.utilities.ui` (BASE_STYLES,
   BUTTON_STYLES, INFO_BOX_STYLES, REDIRECT_SECTION_STYLES, DETAILS_STYLES,
   DETAIL_BOX_STYLES and TOOLTIP_STYLES) plus the `.cimd-badge` block that
   `oauth_proxy/ui.py` appends inline. */

/* ---- page and card ---- */
body {
    background: #f4f1ec;
    color: #25211d;
    font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
        'Helvetica Neue', Arial, sans-serif;
}
.container, .card {
    background: #ffffff;
    border-color: #dcd4c6;
    box-shadow: 0 4px 16px -4px rgba(37, 33, 29, 0.10),
        0 2px 4px -2px rgba(37, 33, 29, 0.06);
}
.logo {
    /* 3x FastMCP's 64px. The lockup is wordmark-plus-mark, so at 64px the
       word is unreadable and the screen reads as un-branded. */
    width: 192px;
    margin-bottom: 2rem;
}
h1, h2, h3 {
    color: #25211d;
    letter-spacing: -0.01em;
}
a {
    color: #6e3730;
}

/* ---- the intro box. `--info` in the product IS the primary maroon, so this
   is tinted maroon rather than the vendor's sky blue, and the emphasised
   client and server names follow it. Both need naming explicitly: FastMCP
   colours them through `.info-box strong` and `.info-box .server-name-link`,
   which outrank a bare `a` on specificity. ---- */
.info-box {
    background: rgba(110, 55, 48, 0.06);
    border-color: rgba(110, 55, 48, 0.22);
    color: #25211d;
}
.info-box strong,
.info-box .server-name-link {
    color: #6e3730;
}
.info-box.error {
    background: rgba(239, 68, 68, 0.06);
    border-color: rgba(239, 68, 68, 0.30);
    color: #25211d;
}

/* ---- verified-domain badge, deliberately still green. The product spends
   colour on semantic state and nothing else, and "this domain was verified"
   is exactly that, so the tokens become ours (`--success`) while the meaning
   stays put. ---- */
.cimd-badge {
    background: rgba(16, 185, 129, 0.08);
    border-color: rgba(16, 185, 129, 0.35);
    color: #1d5b48;
}
.cimd-check {
    color: #10b981;
}

/* ---- the callback address, which is the one thing on this page the user is
   actually asked to check. `--warm`, the product's amber accent, keeps it
   flagged without the vendor's lemon yellow. ---- */
.redirect-section {
    background: rgba(211, 127, 23, 0.08);
    border-color: rgba(211, 127, 23, 0.32);
}
.redirect-section .label {
    color: #655d54;
}
.redirect-section .value,
.detail-value {
    color: #25211d;
    font-family: 'JetBrains Mono', ui-monospace, 'SF Mono', Monaco, Consolas,
        'Courier New', monospace;
}

/* ---- advanced details ---- */
summary {
    color: #655d54;
}
summary:hover {
    background: #ece6db;
    color: #25211d;
}
.detail-box {
    background: #ece6db;
    border-color: #dcd4c6;
}
.detail-row {
    border-bottom-color: #dcd4c6;
}
.detail-label {
    color: #655d54;
}

/* ---- buttons. `--radius` is 8px, and Deny sits light-on-light here, so it
   needs the border the vendor's solid grey fill made unnecessary. ---- */
button {
    border-radius: 8px;
}
button:hover {
    box-shadow: 0 4px 10px -2px rgba(37, 33, 29, 0.18);
}
button:focus-visible {
    outline: 2px solid #6e3730;
    outline-offset: 2px;
}
.btn-approve, .btn-primary {
    background: #6e3730;
    color: #ffffff;
    border: 1px solid #6e3730;
}
.btn-approve:hover, .btn-primary:hover {
    background: #5a2d27;
    border-color: #5a2d27;
}
.btn-deny, .btn-secondary {
    background: #ece6db;
    color: #25211d;
    border: 1px solid #dcd4c6;
}
.btn-deny:hover, .btn-secondary:hover {
    background: #ddd4c4;
}

/* ---- the pinned help link and its tooltip ---- */
.help-link {
    color: #655d54;
    border-bottom-color: #b8ad9d;
}
.help-link:hover {
    color: #6e3730;
    border-bottom-color: #6e3730;
}
.tooltip {
    background: #25211d;
}
.tooltip::after {
    border-top-color: #25211d;
}
.tooltip-link {
    color: #e5c8a8;
}
"""

#: Layout for a page FastMCP hands us as a bare fragment, with no document and
#: no stylesheet of its own. Only what `_BRAND_CSS` assumes is already there:
#: it paints `body` and `.container` and expects something to have centred them.
_BARE_PAGE_STYLES = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1.5rem;
}
.container {
    border: 1px solid #dcd4c6;
    border-radius: 1rem;
    padding: 3rem 2.5rem;
    max-width: 36rem;
    width: 100%;
    text-align: center;
}
h1 {
    font-size: 1.5rem;
    font-weight: 600;
    margin-bottom: 1rem;
}
p {
    font-size: 0.9375rem;
    line-height: 1.5;
    color: #655d54;
}
"""


def _strip_css_comments(css: str) -> str:
    """Drop `/* ... */` from CSS on its way to the browser.

    The comments in `_BRAND_CSS` exist for whoever next reads this file, and
    they name FastMCP and its style constants because that is the only way to
    explain where a selector came from. None of that belongs in bytes we serve:
    it puts the vendor's name in view-source on the consent screen, which is
    exactly what the rest of this module works to keep off that page, and ships
    our own reasoning to anyone who looks.

    Safe as a plain scan because this stylesheet contains no `url()`, no quoted
    string and no `content:` value, so there is nowhere for `/*` to appear
    except as a comment. It is applied to OUR css only, never to FastMCP's.
    """
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Collapse the runs of blank lines the removal leaves behind, so the served
    # stylesheet reads as something written rather than something processed.
    return re.sub(r"\n[ \t]*(?:\n[ \t]*)+", "\n\n", without_comments)


#: `_BRAND_CSS` as served. Built once: the source is authored for a reader and
#: this is what a browser gets.
_BRAND_CSS_WIRE = _strip_css_comments(_BRAND_CSS)

#: Present on every page this module has skinned, and on no other.
_SKIN_MARKER = "--vaquill-brand-skin"


def _inject_brand_css(html: str) -> str:
    """Append the brand skin to a FastMCP-rendered page.

    Inserted before `</style>` so it lands INSIDE the existing stylesheet and
    after the base rules, which is what makes it win without `!important` or a
    specificity fight. The page's CSP already allows inline styles
    (`style-src 'unsafe-inline'`), so this adds no new source.

    Returns the html untouched when there is no `</style>` to insert before, so
    an upstream template change degrades to "unstyled but working" rather than
    to a broken page mid-authorization.

    Idempotent, keyed off the `--vaquill-brand-skin` custom property the sheet
    declares. Nothing double-skins a response today, but the middleware is
    attached per app and a second pass would otherwise stack the whole sheet
    again: harmless to look at, and a silently growing page on the one route
    that must stay predictable.
    """
    if _SKIN_MARKER in html:
        return html
    marker = "</style>"
    if marker not in html:
        return html
    return html.replace(marker, _BRAND_CSS_WIRE + marker, 1)


def _wrap_bare_fragment(fragment: str) -> str:
    """Put a document and a stylesheet around a fragment that has neither.

    Not every page in this flow goes through FastMCP's `create_page`. The
    consent POST handler answers a replayed or mismatched form with a literal
    `"<h1>Error</h1><p>...</p>"`, which a browser renders as unstyled Times New
    Roman on white: a page that reads as a crash rather than as a refusal, on
    the one screen a user reaches when their sign-in has already gone wrong.

    The fragment is inserted verbatim. This adds presentation and never content,
    so it cannot change what the page says, and it carries no CSP of its own
    because these responses ship none.
    """
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        "<title>Vaquill AI</title>\n"
        "<style>"
        + _BARE_PAGE_STYLES
        + _BRAND_CSS_WIRE
        + "</style>\n"
        "</head>\n"
        "<body>\n"
        f'<div class="container">{fragment}</div>\n'
        "</body>\n"
        "</html>\n"
    )


#: FastMCP's help tooltip is the one block of copy on this page that is not
#: ours, and it names the vendor twice ("This FastMCP server requires your
#: consent", "Learn more about FastMCP security") over a link to gofastmcp.com,
#: on the screen where a user decides whether to trust US with their account.
#:
#: REPLACED RATHER THAN HIDDEN, and that is the whole judgement here. This
#: tooltip carries the only explanation on the page of why the screen exists at
#: all. A user who hesitates, looks for a reason and finds nothing is likelier
#: to click Allow on something they should have refused than one who reads it,
#: so deleting the block would trade a branding leak for a security regression.
#:
#: The confused-deputy link stays and still points at the MCP specification,
#: which is a standards document rather than anyone's marketing. Only the
#: second link, the vendor's own, becomes ours.
_HELP_LINK_OPEN = '<div class="help-link-container">'

_HELP_LINK_HTML = """<div class="help-link-container">
            <span class="help-link">
                Why am I seeing this?
                <span class="tooltip">
                    Vaquill asks before a new application may act on your
                    account. It is what protects you from <a
                    href="https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices#confused-deputy-problem"
                    target="_blank" rel="noopener noreferrer"
                    class="tooltip-link">confused deputy attacks</a>, where an
                    application you never authorised borrows this connection to
                    act as you.<br><br>
                    <a href="https://www.vaquill.ai/mcp" target="_blank"
                    rel="noopener noreferrer" class="tooltip-link">About the
                    Vaquill MCP server &#8594;</a>
                </span>
            </span>
        </div>"""


def _rebrand_help_link(html: str) -> str:
    """Swap FastMCP's self-naming help tooltip for our own copy.

    This is the one place the skin rewrites CONTENT rather than presentation,
    so it is deliberately the narrowest edit that can do the job: it finds one
    known container, checks the shape it is about to replace is the shape it
    understands, and substitutes a block carrying the same classes so every
    style rule above still applies.

    Returns the html untouched when the container is absent, unterminated, or
    holds nested markup this does not recognise. An upstream template change
    therefore degrades to the vendor's tooltip rather than to a page with a
    hole cut in it. Re-running it is a no-op on its own output.
    """
    start = html.find(_HELP_LINK_OPEN)
    if start == -1:
        return html
    end = html.find("</div>", start)
    if end == -1:
        return html
    if "<div" in html[start + len(_HELP_LINK_OPEN) : end]:
        return html
    return html[:start] + _HELP_LINK_HTML + html[end + len("</div>") :]


def _skin_html(html: str) -> str:
    """Brand one HTML response, whichever of the two shapes it arrives in.

    A full FastMCP page carries its own stylesheet, so it has the brand CSS
    appended and its one block of vendor copy replaced. A bare fragment gets a
    document built around it. Anything that looks like a document we do not
    recognise, meaning it has an `<html>` but no stylesheet to extend, is left
    exactly as it is: guessing at its structure is how a cosmetic layer turns
    into a broken authorization.
    """
    if "</style>" in html:
        return _inject_brand_css(_rebrand_help_link(html))
    if "<html" in html.lower():
        return html
    return _wrap_bare_fragment(html)


class BrandSkinMiddleware(BaseHTTPMiddleware):
    """Restyle FastMCP's own HTML pages on the way out.

    FastMCP renders the consent screen itself and exposes only `icons`,
    `website_url` and a CSP override; colours, sizing and layout are not
    configurable. Rather than fork a pinned dependency for a cosmetic change,
    the HTML is restyled in transit.

    Deliberately narrow, because this sits in front of the MCP endpoint itself:

    * only `text/html` is touched, so JSON-RPC, discovery documents and token
      responses pass through untouched;
    * streaming responses are passed through, since the MCP transport streams
      and buffering it would break the protocol;
    * it never raises. Any failure returns the original response, so the worst
      case is an unstyled page rather than a broken authorization.

    It is presentation-only with ONE deliberate exception, `_rebrand_help_link`,
    which replaces the vendor's help tooltip. That exception is bounded the same
    way: it rewrites one known container, bails on any shape it does not
    recognise, and keeps the security explanation the tooltip exists to give.
    Nothing here alters what a page states about the authorization itself.
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if "text/html" not in (response.headers.get("content-type") or ""):
            return response
        try:
            body = b"".join([chunk async for chunk in response.body_iterator])
        except Exception:
            return response
        try:
            skinned = _skin_html(body.decode()).encode()
        except Exception:
            skinned = body

        # Rebuilt from `raw_headers`, NEVER from `dict(response.headers)`.
        # Headers are a multi-map and a dict keeps one value per name, so a
        # response that sets more than one cookie loses every cookie but the
        # first. That is not hypothetical here: the consent page sets the new
        # consent-state cookie and then expires the surplus older ones, so the
        # dict form silently discarded the eviction and let the browser's
        # consent cookies grow past `_MAX_CSRF_TOKENS`. Upstream reordering
        # those two writes turns the same bug into a DROPPED live cookie, which
        # fails the double-submit check as a 403 mid-authorization.
        #
        # `content-type` is carried over rather than re-declared, so the
        # original charset survives, and `content-length` is recomputed because
        # the body just changed size.
        skinned_response = Response(
            content=skinned, status_code=response.status_code
        )
        skinned_response.raw_headers = [
            (key, value)
            for key, value in response.raw_headers
            if key.lower() != b"content-length"
        ] + [(b"content-length", str(len(skinned)).encode())]
        skinned_response.background = response.background
        return skinned_response
