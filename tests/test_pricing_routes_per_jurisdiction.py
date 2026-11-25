"""Each jurisdiction reads its prices ONLY from its own pricing routes.

The owner ruled on 2026-11-25 that the countries are properly separated: a US
server never sees India prices and an India server never sees US prices.

* US: `GET /api/v1/api-credits/pricing` (public) and `/pricing/all` (Bearer key).
* India: `GET /api/v1/in/api-credits/pricing` (public) and `/pricing/all` (Bearer
  key). The India `/pricing/all` returns 404 until the backend serves it, so India
  falls back to its own public route, which is the same set while nothing is hidden.

No request carries a cross-country `region`. Only the routes a case expects are
mocked, and the shared `respx_mock` fixture raises on a request to any other URL,
so a fetch that wandered onto the other country's route fails the test.

No real network call is made.
"""

from __future__ import annotations

import json
import pathlib

import httpx
import httpx2
import pytest

from tests.test_resources_and_prompts import _BASE, _spec
from vaquill_mcp.resources import _REFERENCE
from vaquill_mcp.server import _fetch_full_costs, _fetch_public_costs, create_server

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_MATRIX = json.loads((_FIXTURES / "pricing_matrix.json").read_text())

_US = "/api/v1/api-credits/pricing"
_IN = "/api/v1/in/api-credits/pricing"
_US_ROWS = [{"endpoint": "/us/statutes/search", "operation": "US Search", "credits": 2}]
_IN_ROWS = [{"endpoint": "/in/acts/search", "operation": "India Search", "credits": 3}]


def _ok(rows: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"costs": rows})


def _paths(respx_mock) -> list[str]:
    """Every pricing request made, as path plus query."""
    return [c.request.url.raw_path.decode() for c in respx_mock.calls]


# ---------------------------------------------------------------------------
# United States
# ---------------------------------------------------------------------------


def test_us_public_fetch_hits_only_the_us_route(respx_mock) -> None:
    respx_mock.get(f"{_BASE}{_US}").mock(return_value=_ok(_US_ROWS))

    assert _fetch_public_costs(_BASE, "US") == _US_ROWS
    assert _paths(respx_mock) == [_US], "no region and no India route"


def test_us_full_fetch_hits_only_the_us_full_route(respx_mock) -> None:
    route = respx_mock.get(f"{_BASE}{_US}/all").mock(return_value=_ok(_US_ROWS))

    assert _fetch_full_costs(_BASE, "vq_key_x", "US") == _US_ROWS
    assert _paths(respx_mock) == [f"{_US}/all"]
    assert route.calls.last.request.headers["Authorization"] == "Bearer vq_key_x"


def test_us_full_failure_returns_nothing_and_never_reads_india(respx_mock) -> None:
    respx_mock.get(f"{_BASE}{_US}/all").mock(return_value=httpx.Response(500))

    assert _fetch_full_costs(_BASE, "vq_key_x", "US") == []
    assert _paths(respx_mock) == [f"{_US}/all"]


# ---------------------------------------------------------------------------
# India
# ---------------------------------------------------------------------------


def test_india_public_fetch_hits_only_the_india_route(respx_mock) -> None:
    respx_mock.get(f"{_BASE}{_IN}").mock(return_value=_ok(_IN_ROWS))

    assert _fetch_public_costs(_BASE, "IN") == _IN_ROWS
    assert _paths(respx_mock) == [_IN], "no region and no US route"


def test_india_full_fetch_hits_only_the_india_full_route(respx_mock) -> None:
    route = respx_mock.get(f"{_BASE}{_IN}/all").mock(return_value=_ok(_IN_ROWS))

    assert _fetch_full_costs(_BASE, "vq_key_x", "IN") == _IN_ROWS
    assert _paths(respx_mock) == [f"{_IN}/all"], "the public route is not needed"
    assert route.calls.last.request.headers["Authorization"] == "Bearer vq_key_x"


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(404),
        httpx.Response(500),
        httpx.Response(200, content=b"not json"),
        httpx2.ConnectError("boom"),
    ],
    ids=["404", "500", "bad-json", "network"],
)
def test_india_full_falls_back_to_india_public_and_never_touches_a_us_url(
    respx_mock, failure
) -> None:
    full = respx_mock.get(f"{_BASE}{_IN}/all")
    if isinstance(failure, Exception):
        full.mock(side_effect=failure)
    else:
        full.mock(return_value=failure)
    respx_mock.get(f"{_BASE}{_IN}").mock(return_value=_ok(_IN_ROWS))

    assert _fetch_full_costs(_BASE, "vq_key_x", "IN") == _IN_ROWS

    paths = _paths(respx_mock)
    assert paths == [f"{_IN}/all", _IN]
    assert all(p.startswith("/api/v1/in/") for p in paths)
    public_request = respx_mock.calls.last.request
    assert "authorization" not in public_request.headers, "the public route takes no key"


