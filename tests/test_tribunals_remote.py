"""The hosted entry point for the tribunals corpus (`tribunals_main`).

`test_tribunals_tools.py` pins what the six tools do. This file pins how they
are SERVED, and each pin is a failure that reads as something else in the field:

* the app must sit at the ROOT of its own origin. OAuth discovery (RFC 9728) is
  served from the origin's `/.well-known/*`, and a FastMCP app knows only its own
  path, so under a prefix it advertises the wrong `resource` and a connect fails
  in a way that reads as "couldn't reach the MCP server";
* `Mount("/")` matches everything and Starlette never falls through, so `/s` and
  `/health` have to be routed BEFORE it;
* the catalogue served here must be the SIX tribunal tools and must share nothing
  with the regulatory server. Those two corpora are confusable in the direction
  that produces a WRONG answer rather than a missing one: one holds what a
  regulator issued, the other holds matters brought before tribunals. A mixed
  catalogue lets a model answer a question about an order with a circular;
* the corpus sentence must be the tribunals one. `instructions_for` falls back to
  the US text SILENTLY for an unknown corpus key, so a missing `TRIB` key does
  not raise: it tells every client this server returns United States law.

There is a deliberate asymmetry with the regulatory file: that one declares a
module-level entry point in `pyproject.toml` and so does this, and the test below
asserts it, because a server nothing can launch is the defect this file was
added to close. `tribunals.py` and its tools were committed and green while
NOTHING imported `create_tribunals_server` outside the tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import pathlib
import sys
from collections.abc import AsyncIterator

if sys.version_info >= (3, 11):
    import tomllib
else:  # `tomllib` is stdlib only from 3.11; `tomli` is the same parser and is
    # already present on the 3.10 environment. Without this split, `import
    # tomllib` raises at MODULE level on the py3.10 CI leg, which aborts
    # collection for the whole suite rather than failing one test.
    import tomli as tomllib

import httpx
import pytest
from starlette.routing import Mount

from vaquill_mcp.tribunals import TOOL_NAMES, create_tribunals_server

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_BASE = "https://api.vaquill.ai"
_ORIGIN = "https://tribunals-mcp.vaquill.ai"

#: The six priced rows the startup fetch expects. `/forums` is FREE and is the
#: one row whose absence from a price line is legitimate.
_PRICES = [
    {
        "endpoint": "/in/tribunals/forums",
        "operation": "Indian Tribunal Registry",
        "credits": 0,
    },
    {
        "endpoint": "/in/tribunals/matters",
        "operation": "Indian Tribunal Matter Browse",
        "credits": 1,
    },
    {
        "endpoint": "/in/tribunals/matters/search",
        "operation": "Indian Tribunal Matter Search",
        "credits": 2,
    },
    {
        "endpoint": "/in/tribunals/matters/detail",
        "operation": "Indian Tribunal Matter Detail",
        "credits": 1,
    },
    {
        "endpoint": "/in/tribunals/matters/orders",
        "operation": "Indian Tribunal Matter Orders",
        "credits": 1,
    },
    {
        "endpoint": "/in/tribunals/matters/file",
        "operation": "Indian Tribunal Matter Document",
        "credits": 1,
    },
]

_OAUTH_ENV = (
    "VAQUILL_OAUTH_UPSTREAM_JWKS_URI",
    "VAQUILL_OAUTH_UPSTREAM_ISSUER",
    "VAQUILL_OAUTH_AUTHORIZE_URL",
    "VAQUILL_OAUTH_TOKEN_URL",
    "VAQUILL_OAUTH_CLIENT_ID",
    "VAQUILL_OAUTH_CLIENT_SECRET",
    "VAQUILL_PUBLIC_URL",
)


@pytest.fixture(autouse=True)
def _no_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test bearer-only, whatever the developer's shell holds."""
    for name in (*_OAUTH_ENV, "VAQUILL_INTERNAL_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)


@pytest.fixture
def _pricing(respx_mock):
    """The startup price fetch, with the six rows. Returns the route."""
    return respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing").mock(
        return_value=httpx.Response(200, json={"costs": _PRICES})
    )


