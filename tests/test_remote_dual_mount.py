"""The hosted entry point, which serves BOTH jurisdictions from one process.

`remote_main.build_app()` is what `mcp.vaquill.ai` actually runs: each
jurisdiction at two URL shapes (`/mcp` and `/s/{api_key}`, India under `/in`),
mounted into one Starlette application. Nothing covered it until now, which is
uncomfortable for the piece of code with the most ways to go quietly wrong:

* `Mount("/")` matches everything and `Mount` matches on PREFIX without falling
  through, so route ORDER decides whether `/health`, `/in/...` and the path-key
  URLs are reachable at all or are swallowed by a broader mount declared first;
* Starlette does not run a mounted app's lifespan for it, so a missing
  `AsyncExitStack` leaves every httpx client uncreated and every tool call
  failing on a closed session;
* each app fixes its catalogue at build time from ONE document, and the whole
  jurisdiction isolation rests on that.

The isolation assertion is the load-bearing one. It is not a style check: a
leak here puts the entire US statutes surface into an Indian integrator's
context window, and vice versa, and the customer pays per tool for it.

What each URL shape actually SERVES is asserted in `test_remote_mcp_path.py`,
by driving requests through the composed app. This file asserts the routing
table; that one asserts the answers.
"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from starlette.routing import Mount, Route

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_BASE = "https://api.vaquill.ai"


def _spec(jurisdiction: str) -> dict:
    return json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())


@pytest.fixture
def _live_api(monkeypatch: pytest.MonkeyPatch, respx_mock) -> None:
    """Mock the startup fetches both apps make.

    `respx_mock` is the conftest override that targets httpcore2; registering on
    the module-level `respx` router instead would match nothing and let the
    tests hit the real API.
    """
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    respx_mock.get(f"{_BASE}/external/openapi.json").mock(
        return_value=httpx.Response(200, json=_spec("US"))
    )
    respx_mock.get(f"{_BASE}/in/openapi.json").mock(
        return_value=httpx.Response(200, json=_spec("IN"))
    )
    # The hosted server has no key at startup, so it uses the PUBLIC matrix.
    respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )


def test_every_jurisdiction_mounts_both_url_shapes(_live_api: None) -> None:
    from vaquill_mcp.remote_main import _MOUNTS, build_app

    app = build_app()
    # Starlette normalizes `Mount("/")` to the empty path, so the root
    # jurisdiction is "" rather than "/".
    mounts = {r.path for r in app.routes if isinstance(r, Mount)}
    assert mounts == {"/in/s", "/in", "/s", ""}, sorted(mounts)
    assert [j for j, _ in _MOUNTS] == ["IN", "US"]


def test_broader_mounts_are_routed_after_narrower_ones(_live_api: None) -> None:
    """Asserting the ORDER, not merely the presence.

    `Mount` matches on prefix and the router hands the request to the FIRST
    match without ever falling through, so a broader prefix declared early does
    not shadow a narrower one loudly, it shadows it silently: `/in/s/vq_key_...`
    would reach the app that only knows `/mcp` and come back a bare 404, which
    reads as a dead customer URL rather than a routing bug.
    """
    from vaquill_mcp.remote_main import build_app

    app = build_app()
    paths = [r.path for r in app.routes if isinstance(r, (Route, Mount))]
    root = paths.index("")  # the US catch-all

    assert paths.index("/health") < root
    assert paths.index("/in/s") < paths.index("/in") < root
    assert paths.index("/s") < root


async def test_each_mount_serves_only_its_own_jurisdiction(_live_api: None) -> None:
    """The isolation the whole two-app design exists to guarantee."""
    from vaquill_mcp.remote import create_remote_server

    us = {t.name for t in await create_remote_server("US").list_tools()}
    india = {t.name for t in await create_remote_server("IN").list_tools()}

    assert "search_us_statutes" in us and "search_acts" not in us
    assert "search_acts" in india and "search_us_statutes" not in india
    # The generic pair is published by BOTH, and is the one deliberate overlap:
    # each is bound to its own app's client and document.
    assert {"search", "fetch"} <= us
    assert {"search", "fetch"} <= india
    assert (us & india) == {"search", "fetch"}


async def test_the_hosted_catalogue_matches_the_stdio_one(
    _live_api: None, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """Hosted and stdio must not diverge.

    They already did once: `remote.py` hand-declared 28 tools, shipped 9 of
    them, and five more kept calling routers that had been deleted. Deriving
    both from the same document removed the class; this keeps it removed.
    """
    from vaquill_mcp.remote import create_remote_server
    from vaquill_mcp.server import create_server

    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test")
    respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing/all").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )
    for jurisdiction in ("US", "IN"):
        hosted = {t.name for t in await create_remote_server(jurisdiction).list_tools()}
        stdio = {t.name for t in await create_server(jurisdiction).list_tools()}
        assert hosted == stdio, jurisdiction


async def test_every_mounted_app_sorts_and_annotates(_live_api: None) -> None:
    """The two optimisations have to hold on the hosted path too, which is the
    one nearly every customer actually uses."""
    from vaquill_mcp.remote import create_remote_server

    for jurisdiction in ("US", "IN"):
        tools = await create_remote_server(jurisdiction).list_tools()
        names = [t.name for t in tools]
        assert names == sorted(names), jurisdiction
        assert all(t.annotations is not None for t in tools), jurisdiction
