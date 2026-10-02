"""OAuth on the hosted server, and the token -> `vq_key_` turn behind it.

Four things here fail silently, and each has a test that fails loudly instead:

* enabling OAuth must not 401 the customers who already work. `/s/{api_key}`
  sends NO Authorization header, so an auth provider that reached that mount
  would reject every existing customer URL the moment OAuth was switched on;
* a `vq_key_` bearer must keep working at `/mcp`. It is what the README
  recommends, what the published plugin sends, and what every existing Claude
  Code registration holds;
* the OAuth token must NEVER reach `api.vaquill.ai`. The MCP specification
  forbids passing through the client's token, and doing it anyway would also
  bill the wrong thing, because the API authenticates `vq_key_` and nothing
  else;
* a PARTIAL OAuth configuration must not serve. Claude caches a discovery
  document globally by URL for about five minutes, shared across every user, so
  a wrong one outlives the misconfiguration that produced it.
"""

from __future__ import annotations

import httpx
import httpx2
import pytest

from vaquill_mcp.oauth import (
    ConnectorKeyResolver,
    RawApiKeyVerifier,
    build_auth_provider,
    build_connector_key_resolver,
    oauth_enabled,
)

_BASE = "https://api.vaquill.ai"
_RESOLVE = f"{_BASE}/api/v1/internal/connector-keys/resolve"

_OAUTH_ENV = {
    "VAQUILL_OAUTH_UPSTREAM_JWKS_URI": "https://p.supabase.co/auth/v1/.well-known/jwks.json",
    "VAQUILL_OAUTH_UPSTREAM_ISSUER": "https://p.supabase.co/auth/v1",
    "VAQUILL_OAUTH_AUTHORIZE_URL": "https://p.supabase.co/auth/v1/oauth/authorize",
    "VAQUILL_OAUTH_TOKEN_URL": "https://p.supabase.co/auth/v1/oauth/token",
    "VAQUILL_OAUTH_CLIENT_ID": "client-abc",
    "VAQUILL_OAUTH_CLIENT_SECRET": "shh",
    "VAQUILL_PUBLIC_URL": "https://mcp.vaquill.ai",
}


# ---------------------------------------------------------------------------
# Off unless configured
# ---------------------------------------------------------------------------


def test_oauth_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent the env group the server behaves exactly as it did before OAuth,
    so enabling it is a config change rather than a release."""
    for name in _OAUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    assert oauth_enabled() is False
    assert build_auth_provider() is None


def test_a_partial_configuration_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Louder than a half-built discovery document that Claude then caches."""
    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("VAQUILL_OAUTH_CLIENT_SECRET")

    with pytest.raises(ValueError, match="VAQUILL_OAUTH_CLIENT_SECRET"):
        build_auth_provider()


def test_the_resolver_is_absent_without_its_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VAQUILL_INTERNAL_SECRET", raising=False)
    assert build_connector_key_resolver() is None


