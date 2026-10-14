"""How long OAuth credentials live, and whether they can be ended.

Before this, a refresh token lasted a year (FastMCP's fallback, because Supabase
gives none), a stolen one could not be ended because no revocation endpoint was
published, and every dynamically registered client was kept forever although
registration is open to anyone. These tests pin the three fixes:

* `/revoke` (RFC 7009) is published and mounted, and revoking a refresh token
  deletes it from the store so it cannot be exchanged again;
* refresh tokens expire after 30 days by default, renewed by each refresh;
* a registered client expires after 90 days by default, renewed by each refresh
  that client makes, so one in use never loses its record.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tests.test_oauth import _OAUTH_ENV
from vaquill_mcp.oauth import (
    build_auth_provider,
    client_registration_seconds,
    refresh_token_seconds,
)

_DAY = 86400


def _auth(monkeypatch: pytest.MonkeyPatch, **extra_env: str):
    for name, value in {**_OAUTH_ENV, **extra_env}.items():
        monkeypatch.setenv(name, value)
    auth = build_auth_provider()
    assert auth is not None
    return auth


def test_revocation_is_published_and_mounted(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _auth(monkeypatch)
    app = Starlette(routes=auth.get_routes(mcp_path="/mcp"))
    client = TestClient(app, base_url="https://mcp.example.test")

    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["revocation_endpoint"].endswith("/revoke")

    # The route answers (here 401: no such client), where an unmounted one is a 404.
    response = client.post("/revoke", data={"token": "not-a-token", "client_id": "nobody"})
    assert response.status_code != 404


async def test_revoking_a_refresh_token_deletes_it_from_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.server.auth.oauth_proxy.proxy import _hash_token
    from mcp.server.auth.provider import RefreshToken

    auth = _auth(monkeypatch)
    auth.get_routes(mcp_path="/mcp")  # the token issuer is created here, as in the real app
    proxy = auth.server
    deleted: list[str] = []

    async def record(*, key: str, **_kw) -> None:
        deleted.append(key)

    monkeypatch.setattr(proxy._refresh_token_store, "delete", record)
    await proxy.revoke_token(RefreshToken(token="abc.def.ghi", client_id="c1", scopes=[]))
    assert deleted == [_hash_token("abc.def.ghi")]


def test_a_refresh_token_lasts_30_days_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VAQUILL_OAUTH_REFRESH_TOKEN_DAYS", raising=False)
    assert refresh_token_seconds() == 30 * _DAY
    assert _auth(monkeypatch).server._fallback_refresh_token_expiry_seconds == 30 * _DAY


def test_the_lifetimes_can_be_set_and_a_bad_value_fails_at_start_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAQUILL_OAUTH_REFRESH_TOKEN_DAYS", "7")
    monkeypatch.setenv("VAQUILL_OAUTH_CLIENT_REGISTRATION_DAYS", "45")
    assert refresh_token_seconds() == 7 * _DAY
    assert client_registration_seconds() == 45 * _DAY
    assert _auth(monkeypatch).server._fallback_refresh_token_expiry_seconds == 7 * _DAY

    for name, bad in (
        ("VAQUILL_OAUTH_REFRESH_TOKEN_DAYS", "forever"),
        ("VAQUILL_OAUTH_REFRESH_TOKEN_DAYS", "0"),
        ("VAQUILL_OAUTH_REFRESH_TOKEN_DAYS", "9999"),
        ("VAQUILL_OAUTH_CLIENT_REGISTRATION_DAYS", "-3"),
    ):
        monkeypatch.setenv(name, bad)
        with pytest.raises(ValueError, match=name):
            refresh_token_seconds()
            client_registration_seconds()
        monkeypatch.delenv(name)
        monkeypatch.setenv("VAQUILL_OAUTH_REFRESH_TOKEN_DAYS", "7")
        monkeypatch.setenv("VAQUILL_OAUTH_CLIENT_REGISTRATION_DAYS", "45")


def test_a_registration_outlives_the_refresh_tokens_it_can_hold() -> None:
    """Otherwise an active session could outlive its own client record."""
    assert client_registration_seconds() > refresh_token_seconds()


async def test_a_registered_client_is_stored_with_an_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    proxy = _auth(monkeypatch).server
    puts: list[dict] = []
    real_put = proxy._client_store.put

    async def spy(**kwargs):
        puts.append(kwargs)
        return await real_put(**kwargs)

    monkeypatch.setattr(proxy._client_store, "put", spy)
    await proxy.register_client(
        OAuthClientInformationFull(
            client_id="client-under-test",
            redirect_uris=[AnyUrl("http://localhost:4567/callback")],
            token_endpoint_auth_method="none",
        )
    )
    assert puts[0].get("ttl") is None, "FastMCP stores it without an expiry first"
    assert puts[-1]["key"] == "client-under-test"
    assert puts[-1]["ttl"] == 90 * _DAY, "we then set the expiry"


async def test_a_refresh_renews_the_client_and_a_renewal_failure_never_breaks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.server.auth import OAuthProxy

    proxy = _auth(monkeypatch).server
    sentinel = SimpleNamespace(access_token="new")

    async def fake_exchange(self, client, refresh_token, scopes):
        return sentinel

    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", fake_exchange)

    stored = SimpleNamespace(client_id="c1")
    puts: list[dict] = []

    async def get(**_kw):
        return stored

    async def put(**kwargs):
        puts.append(kwargs)

    monkeypatch.setattr(proxy._client_store, "get", get)
    monkeypatch.setattr(proxy._client_store, "put", put)

    result = await proxy.exchange_refresh_token(SimpleNamespace(client_id="c1"), object(), [])
    assert result is sentinel
    assert puts == [{"key": "c1", "value": stored, "ttl": 90 * _DAY}]

    async def broken_put(**_kw):
        raise RuntimeError("redis is down")

    monkeypatch.setattr(proxy._client_store, "put", broken_put)
    assert await proxy.exchange_refresh_token(SimpleNamespace(client_id="c1"), object(), []) is sentinel
