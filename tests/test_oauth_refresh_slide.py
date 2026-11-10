"""A refresh must RESET the refresh-token lifetime, and a refusal must say who it was.

Supabase (GoTrue) answers a refresh with no `refresh_expires_in`. FastMCP's
`OAuthProxy.exchange_refresh_token` then keeps the ORIGINAL expiry on every
rotation (oauth_proxy/proxy.py, "Keep existing expiry if upstream doesn't
provide new one"), so a 30-day refresh lifetime was a hard 30 days from
authorization and not 30 days of disuse. Measured 2026-11-10 with these same
fixtures against the unpatched class: a connection with 1 day left came out of
a SUCCESSFUL refresh with 1.00 day left.

Every test here drives the real `exchange_refresh_token` against a fake
upstream shaped like GoTrue's answer. A test that asserted on a mocked
`super()` would pass with or without the defect.
"""

from __future__ import annotations

import asyncio
import logging
import time
import pytest

from tests.test_oauth import _OAUTH_ENV
from vaquill_mcp.oauth import _token_fingerprint, build_auth_provider, refresh_token_seconds

_DAY = 86400
_CLIENT = "client-under-test"


class _GoTrueUpstream:
    """A successful refresh as Supabase sends it: rotated token, NO `refresh_expires_in`."""

    def __init__(self, response: dict | None = None):
        self.calls = 0
        self.response = response

    async def refresh_token(self, **_kw):
        self.calls += 1
        if self.response is not None:
            return self.response
        return {
            "access_token": f"at-{self.calls}",
            "refresh_token": f"rt-{self.calls}",
            "expires_in": 3600,
            "token_type": "bearer",
        }

    async def aclose(self) -> None:
        pass


def _proxy(monkeypatch: pytest.MonkeyPatch, upstream: _GoTrueUpstream):
    from fastmcp.server.auth import OAuthProxy

    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("VAQUILL_OAUTH_REFRESH_TOKEN_DAYS", raising=False)
    auth = build_auth_provider()
    assert auth is not None
    # Below `VaquillOAuthProxy`, so its refusal-aware wrapper is exercised as shipped.
    monkeypatch.setattr(OAuthProxy, "_create_upstream_oauth_client", lambda self: upstream)
    proxy = auth.server
    proxy.set_mcp_path("/mcp")
    return proxy


async def _connection(
    proxy, *, days_left: float, jwt_expires_in: int | None = None, record: str = "u1", jti: str = "rj1"
):
    """A connection authorized long ago whose refresh token has `days_left` to run.

    Returns the presented `RefreshToken` and the upstream record's id.
    """
    from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet
    from mcp.server.auth.provider import RefreshToken

    now = time.time()
    left = int(days_left * _DAY)
    await proxy._upstream_token_store.put(
        key=record,
        value=UpstreamTokenSet(
            upstream_token_id=record,
            access_token="old-at",
            refresh_token="old-rt",
            refresh_token_expires_at=now + left,
            expires_at=now - 10,
            token_type="Bearer",
            scope="offline_access",
            client_id=_CLIENT,
            created_at=now - 100 * _DAY,
        ),
        ttl=max(left, 1),
    )
    token = proxy.jwt_issuer.issue_refresh_token(
        client_id=_CLIENT,
        scopes=["offline_access"],
        jti=jti,
        expires_in=jwt_expires_in if jwt_expires_in is not None else left,
        upstream_claims=None,
    )
    await proxy._jti_mapping_store.put(
        key=jti,
        value=JTIMapping(jti=jti, upstream_token_id=record, created_at=now),
        ttl=max(left, 1),
    )
    return RefreshToken(
        token=token, client_id=_CLIENT, scopes=["offline_access"], expires_at=int(now + left)
    )


def _client():
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    return OAuthClientInformationFull(
        client_id=_CLIENT, redirect_uris=[AnyUrl("http://localhost/cb")]
    )


