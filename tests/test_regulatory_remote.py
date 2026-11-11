"""The hosted entry point for the regulatory corpus (`regulatory_main`).

`test_regulatory_tools.py` pins what the five tools do. This file pins how they
are SERVED, and each pin is a failure that reads as something else in the field:

* the app must sit at the ROOT of its own origin. OAuth discovery (RFC 9728) is
  served from the origin's `/.well-known/*`, and a FastMCP app knows only its own
  path, so under a prefix it advertises the wrong `resource` and a connect fails
  in a way that reads as "couldn't reach the MCP server";
* `Mount("/")` matches everything and Starlette never falls through, so `/s` and
  `/health` have to be routed BEFORE it;
* the CALLER's key, not a configured one, is what reaches the API, because
  credits land on whoever that header names;
* a cost line comes from the pricing matrix and never from a hand-typed number,
  and a missing price is loud.

What the corpus does for a given tool call is not re-asserted here.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import logging
import pathlib
import re
import sys
from collections.abc import AsyncIterator

if sys.version_info >= (3, 11):
    import tomllib
else:  # `tomllib` is stdlib only from 3.11; `tomli` is the same parser, and is
    # already present on the 3.10 environment (measured: tomli 2.4.0). Without
    # this split, `import tomllib` raises at MODULE level on the py3.10 CI leg,
    # which aborts collection for the whole suite rather than failing one test.
    import tomli as tomllib

import httpx
import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.routing import Mount, Route

from vaquill_mcp.regulatory import (
    DESCRIPTIONS,
    TOOL_NAMES,
    TOOL_ROUTES,
    create_regulatory_server,
    fetch_regulatory_costs,
    regulatory_costs,
)

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_BASE = "https://api.vaquill.ai"
_ORIGIN = "https://reg-mcp.vaquill.ai"
_PRICES = [
    {"endpoint": "/in/reg/documents", "operation": "Reg Search", "credits": 1},
    {"endpoint": "/in/reg/documents/detail", "operation": "Reg Detail", "credits": 1},
    {"endpoint": "/in/reg/documents/file", "operation": "Reg File", "credits": 1},
    {"endpoint": "/in/reg/changes", "operation": "Reg Changes", "credits": 1},
    {"endpoint": "/in/reg/sources", "operation": "Reg Sources", "credits": 0},
]

_OAUTH_ENV = {
    "VAQUILL_OAUTH_UPSTREAM_JWKS_URI": "https://p.supabase.co/auth/v1/.well-known/jwks.json",
    "VAQUILL_OAUTH_UPSTREAM_ISSUER": "https://p.supabase.co/auth/v1",
    "VAQUILL_OAUTH_AUTHORIZE_URL": "https://p.supabase.co/auth/v1/oauth/authorize",
    "VAQUILL_OAUTH_TOKEN_URL": "https://p.supabase.co/auth/v1/oauth/token",
    "VAQUILL_OAUTH_CLIENT_ID": "client-abc",
    "VAQUILL_OAUTH_CLIENT_SECRET": "shh",
    "VAQUILL_PUBLIC_URL": _ORIGIN,
}


@pytest.fixture(autouse=True)
def _no_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test bearer-only, whatever the developer's shell holds."""
    for name in (*_OAUTH_ENV, "VAQUILL_INTERNAL_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)


@pytest.fixture
def _pricing(respx_mock):
    """The startup price fetch, with the five rows. Returns the route."""
    return respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing").mock(
        return_value=httpx.Response(200, json={"costs": _PRICES})
    )


@contextlib.asynccontextmanager
async def _serving() -> AsyncIterator[object]:
    """`build_app()` with every mounted lifespan entered, as uvicorn runs it."""
    from vaquill_mcp.regulatory_main import build_app

    app = build_app()
    async with app.router.lifespan_context(app):
        yield app


def _client(app: object, path: str, headers: dict[str, str] | None = None) -> Client:
    def factory(**kwargs):  # noqa: ANN003, ANN202
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)

    return Client(
        StreamableHttpTransport(
            f"http://reg-mcp.vaquill.ai{path}",
            headers=headers,
            httpx_client_factory=factory,
        )
    )


