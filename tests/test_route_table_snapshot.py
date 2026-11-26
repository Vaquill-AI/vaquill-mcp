"""Pin the route table of every hosted app, plus the two probes served beside it.

The order of a Starlette route list is behaviour, not layout: `Mount("/")`
matches everything and hands a request to the first matching Mount without
falling through, so a route moved below it stops existing. This pins, for
`remote_main`, `regulatory_main` and `tribunals_main`:

* each top-level route's index, path, kind (`Route` or `Mount`) and methods;
* the routes INSIDE each Mount, with OAuth off and with OAuth on, because OAuth
  is attached to the root mount only;
* the `/health` body (version withheld, asserted equal to the package version);
* `/.well-known/openai-apps-challenge` with the token unset and with it set.

Only `remote_main` declares the challenge route. The two single-corpus apps do
not, so the golden records what their root mount answers instead.

Regenerate deliberately, after reading the diff:

    UPDATE_ROUTE_TABLES=1 .venv/bin/python -m pytest tests/test_route_table_snapshot.py
"""

from __future__ import annotations

import contextlib
import importlib
import json

import httpx
import httpx2
import pytest

from tests.surface_helpers import FIXTURES, check_golden
from tests.test_remote_mcp_path import _BASE, _live_api, _live_us, _oauth_on  # noqa: F401
from vaquill_mcp import __version__

_GOLDEN = FIXTURES / "route_tables.json"
_ENV = "UPDATE_ROUTE_TABLES"
_CHALLENGE = "/.well-known/openai-apps-challenge"
_TOKEN = "challenge-token-for-tests"  # fake, set only for the duration of one test

_APPS = [
    pytest.param("vaquill_mcp.remote_main", "https://mcp.vaquill.ai", id="remote_main"),
    pytest.param("vaquill_mcp.regulatory_main", "https://reg-mcp.vaquill.ai", id="regulatory_main"),
    pytest.param(
        "vaquill_mcp.tribunals_main", "https://tribunals-mcp.vaquill.ai", id="tribunals_main"
    ),
]
_OAUTH_GROUP = (
    "VAQUILL_OAUTH_UPSTREAM_JWKS_URI",
    "VAQUILL_OAUTH_UPSTREAM_ISSUER",
    "VAQUILL_OAUTH_AUTHORIZE_URL",
    "VAQUILL_OAUTH_TOKEN_URL",
    "VAQUILL_OAUTH_CLIENT_ID",
    "VAQUILL_OAUTH_CLIENT_SECRET",
    "VAQUILL_PUBLIC_URL",
    "VAQUILL_INTERNAL_SECRET",
    "OPENAI_APPS_CHALLENGE_TOKEN",
)


def _mock_startup(request: pytest.FixtureRequest, module: str, respx_mock) -> None:
    if module == "vaquill_mcp.remote_main":
        request.getfixturevalue("_live_api")
    else:
        respx_mock.get(url__regex=rf"{_BASE}/api/v1(/in)?/api-credits/pricing$").mock(
            return_value=httpx.Response(200, json={"costs": []})
        )


def _table(routes: list) -> list[dict]:
    rows = []
    for index, route in enumerate(routes):
        methods = getattr(route, "methods", None)
        rows.append(
            {
                "index": index,
                "path": route.path,
                "kind": type(route).__name__,
                "methods": sorted(methods) if methods else None,
            }
        )
    return rows


def _mounted(app: object) -> dict[str, list[dict]]:
    """The routes inside each Mount, keyed by `index:path` so order survives."""
    return {
        f"{index}:{route.path or '/'}": _table(route.app.routes)
        for index, route in enumerate(app.routes)  # type: ignore[attr-defined]
        if type(route).__name__ == "Mount"
    }


@contextlib.asynccontextmanager
async def _serving(module: str):
    app = importlib.import_module(module).build_app()
    async with app.router.lifespan_context(app):
        yield app


async def _probe(app: object, origin: str, path: str) -> dict:
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=origin) as raw:
        response = await raw.get(path)
    out: dict = {"status": response.status_code, "contentType": response.headers.get("content-type")}
    try:
        out["body"] = response.json()
    except ValueError:
        out["text"] = response.text
    return out


@pytest.mark.parametrize(("module", "origin"), _APPS)
async def test_the_route_table_matches_the_snapshot(
    module: str,
    origin: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock,
) -> None:
    for name in _OAUTH_GROUP:
        monkeypatch.delenv(name, raising=False)
    _mock_startup(request, module, respx_mock)

    async with _serving(module) as app:
        top = _table(app.routes)
        inside = _mounted(app)
        health = await _probe(app, origin, "/health")
        unset = await _probe(app, origin, _CHALLENGE)
        monkeypatch.setenv("OPENAI_APPS_CHALLENGE_TOKEN", _TOKEN)
        token_set = await _probe(app, origin, _CHALLENGE)
        monkeypatch.delenv("OPENAI_APPS_CHALLENGE_TOKEN")

    assert health["body"].pop("version") == __version__
    check_golden(
        {
            "routes": top,
            "mountedRoutesOAuthOff": inside,
            "health": health,
            "challenge": {"tokenUnset": unset, "tokenSet": token_set},
        },
        _GOLDEN,
        _ENV,
        key=module,
    )


@pytest.mark.parametrize(("module", "origin"), _APPS)
async def test_the_mounted_routes_with_oauth_on_match_the_snapshot(
    module: str,
    origin: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock,
    _oauth_on: None,  # noqa: F811 - fixture imported above
) -> None:
    """OAuth adds its well-known and consent routes to the ROOT mount only."""
    _mock_startup(request, module, respx_mock)
    monkeypatch.setenv("VAQUILL_PUBLIC_URL", origin)

    async with _serving(module) as app:
        inside = _mounted(app)

    check_golden(inside, _GOLDEN, _ENV, key=f"{module}#oauth_on")


def test_the_snapshot_is_not_empty_and_keeps_the_root_mount_last() -> None:
    golden = json.loads(_GOLDEN.read_text())
    for param in _APPS:
        module = param.values[0]
        routes = golden[module]["routes"]
        assert routes[0]["path"] == "/health"
        # The root catch-all swallows everything routed after it.
        assert routes[-1]["kind"] == "Mount" and routes[-1]["path"] == ""
        assert golden[f"{module}#oauth_on"]
    assert golden["vaquill_mcp.remote_main"]["challenge"]["tokenSet"]["text"] == _TOKEN
