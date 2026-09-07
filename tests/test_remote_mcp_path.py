"""What each hosted URL actually SERVES, driven through the composed app.

`test_remote_dual_mount.py` asserts the routing table. This file asserts the
answers, because on this server a green connection proves nothing: the MCP
handshake completes and all tools list with NO credential at all, an unexpanded
`${VAQUILL_API_KEY}`, or an invalid key. Authentication is enforced at the API
call behind each tool, not at the protocol layer, so the only test that means
anything is one that reaches the point where a request is sent to
`api.vaquill.ai` and inspects the `Authorization` header on it.

That is what `_captured_key` does, and it is the assertion that protects
billing: credits land on whoever that header names.

`/mcp` exists because claude.ai will not talk to an MCP endpoint on any other
path -- it completes the OAuth token exchange with a 200, then silently never
sends the MCP request (anthropics/claude-ai-mcp#423). It needs no OAuth to be
useful: `_get_api_key()` reads the Authorization header before it looks at the
path, so a `vq_key_` bearer token works there today.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
from collections.abc import AsyncIterator

import httpx
import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_BASE = "https://api.vaquill.ai"

# Free, and one live call is the only real proof the wiring works.
_FREE_TOOL = "list_statutes_coverage"


def _spec(jurisdiction: str) -> dict:
    return json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())


@pytest.fixture
def _live_us(monkeypatch: pytest.MonkeyPatch, respx_mock) -> None:
    """Mock the startup fetches the US server makes.

    Split from `_live_api` because respx asserts every registered route was
    called, so a US-only test that also declared the India document would fail
    in teardown for a reason unrelated to what it asserts.
    """
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    respx_mock.get(f"{_BASE}/external/openapi.json").mock(
        return_value=httpx.Response(200, json=_spec("US"))
    )
    # The hosted server has no key at startup, so it uses the PUBLIC matrix.
    respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )


@pytest.fixture
def _live_api(_live_us: None, respx_mock) -> None:
    """Mock the startup fetches every mounted app makes."""
    respx_mock.get(f"{_BASE}/in/openapi.json").mock(
        return_value=httpx.Response(200, json=_spec("IN"))
    )


@contextlib.asynccontextmanager
async def _serving() -> AsyncIterator[object]:
    """`build_app()` with every mounted lifespan entered, as uvicorn runs it."""
    from vaquill_mcp.remote_main import build_app

    app = build_app()
    async with app.router.lifespan_context(app):
        yield app


def _client(app: object, path: str, headers: dict[str, str] | None = None) -> Client:
    """An MCP client speaking to the composed app in-process over ASGI.

    ASGI rather than a live port, so the test exercises the real Starlette
    routing table -- mounts, order, prefix stripping -- rather than a FastMCP
    server instance in isolation, which is where every failure this file guards
    against actually lives.
    """

    def factory(**kwargs):  # noqa: ANN003, ANN202
        # `**kwargs` rather than a fixed signature: the MCP SDK passes
        # `follow_redirects` among others, and the set has changed across
        # versions.
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)

    return Client(
        StreamableHttpTransport(
            f"http://mcp.vaquill.ai{path}",
            headers=headers,
            httpx_client_factory=factory,
        )
    )


@pytest.fixture
def _captured_key(respx_mock) -> list[str | None]:
    """Record the Authorization header on each outgoing call to the API."""
    seen: list[str | None] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"states": [], "totalSections": 0})

    respx_mock.get(url__regex=rf"{_BASE}/api/v1/us/statutes/coverage.*").mock(
        side_effect=_record
    )
    return seen


# ---------------------------------------------------------------------------
# The new URL shape
# ---------------------------------------------------------------------------


async def test_every_url_shape_completes_the_handshake(_live_api: None) -> None:
    """All four, including the two customers already depend on."""
    async with _serving() as app:
        for path in ("/mcp", "/in/mcp", "/s/vq_key_test", "/in/s/vq_key_test"):
            async with _client(app, path) as client:
                assert await client.list_tools(), path


async def test_mcp_path_answers_without_a_redirect(_live_api: None) -> None:
    """A redirect here is how the Authorization header gets dropped.

    Mounting the app at the prefix `/mcp` with an inner `/` route makes a bare
    `POST /mcp` miss and come back 307 to `/mcp/`. Claude.ai follows that and
    arrives unauthenticated, while local clients fail fast, which is the
    canonical "works in Claude Code but not claude.ai" shape.
    """
    async with _serving() as app:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://mcp.vaquill.ai"
        ) as raw:
            for path in ("/mcp", "/in/mcp"):
                response = await raw.post(
                    path,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "t", "version": "0"},
                        },
                    },
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                )
                assert response.status_code == 200, (path, response.status_code)
                assert response.headers.get("mcp-session-id"), path


async def test_each_url_shape_serves_only_its_own_jurisdiction(
    _live_api: None,
) -> None:
    """The isolation the two-app design exists for, on the new shape too."""
    async with _serving() as app:
        async with _client(app, "/mcp") as client:
            us = {t.name for t in await client.list_tools()}
        async with _client(app, "/in/mcp") as client:
            india = {t.name for t in await client.list_tools()}

    assert "search_us_statutes" in us and "search_acts" not in us
    assert "search_acts" in india and "search_us_statutes" not in india
    # `search` and `fetch` are published by both, and are the one deliberate
    # overlap: each is bound to its own app's client and document.
    assert (us & india) == {"search", "fetch"}


async def test_both_url_shapes_publish_the_same_catalogue(_live_api: None) -> None:
    """A customer moving off the path URL must not lose or gain a tool."""
    async with _serving() as app:
        async with _client(app, "/mcp") as client:
            via_header = {t.name for t in await client.list_tools()}
        async with _client(app, "/s/vq_key_test") as client:
            via_path = {t.name for t in await client.list_tools()}

    assert via_header == via_path


# ---------------------------------------------------------------------------
# Where the key comes from, which is where the money goes
# ---------------------------------------------------------------------------


async def test_a_bearer_token_on_the_mcp_path_reaches_the_api(
    _live_api: None, _captured_key: list[str | None]
) -> None:
    """`/mcp` needs no OAuth to work today. This is why."""
    async with _serving() as app:
        async with _client(
            app, "/mcp", headers={"Authorization": "Bearer vq_key_header"}
        ) as client:
            await client.call_tool(_FREE_TOOL, {})

    assert _captured_key == ["Bearer vq_key_header"]


async def test_the_path_key_url_still_charges_the_right_user(
    _live_api: None, _captured_key: list[str | None]
) -> None:
    """The Phase 0 regression that matters: an existing customer URL is unchanged.

    Not "still returns 200" -- still sends THAT customer's key upstream. The
    mount moved from `/` to `/s` and the app's own route from `/s/{api_key}` to
    `/{api_key}`; a prefix-stripping mistake there would leave the URL working
    and the path parameter empty.
    """
    async with _serving() as app:
        async with _client(app, "/s/vq_key_from_path") as client:
            await client.call_tool(_FREE_TOOL, {})

    assert _captured_key == ["Bearer vq_key_from_path"]


async def test_a_bearer_token_still_wins_over_the_path(
    _live_api: None, _captured_key: list[str | None]
) -> None:
    """`/s/_` plus a header is a documented form, and the header takes priority."""
    async with _serving() as app:
        async with _client(
            app, "/s/_", headers={"Authorization": "Bearer vq_key_header"}
        ) as client:
            await client.call_tool(_FREE_TOOL, {})

    assert _captured_key == ["Bearer vq_key_header"]


async def test_no_credential_at_all_fails_the_call_rather_than_the_listing(
    _live_api: None, respx_mock
) -> None:
    """The trap that makes every other test in this file necessary.

    Listing tools with no credential SUCCEEDS, by design: the catalogue is
    static and authentication lives at the API call. So a connector that looks
    healthy proves nothing, and the failure has to be asserted where it happens.

    No upstream route is registered on purpose. Nothing may be sent at all: an
    unauthenticated request would be a 401 the caller reads as our outage.
    """
    async with _serving() as app:
        async with _client(app, "/mcp") as client:
            assert await client.list_tools()  # green, and meaningless
            with pytest.raises(Exception, match="[Mm]issing API key"):
                await client.call_tool(_FREE_TOOL, {})

    assert not [c for c in respx_mock.calls if "coverage" in str(c.request.url)]


# ---------------------------------------------------------------------------
# Auth is per-mount, which is the whole reason the two shapes are separate apps
# ---------------------------------------------------------------------------


async def test_the_path_key_mount_never_carries_an_auth_provider(
    _live_us: None,
) -> None:
    """The regression that would break every existing customer at once.

    FastMCP reads `self.auth` when it builds an HTTP app, and
    `RequireAuthMiddleware` 401s a request carrying no Authorization header --
    which is exactly what `https://mcp.vaquill.ai/s/vq_key_...` sends. If the
    two mounts ever came from one server instance again, switching OAuth on
    would 401 every path-form URL in the field, and the failure would look like
    an outage rather than a configuration change.
    """
    from vaquill_mcp.remote import create_remote_server

    sentinel = object()
    key_server = create_remote_server("US")
    mcp_server = create_remote_server("US", auth=sentinel)

    assert key_server.auth is None
    assert mcp_server.auth is sentinel
    assert key_server is not mcp_server


# ---------------------------------------------------------------------------
# The composed app with OAuth actually switched on
# ---------------------------------------------------------------------------

_OAUTH_ENV = {
    "VAQUILL_OAUTH_UPSTREAM_JWKS_URI": "https://p.supabase.co/auth/v1/.well-known/jwks.json",
    "VAQUILL_OAUTH_UPSTREAM_ISSUER": "https://p.supabase.co/auth/v1",
    "VAQUILL_OAUTH_AUTHORIZE_URL": "https://p.supabase.co/auth/v1/oauth/authorize",
    "VAQUILL_OAUTH_TOKEN_URL": "https://p.supabase.co/auth/v1/oauth/token",
    "VAQUILL_OAUTH_CLIENT_ID": "client-abc",
    "VAQUILL_OAUTH_CLIENT_SECRET": "shh",
    "VAQUILL_PUBLIC_URL": "https://mcp.vaquill.ai",
}


@pytest.fixture
def _oauth_on(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """OAuth configured, with the proxy's client store pointed at a temp dir."""
    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VAQUILL_INTERNAL_SECRET", "internal-secret")
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path))


