"""`layout` on the section body tool: exposed, described, and passed through.

The backend's `GET /api/v1/us/statutes/section/{act_id}/body` takes
`layout=reflowed|source` (default `reflowed`) and answers a `layout` field saying
which was served. The tool input schema comes from the OpenAPI document, so these
tests pin the committed fixture, the curated description, and the query string that
actually reaches the API.
"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from fastmcp import FastMCP

from vaquill_mcp.descriptions import PARAM_DESCRIPTIONS_BY_TOOL
from vaquill_mcp.server import create_server

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_BASE = "https://api.vaquill.ai"
_TOOL = "get_us_statute_section_text"
_ACT = "USC_T42_C21_S1983"


def _mount(monkeypatch: pytest.MonkeyPatch, respx_mock) -> FastMCP:
    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test")
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    spec = json.loads((_FIXTURES / "openapi_us.json").read_text())
    respx_mock.get(f"{_BASE}/external/openapi.json").mock(
        return_value=httpx.Response(200, json=spec)
    )
    respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing/all").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )
    return create_server("US")


async def test_layout_is_exposed_with_its_enum_and_curated_description(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    server = _mount(monkeypatch, respx_mock)
    tool = {t.name: t for t in await server.list_tools()}[_TOOL]
    prop = tool.parameters["properties"]["layout"]

    assert prop["enum"] == ["reflowed", "source"]
    assert prop["default"] == "reflowed"
    assert prop["description"] == PARAM_DESCRIPTIONS_BY_TOOL[(_TOOL, "layout")]

    text = prop["description"]
    for fact in ("`reflowed` (default)", "`source`", "publisher's line breaks", "Same cost", "pinpoint"):
        assert fact in text, fact
    assert "\u2014" not in text and "\u2013" not in text


@pytest.mark.parametrize("layout", ["source", "reflowed"])
async def test_layout_reaches_the_body_route_as_a_query_parameter(
    monkeypatch: pytest.MonkeyPatch, respx_mock, layout: str
) -> None:
    server = _mount(monkeypatch, respx_mock)
    api = {"actId": _ACT, "plain": "Every person who,\nunder color", "layout": layout}
    route = respx_mock.get(f"{_BASE}/api/v1/us/statutes/section/{_ACT}/body").mock(
        return_value=httpx.Response(200, json=api)
    )

    result = await server.call_tool(_TOOL, {"act_id": _ACT, "layout": layout})

    assert route.calls.last.request.url.params["layout"] == layout
    payload = result.structured_content or json.loads(result.content[0].text)
    assert payload["layout"] == layout
