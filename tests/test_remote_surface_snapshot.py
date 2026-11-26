"""Pin the hosted server against the stdio one, and pin what a price does to a tool.

Two gaps, one file, because both are about the same fact: the hosted server
(`create_remote_server`) is built by a SECOND constructor that repeats the
provider, middleware, alias, resource and prompt wiring of `create_server`, and
the two have already diverged once. The existing check
(`test_the_hosted_catalogue_matches_the_stdio_one`) compares tool NAMES only.

What this file adds:

* the hosted server's model-facing dict (tool titles, descriptions, schema
  strings, prompts, resources, instructions, guide) must equal the stdio
  server's, built from the same documents, both unpriced and priced;
* the fields only the hosted server has: server name, `website_url`, icon
  sources, and the `/health` body;
* the credit-cost line. `test_model_facing_text_snapshot.py` mocks the price
  matrix as `{"costs": []}`, so no golden contains a `Cost:` sentence and a
  change to how a price is joined onto a description passes unnoticed. Here a
  fixed matrix (`fixtures/pricing_matrix.json`: single, one-credit, fractional,
  tiered, free, and a route two tools share) is served to both constructors, and
  the resulting descriptions are pinned.

The matrix is served the way the live API serves it: the hosted server asks its
own jurisdiction's public route (US `/api/v1/api-credits/pricing`, India
`/api/v1/in/api-credits/pricing`), the stdio server asks that jurisdiction's
authenticated `/pricing/all`. The path (and query) the hosted server asked for is
recorded, because reading the other country's route prices a tool with the wrong
country's rates. Only the jurisdiction's own routes are mocked, so a request to
the other country's route fails the build.

Everything comes from the committed OpenAPI fixtures and the fixed matrix; the
network is never touched. A failure means the hosted surface or a priced
description changed and a person should read the diff. Regenerate deliberately:

    UPDATE_REMOTE_SURFACE=1 .venv/bin/python -m pytest tests/test_remote_surface_snapshot.py
"""

from __future__ import annotations

import difflib
import json
import os
import pathlib

import httpx
import httpx2
import pytest

from tests.test_model_facing_text_snapshot import _model_facing
from tests.test_resources_and_prompts import _BASE, _spec
from vaquill_mcp import __version__
from vaquill_mcp.remote import create_remote_server
from vaquill_mcp.server import create_server

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_GOLDEN = _FIXTURES / "remote_surface.json"
_MATRIX = _FIXTURES / "pricing_matrix.json"
_EMPTY = {"US": {"costs": []}, "IN": {"costs": []}}
_VERSION = "<vaquill_mcp.__version__>"


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _unified(label: str, golden: object, current: object) -> str:
    return "\n".join(
        difflib.unified_diff(
            _dump(golden).splitlines(),
            _dump(current).splitlines(),
            fromfile=f"golden/{label}",
            tofile=f"current/{label}",
            lineterm="",
            n=2,
        )
    )


def _install_api(
    monkeypatch: pytest.MonkeyPatch,
    respx_mock,
    matrix: dict,
    key_region: str,
    *,
    stdio: bool,
) -> tuple[list[str], dict]:
    """Serve one jurisdiction's document and price matrix; return the paths asked for.

    Each jurisdiction's public and `/pricing/all` routes answer with that
    jurisdiction's rows only. The other country's pricing routes are NOT
    registered, so a build that reads them raises. The matrix is read through the
    returned dict, so a test can swap it between two builds.

    Only the routes the build will call are registered, because the shared
    `respx_mock` fixture fails a test that leaves a route uncalled: `key_region`
    is the one document fetched, and `/pricing/all` exists only for the stdio
    constructor.
    """
    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test")
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    monkeypatch.delenv("VAQUILL_PUBLIC_URL", raising=False)  # the icon origin reads it
    asked: list[str] = []
    state = {"matrix": matrix}
    prefix = {"US": "/api/v1", "IN": "/api/v1/in"}[key_region]

    def _public(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.raw_path.decode())
        return httpx.Response(200, json=state["matrix"][key_region])

    def _full(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=state["matrix"][key_region])

    path = {"US": "/external/openapi.json", "IN": "/in/openapi.json"}[key_region]
    respx_mock.get(f"{_BASE}{path}").mock(return_value=httpx.Response(200, json=_spec(key_region)))
    respx_mock.get(f"{_BASE}{prefix}/api-credits/pricing").mock(side_effect=_public)
    if stdio:
        respx_mock.get(f"{_BASE}{prefix}/api-credits/pricing/all").mock(side_effect=_full)
    return asked, state


def _changed(a: dict, b: dict) -> list[str]:
    """Which model-facing sections differ, by name, for a readable failure."""
    out = [k for k in ("instructions", "guide") if a[k] != b[k]]
    for section in ("tools", "prompts", "resources"):
        for name in sorted(set(a[section]) | set(b[section])):
            if a[section].get(name) != b[section].get(name):
                out.append(f"{section[:-1]} {name}")
    return out