async def test_switching_oauth_on_does_not_401_the_existing_customer_urls(
    _live_api: None, _oauth_on: None, _captured_key: list[str | None]
) -> None:
    """The Phase 1 regression that would be worst, and is entirely silent.

    `https://mcp.vaquill.ai/s/vq_key_...` sends NO Authorization header. An auth
    provider that reached that mount would 401 it, and every customer on the
    path form would break at once, on a config change, with a failure that reads
    as an outage.
    """
    async with _serving() as app:
        async with _client(app, "/s/vq_key_from_path") as client:
            assert await client.list_tools()
            await client.call_tool(_FREE_TOOL, {})

    assert _captured_key == ["Bearer vq_key_from_path"]


async def test_a_raw_key_still_authenticates_at_the_mcp_mount(
    _live_api: None, _oauth_on: None, _captured_key: list[str | None]
) -> None:
    """`Bearer vq_key_` is what the README recommends and the plugin sends.
    OAuth composes with it via `MultiAuth`; it does not replace it."""
    async with _serving() as app:
        async with _client(
            app, "/mcp", headers={"Authorization": "Bearer vq_key_header"}
        ) as client:
            await client.call_tool(_FREE_TOOL, {})

    assert _captured_key == ["Bearer vq_key_header"]


async def test_an_anonymous_caller_now_gets_a_401_with_the_challenge(
    _live_api: None, _oauth_on: None
) -> None:
    """What makes Cowork show a Connect card at all.

    Claude does not honour `WWW-Authenticate` on a 200, so a tool that merely
    returns "please sign in" produces no auth prompt: the model reads it as
    text. The 401 has to come from the protocol layer, and it has to carry
    `resource_metadata` pointing at this exact endpoint.

    This is also the end of "a green connection proves nothing" for `/mcp`:
    listing 25 tools to a caller with no credential at all was the pre-OAuth
    behaviour, and it is what made every earlier verification worthless.
    """
    async with _serving() as app:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="https://mcp.vaquill.ai"
        ) as raw:
            response = await raw.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            )

    assert response.status_code == 401
    challenge = response.headers.get("WWW-Authenticate", "")
    assert "Bearer" in challenge
    assert "resource_metadata=" in challenge
    assert "/mcp" in challenge