def test_a_configured_provider_advertises_cimd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude picks CIMD only when the metadata advertises BOTH
    `client_id_metadata_document_supported` and `"none"` in
    `token_endpoint_auth_methods_supported`. Miss either and it falls back to
    DCR silently, with no error anywhere, which is why this is asserted rather
    than assumed."""
    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)

    auth = build_auth_provider()
    assert auth is not None
    routes = auth.get_routes(mcp_path="/mcp")
    paths = {getattr(r, "path", "") for r in routes}
    assert "/.well-known/oauth-authorization-server" in paths
    assert any("oauth-protected-resource" in p for p in paths)


# ---------------------------------------------------------------------------
# Two credential shapes on one endpoint
# ---------------------------------------------------------------------------


class TestRawApiKeyVerifier:
    async def test_a_vq_key_is_accepted_and_passed_through_unchanged(self) -> None:
        token = await RawApiKeyVerifier().verify_token("vq_key_abc123")
        assert token is not None
        assert token.token == "vq_key_abc123"

    async def test_it_carries_no_subject(self) -> None:
        """The subject is what triggers connector-key resolution. A raw key must
        not acquire one, or a customer's own key would be swapped for a
        connector key and the charges would land on the wrong credential."""
        token = await RawApiKeyVerifier().verify_token("vq_key_abc123")
        assert token is not None and token.subject is None

    @pytest.mark.parametrize(
        "value", ["", "eyJhbGciOi.x.y", "Bearer vq_key_x", "vq_ws_abc"]
    )
    async def test_anything_else_is_declined(self, value: str) -> None:
        """Declined, not rejected: `MultiAuth` moves on to the next verifier, so
        returning None is how this defers to OAuth rather than blocking it.

        `vq_ws_` matters specifically. It is the Workspace API's namespace, a
        DIFFERENT product with a different ledger, and accepting one here would
        send it to an API that cannot authenticate it.
        """
        assert await RawApiKeyVerifier().verify_token(value) is None


# ---------------------------------------------------------------------------
# Resolving an OAuth subject to a key
# ---------------------------------------------------------------------------


class TestConnectorKeyResolver:
    async def test_it_resolves_and_then_caches(self, respx_mock) -> None:
        """This sits on the path of EVERY tool call. Without the cache each one
        would pay for a round trip to the backend."""
        route = respx_mock.post(_RESOLVE).mock(
            return_value=httpx.Response(
                200, json={"apiKey": "vq_key_conn", "keyId": "k1"}
            )
        )
        resolver = ConnectorKeyResolver(_BASE, "secret")

        assert await resolver.resolve("user-1") == "vq_key_conn"
        assert await resolver.resolve("user-1") == "vq_key_conn"
        assert route.call_count == 1

    async def test_the_shared_secret_is_sent(self, respx_mock) -> None:
        seen: list[str | None] = []

        def _record(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("x-vaquill-internal"))
            return httpx.Response(200, json={"apiKey": "vq_key_conn", "keyId": "k"})

        respx_mock.post(_RESOLVE).mock(side_effect=_record)
        await ConnectorKeyResolver(_BASE, "secret").resolve("user-1")
        assert seen == ["secret"]

    async def test_forgetting_a_subject_forces_a_re_resolve(self, respx_mock) -> None:
        """Revoking a connection has to take effect without a restart."""
        route = respx_mock.post(_RESOLVE).mock(
            return_value=httpx.Response(
                200, json={"apiKey": "vq_key_conn", "keyId": "k"}
            )
        )
        resolver = ConnectorKeyResolver(_BASE, "secret")
        await resolver.resolve("user-1")
        resolver.forget("user-1")
        await resolver.resolve("user-1")
        assert route.call_count == 2

    async def test_a_failure_does_not_echo_the_response_body(self, respx_mock) -> None:
        """That body carries a raw key on the success path, and this message
        reaches logs and the model's context on the failure path."""
        respx_mock.post(_RESOLVE).mock(
            return_value=httpx.Response(500, text="boom vq_key_LEAKED constraint x")
        )
        with pytest.raises(ValueError) as excinfo:
            await ConnectorKeyResolver(_BASE, "secret").resolve("user-1")

        assert "LEAKED" not in str(excinfo.value)
        assert "vq_key_" not in str(excinfo.value)

    async def test_a_nonsense_credential_is_refused(self, respx_mock) -> None:
        """Fail here rather than sending something unusable to the API and
        reading its 401 as the customer's key being invalid."""
        respx_mock.post(_RESOLVE).mock(
            return_value=httpx.Response(200, json={"apiKey": "not-a-key"})
        )
        with pytest.raises(ValueError):
            await ConnectorKeyResolver(_BASE, "secret").resolve("user-1")


# ---------------------------------------------------------------------------
# What actually reaches api.vaquill.ai
# ---------------------------------------------------------------------------


class TestOutgoingCredential:
    async def _sent_header(
        self, monkeypatch: pytest.MonkeyPatch, subject, resolver
    ) -> str:
        from vaquill_mcp import remote

        monkeypatch.setattr(remote, "_oauth_subject", lambda: subject)
        auth = remote._PerRequestBearerAuth(resolver)
        request = httpx2.Request("GET", f"{_BASE}/api/v1/us/statutes/coverage")
        flow = auth.async_auth_flow(request)
        sent = await flow.__anext__()
        await flow.aclose()
        return sent.headers["Authorization"]

    async def test_an_oauth_subject_is_swapped_for_a_connector_key(
        self, monkeypatch: pytest.MonkeyPatch, respx_mock
    ) -> None:
        """The MCP specification forbids forwarding the client's token, and the
        API could not authenticate it anyway."""
        respx_mock.post(_RESOLVE).mock(
            return_value=httpx.Response(
                200, json={"apiKey": "vq_key_conn", "keyId": "k"}
            )
        )
        header = await self._sent_header(
            monkeypatch, "user-1", ConnectorKeyResolver(_BASE, "secret")
        )
        assert header == "Bearer vq_key_conn"

    async def test_a_raw_key_caller_is_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No subject means no resolution, and the pre-OAuth path unchanged."""
        from vaquill_mcp import remote

        monkeypatch.setattr(remote, "_get_api_key", lambda: "vq_key_theirs")
        header = await self._sent_header(monkeypatch, None, None)
        assert header == "Bearer vq_key_theirs"

    async def test_an_oauth_subject_without_a_resolver_falls_back_loudly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No resolution secret means no way to get a key. Saying so beats
        inventing one, and beats forwarding the OAuth token."""
        from vaquill_mcp import remote

        def _boom():
            raise ValueError("Missing API key.")

        monkeypatch.setattr(remote, "_get_api_key", _boom)
        with pytest.raises(ValueError, match="Missing API key"):
            await self._sent_header(monkeypatch, "user-1", None)


