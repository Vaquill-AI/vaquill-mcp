"""The US case-law (court opinions) tools.

They are derived from the backend's OpenAPI like every other tool, so what needs pinning here is
what the derivation cannot decide on its own:

* the catalogue carries exactly sixteen case-law tools, and the one-court-by-id route is NOT a
  tool (the list tool returns every court record and filters; one more tool widens what a model
  chooses from);
* every tool is read-only, including the four POST-as-query routes;
* the tool names and the guidance between tools are real: a description that sends a model to a
  tool that does not exist is a dead end, and one that sends it to the wrong corpus gives a wrong
  answer (a statute citation is not a case citation);
* a cost line is injected from the pricing matrix and none is written into a description;
* nothing a model reads names a data source, a third-party company or India, and no description
  claims a citator verdict the v1 routes do not carry.
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
from vaquill_mcp.descriptions import TOOL_DESCRIPTIONS, TOOL_TITLES

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

CASE_TOOLS = {
    "search_us_cases",
    "resolve_case_citations",
    "get_us_cases_batch",
    "list_us_cases",
    "get_us_case",
    "get_case_citations",
    "get_case_parentheticals",
    "get_us_opinion",
    "get_us_opinions_batch",
    "get_opinion_citations",
    "list_us_courts",
    "get_us_judge",
    "list_us_judges",
    "list_us_dockets",
    "get_us_docket",
    "get_us_docket_entries",
}

# Case-law routes that are NOT tools (see `_ROUTE_MAPS`), by the name FastMCP would have given
# them, and the citator tools, which are not served.
NOT_TOOLS = {
    "get_court",
    "get_us_court",
    "list_case_changes",
    "list_oral_arguments",
    "get_oral_argument",
    "list_judge_disclosures",
    "get_case_treatment",
    "get_case_statutes",
    "get_statute_cases",
}

# The pricing rows the backend publishes for the case-law routes, as endpoint strings. The numbers
# are placeholders: only the injection is under test, and the live matrix is the source of truth.
_PRICING = [
    {"endpoint": e, "operation": op, "credits": c, "regions": ["US"]}
    for e, op, c in (
        ("/us/cases/search", "US Case Law (Search)", 4),
        ("/us/cases/resolve", "US Case Law (Resolve Citations)", 2),
        ("/us/cases/batch", "US Cases (Batch)", 2),
        ("/us/cases/list", "US Cases (List)", 1),
        ("/us/cases", "US Case (Metadata)", 2),
        ("/us/cases/citations", "US Case (Citations)", 1),
        ("/us/cases/parentheticals", "US Case (Parentheticals)", 1),
        ("/us/opinions", "US Opinion (Full Text)", 6),
        ("/us/opinions/batch", "US Opinions (Batch Full Text)", 6),
        ("/us/opinions/citations", "US Opinion (Citations)", 1),
        ("/us/courts", "US Courts (Registry and Coverage)", 0),
        ("/us/judges", "US Judge (Profile)", 2),
        ("/us/dockets/list", "US Dockets (Filter)", 2),
        ("/us/dockets", "US Docket (Record)", 2),
        ("/us/dockets/entries", "US Docket (Entries)", 1),
    )
]

_FORBIDDEN_WORDS = (
    "courtlistener",
    "free law",
    "midpage",
    "westlaw",
    "lexis",
    "harvard",
    "india",
    "caselaw access",
)


def _spec() -> dict:
    return json.loads((_FIXTURES / "openapi_us.json").read_text())


def _built():
    """The tools a real US server publishes, with costs injected from `_PRICING`."""
    with (
        patch.dict(os.environ, {"VAQUILL_API_KEY": "vq_key_test"}),
        patch.object(server_module, "_fetch_openapi_spec", return_value=_spec()),
        patch.object(server_module, "_fetch_full_costs", return_value=_PRICING),
    ):
        server = server_module.create_server("US")
    return {t.name: t for t in asyncio.run(server._list_tools())}


@pytest.fixture(scope="module")
def tools():
    return _built()


def test_the_catalogue_carries_exactly_the_sixteen_case_tools(tools) -> None:
    present = {n for n in tools if n in CASE_TOOLS}
    assert present == CASE_TOOLS, sorted(CASE_TOOLS - present)


def test_the_routes_that_are_not_tools_are_not_tools(tools) -> None:
    """They stay live in the REST API: the catalogue a model chooses from stays small."""
    assert not NOT_TOOLS & set(tools), sorted(NOT_TOOLS & set(tools))
    assert not NOT_TOOLS & set(TOOL_DESCRIPTIONS)
    assert "list_us_courts" in tools, "the by-id pattern swallowed /us/courts"


def test_no_generic_handler_name_leaks_as_a_tool_name(tools) -> None:
    """The backend's handler names (`get_case`, `search_cases`) read as any lawsuit."""
    for raw in (
        "search_cases",
        "get_case",
        "get_opinion",
        "resolve_citations",
        "batch_cases",
        "list_courts",
        "get_judge",
        "list_cases",
        "list_judges",
        "batch_opinions",
    ):
        assert raw not in tools, f"{raw} should be renamed"