async def test_the_discovery_documents_meet_claudes_stated_conditions(
    _live_api: None, _oauth_on: None
) -> None:
    """Every one of these is a SILENT failure if it regresses.

    Claude selects CIMD only when the authorization server metadata advertises
    BOTH `client_id_metadata_document_supported` and `"none"` in
    `token_endpoint_auth_methods_supported`. Miss either and it falls back to
    Dynamic Client Registration with no error anywhere -- which for a directory
    listing means a new registered client on every fresh connection.

    The `resource` field must match the MCP URL exactly as the user types it,
    path included, and `authorization_servers` must lead with the real issuer
    because Claude uses the first entry and does not fall back to later ones.
    """
    async with _serving() as app:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="https://mcp.vaquill.ai"
        ) as raw:
            resource = (
                await raw.get("/.well-known/oauth-protected-resource/mcp")
            ).json()
            server = (await raw.get("/.well-known/oauth-authorization-server")).json()

    assert resource["resource"] == "https://mcp.vaquill.ai/mcp"
    assert len(resource["authorization_servers"]) == 1

    assert server["client_id_metadata_document_supported"] is True
    assert "none" in server["token_endpoint_auth_methods_supported"]
    # Required by the MCP authorization spec so clients can verify PKCE support
    # before starting a flow. Claude sends S256 on every authorization request.
    assert server["code_challenge_methods_supported"] == ["S256"]


