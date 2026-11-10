"""Boolean (terms-and-connectors) search and counting, end to end through the MCP layer.

The backend added, on 2026-10-22/23:

* `POST /us/statutes/search` with `matchType: "boolean"`, paging to the end of the
  matches (offset up to 10,000 in that mode, 70 in the others) and the response
  fields `totalMatches`, `totalMatchesExact`, `relevanceRankedCount`, `nextOffset`,
  `parsedQuery` and `queryWarnings`;
* `POST /us/statutes/count` accepting `query` + `matchType: "boolean"` and answering
  `isExact` and `parsedQuery`.

The tool INPUT schemas come from the OpenAPI document, so they follow the backend
without a release; what this package owns is the prose that teaches a model to use
them, and the guarantee that a response field reaches the model unaltered. These
tests pin both, against the committed fixture (regenerated from the backend's
local schema) rather than the live API.
"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from fastmcp import FastMCP

from vaquill_mcp.descriptions import PARAM_DESCRIPTIONS_BY_TOOL, TOOL_DESCRIPTIONS
from vaquill_mcp.resources import guide_for
from vaquill_mcp.server import create_server

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_BASE = "https://api.vaquill.ai"


def _spec() -> dict:
    return json.loads((_FIXTURES / "openapi_us.json").read_text())


def _mount(monkeypatch: pytest.MonkeyPatch, respx_mock) -> FastMCP:
    """The real US stdio server over the committed document, network mocked."""
    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test")
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    respx_mock.get(f"{_BASE}/external/openapi.json").mock(
        return_value=httpx.Response(200, json=_spec())
    )
    respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing/all").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )
    return create_server("US")


async def _tool(server: FastMCP, name: str):
    tools = {t.name: t for t in await server.list_tools()}
    return tools[name]


def _payload(result) -> dict:
    return result.structured_content or json.loads(result.content[0].text)


# ---------------------------------------------------------------------------
# Input contract: what the published tools accept
# ---------------------------------------------------------------------------


async def test_search_accepts_boolean_and_pages_past_the_ranked_cap(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """`boolean` is a matchType value, and offset's schema ceiling is the Boolean one.

    The 70 cap for ranked modes is enforced by the API (a 422), not the schema, so
    the schema must allow 10,000 or a client that validates arguments locally would
    refuse a legitimate Boolean page before it ever reached the API.
    """
    tool = await _tool(_mount(monkeypatch, respx_mock), "search_us_statutes")
    props = tool.parameters["properties"]
    assert props["matchType"]["enum"] == ["any", "all", "phrase", "boolean"]
    assert props["matchType"]["default"] == "any"
    assert props["offset"]["maximum"] == 10000
    assert props["offset"]["minimum"] == 0
    assert props["limit"]["maximum"] == 50


async def test_count_accepts_a_boolean_query(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    tool = await _tool(_mount(monkeypatch, respx_mock), "count_statute_sections")
    props = tool.parameters["properties"]
    assert "query" in props
    match_type = props["matchType"]
    consts = [branch.get("const") for branch in match_type.get("anyOf", [])]
    assert "boolean" in consts, "count takes only the Boolean matchType"
    # Neither is required: omitting both still counts a bare scope.
    assert "query" not in (tool.parameters.get("required") or [])
    assert "matchType" not in (tool.parameters.get("required") or [])


def test_the_fixture_publishes_the_boolean_response_fields() -> None:
    """The committed document carries the fields the descriptions tell a model to read.

    If a later fixture refresh loses one, every description that names it is
    pointing the model at a field the contract no longer promises.
    """
    schemas = _spec()["components"]["schemas"]
    search = schemas["StatuteSearchResponse"]["properties"]
    for field in (
        "totalMatches",
        "totalMatchesExact",
        "relevanceRankedCount",
        "nextOffset",
        "parsedQuery",
        "queryWarnings",
        "hasMore",
    ):
        assert field in search, field
    count = schemas["StatuteCountResponse"]["properties"]
    assert {"isExact", "parsedQuery", "count"} <= set(count)


# ---------------------------------------------------------------------------
# Call-through: arguments reach the API, response fields reach the model
# ---------------------------------------------------------------------------


async def test_boolean_search_sends_its_arguments_and_returns_every_paging_field(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    server = _mount(monkeypatch, respx_mock)
    api = {
        "results": [{"actId": "CA_CIV_1950.5", "citation": "Cal. Civ. Code 1950.5"}],
        "count": 1,
        "offset": 120,
        "hasMore": True,
        "query": '"security deposit" /s (return OR refund) NOT commercial',
        "parsedQuery": '("security deposit" /s (return OR refund)) NOT commercial',
        "queryWarnings": ["AND and OR combined without parentheses"],
        "totalMatches": 214,
        "totalMatchesExact": False,
        "relevanceRankedCount": 120,
        "nextOffset": 170,
        "noGoodMatch": False,
        "matchQuality": None,
        "creditsConsumed": 4,
    }
    route = respx_mock.post(f"{_BASE}/api/v1/us/statutes/search").mock(
        return_value=httpx.Response(200, json=api)
    )
    args = {
        "query": '"security deposit" /s (return OR refund) NOT commercial',
        "matchType": "boolean",
        "offset": 120,
        "limit": 50,
        "state": "ca",
    }
    result = await server.call_tool("search_us_statutes", args)

    sent = json.loads(route.calls.last.request.content)
    assert sent["matchType"] == "boolean"
    assert sent["offset"] == 120
    assert sent["limit"] == 50
    assert sent["query"] == args["query"]

    payload = _payload(result)
    for field in (
        "totalMatches",
        "totalMatchesExact",
        "relevanceRankedCount",
        "nextOffset",
        "parsedQuery",
        "queryWarnings",
        "hasMore",
    ):
        assert payload[field] == api[field], field


async def test_boolean_count_sends_query_and_returns_is_exact_and_parsed_query(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    server = _mount(monkeypatch, respx_mock)
    api = {
        "count": 1342,
        "isExact": False,
        "parsedQuery": "retaliat* /s whistleblow*",
        "scope": {"state": ["ca"]},
        "creditsConsumed": 1,
    }
    route = respx_mock.post(f"{_BASE}/api/v1/us/statutes/count").mock(
        return_value=httpx.Response(200, json=api)
    )
    result = await server.call_tool(
        "count_statute_sections",
        {"query": "retaliat! /s whistleblow!", "matchType": "boolean", "state": "ca"},
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["query"] == "retaliat! /s whistleblow!"
    assert sent["matchType"] == "boolean"
    payload = _payload(result)
    assert payload["isExact"] is False
    assert payload["parsedQuery"] == api["parsedQuery"]
    assert payload["count"] == 1342


# ---------------------------------------------------------------------------
# The prose a model reads: each clause guards a known wrong-answer mode
# ---------------------------------------------------------------------------


def test_match_type_description_teaches_the_boolean_syntax() -> None:
    text = PARAM_DESCRIPTIONS_BY_TOOL[("search_us_statutes", "matchType")]
    for token in ("`AND`", "`OR`", "`NOT`", "`/s`", "`/p`", "`/5`", "retaliat*", "`( )`"):
        assert token in text, token
    assert "UPPERCASE" in text
    assert "PHRASE" in text, "a space joins words into a phrase; say so"
    assert "OR binds" in text, "precedence differs from textbook Boolean; say so"
    assert "parsedQuery" in text and "queryWarnings" in text
    assert "422" in text and "too heavy" in text


def test_offset_description_teaches_paging_with_next_offset() -> None:
    text = PARAM_DESCRIPTIONS_BY_TOOL[("search_us_statutes", "offset")]
    for token in ("nextOffset", "hasMore", "totalMatches", "totalMatchesExact", "10,000", "70"):
        assert token in text, token
    assert "upper bound" in text
    assert "never compute" in text


def test_search_description_separates_change_from_retry() -> None:
    """422 means change the request; 503 means retry. Swapping them loops or gives up."""
    text = TOOL_DESCRIPTIONS["search_us_statutes"]
    assert "boolean" in text
    assert "422: change the request, never resend it unchanged" in text
    assert "503: retry with backoff" in text


def test_count_description_no_longer_says_there_is_no_query() -> None:
    text = TOOL_DESCRIPTIONS["count_statute_sections"]
    assert "There is no query parameter" not in text
    assert "`boolean`" in text and "isExact" in text


def test_the_guide_carries_the_full_boolean_contract() -> None:
    guide = guide_for("US") or ""
    for token in (
        'matchType: "boolean"',
        "nextOffset",
        "totalMatchesExact",
        "upper bound",
        "422",
        "503",
        "count_statute_sections",
        "parsedQuery",
        "queryWarnings",
    ):
        assert token in guide, token
