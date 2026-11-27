"""The India catalogue is curated to the level of its acts tools, on the LIVE document.

Until 2026-11-27 `tests/fixtures/openapi_in.json` was the 19-path document of 2026-11-10 while the
live one had 49, so every guard below read a catalogue that did not contain the case-law,
tribunal or version tools: 18 of 38 published tools served raw OpenAPI prose with no title and
`resolve_india_cases_batch` was annotated as a write, and nothing failed. These tests build the
real server from the fixture and read what a model reads.

Refresh the fixture from the backend with the command in `test_derived_catalogue.py`.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
from unittest.mock import patch

import pytest

from vaquill_mcp import server as server_module
from vaquill_mcp.descriptions import PARAM_DESCRIPTIONS_BY_TOOL, TOOL_DESCRIPTIONS, TOOL_TITLES
from vaquill_mcp.instructions import instructions_for
from vaquill_mcp.resources import _GUIDES

_SPEC = json.loads(
    (pathlib.Path(__file__).resolve().parent / "fixtures" / "openapi_in.json").read_text()
)

# The shape of the live India document. A fixture that loses any of these has gone stale again
# and every assertion in this file would be about a smaller catalogue than customers see.
_LIVE_GROUPS = (
    "/api/v1/in/acts/",
    "/api/v1/in/cases/search",
    "/api/v1/in/cases/{caseId}/body",
    "/api/v1/in/courts",
    "/api/v1/in/tribunals/matters/search",
    "/api/v1/in/tribunals/forums",
)


def _built() -> dict:
    with (
        patch.dict(os.environ, {"VAQUILL_API_KEY": "vq_key_test"}),
        patch.object(server_module, "_fetch_openapi_spec", return_value=_SPEC),
        patch.object(server_module, "_fetch_full_costs", return_value=[]),
    ):
        server = server_module.create_server("IN")
    return {t.name: t for t in asyncio.run(server._list_tools())}


@pytest.fixture(scope="module")
def tools() -> dict:
    return _built()


def test_the_india_fixture_is_the_live_document_shape() -> None:
    paths = set(_SPEC["paths"])
    for group in _LIVE_GROUPS:
        assert any(p.startswith(group) for p in paths), f"fixture lacks {group}: it is stale"
    assert len(paths) >= 40, len(paths)


def test_the_catalogue_covers_acts_case_law_and_tribunals(tools: dict) -> None:
    assert len(tools) >= 33, sorted(tools)
    for name in (
        "search_acts",
        "get_act_versions",
        "search_india_cases",
        "get_india_case_body",
        "resolve_india_cases_batch",
        "list_india_courts",
        "search_india_tribunal_matters",
        "get_india_tribunal_matter",
    ):
        assert name in tools, name


def test_every_india_tool_is_curated_titled_and_annotated(tools: dict) -> None:
    for name, tool in sorted(tools.items()):
        if name in _ALIASES:
            continue  # `search` / `fetch`: titled here, described in aliases.py
        assert name in TOOL_DESCRIPTIONS, f"{name}: no curated description"
        assert name in TOOL_TITLES, f"{name}: no title"
        assert tool.description.startswith(TOOL_DESCRIPTIONS[name]), name
        assert tool.title == TOOL_TITLES[name], name
        assert tool.annotations is not None and tool.annotations.title == TOOL_TITLES[name], name


def test_the_batch_case_resolver_is_a_read(tools: dict) -> None:
    assert tools["resolve_india_cases_batch"].annotations.read_only_hint is True
    assert tools["resolve_india_cases_batch"].annotations.destructive_hint is False


_ALIASES = frozenset({"search", "fetch"})

_ROUTE = re.compile(r"\b(?:GET|POST)\s+/|`/(?:in|acts|cases|tribunals|matters|forums)\b|/api/v1/")


def _texts(tool) -> list[tuple[str, str]]:
    out = [("description", tool.description)]
    for pname, prop in (tool.parameters.get("properties") or {}).items():
        if pname == "Idempotency-Key":
            continue
        out.append((f"param {pname}", prop.get("description") or ""))
    return out


def test_no_curated_india_tool_text_names_a_rest_route(tools: dict) -> None:
    """A model calls tools, not routes: "GET /cases/search" is an instruction it cannot follow.

    Scoped to the tools curated on 2026-11-27 plus the two whose `version` text was rewritten.
    The older acts tools still inherit route names in some parameter descriptions
    (`status`, `actStatus`, `search`); widening this to every tool is the next cleanup.
    """
    scope = {
        n
        for n in tools
        if "case" in n
        or "tribunal" in n
        or n in {"list_india_courts", "get_act_versions", "get_changes"}
    } | {"get_act_section", "get_act_section_body"}
    assert len(scope) >= 17, sorted(scope)
    bad = [
        (name, where, m.group(0))
        for name in sorted(scope)
        for where, text in _texts(tools[name])
        if (m := _ROUTE.search(text))
    ]
    assert not bad, bad


def test_the_case_law_and_tribunal_tools_do_not_hardcode_corpus_statistics(tools: dict) -> None:
    """Counts in prose go stale the day the corpus moves (1,261,877 orders, 813,168 matters)."""
    for name in tools:
        if "case" in name or "tribunal" in name or "court" in name:
            for where, text in _texts(tools[name]):
                assert not re.search(r"\d{1,3},\d{3}\b", text), (name, where)


def test_the_parameters_that_change_the_answer_are_named(tools: dict) -> None:
    d = TOOL_DESCRIPTIONS
    assert "asAtDate" in d["resolve_india_citation"]
    for name in ("get_act_section", "get_act_section_body"):
        assert "version" in d[name] and "get_act_versions" in d[name], name
        assert "not legal effective dates" in d[name], name
    assert "include" in d["get_act_section"] and "history" in d["get_act_section"]
    assert "actId" in d["get_changes"] and "get_act_versions" in d["get_changes"]
    assert "newSection" in d["get_corresponding_provisions"]
    assert "include filters" in d["get_coverage"]
    assert "repealClaim" in d["india_act_record"] and "servedStatus" in d["india_act_record"]
    assert "pageSize" in d["search_acts"] and "100" in d["search_acts"]
    assert "NOT legal effective dates" in d["get_act_versions"]
    assert "get_india_case_body" in d["get_india_case_detail"]


def test_the_deprecated_limit_alias_is_not_published(tools: dict) -> None:
    for name in ("india_act_cited_by", "india_act_subordinate", "india_act_definitions"):
        props = tools[name].parameters["properties"]
        assert "limit" not in props, name
        assert "pageSize" in props, name
    # A tool whose `limit` is the real parameter keeps it.
    assert "limit" in tools["search_india_cases"].parameters["properties"]


def test_every_india_act_id_tool_is_described_for_india(tools: dict) -> None:
    for name, tool in tools.items():
        if "act_id" in tool.parameters.get("properties", {}):
            assert (name, "act_id") in PARAM_DESCRIPTIONS_BY_TOOL, name
            assert "IND_central" in PARAM_DESCRIPTIONS_BY_TOOL[(name, "act_id")], name


def test_the_server_describes_what_the_origin_serves() -> None:
    text = instructions_for("IN")
    assert "judgments" in text and "tribunals" in text and "Acts" in text
    assert "regulator publications" not in text and "India Code" not in text


def test_the_guide_covers_all_three_corpora_and_names_only_published_tools(tools: dict) -> None:
    guide = _GUIDES["IN"]
    for needle in ("Case law", "Tribunals", "get_act_section_body", "get_act_versions", "asAtDate"):
        assert needle in guide, needle
    assert "India legislation corpus" not in guide
    named = set(re.findall(r"`((?:get|list|search|resolve|india)_[a-z_]+)`", guide))
    assert named, "the guide names no tools: the check below would be vacuous"
    assert not named - set(tools), sorted(named - set(tools))