def _asgi(app: object) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://reg-mcp.vaquill.ai"
    )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_the_app_mounts_at_the_origin_root_and_nowhere_else(_pricing) -> None:
    """No prefix mount: a regulatory app under a path would advertise the wrong
    OAuth `resource`, which is the whole reason this has its own origin."""
    from vaquill_mcp.regulatory_main import build_app

    mounts = {r.path for r in build_app().routes if isinstance(r, Mount)}
    assert mounts == {"/s", ""}, sorted(mounts)


def test_the_root_catch_all_is_routed_last(_pricing) -> None:
    """Starlette hands a request to the FIRST matching Mount and never falls
    through, so anything declared after `Mount("/")` is unreachable and answers
    a bare 404 that reads as a dead customer URL."""
    from vaquill_mcp.regulatory_main import build_app

    paths = [r.path for r in build_app().routes if isinstance(r, (Route, Mount))]
    root = paths.index("")
    assert paths.index("/health") < root
    assert paths.index("/s") < root
    assert root == len(paths) - 1


def test_the_live_connectors_are_not_touched() -> None:
    """The regulatory server is separate from the process behind mcp.vaquill.ai."""
    from vaquill_mcp import remote_main

    assert [j for j, _ in remote_main._MOUNTS] == ["IN", "US"]


# ---------------------------------------------------------------------------
# What it serves, and whose key it uses
# ---------------------------------------------------------------------------


async def test_health_names_the_corpus(_pricing) -> None:
    async with _serving() as app, _asgi(app) as http:
        body = (await http.get("/health")).json()
    assert body["status"] == "ok" and body["corpus"] == "regulatory"


async def test_both_url_shapes_serve_exactly_the_five_tools(_pricing) -> None:
    async with _serving() as app:
        async with _client(app, "/mcp", {"Authorization": "Bearer vq_key_h"}) as c:
            header_names = sorted(t.name for t in await c.list_tools())
        async with _client(app, "/s/vq_key_p") as c:
            path_names = sorted(t.name for t in await c.list_tools())
    assert header_names == path_names == sorted(TOOL_NAMES)


@pytest.mark.parametrize(
    ("path", "headers", "expected"),
    [
        ("/mcp", {"Authorization": "Bearer vq_key_header"}, "Bearer vq_key_header"),
        ("/s/vq_key_inpath", None, "Bearer vq_key_inpath"),
    ],
)
async def test_the_callers_own_key_is_what_reaches_the_api(
    _pricing, respx_mock, path: str, headers: dict | None, expected: str
) -> None:
    """Credits land on whoever this header names, so it is the assertion that
    protects billing. Both URL shapes, and the request must actually be sent."""
    seen: list[str | None] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"sources": []})

    respx_mock.get(f"{_BASE}/api/v1/in/reg/sources").mock(side_effect=_record)
    async with _serving() as app, _client(app, path, headers) as c:
        await c.call_tool("get_regulatory_coverage", {})
    assert seen == [expected]


