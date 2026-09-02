"""The `search` / `fetch` pair, and the end-to-end catalogue they join.

Two jobs here. The alias tests prove the pair honours the contract OpenAI's
deep-research clients require (single string parameter, non-empty `url`, lenient
`fetch`). The catalogue test is the outer regression guard: it builds the real
servers from the real published documents and asserts that the optimisation
added exactly the two aliases and changed nothing else about what is callable.
"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from fastmcp import FastMCP

from vaquill_mcp.aliases import _coerce_act_id, register_aliases
from vaquill_mcp.ordering import DeterministicToolOrder
from vaquill_mcp.server import create_server, published_tool_names

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_BASE = "https://api.vaquill.ai"


def _spec(jurisdiction: str) -> dict:
    return json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())


def _mount(jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock) -> FastMCP:
    """Build the real stdio server for a jurisdiction, with the network mocked.

    `respx_mock` is the conftest override targeting httpcore2 (the package uses
    httpx2 since fastmcp 4). Registering on the module-level `respx` router
    would match nothing and let these tests reach the real API.
    """
    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test")
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    path = "/external/openapi.json" if jurisdiction == "US" else "/in/openapi.json"
    respx_mock.get(f"{_BASE}{path}").mock(
        return_value=httpx.Response(200, json=_spec(jurisdiction))
    )
    respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing/all").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )
    return create_server(jurisdiction)


# ---------------------------------------------------------------------------
# The outer regression guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_catalogue_gains_exactly_the_two_aliases(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """Nothing that was callable stopped being callable.

    The point of the whole change is fewer tokens per tool, never fewer tools.
    This compares the live server against the names the document itself
    publishes, so a tool lost to a route map, a rename or an alias collision
    fails here.
    """
    server = _mount(jurisdiction, monkeypatch, respx_mock)
    published = {tool.name for tool in await server.list_tools()}
    expected = published_tool_names(_spec(jurisdiction)) | {"search", "fetch"}
    assert published == expected


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_tools_list_is_sorted(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """A reordered list invalidates the provider prompt-prefix cache on every
    call, which would cost more than the schema slimming saves."""
    server = _mount(jurisdiction, monkeypatch, respx_mock)
    names = [tool.name for tool in await server.list_tools()]
    assert names == sorted(names)


async def test_every_tool_carries_a_read_or_write_annotation(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    server = _mount("US", monkeypatch, respx_mock)
    for tool in await server.list_tools():
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is not None, tool.name


async def test_the_write_tools_are_the_only_non_read_tools(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """The annotation split a client uses to auto-approve reads safely."""
    server = _mount("US", monkeypatch, respx_mock)
    writes = {
        tool.name
        for tool in await server.list_tools()
        if not tool.annotations.read_only_hint
    }
    assert writes == {"create_watch", "update_watch", "delete_watch", "test_watch"}


# ---------------------------------------------------------------------------
# The generic pair
# ---------------------------------------------------------------------------


async def test_search_and_fetch_take_exactly_one_string(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """The interface OpenAI matches on. A second parameter, or a non-string
    one, and deep research rejects the server as non-conforming."""
    server = _mount("US", monkeypatch, respx_mock)
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name, param in (("search", "query"), ("fetch", "id")):
        schema = tools[name].parameters
        assert list(schema["properties"]) == [param], name
        assert schema["properties"][param]["type"] == "string", name
        assert schema.get("required") == [param], name


async def test_search_always_returns_a_non_empty_url(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """ChatGPT creates a citation ONLY when `url` is a non-empty string, so a
    result whose publisher link is null must still fall back to something."""
    server = _mount("US", monkeypatch, respx_mock)
    respx_mock.post(f"{_BASE}/api/v1/us/statutes/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "actId": "USC_T42_C21_S1983",
                        "citation": "42 U.S.C. 1983",
                        "sectionTitle": "Civil action for deprivation of rights",
                        "excerpt": "Every person who...",
                        "externalUrl": "https://uscode.house.gov/x",
                    },
                    # The case that matters: every link is null.
                    {
                        "actId": "STATE_TX_Cpr_C93_S93.005",
                        "citation": "Tex. Prop. Code 93.005",
                        "externalUrl": None,
                        "htmlUrl": None,
                        "stateHtmlUrl": None,
                    },
                    {"citation": "no act id, dropped"},
                ]
            },
        )
    )
    result = await server.call_tool("search", {"query": "civil rights"})
    payload = result.structured_content or json.loads(result.content[0].text)
    results = payload["results"]
    assert len(results) == 2, "a result without an actId is not addressable"
    assert results[0]["url"] == "https://uscode.house.gov/x"
    assert results[1]["url"] == (
        f"{_BASE}/api/v1/us/statutes/section/STATE_TX_Cpr_C93_S93.005"
    )
    assert all(r["url"] for r in results)
    assert results[0]["title"] == "Civil action for deprivation of rights"
    # A section with no sectionTitle still needs a title.
    assert results[1]["title"] == "Tex. Prop. Code 93.005"


async def test_fetch_accepts_a_bare_act_id(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    server = _mount("US", monkeypatch, respx_mock)
    respx_mock.get(f"{_BASE}/api/v1/us/statutes/section/USC_T42_C21_S1983").mock(
        return_value=httpx.Response(
            200,
            json={
                "section": {
                    "actId": "USC_T42_C21_S1983",
                    "citation": "42 U.S.C. 1983",
                    "sectionTitle": "Civil action",
                    "corpusType": "USC",
                    "goodLawStatus": "good_law",
                }
            },
        )
    )
    respx_mock.get(f"{_BASE}/api/v1/us/statutes/section/USC_T42_C21_S1983/body").mock(
        return_value=httpx.Response(
            200,
            json={
                "plain": "Every person who, under color of any statute...",
                "sourceUrl": "https://uscode.house.gov/x",
            },
        )
    )
    result = await server.call_tool("fetch", {"id": "USC_T42_C21_S1983"})
    payload = result.structured_content or json.loads(result.content[0].text)
    assert payload["id"] == "USC_T42_C21_S1983"
    assert payload["text"].startswith("Every person who")
    assert payload["url"] == "https://uscode.house.gov/x"
    assert payload["metadata"]["goodLawStatus"] == "good_law"


async def test_fetch_resolves_a_citation_instead_of_404ing(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """The leniency that matters most on a legal corpus.

    A model handed a citation in the conversation will hand it back as the `id`.
    Refusing is technically correct and burns a turn; resolving answers.
    """
    server = _mount("US", monkeypatch, respx_mock)
    resolve = respx_mock.get(f"{_BASE}/api/v1/us/statutes/resolve").mock(
        return_value=httpx.Response(
            200,
            json={"resolved": True, "section": {"actId": "USC_T42_C21_S1983"}},
        )
    )
    respx_mock.get(f"{_BASE}/api/v1/us/statutes/section/USC_T42_C21_S1983").mock(
        return_value=httpx.Response(200, json={"section": {"citation": "42 U.S.C. 1983"}})
    )
    respx_mock.get(f"{_BASE}/api/v1/us/statutes/section/USC_T42_C21_S1983/body").mock(
        return_value=httpx.Response(200, json={"plain": "text", "sourceUrl": "https://x"})
    )
    result = await server.call_tool("fetch", {"id": "42 U.S.C. 1983"})
    payload = result.structured_content or json.loads(result.content[0].text)
    assert resolve.called
    assert payload["id"] == "USC_T42_C21_S1983"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("USC_T42_C21_S1983", "USC_T42_C21_S1983"),
        (f"{_BASE}/api/v1/us/statutes/section/USC_T42_C21_S1983", "USC_T42_C21_S1983"),
        ("/api/v1/us/statutes/section/CFR_T21_P314_S314_50/body", "CFR_T21_P314_S314_50"),
        ("api/v1/in/acts/IND_central_2065/text", "IND_central_2065"),
        ("  STATE_TX_Cpr_C93_S93.005  ", "STATE_TX_Cpr_C93_S93.005"),
        ("<USC_T42_C21_S1983>", "USC_T42_C21_S1983"),
    ],
)
def test_fetch_unwraps_the_shapes_models_actually_hand_back(
    given: str, expected: str
) -> None:
    act_id, citation = _coerce_act_id(given)
    assert act_id == expected, (act_id, citation)


@pytest.mark.parametrize("given", ["42 U.S.C. 1983", "Cal. Civ. Code 1950.5"])
def test_a_citation_is_not_mistaken_for_an_id(given: str) -> None:
    act_id, citation = _coerce_act_id(given)
    assert act_id is None
    assert citation == given


async def test_aliases_stand_down_rather_than_shadow_a_real_endpoint() -> None:
    """If the backend ever publishes an operation named `search` or `fetch`, the
    real one wins. A convenience shim silently replacing an endpoint would be a
    regression in what the server can actually do."""
    server = FastMCP("t")
    client = httpx.AsyncClient(base_url=_BASE)
    register_aliases(server, client, "US", _BASE, existing={"search"})
    assert {t.name for t in await server.list_tools()} == set()


async def test_unknown_jurisdiction_registers_nothing() -> None:
    server = FastMCP("t")
    client = httpx.AsyncClient(base_url=_BASE)
    register_aliases(server, client, "ZZ", _BASE, existing=set())
    assert {t.name for t in await server.list_tools()} == set()


async def test_ordering_middleware_sorts_a_shuffled_catalogue() -> None:
    """Direct test of the middleware, so it is covered even if the OpenAPI
    provider happens to emit sorted names on its own."""
    server = FastMCP("t")
    server.add_middleware(DeterministicToolOrder())
    for name in ("zebra", "alpha", "mango"):
        server.tool(name=name)(lambda: None)
    names = [t.name for t in await server.list_tools()]
    assert names == ["alpha", "mango", "zebra"]