def test_the_consent_screen_carries_our_branding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FastMCP's OAuth consent page reads these off the server object.

    `consent.py` renders `fastmcp.icons[0].src` and `fastmcp.website_url`, so
    leaving them unset shows FastMCP's own logo and no link on the screen a user
    is looking at while deciding whether to grant access to their account. That
    screen is the one naming the ACTUAL calling client, which makes it the one
    worth getting right.
    """
    import httpx as _httpx

    from vaquill_mcp.remote import create_remote_server

    base = "https://api.vaquill.ai"
    monkeypatch.setenv("VAQUILL_BASE_URL", base)
    import json
    import pathlib

    fixtures = pathlib.Path(__file__).resolve().parent / "fixtures"
    import respx

    with respx.mock(using="httpcore2", assert_all_called=False) as router:
        router.get(f"{base}/external/openapi.json").mock(
            return_value=_httpx.Response(
                200, json=json.loads((fixtures / "openapi_us.json").read_text())
            )
        )
        router.get(f"{base}/api/v1/api-credits/pricing").mock(
            return_value=_httpx.Response(200, json={"costs": []})
        )
        server = create_remote_server("US")

    assert server.website_url == "https://www.vaquill.ai"
    assert server.icons, "no icon: the consent page falls back to FastMCP's logo"
    assert "vaquill" in server.icons[0].src.lower()


def test_the_consent_screen_is_remembered_never_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxy's consent screen is the only one naming the ACTUAL caller.

    The upstream's screen names the client registered with it, which is this
    proxy's single static OAuth app, so it reads the same regardless of who is
    asking. Disabling the proxy's screen therefore removes the confused-deputy
    defence entirely, and the MCP specification requires a proxy to keep a
    per-user registry of approved client_ids precisely to prevent that.

    "remember" keeps the first authorization gated and still prompts on
    cross-site navigations; it only drops the repeat prompt for a client the
    user already vouched for in this browser. False is the setting FastMCP
    warns about, and this asserts we never drift into it.
    """
    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)

    auth = build_auth_provider()
    assert auth is not None
    proxy = auth.server  # MultiAuth wraps the OAuthProxy
    assert proxy._require_authorization_consent == "remember"
    assert proxy._require_authorization_consent is not False


# ---------------------------------------------------------------------------
# A refresh the upstream refuses must be an OAuth error, not a 500
# ---------------------------------------------------------------------------
#
# Observed in production 2026-10-02: eight `POST /token` answered
# `500 KeyError: 'access_token'`, and every one was followed within seconds by a
# burst of `401 invalid_token` on /mcp from the same client. FastMCP reads
# `token_response["access_token"]` unguarded after the upstream refresh. Supabase
# (GoTrue) refuses a refresh with `{"code", "error_code", "msg"}`, which carries
# no `error` key, so the OAuth client library does not raise, the body comes back
# as a dict with no token in it, and the lookup crashes. A 500 tells a client
# "retry"; it keeps presenting its dead access token and the log fills with
# `invalid_token`. RFC 6749 section 5.2 says what to answer: 400 `invalid_grant`,
# which tells it to throw the credentials away and authorize again.


def _provider(monkeypatch: pytest.MonkeyPatch):
    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)
    auth = build_auth_provider()
    assert auth is not None
    # The token issuer is created when routes are built, as it is in the real app.
    auth.get_routes(mcp_path="/mcp")
    return auth.server


def test_the_proxy_is_the_refresh_safe_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp.server.auth import OAuthProxy

    proxy = _provider(monkeypatch)
    assert isinstance(proxy, OAuthProxy)
    assert type(proxy) is not OAuthProxy, "the stock proxy 500s on a refused refresh"