@contextlib.asynccontextmanager
async def _serving() -> AsyncIterator[object]:
    """`build_app()` with every mounted lifespan entered, as uvicorn runs it."""
    from vaquill_mcp.tribunals_main import build_app

    app = build_app()
    async with app.router.lifespan_context(app):
        yield app


def test_the_app_mounts_at_the_origin_root_and_nowhere_else(_pricing) -> None:
    """No prefix mount: a tribunals app under a path would advertise the wrong
    OAuth `resource`, which is the whole reason this has its own origin."""
    from vaquill_mcp.tribunals_main import build_app

    mounts = {r.path for r in build_app().routes if isinstance(r, Mount)}
    assert mounts == {"/s", ""}, sorted(mounts)


def test_the_root_catch_all_is_routed_last(_pricing) -> None:
    """Starlette hands a request to the FIRST matching Mount and never falls
    through, so anything declared after `Mount("/")` is unreachable and answers
    a bare 404 that reads as a dead customer URL."""
    from vaquill_mcp.tribunals_main import build_app

    paths = [getattr(r, "path", None) for r in build_app().routes]
    assert paths[-1] == "", paths
    assert "/s" in paths, paths
    assert paths.index("/s") < paths.index(""), paths
    assert paths.index("/health") < paths.index(""), paths


def test_the_lifespan_runs_and_the_app_serves(_pricing) -> None:
    """The shared client is closed by the app's own lifespan. Starlette does not
    start a mounted app's lifespan for it, so a missing entry here leaks the
    client and the mounted apps never initialise."""

    async def go() -> str:
        async with _serving() as app:
            return type(app).__name__

    assert asyncio.run(go()) == "Starlette"


def test_the_catalogue_is_the_six_tribunal_tools() -> None:
    """The served catalogue is exactly `TOOL_NAMES`, computed from the module
    rather than retyped here: a seventh tool added without a price line should
    fail the price warning, not this count."""

    async def go() -> list[str]:
        server = create_tribunals_server()
        return [t.name for t in await server.list_tools()]

    served = asyncio.run(go())
    assert sorted(served) == sorted(TOOL_NAMES), sorted(served)


def test_the_catalogue_shares_nothing_with_the_regulatory_server() -> None:
    """One connection, one corpus.

    These two are confusable in the direction that yields a wrong answer rather
    than an empty one: the regulatory corpus holds what a regulator ISSUED, this
    one holds matters brought BEFORE tribunals, regulators and boards. An
    overlapping catalogue on one origin lets a model satisfy a question about a
    tribunal order with a circular and never notice.
    """
    from vaquill_mcp.regulatory import create_regulatory_server

    async def go() -> tuple[set[str], set[str]]:
        trib = {t.name for t in await create_tribunals_server().list_tools()}
        reg = {t.name for t in await create_regulatory_server().list_tools()}
        return trib, reg

    trib, reg = asyncio.run(go())
    assert trib and reg, (trib, reg)
    assert not (trib & reg), sorted(trib & reg)


def test_the_served_instructions_name_this_corpus_and_not_united_states_law() -> None:
    """`instructions_for` falls back to the US sentence SILENTLY for a corpus key
    it does not know, so a missing `TRIB` entry raises nothing: it just tells
    every client this server returns United States law. Both halves are asserted,
    because the presence of the right words would not by itself prove the wrong
    ones are gone.
    """

    async def go() -> str:
        return create_tribunals_server().instructions or ""

    text = asyncio.run(go())
    assert "tribunal" in text.lower(), text[:300]
    assert "United States" not in text, text[:300]


def test_a_console_script_can_actually_launch_this_server() -> None:
    """The gap this file closed.

    `tribunals.py` was committed, tested and green while nothing outside the
    tests ever imported `create_tribunals_server`: there was no entry point, so
    the six tools could not be served to anyone. A test that only exercised
    `build_app()` would still have passed in that state, so this one asserts the
    declared console script and that its target resolves.
    """
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = config["project"]["scripts"]
    target = scripts.get("vaquill-mcp-tribunals")
    assert target == "vaquill_mcp.tribunals_main:main", scripts

    module_name, _, attr = target.partition(":")
    import importlib

    module = importlib.import_module(module_name)
    assert callable(getattr(module, attr)), target
