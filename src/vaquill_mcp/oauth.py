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
import time

import httpx2
from fastmcp.server.auth import AccessToken, TokenVerifier

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

    proxy = OAuthProxy(
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
        # ON, despite the upstream ALSO showing a consent screen we build and
        # own. The two screens are not redundant: Supabase's names the client
        # registered with it, which is this proxy's single static OAuth app, so
        # it says "Vaquill MCP" no matter which downstream client is really
        # asking. Only the proxy's screen names the actual caller, which is what
        # makes it the defence against a confused-deputy attack -- a malicious
        # client borrowing our static upstream client_id to obtain a token the
        # user never knowingly granted. FastMCP warns loudly when this is off,
        # and the warning is right.
        #
        # If the double prompt proves too much friction, the knob is
        # "remember", which shows the screen once per (client_id, redirect_uri)
        # per browser and still prompts on cross-site navigations. Do not set
        # it to False.
        require_authorization_consent=True,
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