def _spy_puts(monkeypatch: pytest.MonkeyPatch, proxy) -> dict[str, list[int]]:
    """TTLs (seconds) of every write to the three stores a refresh token lives in."""
    ttls: dict[str, list[int]] = {"upstream": [], "jti": [], "refresh": []}
    for label, store in (
        ("upstream", proxy._upstream_token_store),
        ("jti", proxy._jti_mapping_store),
        ("refresh", proxy._refresh_token_store),
    ):
        real = store.put

        async def spy(*, _real=real, _label=label, **kwargs):
            ttls[_label].append(kwargs["ttl"])
            return await _real(**kwargs)

        monkeypatch.setattr(store, "put", spy)
    return ttls


async def test_a_refresh_with_one_day_left_comes_back_with_a_full_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE test: the real exchange against a GoTrue-shaped upstream.

    With the defect this fails with "1.00 days", the figure the unpatched class
    produced when measured.
    """
    from fastmcp.server.auth.oauth_proxy.proxy import _hash_token

    proxy = _proxy(monkeypatch, _GoTrueUpstream())
    presented = await _connection(proxy, days_left=1)
    ttls = _spy_puts(monkeypatch, proxy)

    result = await proxy.exchange_refresh_token(_client(), presented, ["offline_access"])

    full = refresh_token_seconds()
    assert full == 365 * _DAY

    metadata = await proxy._refresh_token_store.get(key=_hash_token(result.refresh_token))
    remaining = (metadata.expires_at - time.time()) / _DAY
    assert remaining == pytest.approx(365, abs=0.01), f"{remaining:.2f} days left, not 365"

    # The refresh JWT's own `exp` has to slide too, or the client presents a
    # token that is dead by signature while every record still looks alive.
    claims = proxy.jwt_issuer.verify_token(result.refresh_token, expected_token_use="refresh")
    assert (claims["exp"] - time.time()) / _DAY == pytest.approx(365, abs=0.01)

    # And so must every storage TTL, or Redis evicts the record under a token that
    # still looks valid. The upstream record is written twice (by the slide, then
    # by the exchange): both at the full lifetime.
    for label, values in ttls.items():
        assert values, f"{label} store was never written"
    for ttl in ttls["upstream"] + ttls["refresh"]:
        assert ttl == pytest.approx(full, abs=60), f"store ttl {ttl / _DAY:.2f} days"
    # The JTI store also takes the one-hour ACCESS token mapping; the refresh
    # token's own mapping is the longer of the two.
    assert max(ttls["jti"]) == pytest.approx(full, abs=60), f"jti ttl {max(ttls['jti']) / _DAY:.2f} days"

    record = await proxy._upstream_token_store.get(key="u1")
    assert (record.refresh_token_expires_at - time.time()) / _DAY == pytest.approx(365, abs=0.01)


async def test_every_refresh_slides_again_not_just_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chain two rotations, aging the record between them, as a real session does."""
    from fastmcp.server.auth.oauth_proxy.proxy import _hash_token
    from mcp.server.auth.provider import RefreshToken

    proxy = _proxy(monkeypatch, _GoTrueUpstream())
    presented = await _connection(proxy, days_left=2)

    first = await proxy.exchange_refresh_token(_client(), presented, ["offline_access"])

    # 300 days later the record has 65 days left; the second refresh must restore it.
    record = await proxy._upstream_token_store.get(key="u1")
    record.refresh_token_expires_at = time.time() + 65 * _DAY
    await proxy._upstream_token_store.put(key="u1", value=record, ttl=65 * _DAY)

    metadata = await proxy._refresh_token_store.get(key=_hash_token(first.refresh_token))
    second = await proxy.exchange_refresh_token(
        _client(),
        RefreshToken(
            token=first.refresh_token,
            client_id=_CLIENT,
            scopes=metadata.scopes,
            expires_at=metadata.expires_at,
        ),
        ["offline_access"],
    )
    after = await proxy._refresh_token_store.get(key=_hash_token(second.refresh_token))
    assert (after.expires_at - time.time()) / _DAY == pytest.approx(365, abs=0.01)


