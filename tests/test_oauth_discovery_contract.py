"""The OAuth discovery contract on the remote `/mcp` path, asserted in process.

Three documents decide whether a client can connect at all, and every failure is
silent (the client reports "couldn't reach the MCP server"):

* the 401 `WWW-Authenticate` challenge, which must carry `resource_metadata`
  pointing at THIS endpoint, because clients do not honour a challenge on a 200;
* the Protected Resource Metadata (RFC 9728), whose `resource` must equal the
  URL the user typed, path included;
* the Authorization Server metadata (RFC 8414), where Claude picks Client ID
  Metadata Documents only if BOTH `client_id_metadata_document_supported` and
  `"none"` auth are advertised.

Driven through `build_app()` over ASGI with the existing fixtures from
`test_remote_mcp_path.py`; no network. The flow itself is covered by
`tests/oauth_lab/`.

KNOWN DEVIATION, pinned on purpose: `offline_access` appears in PRM
`scopes_supported` (and in AS metadata). The MCP spec says servers SHOULD NOT
list it in PRM. FastMCP builds PRM and AS metadata from the SAME `valid_scopes`,
and that list is also what the proxy forwards upstream, so dropping it from PRM
means dropping it everywhere and no client is asked for a refresh token (the
connection then dies when the upstream access token expires, about an hour). See
the comment at `valid_scopes` in `oauth.py`. If this test fails because the
scope moved, check refresh still works end to end before accepting it.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx2
import pytest

from tests.test_remote_mcp_path import _live_api, _live_us, _oauth_on, _serving  # noqa: F401

_ORIGIN = "https://mcp.vaquill.ai"
_PRM_PATH = "/.well-known/oauth-protected-resource/mcp"
_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture
async def _documents(_live_api: None, _oauth_on: None):  # noqa: F811 - fixtures imported above
    """(challenge header, PRM json, AS metadata json) from one running app."""
    async with _serving() as app:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=_ORIGIN) as raw:
            challenge = await raw.post("/mcp", json=_INIT, headers=_HEADERS)
            prm = await raw.get(_PRM_PATH)
            meta = await raw.get("/.well-known/oauth-authorization-server")
    assert challenge.status_code == 401
    assert prm.status_code == 200
    assert meta.status_code == 200
    return challenge.headers.get("WWW-Authenticate", ""), prm.json(), meta.json()


async def test_challenge_points_at_the_protected_resource_document(_documents) -> None:
    challenge, _prm, _meta = _documents

    assert challenge.startswith("Bearer")
    assert f'resource_metadata="{_ORIGIN}{_PRM_PATH}"' in challenge


async def test_the_challenge_url_is_the_document_that_is_served(_documents) -> None:
    """The URL in the challenge must resolve to the PRM, not just look right."""
    challenge, prm, _meta = _documents
    url = challenge.split('resource_metadata="', 1)[1].split('"', 1)[0]

    assert urlparse(url).path == _PRM_PATH
    assert prm["resource"] == f"{_ORIGIN}/mcp"


async def test_protected_resource_metadata(_documents) -> None:
    _challenge, prm, meta = _documents

    # Must match the URL the user types, path included.
    assert prm["resource"] == f"{_ORIGIN}/mcp"
    # Claude uses only the first entry and never falls back to a later one.
    assert prm["authorization_servers"] == [meta["issuer"]]
    assert prm["bearer_methods_supported"] == ["header"]
    # Known deviation, see the module docstring.
    assert prm["scopes_supported"] == ["offline_access"]
    assert "openid" not in prm["scopes_supported"]


async def test_authorization_server_metadata(_documents) -> None:
    _challenge, _prm, meta = _documents

    assert meta["issuer"].rstrip("/") == _ORIGIN
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        assert meta[key].startswith(f"{_ORIGIN}/"), key
    assert meta["response_types_supported"] == ["code"]
    assert "authorization_code" in meta["grant_types_supported"]
    assert "refresh_token" in meta["grant_types_supported"]
    # PKCE support must be advertised; Claude always sends S256.
    assert meta["code_challenge_methods_supported"] == ["S256"]
    # Both are needed for Claude to choose CIMD over DCR.
    assert meta["client_id_metadata_document_supported"] is True
    assert "none" in meta["token_endpoint_auth_methods_supported"]
    assert "offline_access" in meta["scopes_supported"]
    assert "openid" not in meta["scopes_supported"]


@pytest.mark.usefixtures("_live_api", "_oauth_on")
async def test_india_publishes_no_discovery_documents() -> None:
    """`/in` stays key-only: no PRM at the location a client would derive."""
    async with _serving() as app:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=_ORIGIN) as raw:
            stray = await raw.get("/.well-known/oauth-protected-resource/in/mcp")
            india = await raw.post("/in/mcp", json=_INIT, headers=_HEADERS)

    assert stray.status_code == 404
    assert "resource_metadata" not in india.headers.get("WWW-Authenticate", "")