@pytest.mark.parametrize("name", sorted(CASE_TOOLS))
def test_every_case_tool_is_read_only(tools, name: str) -> None:
    ann = tools[name].to_mcp_tool(name=name).annotations
    assert ann is not None
    assert ann.read_only_hint is True, f"{name} would ask for write approval"
    assert ann.destructive_hint is False
    assert ann.open_world_hint is False
    assert ann.title, f"{name} has no annotations.title"


@pytest.mark.parametrize("name", sorted(CASE_TOOLS))
def test_every_case_tool_has_a_curated_title(name: str) -> None:
    assert name in TOOL_TITLES
    assert not {"Us", "Api"} & set(TOOL_TITLES[name].split()), TOOL_TITLES[name]


@pytest.mark.parametrize("name", sorted(CASE_TOOLS))
def test_a_cost_line_is_injected_and_never_hardcoded(tools, name: str) -> None:
    assert not re.search(r"\d+\s*credit", TOOL_DESCRIPTIONS[name], re.IGNORECASE)
    description = tools[name].description or ""
    # A free row renders as "Free.", every other as "Cost: N credits."
    assert re.search(r"Cost: [^.]*\d+ credits?|Free\.$", description), description[-120:]


def test_the_courts_list_is_free(tools) -> None:
    """The court by id shares this row, so the list must still read it."""
    assert (tools["list_us_courts"].description or "").endswith("Free.")


def test_the_four_post_routes_are_search_resolve_and_the_two_batches() -> None:
    posts = {
        path
        for path, item in _spec()["paths"].items()
        if "post" in item
        and re.match(r"/api/v1/us/(cases|opinions|courts|judges|dockets)", path)
    }
    assert posts == {
        "/api/v1/us/cases/search",
        "/api/v1/us/cases/resolve",
        "/api/v1/us/cases/batch",
        "/api/v1/us/opinions/batch",
    }


@pytest.mark.parametrize("name", sorted(CASE_TOOLS))
def test_guidance_names_only_tools_that_exist(tools, name: str) -> None:
    """A model told to call a tool that is not there stops, or guesses."""
    mentioned = set(
        re.findall(
            r"\b((?:search|get|list|resolve|extract)_[a-z_]+)\b", TOOL_DESCRIPTIONS[name]
        )
    )
    # `list_us_courts` etc. are tools; a parameter spelled with underscores is not matched.
    unknown = sorted(m for m in mentioned if m not in tools)
    assert not unknown, f"{name} points at tools the server does not publish: {unknown}"


