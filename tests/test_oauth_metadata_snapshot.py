"""Whole-document goldens of the OAuth discovery surface, one row per origin.

`test_oauth_discovery_contract.py` asserts RULES about the US mount (fields,
prefixes, one scope). This pins the DOCUMENTS, so a moved line that changes any
key, URL or scope shows up as a diff. For every hosted origin it records:

* the Protected Resource Metadata (RFC 9728), as served at the origin's
  well-known path;
* the Authorization Server metadata (RFC 8414);
* the 401 `WWW-Authenticate` challenge a caller with no credential gets on `/mcp`.

On the US origin it also records that `/in/mcp` publishes NO discovery routes.
Measured 2026-11-25: an anonymous `initialize` there answers 200 with NO
`WWW-Authenticate` header at all (India is key-only because the key is required
per call, not because the door challenges). The extraction brief expected a 401;
the golden records what the code does, so a change to either is a visible diff.

A new origin is ONE row in `_ORIGINS`: `(origin, prm_path, app module)`. The
origin is also the value of `VAQUILL_PUBLIC_URL` for that row, because every
metadata URL, the `resource` and the issuer derive from it.

No network: the startup fetches are mocked, the OAuth group holds fake values.
Regenerate deliberately, after reading the diff:

    UPDATE_OAUTH_METADATA=1 .venv/bin/python -m pytest tests/test_oauth_metadata_snapshot.py
"""

from __future__ import annotations

import contextlib
import importlib
import json

import httpx
import httpx2
import pytest

from tests.surface_helpers import FIXTURES, check_golden
from tests.test_remote_mcp_path import _BASE, _live_api, _live_us, _oauth_on, _serving  # noqa: F401

_GOLDEN = FIXTURES / "oauth_metadata.json"
_ENV = "UPDATE_OAUTH_METADATA"
_PRM = "/.well-known/oauth-protected-resource/mcp"
_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

# One row per hosted origin. Australia's origin is one more line here.
_ORIGINS = [
    pytest.param("https://mcp.vaquill.ai", _PRM, "vaquill_mcp.remote_main", id="mcp"),
    pytest.param("https://reg-mcp.vaquill.ai", _PRM, "vaquill_mcp.regulatory_main", id="reg-mcp"),
    pytest.param(
        "https://tribunals-mcp.vaquill.ai", _PRM, "vaquill_mcp.tribunals_main", id="tribunals-mcp"
    ),
]


@contextlib.asynccontextmanager
async def _app_for(module: str):
    """`build_app()` of one hosted entry point with its lifespans entered."""
    if module == "vaquill_mcp.remote_main":
        async with _serving() as app:  # the fixture-module helper, reused as is
            yield app
        return
    app = importlib.import_module(module).build_app()
    async with app.router.lifespan_context(app):
        yield app


def _backend(request: pytest.FixtureRequest, module: str, respx_mock) -> None:
    """Mock the startup fetches this entry point makes."""
    if module == "vaquill_mcp.remote_main":
        request.getfixturevalue("_live_api")
        return
    respx_mock.get(url__regex=rf"{_BASE}/api/v1(/in)?/api-credits/pricing$").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )


def _record(response: httpx2.Response) -> dict:
    out: dict = {"status": response.status_code}
    challenge = response.headers.get("WWW-Authenticate")
    if challenge is not None:
        out["wwwAuthenticate"] = challenge
    return out


@pytest.mark.parametrize(("origin", "prm_path", "module"), _ORIGINS)
async def test_the_discovery_documents_match_the_snapshot(
    origin: str,
    prm_path: str,
    module: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock,
    _oauth_on: None,  # noqa: F811 - fixture imported above
) -> None:
    _backend(request, module, respx_mock)
    # The group `_oauth_on` sets names the US origin; each row serves its own.
    monkeypatch.setenv("VAQUILL_PUBLIC_URL", origin)

    async with _app_for(module) as app:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=origin
        ) as raw:
            challenge = await raw.post("/mcp", json=_INIT, headers=_HEADERS)
            prm = await raw.get(prm_path)
            meta = await raw.get("/.well-known/oauth-authorization-server")

    assert challenge.status_code == 401
    assert prm.status_code == 200
    assert meta.status_code == 200
    current = {
        "challenge": _record(challenge),
        "protectedResource": prm.json(),
        "authorizationServer": meta.json(),
    }
    check_golden(current, _GOLDEN, _ENV, key=origin)


@pytest.mark.usefixtures("_live_api", "_oauth_on")
async def test_no_india_route_or_discovery_document_survives_on_this_host() -> None:
    """Every India shape 404s here, documents included.

    Until 2026-11-26 this asserted the opposite and recorded it: `/in/mcp`
    answered 200 with NO challenge, and each of these five well-known paths was
    a measured 404, which together were the proof a path mount cannot do OAuth.
    India now lives at `in-mcp.vaquill.ai`, an origin root, so the snapshot
    records the clean removal instead.

    All five probes stay. A partial removal (the mount gone while a stale
    discovery document is still published, or the reverse) is the state that
    makes a client retry against a half-server, and it is indistinguishable
    from a clean removal unless both shapes are probed.
    """
    origin = "https://mcp.vaquill.ai"
    probes = (
        "/.well-known/oauth-protected-resource/in/mcp",
        "/.well-known/oauth-authorization-server/in",
        "/in/.well-known/oauth-protected-resource",
        "/in/.well-known/oauth-authorization-server",
        "/in/mcp/.well-known/oauth-protected-resource",
    )
    async with _serving() as app:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=origin
        ) as raw:
            india = await raw.post("/in/mcp", json=_INIT, headers=_HEADERS)
            well_known = {path: (await raw.get(path)).status_code for path in probes}

    assert india.status_code == 404, "the /in/mcp mount is still bound"
    assert set(well_known.values()) == {404}, well_known
    current = {"challenge": _record(india), "wellKnownStatus": well_known}
    assert "wwwAuthenticate" not in current["challenge"]
    check_golden(current, _GOLDEN, _ENV, key=f"{origin}/in/mcp")


def test_the_snapshot_covers_every_origin_row_and_holds_no_secret() -> None:
    golden = json.loads(_GOLDEN.read_text())
    rows = {param.values[0] for param in _ORIGINS}
    assert rows | {"https://mcp.vaquill.ai/in/mcp"} == set(golden)
    text = json.dumps(golden)
    for needle in ("client-abc", "shh", "internal-secret", "vq_key_"):
        assert needle not in text, f"{needle!r} must never appear in the fixture"
    for origin in rows:
        assert golden[origin]["challenge"]["status"] == 401
        assert golden[origin]["protectedResource"]["resource"] == f"{origin}/mcp"
