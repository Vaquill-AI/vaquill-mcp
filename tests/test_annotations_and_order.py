"""Tool annotations and deterministic `tools/list` ordering.

Both are cheap changes guarding expensive failures. Without annotations a client
must auto-approve `delete_watch` to auto-approve `search_us_statutes`. Without
sorting, a reordered catalogue invalidates the provider prompt-prefix cache on
every call, which costs more than the schema optimisation saves.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastmcp.utilities.openapi.models import HTTPRoute, ResponseInfo

from vaquill_mcp.server import (
    _ACKNOWLEDGED_WRITE_POSTS,
    _READ_ONLY_POSTS,
    _annotations_for,
    _is_read_only,
    _pricing_endpoint_for_route,
)

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _routes(jurisdiction: str) -> list[tuple[str, str, set[str]]]:
    """(method, path, response codes) for every operation in a document."""
    spec = json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())
    out = []
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if isinstance(op, dict) and op.get("operationId"):
                out.append((method.upper(), path, set(op.get("responses") or {})))
    return sorted(out)


_ALL = [(j, *r) for j in ("US", "IN") for r in _routes(j)]


def _route(method: str, path: str, codes: set[str]) -> HTTPRoute:
    return HTTPRoute(
        method=method,
        path=path,
        operation_id="op",
        responses={code: ResponseInfo(description="") for code in codes},
    )


def test_there_are_routes_to_classify() -> None:
    assert len(_ALL) >= 25, len(_ALL)


@pytest.mark.parametrize(("jur", "method", "path", "codes"), _ALL)
def test_every_post_route_is_classified(
    jur: str, method: str, path: str, codes: set[str]
) -> None:
    """A new POST must be decided by a human, not defaulted by accident.

    POST is genuinely ambiguous in REST: three of this API's READ tools are POST
    because their input is too large for a query string, and two of its WRITES
    are POST as well. There is no signal in the document that separates them
    beyond a 201, so an unrecognized POST fails here until somebody classifies
    it. The runtime default is the safe one (treat it as a write), so this test
    failing costs a decision, never a wrongly auto-approved mutation.
    """
    if method != "POST":
        pytest.skip("only POST is ambiguous")
    endpoint = _pricing_endpoint_for_route(path)
    assert (
        "201" in codes
        or endpoint in _READ_ONLY_POSTS
        or endpoint in _ACKNOWLEDGED_WRITE_POSTS
    ), (
        f"{jur} POST {path} is unclassified: it returns no 201 and appears in "
        "neither _READ_ONLY_POSTS nor _ACKNOWLEDGED_WRITE_POSTS. Decide which it "
        "is in server.py."
    )


@pytest.mark.parametrize(("jur", "method", "path", "codes"), _ALL)
def test_annotations_are_derivable_for_every_route(
    jur: str, method: str, path: str, codes: set[str]
) -> None:
    annotations = _annotations_for(_route(method, path, codes))
    assert annotations.read_only_hint is not None
    if annotations.read_only_hint:
        # destructiveHint is defined as meaningful only when readOnlyHint is
        # false, so emitting one here would be noise a client must ignore.
        assert annotations.destructive_hint is None
    else:
        # It defaults to TRUE, so a non-destructive write has to say so or a
        # client is entitled to treat create_watch like delete_watch.
        assert annotations.destructive_hint is not None


def test_the_three_post_reads_are_read_only() -> None:
    """The specific case that makes 'derive from the verb' wrong.

    These are POST only because their bodies are too big for a query string.
    Marking them writes would strip auto-approval from the most-used tools in
    the catalogue.
    """
    for path in (
        "/api/v1/us/statutes/search",
        "/api/v1/us/statutes/sections",
        "/api/v1/us/statutes/resolve",
        "/api/v1/in/acts/search",
    ):
        assert _is_read_only(_route("POST", path, {"200"})), path


def test_the_writes_are_not_read_only() -> None:
    assert not _is_read_only(_route("POST", "/api/v1/watches", {"201"}))
    assert not _is_read_only(_route("PATCH", "/api/v1/watches/{watch_id}", {"200"}))
    assert not _is_read_only(_route("DELETE", "/api/v1/watches/{watch_id}", {"204"}))
    # The one no rule derives: a 200-returning POST that really does act. It is
    # not on the allow-list, so the fail-closed default catches it.
    assert not _is_read_only(_route("POST", "/api/v1/watches/{watch_id}/test", {"200"}))


def test_delete_is_the_only_destructive_tool() -> None:
    assert _annotations_for(_route("DELETE", "/api/v1/watches/{id}", {"204"})).destructive_hint
    assert (
        _annotations_for(_route("POST", "/api/v1/watches", {"201"})).destructive_hint
        is False
    )
    assert (
        _annotations_for(_route("POST", "/api/v1/watches/{id}/test", {"200"})).destructive_hint
        is False
    )


def test_an_unknown_post_fails_closed() -> None:
    """The property that makes the allow-list safe to keep by hand.

    A future POST nobody has classified is treated as a write. The cost is one
    approval prompt; the alternative default would auto-approve a mutation.
    """
    assert not _is_read_only(_route("POST", "/api/v1/something/new", {"200"}))


def test_read_only_post_list_has_no_dead_entries() -> None:
    """A stale allow-list entry is how this silently starts lying."""
    live = {_pricing_endpoint_for_route(path) for _, path, _ in _routes("US")}
    live |= {_pricing_endpoint_for_route(path) for _, path, _ in _routes("IN")}
    for name, listed in (
        ("_READ_ONLY_POSTS", _READ_ONLY_POSTS),
        ("_ACKNOWLEDGED_WRITE_POSTS", _ACKNOWLEDGED_WRITE_POSTS),
    ):
        assert not (listed - live), (
            f"{name} names routes no document publishes: {sorted(listed - live)}"
        )


def test_annotations_serialize_as_camel_case_on_the_wire() -> None:
    """The Python field names are snake_case; the MCP wire format is not.

    MCP SDK v2 renamed `readOnlyHint` to `read_only_hint` in Python, but the
    protocol still specifies camelCase. If the serialization alias were ever
    lost, every client would stop seeing the hints while the Python-side tests
    kept passing, so the wire shape is asserted directly.
    """
    from vaquill_mcp.server import _DESTRUCTIVE, _READ_ONLY, _WRITE

    def wire(annotations) -> dict:
        return annotations.model_dump(mode="json", exclude_none=True, by_alias=True)

    assert wire(_READ_ONLY) == {"readOnlyHint": True}
    assert wire(_WRITE) == {"readOnlyHint": False, "destructiveHint": False}
    assert wire(_DESTRUCTIVE) == {"readOnlyHint": False, "destructiveHint": True}


def _built_tools(jurisdiction: str):
    """Every tool a real server would publish for one jurisdiction.

    Built through `create_server` rather than read off the spec, because the
    `search` / `fetch` aliases are registered in code and never appear in an
    OpenAPI document. They were the two tools that kept their derived titles
    when `TOOL_TITLES` was first wired only into the OpenAPI customizer.
    """
    import asyncio
    import os
    from unittest.mock import patch

    from vaquill_mcp import server as server_module

    spec = json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())
    # The key is patched IN, not read from the environment. `create_server`
    # calls `get_api_key()`, which raises when VAQUILL_API_KEY is unset: a
    # developer with a real key exported sees this pass and CI, which has none,
    # sees it fail. That is exactly how it shipped red.
    with (
        patch.dict(os.environ, {"VAQUILL_API_KEY": "vq_key_test"}),
        patch.object(server_module, "_fetch_openapi_spec", return_value=spec),
        patch.object(server_module, "_fetch_full_costs", return_value=[]),
    ):
        server = server_module.create_server(jurisdiction)
    return asyncio.run(server._list_tools())


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
def test_every_tool_has_a_title_and_no_mangled_acronym(jurisdiction: str) -> None:
    """The connector directory REQUIRES a title on every tool, and shows it.

    FastMCP derives one from the tool name when `TOOL_TITLES` has no entry,
    which title-cases the underscores: `get_us_statute_section_text` becomes
    "Get Us Statute Section Text". That satisfies the requirement and still
    reads wrong, because "Us" is the pronoun rather than the country name. A
    derived title is acceptable only where the name carries no acronym, so this
    fails on the acronyms rather than on the absence of a map entry.
    """
    mangled = {"Us", "Cfr", "Usc", "Ipc", "Crpc", "Bns", "Bnss", "Api"}

    for tool in _built_tools(jurisdiction):
        mcp_tool = tool.to_mcp_tool(name=tool.name)

        # BOTH fields, and annotations.title is the load-bearing one: the
        # Anthropic connector directory reads it, reports "Missing annotations:
        # title" when only the top-level field is set, and then displays a
        # name-derived label instead. Asserting only `mcp_tool.title` passed
        # while the live submission portal flagged all 25 tools.
        assert mcp_tool.title, f"{jurisdiction}/{tool.name} has no Tool.title"
        assert mcp_tool.annotations is not None, f"{jurisdiction}/{tool.name} has no annotations"
        assert mcp_tool.annotations.title, (
            f"{jurisdiction}/{tool.name} has no annotations.title; the directory "
            "reads that field, not the tool's own title"
        )
        assert mcp_tool.annotations.title == mcp_tool.title, (
            f"{jurisdiction}/{tool.name} disagrees with itself: "
            f"{mcp_tool.annotations.title!r} vs {mcp_tool.title!r}"
        )
        words = set(mcp_tool.title.replace("/", " ").split())
        assert not (words & mangled), (
            f"{jurisdiction}/{tool.name} title {mcp_tool.title!r} title-cases the "
            f"acronym {sorted(words & mangled)}. Add an entry to TOOL_TITLES."
        )
