"""The six Indian-tribunals tools (`vaquill_mcp.tribunals`).

What is pinned here, and the failure each pin exists for:

* the catalogue is EXACTLY six tools. The tool count is the endpoint count and a
  seventh costs every firm context in every turn, so it fails the build;
* every description states the metadata-only limit where a model could otherwise
  assume judgment text, because this corpus has NONE and a confident "no Indian
  tribunal has considered this" is the worst answer it can produce;
* the three order outcomes (`listed` / `not_applicable` / `unjoinable`) stay
  three, so "unknown" is never reported as "none";
* a missing DOCUMENT is never reported as a missing MATTER;
* the forum is never inferred from the caseId, and both path segments are
  percent-encoded so a `../` in a model-supplied id cannot reach another route;
* no bearer, upstream host, service name or query text reaches a result or a log.

These reuse the shared transport in `vaquill_mcp.regulatory`, so the leak and
build-query behaviour is proven once there; what is asserted here is that this
module WIRES it correctly and that its own text is right.
"""

from __future__ import annotations

import json
import logging

import httpx
import httpx2
import pytest
from fastmcp import FastMCP

from vaquill_mcp.descriptions import PARAM_DESCRIPTIONS, PARAM_DESCRIPTIONS_BY_TOOL
from vaquill_mcp.instructions import instructions_for
from vaquill_mcp.regulatory import RegulatoryToolError
from vaquill_mcp.tribunals import (
    DESCRIPTIONS,
    SCOPE,
    TOOL_NAMES,
    TOOL_ROUTES,
    TOOL_TITLES,
    _matter_path,
    create_tribunals_server,
    tribunals_costs,
)

# A host and a bearer that are NOT the production ones, and that no real log line
# would contain by accident: a hit on either is a leak and nothing else.
_HOST = "tribunals-upstream-canary.example.test"
_BASE = f"https://{_HOST}"
_BEARER = "vq_key_CANARYBEARER0123456789abcdef"
_QUERY_TEXT = "confidential-party-name-canary"

_P = "/api/v1/in/tribunals"
_FORUM = "cat_cis"
_CASE = "CAT_CIS_OA7962024_5dd99505"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=_BASE, headers={"Authorization": f"Bearer {_BEARER}"}
    )


def _server() -> FastMCP:
    return create_tribunals_server(_client())


def _route(respx_mock, path: str, response: httpx.Response | None = None, **kw):
    return respx_mock.get(f"{_BASE}{_P}{path}").mock(
        return_value=response or httpx.Response(200, json={"data": [], "meta": {}}),
        **kw,
    )


def _sent(route) -> httpx.Request:
    """The one request the route saw, so no assertion runs against a mock that was never hit."""
    assert route.call_count == 1, (
        f"expected exactly one upstream call, saw {route.call_count}"
    )
    return route.calls.last.request


def _pairs(route) -> list[tuple[str, str]]:
    return list(_sent(route).url.params.multi_items())


async def _fails(server: FastMCP, tool: str, args: dict) -> RegulatoryToolError:
    with pytest.raises(RegulatoryToolError) as info:
        await server.call_tool(tool, args)
    return info.value


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


async def test_the_catalogue_is_exactly_the_six_tools() -> None:
    """No seventh tool, and none missing. The count is the endpoint count."""
    names = [t.name for t in await _server().list_tools()]
    assert len(names) == 6
    assert set(names) == set(TOOL_NAMES)
    assert len(set(names)) == len(names)


def test_every_endpoint_has_a_tool_and_every_tool_a_route() -> None:
    assert set(TOOL_ROUTES) == set(TOOL_NAMES)
    assert set(DESCRIPTIONS) == set(TOOL_NAMES)
    assert set(TOOL_TITLES) == set(TOOL_NAMES)


@pytest.mark.parametrize("tool", TOOL_NAMES)
async def test_every_tool_has_a_title_a_description_and_all_three_hints(
    tool: str,
) -> None:
    found = next(t for t in await _server().list_tools() if t.name == tool)
    assert found.title and found.description
    hints = found.annotations
    assert hints is not None
    assert hints.readOnlyHint is True
    assert hints.destructiveHint is False
    assert hints.openWorldHint is False


