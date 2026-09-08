"""OAuth state must survive a redeploy, or every connected user is signed out.

The proxy's `client_storage` is not a cache. It holds active authorization
transactions, issued authorization codes, client registrations, the JTI mapping
from a FastMCP token to its upstream one, and refresh-token metadata.

FastMCP's default is a local encrypted file store, documented as
development-only. On this image that path lives inside the container, so a
redeploy clears it. Measured 2026-09-08: two routine deploys signed a live
session out twice in one afternoon, and the user saw only "Connection has
expired".
"""

from __future__ import annotations

import pytest

from vaquill_mcp.oauth import _STORAGE_ENV, _durable_storage

_FERNET_KEY = "yBpH1n3rXQ8m0kR2sT4uV6wX8yZ0aB2cD4eF6gH8iJk="


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _STORAGE_ENV:
        monkeypatch.delenv(name, raising=False)


def test_unconfigured_falls_back_rather_than_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local checkout must still boot without Redis.

    Returning `(None, None)` leaves FastMCP's own defaults in place, which is
    right for development. It is wrong for production, which is why the code
    logs a warning rather than staying silent about it.
    """
    _clear(monkeypatch)
    assert _durable_storage() == (None, None)


@pytest.mark.parametrize("supplied", sorted(_STORAGE_ENV))
def test_half_configured_raises_instead_of_pretending(
    monkeypatch: pytest.MonkeyPatch, supplied: str
) -> None:
    """Partial configuration is worse than none: it LOOKS durable.

    Redis without an explicit `jwt_signing_key` is the subtle one. FastMCP
    derives that key from the upstream client secret, and derives the storage
    encryption key from it in turn, so rotating the Supabase client secret would
    orphan the whole store and sign everyone out with no error anywhere.
    """
    _clear(monkeypatch)
    monkeypatch.setenv(supplied, _FERNET_KEY if "ENCRYPTION" in supplied else "x")

    with pytest.raises(RuntimeError, match="half-configured"):
        _durable_storage()


def test_fully_configured_returns_an_encrypted_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Encrypted AT REST, not merely behind Redis auth.

    The upstream Supabase access and refresh tokens are inside these values, so
    anyone able to read the keyspace could otherwise read them in plaintext.
    """
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    _clear(monkeypatch)
    monkeypatch.setenv("VAQUILL_OAUTH_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("VAQUILL_OAUTH_JWT_SIGNING_KEY", "a-long-stable-secret")
    monkeypatch.setenv("VAQUILL_OAUTH_STORAGE_ENCRYPTION_KEY", _FERNET_KEY)

    storage, signing_key = _durable_storage()

    assert isinstance(storage, FernetEncryptionWrapper), "tokens would be stored in plaintext"
    assert signing_key == "a-long-stable-secret"


def test_the_signing_key_is_ours_and_not_derived_from_the_client_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two halves are one setting, and this is the half that is easy to miss.

    Persisting tokens to Redis while letting the signing key stay derived means
    the store survives a redeploy and still dies on a credential rotation. Both
    must come from the environment for either to be worth configuring.
    """
    _clear(monkeypatch)
    monkeypatch.setenv("VAQUILL_OAUTH_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("VAQUILL_OAUTH_JWT_SIGNING_KEY", "independent-of-supabase")
    monkeypatch.setenv("VAQUILL_OAUTH_STORAGE_ENCRYPTION_KEY", _FERNET_KEY)
    monkeypatch.setenv("VAQUILL_OAUTH_CLIENT_SECRET", "the-supabase-secret")

    _, signing_key = _durable_storage()

    assert signing_key == "independent-of-supabase"
    assert signing_key != "the-supabase-secret"