def test_india_full_and_public_both_failing_gives_no_prices_not_us_prices(respx_mock) -> None:
    respx_mock.get(f"{_BASE}{_IN}/all").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{_BASE}{_IN}").mock(return_value=httpx.Response(503))

    assert _fetch_full_costs(_BASE, "vq_key_x", "IN") == []
    assert all(p.startswith("/api/v1/in/") for p in _paths(respx_mock))


@pytest.mark.parametrize("fetch", ["public", "full"])
def test_an_unknown_jurisdiction_is_refused_rather_than_priced_as_us(fetch: str) -> None:
    with pytest.raises(ValueError, match="jurisdiction"):
        if fetch == "public":
            _fetch_public_costs(_BASE, "ZZ")
        else:
            _fetch_full_costs(_BASE, "vq_key_x", "ZZ")


# ---------------------------------------------------------------------------
# The reference resource
# ---------------------------------------------------------------------------


def _pricing_entry(jurisdiction: str) -> tuple[str, str, str, str]:
    (entry,) = [e for e in _REFERENCE[jurisdiction] if e[0] == "vaquill://pricing"]
    return entry


def test_the_india_pricing_resource_reads_the_india_route() -> None:
    _uri, path, _name, description = _pricing_entry("IN")

    assert path == _IN
    assert path.startswith("/api/v1/in/")
    assert "India surface's prices" in description


def test_the_us_pricing_resource_reads_the_us_route() -> None:
    _uri, path, _name, description = _pricing_entry("US")

    assert path == _US
    assert "/in/" not in path
    assert "India" not in description


# ---------------------------------------------------------------------------
# The whole server, end to end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("jurisdiction", "spec_path", "prefix"),
    [("US", "/external/openapi.json", "/api/v1"), ("IN", "/in/openapi.json", "/api/v1/in")],
)
async def test_a_stdio_server_prices_its_tools_from_its_own_jurisdictions_matrix(
    jurisdiction: str,
    spec_path: str,
    prefix: str,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock,
) -> None:
    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test")
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    respx_mock.get(f"{_BASE}{spec_path}").mock(
        return_value=httpx.Response(200, json=_spec(jurisdiction))
    )
    respx_mock.get(f"{_BASE}{prefix}/api-credits/pricing/all").mock(
        return_value=_ok(_MATRIX[jurisdiction]["costs"])
    )

    server = create_server(jurisdiction)
    tools = {t.name: t.description for t in await server.list_tools()}

    search = "search_us_statutes" if jurisdiction == "US" else "search_acts"
    assert "Cost: 2 credits" in tools[search]
    assert all(p.startswith(prefix + "/") or p == spec_path for p in _paths(respx_mock))


async def test_india_stdio_keeps_its_cost_lines_while_the_full_route_is_not_deployed(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """This release ships BEFORE the backend: `/in/.../pricing/all` is a 404."""
    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test")
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    respx_mock.get(f"{_BASE}/in/openapi.json").mock(
        return_value=httpx.Response(200, json=_spec("IN"))
    )
    respx_mock.get(f"{_BASE}{_IN}/all").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{_BASE}{_IN}").mock(return_value=_ok(_MATRIX["IN"]["costs"]))

    server = create_server("IN")
    tools = {t.name: t.description for t in await server.list_tools()}

    assert "Cost: 2 credits" in tools["search_acts"]
    pricing = [p for p in _paths(respx_mock) if "api-credits" in p]
    assert pricing == [f"{_IN}/all", _IN], "India only, full first, then its own public route"


def test_httpx2_errors_are_what_the_fallback_catches() -> None:
    """Guard the assumption the fallback rests on: a 404 raises `httpx2.HTTPError`."""
    assert issubclass(httpx2.HTTPStatusError, httpx2.HTTPError)
    assert issubclass(httpx2.ConnectError, httpx2.HTTPError)