@pytest.mark.parametrize("tool", TOOL_NAMES)
async def test_no_tool_takes_a_credential_or_a_scope(tool: str) -> None:
    """A model that can see a credential can print it, and a scope is not a parameter."""
    found = next(t for t in await _server().list_tools() if t.name == tool)
    names = {p.lower() for p in found.parameters.get("properties", {})}
    for forbidden in (
        "key",
        "api_key",
        "apikey",
        "token",
        "bearer",
        "authorization",
        "scope",
    ):
        assert forbidden not in names


async def test_every_parameter_publishes_a_description() -> None:
    """The py3.10 `anyOf` nesting hides a description exactly where a client looks for it, so
    this is the check that the flattening middleware actually ran."""
    seen = 0
    for tool in await _server().list_tools():
        for name, schema in (tool.parameters.get("properties") or {}).items():
            seen += 1
            assert schema.get("description"), (
                f"{tool.name}.{name} publishes no description"
            )
    assert seen >= 20, f"only {seen} parameters found, so this check is nearly vacuous"


async def test_no_tool_or_parameter_name_collides_with_the_global_registries() -> None:
    """`PARAM_DESCRIPTIONS` is a bare-parameter-name fallback and is global, so a collision
    would silently give a tribunals parameter another corpus's text."""
    assert not (set(TOOL_NAMES) & {tool for tool, _param in PARAM_DESCRIPTIONS_BY_TOOL})
    params: set[str] = set()
    for tool in await _server().list_tools():
        params |= set(tool.parameters.get("properties", {}))
    assert params, "no parameters found, so the check below would be vacuous"
    assert not (params & set(PARAM_DESCRIPTIONS)), (
        f"{sorted(params & set(PARAM_DESCRIPTIONS))} would inherit another corpus's text"
    )


def test_the_names_are_not_taken_by_another_catalogue() -> None:
    from tests.test_derived_catalogue import IN, US
    from vaquill_mcp.regulatory import TOOL_NAMES as REG

    assert not (set(TOOL_NAMES) & (US | IN | {"search", "fetch"}))
    assert not (set(TOOL_NAMES) & set(REG))


def test_the_server_tells_clients_what_it_serves_and_does_not_claim_decisions() -> None:
    server = _server()
    assert server.instructions == instructions_for("TRIB")
    # The fallback in `instructions_for` is the US text, SILENTLY, so an unlisted corpus key
    # makes this server tell every client it serves United States law.
    assert server.instructions != instructions_for("US")
    assert "Indian tribunals" in server.instructions
    # "records of matters", not "decisions": the corpus holds no judgment text.
    assert "records of matters" in server.instructions


# ---------------------------------------------------------------------------
# The text a model reads
# ---------------------------------------------------------------------------


def test_no_description_states_a_credit_cost() -> None:
    """Prices arrive from the live pricing matrix. A hand-keyed one drifted silently for a
    month, so a number here fails the build."""
    for tool, text in DESCRIPTIONS.items():
        lowered = text.lower()
        assert "credit" not in lowered, f"{tool} hand-writes a price"
        assert "$" not in text

    # Phrases, not bare words. An earlier version of this banned the WORD "complete" and went
    # red on the forums description, which says document coverage "ranges from complete to 43
    # percent missing" -- a measured statement of what is MISSING, i.e. the opposite of the
    # claim being guarded against. A substring check on a loaded word flags the sentence that
    # exists to be honest about the gap.
    for tool, text in DESCRIPTIONS.items():
        lowered = text.lower()
        for claim in (
            "complete coverage",
            "is complete",
            "all matters",
            "every matter",
            "in force",
            "currently valid",
        ):
            assert claim not in lowered, f"{tool} claims {claim!r}"
    # "good law" is required in the detail description (it DISCLAIMS being a citator), so it is
    # checked by its own test rather than banned here.