@pytest.mark.parametrize("priced", [False, True], ids=["unpriced", "priced"])
@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_the_hosted_server_reads_the_same_as_the_stdio_one(
    jurisdiction: str, priced: bool, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    matrix = json.loads(_MATRIX.read_text()) if priced else _EMPTY
    _install_api(monkeypatch, respx_mock, matrix, jurisdiction, stdio=True)

    hosted = await _model_facing(create_remote_server(jurisdiction), jurisdiction)
    stdio = await _model_facing(create_server(jurisdiction), jurisdiction)

    assert hosted == stdio, (
        f"{jurisdiction} ({'priced' if priced else 'unpriced'}): the hosted server no "
        f"longer reads the same as the stdio server. Differs in: {_changed(hosted, stdio)}"
    )


async def _remote_only(server) -> dict:
    transport = httpx2.ASGITransport(app=server.http_app())
    async with httpx2.AsyncClient(transport=transport, base_url="http://mcp.test") as http:
        response = await http.get("/health")
    health = response.json()
    assert health.get("version") == __version__, "health must report the package version"
    health["version"] = _VERSION  # a release must not break a surface golden
    return {
        "serverName": server.name,
        "websiteUrl": server.website_url,
        "icons": [
            {"src": i.src, "mimeType": i.mime_type, "sizes": list(i.sizes or [])}
            for i in (server.icons or [])
        ],
        "healthStatus": response.status_code,
        "healthBody": health,
    }


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_the_remote_only_fields_and_priced_descriptions_match_the_snapshot(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    # Unpriced build: the baseline each priced description is measured against, and
    # the server whose remote-only fields are recorded.
    asked, state = _install_api(monkeypatch, respx_mock, _EMPTY, jurisdiction, stdio=False)
    plain_server = create_remote_server(jurisdiction)
    plain = {t.name: t.description for t in await plain_server.list_tools()}
    remote_only = await _remote_only(plain_server)

    # Priced build: same documents, the fixed matrix.
    state["matrix"] = json.loads(_MATRIX.read_text())
    priced_server = create_remote_server(jurisdiction)
    priced: dict[str, str] = {}
    without: list[str] = []
    for tool in sorted(await priced_server.list_tools(), key=lambda t: t.name):
        if tool.description == plain[tool.name]:
            without.append(tool.name)
        else:
            assert tool.description.startswith(plain[tool.name] + " "), (
                f"{jurisdiction}/{tool.name}: a cost line must be APPENDED to the description"
            )
            priced[tool.name] = tool.description

    current = {
        **remote_only,
        "pricedDescriptions": priced,
        "toolsWithoutACostLine": without,
        # Both builds asked; every ask must be this jurisdiction's own route, no query.
        "pricingPathsRequested": sorted(set(asked)),
    }

    if os.environ.get("UPDATE_REMOTE_SURFACE"):
        golden = json.loads(_GOLDEN.read_text()) if _GOLDEN.exists() else {}
        golden[jurisdiction] = current
        _GOLDEN.write_text(_dump(golden) + "\n")
        pytest.skip(f"wrote {jurisdiction} to {_GOLDEN.name}")

    golden = json.loads(_GOLDEN.read_text())[jurisdiction]
    changed = [k for k in sorted(set(current) | set(golden)) if current.get(k) != golden.get(k)]
    assert not changed, (
        f"{jurisdiction}: the hosted surface or a priced description changed: {changed}. "
        "If intended, regenerate with UPDATE_REMOTE_SURFACE=1.\n"
        + "\n".join(_unified(k, golden.get(k), current.get(k)) for k in changed)
    )


def test_the_snapshot_and_the_matrix_are_not_empty() -> None:
    """A truncated golden or matrix would make the tests above vacuous."""
    golden = json.loads(_GOLDEN.read_text())
    matrix = json.loads(_MATRIX.read_text())
    assert set(golden) == {"US", "IN"} == set(matrix)
    for jurisdiction in ("US", "IN"):
        surface = golden[jurisdiction]
        assert surface["pricedDescriptions"], f"{jurisdiction}: no priced description pinned"
        assert any("Cost:" in d for d in surface["pricedDescriptions"].values())
        assert any(d.endswith("Free.") for d in surface["pricedDescriptions"].values())
        assert surface["icons"] and surface["websiteUrl"]
        assert len(matrix[jurisdiction]["costs"]) >= 3
    # A tiered row: one endpoint priced more than once.
    endpoints = [row["endpoint"] for row in matrix["US"]["costs"]]
    assert len(endpoints) != len(set(endpoints)), "the US matrix lost its tiered row"