async def test_india_is_left_alone_by_oauth(_live_api: None, _oauth_on: None) -> None:
    """A FastMCP app knows its own `path` and nothing about the Mount above it.

    Under `/in` that means it would advertise `resource:
    https://mcp.vaquill.ai/mcp` -- the US URL -- and serve its document at
    `/in/.well-known/...` where discovery never looks, while
    `/.well-known/oauth-protected-resource/in/mcp` 404s. Both measured. An
    India OAuth connect would fail discovery in a way that reads as "couldn't
    reach the MCP server", so India stays on the credential shapes that work.
    """
    async with _serving() as app:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="https://mcp.vaquill.ai"
        ) as raw:
            init = await raw.post(
                "/in/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            )
            # And no misleading half-document is published for it.
            stray = await raw.get("/.well-known/oauth-protected-resource/in/mcp")

    assert init.status_code == 200
    assert stray.status_code == 404


async def test_offline_access_is_advertised_so_refresh_is_possible(
    _live_api: None, _oauth_on: None
) -> None:
    """Without this the connection dies about an hour after it is made.

    Claude requests the scopes this server advertises and the proxy forwards
    them upstream. Advertising none means the upstream is never asked for a
    refresh token, so when its access token expires there is nothing to refresh
    with and the user has to reconnect by hand, with no error naming the cause.

    `openid` must stay OUT: it makes Supabase mint an ID token, which its own
    docs say FAILS on a symmetric HS256 project, and nothing here reads one.
    """
    async with _serving() as app:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="https://mcp.vaquill.ai"
        ) as raw:
            server = (await raw.get("/.well-known/oauth-authorization-server")).json()

    assert "offline_access" in server["scopes_supported"]
    assert "openid" not in server["scopes_supported"]


async def test_html_pages_are_skinned_and_json_is_untouched(
    _live_api: None, _oauth_on: None
) -> None:
    """The brand skin must reach FastMCP's pages and nothing else.

    FastMCP renders the consent screen itself and exposes only `icons`,
    `website_url` and a CSP override, so its colours and 64px logo are not
    configurable. The middleware restyles HTML in transit rather than forking a
    pinned dependency.

    The second half is the half that matters: this middleware sits in front of
    the MCP endpoint, so touching anything but `text/html` would corrupt
    JSON-RPC, the discovery documents, or a token response.
    """
    from fastmcp.utilities.ui import create_page

    from vaquill_mcp.oauth import _inject_brand_css

    # Against a REAL FastMCP page, not a hand-written string: the injection
    # depends on that template still having a `</style>` to land before.
    page = create_page("<p>hi</p>", title="Application Access Request")
    skinned = _inject_brand_css(page)
    assert "vaquill brand skin" in skinned
    assert "#6e3730" in skinned, "brand colour missing"
    assert "width: 192px" in skinned, "logo not enlarged"
    # It must land INSIDE the stylesheet and AFTER the base rules, which is what
    # makes it win without !important.
    assert skinned.index("width: 64px") < skinned.index("width: 192px")
    assert skinned.index("width: 192px") < skinned.index("</style>")

    # A page with no stylesheet is returned untouched rather than mangled, so an
    # upstream template change degrades to "unstyled" not "broken".
    assert _inject_brand_css("<h1>Error</h1>") == "<h1>Error</h1>"

    async with _serving() as app:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="https://mcp.vaquill.ai"
        ) as raw:
            discovery = await raw.get("/.well-known/oauth-authorization-server")
            resource = await raw.get("/.well-known/oauth-protected-resource/mcp")

    for doc in (discovery, resource):
        assert doc.status_code == 200
        assert "vaquill brand skin" not in doc.text
    assert discovery.json()["client_id_metadata_document_supported"] is True
    assert resource.json()["resource"] == "https://mcp.vaquill.ai/mcp"