async def test_the_shared_client_is_closed_when_the_app_stops(
    _pricing, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[int] = []
    original = httpx2.AsyncClient.aclose

    async def _spy(self) -> None:  # noqa: ANN001
        closed.append(1)
        await original(self)

    monkeypatch.setattr(httpx2.AsyncClient, "aclose", _spy)
    async with _serving():
        assert closed == []
    assert closed, "the app's own client was never closed"


# ---------------------------------------------------------------------------
# OAuth at the root
# ---------------------------------------------------------------------------


async def test_oauth_discovery_names_the_url_the_user_types(
    _pricing, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason for the subdomain, measured: `resource` equals the URL typed,
    path included, and the document is served at the origin's well-known path."""
    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)
    async with _serving() as app, _asgi(app) as http:
        response = await http.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200, response.text
    assert response.json()["resource"] == f"{_ORIGIN}/mcp"


async def test_with_oauth_on_the_bearer_url_gates_and_the_path_url_does_not(
    _pricing, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two servers exist for this. `RequireAuthMiddleware` 401s a request
    with no Authorization header, which is exactly what `/s/{api_key}` sends."""
    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)
    async with _serving() as app, _asgi(app) as http:
        gated = await http.post("/mcp", json={})
        assert gated.status_code == 401
        assert "resource_metadata" in gated.headers.get("www-authenticate", "")
    async with _serving() as app, _client(app, "/s/vq_key_inpath") as c:
        assert {t.name for t in await c.list_tools()} == set(TOOL_NAMES)


# ---------------------------------------------------------------------------
# Cost lines
# ---------------------------------------------------------------------------


def test_every_tool_has_a_priced_route_and_the_five_are_the_whole_table() -> None:
    assert set(TOOL_ROUTES) == set(TOOL_NAMES)
    assert len(set(TOOL_ROUTES.values())) == 5


def test_each_route_is_priced_by_its_derived_endpoint() -> None:
    """The version prefix and the `{id}` come off, the same rule the derived
    tools use. A mismatch is silent by design: the line just never appears."""
    costs = regulatory_costs(_PRICES)
    assert costs == {
        "search_regulatory_documents": "Cost: 1 credit.",
        "get_regulatory_document": "Cost: 1 credit.",
        "get_regulatory_document_file": "Cost: 1 credit.",
        "list_regulatory_changes": "Cost: 1 credit.",
        "get_regulatory_coverage": "Free.",
    }


def test_a_stale_pricing_spelling_matches_nothing() -> None:
    stale = [{**row, "endpoint": row["endpoint"].replace("/in/reg", "/reg")} for row in _PRICES]
    assert regulatory_costs(stale) == {}


async def test_the_price_reaches_the_description_a_model_reads(_pricing) -> None:
    async with _serving() as app, _client(app, "/mcp", {"Authorization": "Bearer vq_key_h"}) as c:
        tools = {t.name: t.description for t in await c.list_tools()}
    assert tools["get_regulatory_coverage"] == f"{DESCRIPTIONS['get_regulatory_coverage']} Free."
    for name in TOOL_NAMES:
        if name != "get_regulatory_coverage":
            assert tools[name] == f"{DESCRIPTIONS[name]} Cost: 1 credit.", name


async def test_without_costs_the_description_is_the_hand_written_text() -> None:
    server = create_regulatory_server(httpx2.AsyncClient(base_url=_BASE))
    for tool in await server.list_tools():
        assert tool.description == DESCRIPTIONS[tool.name]


def test_the_price_fetch_asks_for_india(_pricing) -> None:
    """The route is not scoped by a mount and defaults to US, which would label
    these tools with United States prices."""
    fetch_regulatory_costs(_BASE)
    assert dict(_pricing.calls.last.request.url.params) == {"region": "IN"}


def test_an_unpriced_tool_is_named_in_a_warning(
    respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing").mock(
        return_value=httpx.Response(200, json={"costs": _PRICES[:3]})
    )
    with caplog.at_level(logging.WARNING, logger="vaquill_mcp.regulatory"):
        costs = fetch_regulatory_costs(_BASE)
    assert set(costs) == set(TOOL_NAMES[:3])
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "list_regulatory_changes" in warned and "get_regulatory_coverage" in warned
    assert "search_regulatory_documents" not in warned


# ---------------------------------------------------------------------------
# Logging: a key in `/s/{api_key}` must not reach an access line
# ---------------------------------------------------------------------------


def test_main_redacts_after_logging_is_configured_and_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ORDER, not presence. `install_key_redaction` filters the root handlers
    that `basicConfig` creates, so run before it there is nothing to filter and
    the key is printed by uvicorn's access log, silently."""
    import uvicorn

    from vaquill_mcp import regulatory_main

    calls: list[str] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **_: calls.append("config"))
    monkeypatch.setattr(regulatory_main, "install_key_redaction", lambda: calls.append("redact"))
    monkeypatch.setattr(regulatory_main, "build_app", lambda: object())
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append("serve"))
    regulatory_main.main()
    assert calls == ["config", "redact", "serve"]