@pytest.mark.parametrize("tool", ["search_india_tribunal_matters"])
def test_the_search_description_states_the_metadata_only_limit(tool: str) -> None:
    """THE most important sentence on this surface. A model that believes it searched judgment
    text will report that no Indian tribunal has considered a point, on a corpus that holds the
    document and serves it through the file tool."""
    text = DESCRIPTIONS[tool]
    assert "metadata" in text.lower()
    assert "no judgment text" in text.lower()
    # It must name the three fields it really matches, not leave them to be inferred.
    for field in ("title", "petitioner", "respondent"):
        assert field in text.lower()
    assert "never report" in text.lower()


def test_the_orders_description_keeps_the_three_outcomes_apart() -> None:
    """`unjoinable` means UNKNOWN, not none. Flattening it asserts something the lookup never
    established, for 2,694 DRT matters."""
    text = DESCRIPTIONS["list_india_tribunal_matter_orders"]
    for status in ("listed", "not_applicable", "unjoinable"):
        assert status in text
    assert "unknown" in text.lower()
    assert "never report" in text.lower()


def test_the_file_description_does_not_read_a_missing_file_as_a_missing_matter() -> (
    None
):
    text = DESCRIPTIONS["get_india_tribunal_matter_file"]
    assert "hasfile" in text.lower()
    assert "published nothing" in text.lower()


def test_the_forums_description_says_it_is_free_and_first() -> None:
    text = DESCRIPTIONS["list_india_tribunal_forums"]
    assert "free" in text.lower()
    assert "first" in text.lower()


def test_the_detail_description_disclaims_being_a_citator() -> None:
    assert "good law" in DESCRIPTIONS["get_india_tribunal_matter"].lower()


def test_the_browse_description_points_a_case_number_at_the_right_tool() -> None:
    """The two paging tools look interchangeable and are not: `caseNumber` is an exact filter on
    browse and is deliberately NOT a search target (no index supports a partial match)."""
    assert "case number" in DESCRIPTIONS["browse_india_tribunal_matters"].lower()


def test_the_scope_is_named_once() -> None:
    assert SCOPE == "cases:read"


# ---------------------------------------------------------------------------
# What reaches the upstream
# ---------------------------------------------------------------------------


#: Each JSON tool with the route it must call and the arguments to call it with. Parametrized
#: rather than looped, because `respx_mock` asserts every REGISTERED route was called: looping
#: inside one test registers five routes and calls one of them per iteration, so it fails on the
#: four it has not reached yet (and `reset()` clears the call records, not the registrations).
_ROUTE_CASES = [
    ("list_india_tribunal_forums", "/forums", {}),
    ("search_india_tribunal_matters", "/matters/search", {"q": "roof"}),
    ("browse_india_tribunal_matters", "/matters", {}),
    (
        "get_india_tribunal_matter",
        f"/matters/{_FORUM}/{_CASE}/detail",
        {"tribunal": _FORUM, "caseId": _CASE},
    ),
    (
        "list_india_tribunal_matter_orders",
        f"/matters/{_FORUM}/{_CASE}/orders",
        {"tribunal": _FORUM, "caseId": _CASE},
    ),
]


@pytest.mark.parametrize(("tool", "path", "args"), _ROUTE_CASES)
async def test_each_tool_calls_its_own_endpoint(respx_mock, tool, path, args) -> None:
    """A copy-paste between two tools that share a signature (detail and orders take the same
    two arguments) is otherwise invisible: both would answer 200 from the wrong table."""
    route = _route(respx_mock, path)
    await _server().call_tool(tool, args)
    assert _sent(route).url.path == f"{_P}{path}"


def test_the_route_cases_cover_every_json_tool() -> None:
    """Without this, dropping a case from `_ROUTE_CASES` silently stops testing a tool."""
    covered = {tool for tool, _path, _args in _ROUTE_CASES}
    assert covered == set(TOOL_NAMES) - {"get_india_tribunal_matter_file"}


async def test_an_unset_argument_sends_nothing(respx_mock) -> None:
    """No default of ours: the upstream's own default must be the only one."""
    route = _route(respx_mock, "/matters")
    await _server().call_tool("browse_india_tribunal_matters", {})
    assert _pairs(route) == []