async def test_a_refresh_token_that_has_expired_is_not_revived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sliding extends a LIVE session. It must never resurrect a lapsed one."""
    from mcp.server.auth.provider import TokenError

    upstream = _GoTrueUpstream()
    proxy = _proxy(monkeypatch, upstream)
    presented = await _connection(proxy, days_left=1, jwt_expires_in=-60)
    before = (await proxy._upstream_token_store.get(key="u1")).refresh_token_expires_at

    with pytest.raises(TokenError) as raised:
        await proxy.exchange_refresh_token(_client(), presented, ["offline_access"])
    assert raised.value.error == "invalid_grant"

    assert upstream.calls == 0, "an expired token must never reach the upstream"
    after = (await proxy._upstream_token_store.get(key="u1")).refresh_token_expires_at
    assert after == before, "the record of a lapsed token was extended"


async def test_a_lifetime_the_upstream_states_is_still_honoured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An IdP that DOES send `refresh_expires_in` keeps its own, shorter, say."""
    from fastmcp.server.auth.oauth_proxy.proxy import _hash_token

    proxy = _proxy(
        monkeypatch,
        _GoTrueUpstream(
            {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 3600,
                "refresh_expires_in": 7 * _DAY,
            }
        ),
    )
    presented = await _connection(proxy, days_left=1)
    result = await proxy.exchange_refresh_token(_client(), presented, ["offline_access"])
    metadata = await proxy._refresh_token_store.get(key=_hash_token(result.refresh_token))
    assert (metadata.expires_at - time.time()) / _DAY == pytest.approx(7, abs=0.01)