def test_a_path_key_is_redacted_in_the_real_uvicorn_access_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real `main()`, then the real uvicorn `Config`, then a real access line."""
    import uvicorn

    from vaquill_mcp import regulatory_main

    key = "vq_key_REGCANARY0123456789abcdef"

    def fake_run(app: object, host: str, port: int) -> None:
        uvicorn.Config(app, host=host, port=port)  # runs configure_logging()
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d', "10.0.1.3:41456", "POST", f"/s/{key}", "1.1", 200
        )

    monkeypatch.setattr(regulatory_main, "build_app", lambda: object())
    monkeypatch.setattr(uvicorn, "run", fake_run)
    regulatory_main.main()
    out = capsys.readouterr().out
    assert key not in out
    assert '"POST /s/vq_key_[redacted] HTTP/1.1" 200' in out


def test_host_and_port_come_from_the_environment_with_the_documented_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uvicorn

    from vaquill_mcp import regulatory_main

    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(regulatory_main, "build_app", lambda: object())
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: seen.append((host, port)))
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    regulatory_main.main()
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9123")
    regulatory_main.main()
    assert seen == [("0.0.0.0", 8000), ("127.0.0.1", 9123)]


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------


def test_the_entry_point_is_a_console_script() -> None:
    scripts = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["vaquill-mcp-regulatory"] == "vaquill_mcp.regulatory_main:main"
    assert scripts["vaquill-mcp-remote"] == "vaquill_mcp.remote_main:main"


def test_the_dockerfile_defaults_to_the_live_server_and_can_build_the_other() -> None:
    """One Dockerfile for both apps. The default must stay exactly what the image
    ran before, so an unset build argument cannot change the live deployment."""
    text = (_ROOT / "Dockerfile").read_text()
    assert "ARG SERVER=vaquill-mcp-remote\n" in text
    assert "ENV VAQUILL_SERVER=${SERVER}" in text
    assert 'exec \\"$VAQUILL_SERVER\\""]' in text
    assert "vaquill-mcp-regulatory" in text


def _hosted_console_scripts() -> set[str]:
    """Every `vaquill-mcp-*` console script whose module defines `build_app`.

    `build_app` is what makes a server HOSTED: it returns the Starlette app that
    uvicorn serves on its own origin. The stdio entry (`vaquill-mcp`) has none,
    so the distinction is read from the code and not from a name blocklist. The
    module is parsed, not imported, so collecting this test starts no server and
    reads no environment.
    """
    scripts = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    hosted: set[str] = set()
    for name, target in scripts.items():
        if not name.startswith("vaquill-mcp-"):
            continue
        module = target.split(":", 1)[0]
        spec = importlib.util.find_spec(module)
        assert spec is not None and spec.origin is not None, f"{name}: {module} not found"
        tree = ast.parse(pathlib.Path(spec.origin).read_text())
        if any(isinstance(n, ast.FunctionDef) and n.name == "build_app" for n in tree.body):
            hosted.add(name)
    return hosted


def test_the_dockerfile_names_every_hosted_server_it_can_build() -> None:
    """The comment above `ARG SERVER` is the only place an operator learns which
    value builds which origin, and it is a hand-kept list beside the real one
    (`[project.scripts]`). It went stale once already: `vaquill-mcp-tribunals`
    existed and was not listed, so nobody setting up that Dokploy application
    could tell what to pass. The expected set is parsed, never retyped."""
    hosted = _hosted_console_scripts()
    # Zero must be loud: an empty set would make the loop below pass vacuously.
    assert hosted, "no hosted console script found; the derivation is broken"
    # The stdio entry is deliberately outside the list.
    assert "vaquill-mcp" not in hosted

    text = (_ROOT / "Dockerfile").read_text()
    start = text.index("# WHICH SERVER THIS IMAGE RUNS")
    block = text[start : text.index("ARG SERVER=", start)]
    # `(?![\w-])` so `vaquill-mcp-remote` cannot be satisfied by a longer name.
    missing = sorted(n for n in hosted if not re.search(rf"{re.escape(n)}(?![\w-])", block))
    assert not missing, f"Dockerfile server list does not name: {missing}"