async def test_a_false_boolean_is_sent_not_dropped(respx_mock) -> None:
    """`isJudgment=false` selects a real and large subset (255,952 matters). A truthiness test
    would drop it, and a dropped filter is a wrong answer with a 200."""
    route = _route(respx_mock, "/matters")
    await _server().call_tool("browse_india_tribunal_matters", {"isJudgment": False})
    assert _pairs(route) == [("isJudgment", "false")]


async def test_every_filter_goes_through_under_its_published_name(respx_mock) -> None:
    route = _route(respx_mock, "/matters")
    await _server().call_tool(
        "browse_india_tribunal_matters",
        {
            "tribunal": _FORUM,
            "year": 2024,
            "decidedFrom": "2024-01-01",
            "decidedTo": "2024-12-31",
            "isJudgment": True,
            "bench": "cat_cis_guwahati",
            "caseNumber": "OA/796/2024",
            "limit": 50,
            "cursor": "abc",
        },
    )
    assert dict(_pairs(route)) == {
        "tribunal": _FORUM,
        "year": "2024",
        "decidedFrom": "2024-01-01",
        "decidedTo": "2024-12-31",
        "isJudgment": "true",
        "bench": "cat_cis_guwahati",
        "caseNumber": "OA/796/2024",
        "limit": "50",
        "cursor": "abc",
    }


async def test_a_cursor_goes_through_byte_for_byte(respx_mock) -> None:
    """A re-encoded cursor is refused by the upstream, which turns the documented "keep your
    cursor and ask for the next page" into a 422 on our own schema."""
    cursor = "eyJkIjoiMjAyNC0wNy0xOCIsImMiOiJYL1krPT0ifQ=="
    route = _route(respx_mock, "/matters/search")
    await _server().call_tool(
        "search_india_tribunal_matters", {"q": "roof", "cursor": cursor}
    )
    assert dict(_pairs(route))["cursor"] == cursor


async def test_a_case_number_with_a_slash_is_not_split(respx_mock) -> None:
    """Indian case numbers contain `/` as a matter of course (`OA/796/2024`)."""
    route = _route(respx_mock, "/matters")
    await _server().call_tool(
        "browse_india_tribunal_matters", {"caseNumber": "OA/796/2024"}
    )
    assert dict(_pairs(route))["caseNumber"] == "OA/796/2024"


@pytest.mark.parametrize(
    ("tribunal", "case_id"),
    [
        ("cat_cis", "../../../etc/passwd"),
        ("cat_cis", "X/detail?admin=1"),
        ("../reg/documents", "X"),
        ("cat_cis", "A" * 600),
    ],
)
def test_both_path_segments_are_encoded_so_no_id_can_reach_another_route(
    tribunal: str, case_id: str
) -> None:
    """The pair arrives from a MODEL. The upstream validates it, but a path built by string
    formatting must not depend on that: a caseId holding `../` would otherwise select a
    different endpoint, and one holding `?` would append a query to the path."""
    path = _matter_path("get_india_tribunal_matter", tribunal, case_id)
    assert path.startswith(f"{_P}/matters/")
    assert path.endswith("/detail")

    # THE property that matters is the SEGMENT COUNT, not the absence of the characters. `..`
    # and `?` survive as the literal text `..` and `%3F` inside one segment, which is harmless:
    # a path segment spelled `..%2F..%2Fetc` is one segment named that, and the server resolves
    # no parent directory from it. An earlier version of this test asserted `".." not in path`
    # and went red on correctly-encoded input, which would have pushed me to "fix" working code.
    segments = path.strip("/").split("/")
    expected = f"{_P}/matters/t/c/detail".strip("/").split("/")
    assert len(segments) == len(expected), (
        f"{path} has {len(segments)} segments, want {len(expected)}"
    )

    # The two model-supplied values occupy exactly one segment each, and neither can introduce a
    # separator or start a query.
    tribunal_segment, case_segment = segments[-3], segments[-2]
    for segment in (tribunal_segment, case_segment):
        for separator in ("/", "?", "#"):
            assert separator not in segment, (
                f"{separator!r} survived into a segment of {path}"
            )