def test_the_hops_between_tools_are_stated() -> None:
    """Resolve a citation first, then read the case; statutes stay on the statute tools."""
    d = TOOL_DESCRIPTIONS
    assert "resolve_case_citations" in d["search_us_cases"]
    assert "search_us_statutes" in d["search_us_cases"]
    assert "get_us_case" in d["resolve_case_citations"]
    assert "resolve_statute_citation" in d["resolve_case_citations"]
    assert "get_us_opinion" in d["get_us_case"]
    assert "get_us_docket_entries" in d["get_us_docket"]
    assert "get_us_docket" in d["list_us_dockets"]
    assert "get_us_cases_batch" in d["get_us_case"]
    assert "list_us_courts" in d["search_us_cases"]
    assert "search_us_cases" in d["get_us_judge"]
    # And the other direction: the statute side hands case citations over.
    assert "resolve_case_citations" in d["resolve_statute_citation"]
    assert "resolve_case_citations" in d["extract_statute_citations"]


def test_paged_tools_say_how_to_page_and_what_an_empty_page_costs() -> None:
    for name in (
        "search_us_cases",
        "get_case_citations",
        "get_opinion_citations",
        "get_case_parentheticals",
        "list_us_courts",
        "list_us_judges",
        "list_us_dockets",
        "get_us_docket_entries",
    ):
        text = TOOL_DESCRIPTIONS[name]
        assert "hasMore" in text and "offset" in text, name
        if name != "list_us_courts":  # the free one has no charge to state
            assert "empty page is charged" in text, name
    assert "nextCursor" in TOOL_DESCRIPTIONS["list_us_cases"]


def test_unknown_ids_are_documented_as_uncharged() -> None:
    for name in (
        "get_us_case",
        "get_us_opinion",
        "get_us_judge",
        "get_us_cases_batch",
        "get_us_opinions_batch",
        "get_case_citations",
        "get_us_docket",
        "get_us_docket_entries",
    ):
        text = TOOL_DESCRIPTIONS[name]
        assert "not charged" in text or "neither is charged" in text, name


def test_the_graph_tools_list_citations_and_claim_no_verdict() -> None:
    """The graph lists citations and counts. It never claims a case is good law."""
    for name in ("get_case_citations", "get_opinion_citations"):
        text = TOOL_DESCRIPTIONS[name]
        assert "Lists citations only" in text, name
        assert "good law" not in text, name


def test_unserved_inputs_say_so(tools) -> None:
    """A model must not pay for, or be refused, an input that is not served."""
    for name, param in (
        ("resolve_case_citations", "includeStatus"),
        ("get_case_citations", "treatment"),
        ("get_opinion_citations", "category"),
    ):
        props = tools[name].parameters["properties"]
        assert "Not served yet" in props[param]["description"], name
    props = tools["get_us_docket_entries"].parameters["properties"]
    assert "Leave false" in props["includeDocuments"]["description"]


def test_the_dockets_say_identifiers_are_masked_and_files_are_not_returned() -> None:
    assert "masked" in TOOL_DESCRIPTIONS["list_us_dockets"]
    assert "masked" in TOOL_DESCRIPTIONS["get_us_docket_entries"]
    assert "not returned" in TOOL_DESCRIPTIONS["get_us_docket_entries"]


@pytest.mark.parametrize("name", sorted(CASE_TOOLS))
def test_nothing_a_model_reads_names_a_source_or_a_third_party(tools, name: str) -> None:
    text = json.dumps(
        {
            "description": tools[name].description,
            "title": TOOL_TITLES[name],
            "parameters": tools[name].parameters,
        }
    ).lower()
    for word in _FORBIDDEN_WORDS:
        assert word not in text, f"{name} mentions {word!r}"


def test_the_opinion_tool_exposes_both_formats(tools) -> None:
    props = tools["get_us_opinion"].parameters["properties"]
    assert props["format"]["enum"] == ["html", "text"]
    assert props["format"]["default"] == "html"


def test_search_exposes_every_filter_the_backend_accepts(tools) -> None:
    props = set(tools["search_us_cases"].parameters["properties"])
    assert {
        "query",
        "courtIds",
        "state",
        "dateFrom",
        "dateTo",
        "publicationStatus",
        "judgeId",
        "limit",
        "offset",
    } <= props