async def test_a_failure_while_sliding_never_breaks_the_refresh(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If a fastmcp release renames the internals, sign-in still works and says so."""
    proxy = _proxy(monkeypatch, _GoTrueUpstream())
    presented = await _connection(proxy, days_left=1)

    real_get = proxy._jti_mapping_store.get
    calls = {"n": 0}

    async def failing_once(**kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # 1 is the lock's lookup, 2 the slide's; fastmcp's own is 3
            raise RuntimeError("redis is down")
        return await real_get(**kwargs)

    monkeypatch.setattr(proxy._jti_mapping_store, "get", failing_once)
    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        result = await proxy.exchange_refresh_token(_client(), presented, ["offline_access"])
    assert result.access_token
    assert "oauth_refresh_slide_skipped reason=RuntimeError" in caplog.text


async def test_a_renamed_lock_helper_still_refreshes_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The lock fallback must be LOUD, for the same reason the slide's is.

    Both halves of the override read private fastmcp members. A silent lock
    fallback would reopen the rotation race on a future release while every
    test stayed green, which is the shape of the hard cap this work replaced:
    protection that stops working and never says so.
    """
    proxy = _proxy(monkeypatch, _GoTrueUpstream())
    presented = await _connection(proxy, days_left=1)

    # `_get_refresh_lock` is defined on fastmcp's OAuthProxy, not on our
    # subclass, so it cannot be deleted from either. Setting it to None on the
    # instance is what a rename looks like to `getattr(self, name, None)`.
    monkeypatch.setattr(proxy, "_get_refresh_lock", None, raising=False)
    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        result = await proxy.exchange_refresh_token(_client(), presented, ["offline_access"])

    assert result.access_token, "a renamed helper must not break sign-in"
    assert "oauth_refresh_lock_skipped reason=no_lock_helper" in caplog.text


async def test_the_lock_is_not_skipped_on_the_versions_we_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """...and the warning must stay SILENT while the member exists.

    Without this, the test above passes on a release where the lock never
    engages at all: a no-data case and a negative case producing the same
    output. Measured on fastmcp 4.0.1 and 4.1.7 (2026-11-10), the lock engages.
    """
    proxy = _proxy(monkeypatch, _GoTrueUpstream())
    presented = await _connection(proxy, days_left=1)

    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        result = await proxy.exchange_refresh_token(_client(), presented, ["offline_access"])

    assert result.access_token
    assert "oauth_refresh_lock_skipped" not in caplog.text


# ---------------------------------------------------------------------------
# Forensics: which client, which token, and why
# ---------------------------------------------------------------------------


def test_the_fingerprint_is_eight_hex_of_a_hash_and_never_the_token() -> None:
    import hashlib

    token = "eyJhbGciOi.super.secret"
    fingerprint = _token_fingerprint(token)
    assert fingerprint == hashlib.sha256(token.encode()).hexdigest()[:8]
    assert len(fingerprint) == 8
    assert token not in fingerprint
    assert _token_fingerprint(None) == "none"
    assert _token_fingerprint("") == "none"


async def test_an_upstream_refusal_names_the_client_and_a_token_hash_but_not_the_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from mcp.server.auth.provider import TokenError

    # GoTrue's refusal shape: no `access_token`, no `error` key.
    refusal = {"code": 400, "error_code": "refresh_token_already_used", "msg": "Already Used"}
    proxy = _proxy(monkeypatch, _GoTrueUpstream(refusal))
    presented = await _connection(proxy, days_left=10)

    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        with pytest.raises(TokenError) as raised:
            await proxy.exchange_refresh_token(_client(), presented, ["offline_access"])
    assert raised.value.error == "invalid_grant"

    fingerprint = _token_fingerprint(presented.token)
    assert (
        f"oauth_refresh_refused client_id={_CLIENT} token={fingerprint} reason=upstream_refused"
        in caplog.text
    )
    assert "refresh_token_already_used" in caplog.text, "the existing upstream warning is kept"
    assert presented.token not in caplog.text, "a raw refresh token reached the log"


async def test_a_token_fastmcp_refuses_is_logged_with_its_error_code(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from mcp.server.auth.provider import RefreshToken, TokenError

    proxy = _proxy(monkeypatch, _GoTrueUpstream())
    bogus = RefreshToken(token="not.a.jwt", client_id=_CLIENT, scopes=[])
    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        with pytest.raises(TokenError):
            await proxy.exchange_refresh_token(_client(), bogus, [])
    assert (
        f"oauth_refresh_refused client_id={_CLIENT} token={_token_fingerprint('not.a.jwt')} "
        "reason=jwt_invalid jwt_error=DecodeError" in caplog.text
    )
    assert "not.a.jwt" not in caplog.text


async def test_load_refresh_token_rejection_says_why(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from fastmcp.server.auth.oauth_proxy.proxy import _hash_token
    from fastmcp.server.auth.oauth_proxy.models import RefreshTokenMetadata

    proxy = _proxy(monkeypatch, _GoTrueUpstream())
    presented = await _connection(proxy, days_left=10)
    fingerprint = _token_fingerprint(presented.token)

    # A valid token the store has lost (already rotated, revoked or evicted).
    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        assert await proxy.load_refresh_token(_client(), presented.token) is None
    assert (
        f"oauth_refresh_token_rejected client_id={_CLIENT} token={fingerprint} "
        "reason=not_in_store jwt_valid=True" in caplog.text
    )

    # The same token held by the wrong client.
    await proxy._refresh_token_store.put(
        key=_hash_token(presented.token),
        value=RefreshTokenMetadata(
            client_id="someone-else", scopes=[], expires_at=int(time.time()) + 100, created_at=0
        ),
        ttl=100,
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        assert await proxy.load_refresh_token(_client(), presented.token) is None
    assert "reason=client_mismatch jwt_valid=True" in caplog.text

    # Not our token at all (expired, or signed by a key this process lacks).
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        assert await proxy.load_refresh_token(_client(), "garbage") is None
    assert "reason=not_in_store jwt_valid=False" in caplog.text
    assert presented.token not in caplog.text


async def test_an_unknown_client_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`/token` answers 401 invalid_client and nothing else; this is the only trace."""
    proxy = _proxy(monkeypatch, _GoTrueUpstream())
    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        assert await proxy.get_client("lapsed-registration") is None
    assert "oauth_client_unknown client_id='lapsed-registration'" in caplog.text


def test_a_registration_not_longer_than_the_refresh_lifetime_warns_at_start_up(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VAQUILL_OAUTH_CLIENT_REGISTRATION_DAYS", "45")
    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        assert build_auth_provider() is not None
    assert "CLIENT_REGISTRATION_DAYS (45) is not longer than" in caplog.text

    caplog.clear()
    monkeypatch.delenv("VAQUILL_OAUTH_CLIENT_REGISTRATION_DAYS")
    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.oauth"):
        assert build_auth_provider() is not None
    assert "is not longer than" not in caplog.text



# ---------------------------------------------------------------------------
# The rotation race: a client refresh and a transparent refresh, one record
# ---------------------------------------------------------------------------


class _GatedUpstream(_GoTrueUpstream):
    """Holds every refresh open until released, counting how many are in flight at once."""

    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()
        self.in_flight = 0
        self.max_in_flight = 0

    async def refresh_token(self, **kw):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await self.gate.wait()
            return await super().refresh_token(**kw)
        finally:
            self.in_flight -= 1


async def _settle() -> None:
    """Let every task that can run without the gate run. The stores are in memory."""
    await asyncio.sleep(0.05)


async def test_a_client_refresh_waits_for_an_in_flight_transparent_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`load_access_token` rotates the upstream refresh token under a per-record
    lock; `exchange_refresh_token` took none, so both presented the SAME upstream
    token to Supabase. Here the transparent refresh is the real
    `load_access_token`, held open at the upstream, with the client's refresh
    arriving while it is. Unserialised, the second call reaches the upstream
    while the first is still in flight (`max_in_flight == 2`).
    """
    upstream = _GatedUpstream()
    proxy = _proxy(monkeypatch, upstream)
    presented = await _connection(proxy, days_left=10)

    # An access token whose upstream half has expired, so load_access_token refreshes.
    from fastmcp.server.auth.oauth_proxy.models import JTIMapping

    access = proxy.jwt_issuer.issue_access_token(
        client_id=_CLIENT, scopes=["offline_access"], jti="aj1", expires_in=3600,
        upstream_claims=None,
    )
    await proxy._jti_mapping_store.put(
        key="aj1",
        value=JTIMapping(jti="aj1", upstream_token_id="u1", created_at=time.time()),
        ttl=3600,
    )

    async def not_valid(_token):
        return None

    monkeypatch.setattr(proxy._token_validator, "verify_token", not_valid)

    transparent = asyncio.create_task(proxy.load_access_token(access))
    await _settle()
    assert upstream.in_flight == 1, "the transparent refresh should be waiting at the upstream"

    client_side = asyncio.create_task(
        proxy.exchange_refresh_token(_client(), presented, ["offline_access"])
    )
    await _settle()
    assert upstream.calls == 0 and upstream.max_in_flight == 1
    assert upstream.in_flight == 1, "the client's refresh reached the upstream while another was in flight"
    assert not client_side.done()

    upstream.gate.set()
    await transparent
    result = await client_side
    assert result.access_token
    assert upstream.max_in_flight == 1
    assert upstream.calls == 2, "both refreshes still happened, one after the other"


async def test_the_lock_is_per_record_so_other_users_are_not_held_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing the race must not serialise every refresh behind one lock.

    Two DIFFERENT users refresh at once. Both must be at the upstream together.
    """
    upstream = _GatedUpstream()
    proxy = _proxy(monkeypatch, upstream)
    first = await _connection(proxy, days_left=10, record="u1", jti="rj1")
    second = await _connection(proxy, days_left=10, record="u2", jti="rj2")

    tasks = [
        asyncio.create_task(proxy.exchange_refresh_token(_client(), presented, ["offline_access"]))
        for presented in (first, second)
    ]
    await _settle()
    assert upstream.in_flight == 2, "one user's refresh waited on another's"

    upstream.gate.set()
    for task in tasks:
        assert (await task).access_token