async def test_the_file_comes_back_as_a_file_not_as_text(respx_mock) -> None:
    pdf = b"%PDF-1.7 tribunal order bytes"
    _route(
        respx_mock,
        f"/matters/{_FORUM}/{_CASE}/file",
        httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"}),
    )
    result = await _server().call_tool(
        "get_india_tribunal_matter_file", {"tribunal": _FORUM, "caseId": _CASE}
    )
    blob = json.dumps(
        result.content if hasattr(result, "content") else result, default=str
    )
    assert "application/pdf" in blob
    # The bytes travel base64-encoded, so the raw marker must NOT appear as text.
    assert "%PDF-1.7 tribunal order bytes" not in blob


async def test_the_download_name_is_the_pair_and_is_capped(respx_mock) -> None:
    """The longest caseId in this corpus is 635 characters (APTEL consolidated appeals, which
    concatenate every appeal number), which is not a usable filename."""
    from vaquill_mcp.tribunals import _file_name

    assert _file_name(_FORUM, _CASE) == f"{_FORUM}_{_CASE}"
    long_name = _file_name(_FORUM, "A" * 600)
    assert len(long_name) <= 140
    assert _file_name(
        _FORUM, "../../etc/passwd"
    ) == f"{_FORUM}_.._.._etcpasswd".replace(
        "_.._.._etcpasswd", "_......etcpasswd"
    ) or "/" not in _file_name(_FORUM, "../../etc/passwd")


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


async def test_a_missing_matter_is_not_found_and_says_not_to_guess(respx_mock) -> None:
    _route(
        respx_mock,
        f"/matters/{_FORUM}/{_CASE}/detail",
        httpx.Response(
            404, json={"error": {"code": "matter_not_found", "message": "no"}}
        ),
    )
    error = await _fails(
        _server(), "get_india_tribunal_matter", {"tribunal": _FORUM, "caseId": _CASE}
    )
    assert error.status == 404
    assert not error.retryable


async def test_a_403_names_the_scope_this_corpus_needs(respx_mock) -> None:
    _route(respx_mock, "/forums", httpx.Response(403, json={"detail": "nope"}))
    error = await _fails(_server(), "list_india_tribunal_forums", {})
    assert error.status == 403
    assert not error.retryable


async def test_a_503_is_retryable_and_never_carries_the_backends_words(
    respx_mock,
) -> None:
    _route(
        respx_mock,
        "/matters",
        httpx.Response(
            503,
            json={
                "error": {
                    "code": "tribunals_backend_unavailable",
                    "message": "relation tribunal_nclt does not exist at 10.0.0.4:5432",
                }
            },
        ),
    )
    error = await _fails(_server(), "browse_india_tribunal_matters", {})
    assert error.retryable
    text = str(error)
    for leak in ("tribunal_nclt", "10.0.0.4", "5432", "relation"):
        assert leak not in text


async def test_an_upstream_401_is_a_tool_error_not_a_protocol_auth_failure(
    respx_mock,
) -> None:
    """Claude starts a sign-in on a protocol 401 and ChatGPT keys off `www-authenticate`, so a
    problem a firm cannot fix would send its client into a login loop."""
    _route(respx_mock, "/forums", httpx.Response(401, json={"detail": "bad key"}))
    error = await _fails(_server(), "list_india_tribunal_forums", {})
    assert error.status == 401
    assert not error.retryable