async def test_a_refused_upstream_refresh_is_invalid_grant_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drives the REAL refresh code with GoTrue's actual error shape.

    The stubs sit one level below `exchange_refresh_token`, so the library's own
    `token_response["access_token"]` line runs. That is the point: patching the
    method itself would prove only that the wrapper wraps.
    """
    import contextlib
    from types import SimpleNamespace

    from mcp.server.auth.provider import TokenError

    proxy = _provider(monkeypatch)
    gotrue_error = {
        "code": 400,
        "error_code": "refresh_token_already_used",
        "msg": "Invalid Refresh Token: Already Used",
    }

    class _Upstream:
        async def refresh_token(self, **_kw):
            return gotrue_error

    @contextlib.asynccontextmanager
    async def _client():
        yield _Upstream()

    async def _jti_get(key):
        return SimpleNamespace(upstream_token_id="upstream-token-id")

    async def _upstream_get(key):
        return SimpleNamespace(
            refresh_token="upstream-refresh", scope="offline_access", access_token="old"
        )

    monkeypatch.setattr(
        proxy.jwt_issuer, "verify_token", lambda *_a, **_k: {"jti": "refresh-jti"}
    )
    monkeypatch.setattr(proxy._jti_mapping_store, "get", _jti_get)
    monkeypatch.setattr(proxy._upstream_token_store, "get", _upstream_get)
    monkeypatch.setattr(proxy, "_upstream_oauth_client", _client)

    client = SimpleNamespace(client_id="https://claude.ai/oauth/mcp-oauth-client-metadata")
    refresh = SimpleNamespace(token="fastmcp-refresh", scopes=["offline_access"])
    with pytest.raises(TokenError) as caught:
        await proxy.exchange_refresh_token(client, refresh, ["offline_access"])
    assert caught.value.error == "invalid_grant"


async def test_only_the_missing_access_token_is_translated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any other failure keeps its own shape: this must not become a catch-all."""
    from fastmcp.server.auth import OAuthProxy
    from mcp.server.auth.provider import TokenError

    proxy = _provider(monkeypatch)

    async def _other_key(self, *_a, **_k):
        raise KeyError("something_else")

    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", _other_key)
    with pytest.raises(KeyError):
        await proxy.exchange_refresh_token(object(), object(), [])

    async def _already_oauth(self, *_a, **_k):
        raise TokenError("invalid_grant", "Refresh token mapping not found")

    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", _already_oauth)
    with pytest.raises(TokenError) as caught:
        await proxy.exchange_refresh_token(object(), object(), [])
    assert "mapping not found" in str(caught.value.error_description)


async def test_a_good_refresh_passes_through_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.server.auth import OAuthProxy

    proxy = _provider(monkeypatch)
    sentinel = object()

    async def _ok(self, *_a, **_k):
        return sentinel

    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", _ok)
    assert await proxy.exchange_refresh_token(object(), object(), []) is sentinel


async def test_the_reason_the_upstream_refused_is_logged_without_a_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Eight of fifteen refreshes failed in two hours and the log said only KeyError.

    The upstream's error code is what tells a rotation race from a revoked grant,
    so it has to survive. Only the code and message are logged: the response body
    of a SUCCESSFUL refresh holds tokens, and a refusal body is not trusted to
    be free of them either, so it is never logged whole.
    """
    import logging
    from types import SimpleNamespace

    from mcp.server.auth.provider import TokenError

    proxy = _provider(monkeypatch)

    class _Upstream:
        async def refresh_token(self, **_kw):
            return {
                "code": 400,
                "error_code": "refresh_token_already_used",
                "msg": "Invalid Refresh Token: Already Used",
                "access_token_hint": "SECRET-SHOULD-NOT-APPEAR",
            }

        async def aclose(self):
            return None

    # The PARENT's factory, so our override wraps the stub exactly as it wraps the
    # real client. Patching the instance would replace the override itself and
    # prove nothing.
    from fastmcp.server.auth import OAuthProxy

    monkeypatch.setattr(OAuthProxy, "_create_upstream_oauth_client", lambda self: _Upstream())

    async def _jti_get(key):
        return SimpleNamespace(upstream_token_id="upstream-token-id")

    async def _upstream_get(key):
        return SimpleNamespace(
            refresh_token="upstream-refresh", scope="offline_access", access_token="old"
        )

    monkeypatch.setattr(
        proxy.jwt_issuer, "verify_token", lambda *_a, **_k: {"jti": "refresh-jti"}
    )
    monkeypatch.setattr(proxy._jti_mapping_store, "get", _jti_get)
    monkeypatch.setattr(proxy._upstream_token_store, "get", _upstream_get)

    client = SimpleNamespace(client_id="https://claude.ai/oauth/mcp-oauth-client-metadata")
    refresh = SimpleNamespace(token="fastmcp-refresh", scopes=["offline_access"])
    with caplog.at_level(logging.WARNING), pytest.raises(TokenError) as caught:
        await proxy.exchange_refresh_token(client, refresh, ["offline_access"])

    assert caught.value.error == "invalid_grant"
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "refresh_token_already_used" in logged
    assert "Already Used" in logged
    assert "SECRET-SHOULD-NOT-APPEAR" not in logged
    assert "upstream-refresh" not in logged


async def test_the_wrapped_upstream_client_still_delegates_everything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wrapper must be invisible to every call it does not care about."""
    proxy = _provider(monkeypatch)
    sentinel = object()

    class _Upstream:
        marker = sentinel

        async def aclose(self):
            self.closed = True

    inner = _Upstream()
    monkeypatch.setattr(type(proxy).__mro__[1], "_create_upstream_oauth_client", lambda self: inner)
    async with proxy._upstream_oauth_client() as wrapped:
        assert wrapped.marker is sentinel
    assert inner.closed is True
