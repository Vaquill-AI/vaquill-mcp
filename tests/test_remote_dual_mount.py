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

from tests.surface_helpers import mock_spec_fetches, mounted_jurisdictions

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
    # One document per MOUNTED jurisdiction, derived from `_MOUNTS`.
    mock_spec_fetches(respx_mock, _BASE, _spec, mounted_jurisdictions())
    # The hosted server has no key at startup, so it uses the PUBLIC matrix.
    respx_mock.get(url__regex=rf"{_BASE}/api/v1(/in)?/api-credits/pricing$").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )


@pytest.fixture
def _live_india(_live_api: None, respx_mock) -> None:
    """The India spec too, for tests that build an India server DIRECTLY.

    Only when `_live_api` has not already registered it: respx asserts at
    TEARDOWN that every registered route was called, so the document is mocked
    exactly once whether or not India is in the mount table.
    """
    if "IN" not in mounted_jurisdictions():
        mock_spec_fetches(respx_mock, _BASE, _spec, ["IN"])



def test_every_jurisdiction_mounts_both_url_shapes(_live_api: None) -> None:
    """Both URL shapes, for every jurisdiction in the table, and no others.

    India was removed from this host on 2026-11-26, so the table is US alone
    and the expected mounts are `/s` and the root. This used to expect
    `{"/in/s", "/in", "/s", ""}`; the four-mount shape is what `in-mcp.vaquill.ai`
    now has on its own origin, where OAuth can actually work.

    The expectation is DERIVED from `_MOUNTS` rather than hard-coded, so adding
    a jurisdiction back cannot leave this test passing while asserting the old
    world. The membership check on `_MOUNTS` itself stays, because a derived
    expectation alone would also accept an EMPTY table.
    """
    from vaquill_mcp.remote_main import _MOUNTS, build_app

    app = build_app()
    # Starlette normalizes `Mount("/")` to the empty path, so the root
    # jurisdiction is "" rather than "/".
    mounts = {r.path for r in app.routes if isinstance(r, Mount)}
    expected = {f"{prefix}/s" for _, prefix in _MOUNTS} | {
        prefix or "" for _, prefix in _MOUNTS
    }
    assert mounts == expected, sorted(mounts)
    assert [j for j, _ in _MOUNTS] == ["US"], "India moved to in-mcp.vaquill.ai"


def test_broader_mounts_are_routed_after_narrower_ones(_live_api: None) -> None:
    """Asserting the ORDER, not merely the presence.

    `Mount` matches on prefix and the router hands the request to the FIRST
    match without ever falling through, so a broader prefix declared early does
    not shadow a narrower one loudly, it shadows it silently: `/in/s/vq_key_...`
    would reach the app that only knows `/mcp` and come back a bare 404, which
    reads as a dead customer URL rather than a routing bug.
    """
    from vaquill_mcp.remote_main import _MOUNTS, build_app

    app = build_app()
    paths = [r.path for r in app.routes if isinstance(r, (Route, Mount))]
    root = paths.index("")  # the US catch-all

    assert paths.index("/health") < root
    assert paths.index("/s") < root
    # India's `/in/s` < `/in` < root chain was asserted here until 2026-11-26.
    # The invariant it protected is general, so it is asserted generally now:
    # every path-key mount must precede its own jurisdiction's mcp mount, and
    # both must precede the root catch-all. With one jurisdiction at the root
    # that is the `/s` line above; with a second it is this loop, so adding one
    # back cannot reintroduce the silent shadowing without failing here.
    for _, prefix in _MOUNTS:
        if prefix:
            assert paths.index(f"{prefix}/s") < paths.index(prefix) < root


async def test_each_mount_serves_only_its_own_jurisdiction(_live_india: None) -> None:
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
    _live_india: None, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """Hosted and stdio must not diverge.

    They already did once: `remote.py` hand-declared 28 tools, shipped 9 of
    them, and five more kept calling routers that had been deleted. Deriving
    both from the same document removed the class; this keeps it removed.
    """
    from vaquill_mcp.remote import create_remote_server
    from vaquill_mcp.server import create_server

    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test")
    respx_mock.get(url__regex=rf"{_BASE}/api/v1(/in)?/api-credits/pricing/all$").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )
    for jurisdiction in ("US", "IN"):
        hosted = {t.name for t in await create_remote_server(jurisdiction).list_tools()}
        stdio = {t.name for t in await create_server(jurisdiction).list_tools()}
        assert hosted == stdio, jurisdiction