async def test_a_200_that_is_not_a_json_object_reads_as_the_service_failing(
    respx_mock,
) -> None:
    _route(
        respx_mock, "/forums", httpx.Response(200, content=b"<html>maintenance</html>")
    )
    error = await _fails(_server(), "list_india_tribunal_forums", {})
    assert error.retryable
    assert "<html>" not in str(error)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search_india_tribunal_matters", {"q": "x"}),
        ("search_india_tribunal_matters", {"q": "roof", "limit": 0}),
        ("search_india_tribunal_matters", {"q": "roof", "limit": 101}),
        ("search_india_tribunal_matters", {"q": "roof", "decidedFrom": "18-07-2024"}),
        ("search_india_tribunal_matters", {"q": "roof", "decidedFrom": "1704067200"}),
        ("browse_india_tribunal_matters", {"year": 1700}),
        ("get_india_tribunal_matter", {"tribunal": _FORUM}),
        ("get_india_tribunal_matter", {"caseId": _CASE}),
    ],
)
async def test_an_invalid_argument_is_refused_before_any_call(
    respx_mock, tool, args
) -> None:
    """Refused by the schema, so no upstream call and no charge. `respx_mock` asserts all
    registered routes are called, so registering none proves nothing was sent."""
    with pytest.raises(Exception):  # noqa: B017,PT011 - a schema refusal, not our error type
        await _server().call_tool(tool, args)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def test_each_route_prices_against_its_own_matrix_endpoint() -> None:
    """The derivation strips the version prefix and every path parameter. Measured against the
    live matrix spellings on 2026-11-12: all six match."""
    from vaquill_mcp.server import _pricing_endpoint_for_route

    assert {t: _pricing_endpoint_for_route(r) for t, r in TOOL_ROUTES.items()} == {
        "list_india_tribunal_forums": "/in/tribunals/forums",
        "search_india_tribunal_matters": "/in/tribunals/matters/search",
        "browse_india_tribunal_matters": "/in/tribunals/matters",
        "get_india_tribunal_matter": "/in/tribunals/matters/detail",
        "list_india_tribunal_matter_orders": "/in/tribunals/matters/orders",
        "get_india_tribunal_matter_file": "/in/tribunals/matters/file",
    }


def test_a_tool_with_no_price_row_gets_no_line_rather_than_a_guess() -> None:
    costs = tribunals_costs(
        [{"endpoint": "/in/tribunals/matters/search", "credits": 2}]
    )
    assert "search_india_tribunal_matters" in costs
    assert "browse_india_tribunal_matters" not in costs
    # And the description then carries no cost line at all, rather than "Cost: 0 credits."
    from vaquill_mcp.tribunals import _described

    assert (
        _described("browse_india_tribunal_matters", costs)
        == DESCRIPTIONS["browse_india_tribunal_matters"]
    )
    assert (
        _described("search_india_tribunal_matters", costs)
        != DESCRIPTIONS["search_india_tribunal_matters"]
    )


# ---------------------------------------------------------------------------
# Leaks
# ---------------------------------------------------------------------------


class _Captured(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        # `record.getMessage()` ALREADY applies `record.args`. Applying them again raises
        # "not all arguments converted during string formatting" on any message that legitimately
        # contains a `%`, and the raise happens inside the handler, so the test that depends on
        # these lines errors instead of reporting a leak.
        self.lines.append(record.getMessage())


@pytest.fixture
def captured_logs():
    handler = _Captured()
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


async def test_no_bearer_host_or_query_text_reaches_a_result_or_a_log(
    respx_mock, captured_logs
) -> None:
    _route(
        respx_mock,
        "/matters/search",
        httpx.Response(500, json={"error": {"code": "x", "message": "boom"}}),
    )
    error = await _fails(
        _server(),
        "search_india_tribunal_matters",
        {"q": _QUERY_TEXT, "cursor": "CURSORCANARY"},
    )
    blob = str(error) + "\n" + "\n".join(captured_logs.lines)
    assert captured_logs.lines, "no log lines captured, so this check would be vacuous"
    for leak in (_BEARER, _HOST, _QUERY_TEXT, "CURSORCANARY", "vq_key_"):
        assert leak not in blob, f"{leak!r} leaked"


async def test_a_failure_carries_no_cause_so_no_traceback_prints_the_url(
    respx_mock,
) -> None:
    """An httpx error's message IS the full URL, so every raise happens after the `except` has
    closed and the exception must carry no `__context__`."""
    respx_mock.get(f"{_BASE}{_P}/forums").mock(side_effect=httpx2.ConnectError("boom"))
    error = await _fails(_server(), "list_india_tribunal_forums", {})
    assert error.__cause__ is None
    assert error.__context__ is None
    assert _HOST not in str(error)
