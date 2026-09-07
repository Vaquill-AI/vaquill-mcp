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