async def test_the_hosted_us_app_publishes_no_alert_tools(_live_india: None) -> None:
    """`/mcp` and `/s/{api_key}` serve the same app, built here, and it must not
    publish the law-change alert tools any more than stdio does. India is built
    too, to prove the exclusion leaves its catalogue untouched."""
    from vaquill_mcp.remote import create_remote_server

    us = {t.name for t in await create_remote_server("US").list_tools()}
    india = {t.name for t in await create_remote_server("IN").list_tools()}
    assert not [n for n in us if "watch" in n or "board" in n], sorted(us)
    # 35 since 2026-11-03: 29, plus the six US rulemaking tools
    # (get_rulemaking_docket, list_rulemaking_dockets, list_docket_documents,
    # get_rulemaking_rin, list_rin_agenda, get_rulemaking_coverage).
    # 29 on 2026-10-30 was 18, plus the seven US state session-law tools,
    # extract_statute_citations, get_session_law_versions and the two CFR
    # rulemaking tools (section and part).
    # 37 since 2026-11-07: 35, plus list_rulemaking_committee_records and
    # get_rulemaking_committee_record.
    # 52 since 2026-11-22: 37, plus the fifteen US case-law tools listed in
    # `test_us_case_law_tools.CASE_TOOLS`. Several case-law routes are deliberately
    # not tools (see `_ROUTE_MAPS`).
    assert len(us) == 52, sorted(us)
    assert len(india) == 22, sorted(india)


async def test_every_mounted_app_sorts_and_annotates(_live_india: None) -> None:
    """The two optimisations have to hold on the hosted path too, which is the
    one nearly every customer actually uses."""
    from vaquill_mcp.remote import create_remote_server

    for jurisdiction in ("US", "IN"):
        tools = await create_remote_server(jurisdiction).list_tools()
        names = [t.name for t in tools]
        assert names == sorted(names), jurisdiction
        assert all(t.annotations is not None for t in tools), jurisdiction


def test_each_jurisdiction_asks_for_its_own_prices(_live_api: None, respx_mock) -> None:
    """Each jurisdiction's pricing fetch must hit that jurisdiction's own route.

    US reads `/api/v1/api-credits/pricing`, India reads
    `/api/v1/in/api-credits/pricing`, and neither sends a `region`: the route is
    the jurisdiction. Until 2026-09-08 the API served every region's rows to every
    caller, and the India server then read the US route with `?region=IN`, so a
    customer saw India prices on a US tool (and the reverse) in Claude before we
    did. The backend now serves only one country's rows per route.

    The expected routes are DERIVED from `_MOUNTS`, not listed: India left this
    host on 2026-11-26 and a hardcoded pair would have had to be edited to the
    new world, which is the edit that quietly turns a cross-country assertion
    into a single-country one. Derived, it keeps its full force for whatever the
    table holds, and `assert asked` keeps an EMPTY table from passing vacuously.
    """
    from vaquill_mcp.remote_main import _MOUNTS, build_app

    build_app()

    asked = [
        (call.request.url.path, call.request.url.query)
        for call in respx_mock.calls
        if "api-credits/pricing" in call.request.url.path
    ]
    assert asked, "no pricing fetch was made at startup"
    expected = {f"/api/v1{prefix}/api-credits/pricing" for _, prefix in _MOUNTS}
    assert {path for path, _ in asked} == expected, (
        f"pricing fetched from {asked}; each jurisdiction must ask only its own "
        f"route (expected {sorted(expected)})"
    )
    assert all(query == b"" for _, query in asked), "a cross-country region was sent"


def test_openai_challenge_404s_until_a_token_is_configured(
    _live_api: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent token means 404, which is what the path did before it existed.

    OpenAI's plugin directory verifies control of the MCP host by fetching an
    exact token from this path. The token is issued per submission and rotates,
    so it comes from the environment: a redeploy has to be able to answer a NEW
    challenge without a code change, and a committed file could not.

    Serving something when nothing is configured would be worse than 404: it
    would look verified while failing OpenAI's exact-match check.
    """
    from starlette.testclient import TestClient

    from vaquill_mcp.remote_main import build_app

    monkeypatch.delenv("OPENAI_APPS_CHALLENGE_TOKEN", raising=False)
    with TestClient(build_app()) as client:
        assert client.get("/.well-known/openai-apps-challenge").status_code == 404


def test_openai_challenge_serves_the_exact_token(
    _live_api: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Byte-for-byte, with no JSON wrapper and no trailing newline.

    OpenAI compares the response body to the token it issued. Any framing at
    all fails the check, which is why this is PlainTextResponse rather than the
    JSONResponse used by /health next to it.
    """
    from starlette.testclient import TestClient

    from vaquill_mcp.remote_main import build_app

    monkeypatch.setenv("OPENAI_APPS_CHALLENGE_TOKEN", "openai-apps-verify-abc123")
    with TestClient(build_app()) as client:
        r = client.get("/.well-known/openai-apps-challenge")
        assert r.status_code == 200
        assert r.text == "openai-apps-verify-abc123"
        assert r.headers["content-type"].startswith("text/plain")


def test_the_challenge_is_not_shadowed_by_the_root_mount(
    _live_api: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The US app mounts at "/" and would otherwise swallow this path.

    Starlette hands a request to the FIRST match and never falls through, so a
    route declared AFTER the root Mount is unreachable. This is the same trap
    that made `/mcp` 307 when it was a Mount prefix; asserting it here means a
    future reordering fails loudly instead of 404ing during someone's review.
    """
    from starlette.testclient import TestClient

    from vaquill_mcp.remote_main import build_app

    monkeypatch.setenv("OPENAI_APPS_CHALLENGE_TOKEN", "tok-not-shadowed")
    with TestClient(build_app()) as client:
        assert client.get("/.well-known/openai-apps-challenge").text == "tok-not-shadowed"
