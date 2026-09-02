"""The call contract must survive the token optimisation byte for byte.

`schema_slim` rewrites the descriptions inside every tool's input schema, which
is a large change to what an agent READS and must be a zero change to what the
tool ACCEPTS. Reading the diff is not evidence of that. These tests are.

The central one, `test_call_contract_is_unchanged_for_every_tool`, strips every
annotation keyword from the before and after schemas of every tool in both
published documents and asserts deep equality on what remains: property names,
types, enums, `required`, defaults, bounds, `$defs`, `anyOf`. If the optimisation
ever narrows an enum, drops a parameter or changes a default, that test fails
before anything ships.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from vaquill_mcp.descriptions import (
    PARAM_DESCRIPTIONS,
    PARAM_DESCRIPTIONS_BY_TOOL,
)
from vaquill_mcp.schema_slim import (
    _DESCRIPTION_BUDGET,
    _strip_titles,
    curated_description,
    slim_input_schema,
    uncurated_overruns,
)
from vaquill_mcp.server import _derive_mcp_names

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _spec(jurisdiction: str) -> dict:
    return json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())


def _tool_schemas(jurisdiction: str) -> dict[str, dict]:
    """tool name -> the request/parameter schemas the document declares.

    Built from the raw document rather than from a live provider so the guard
    keeps working with no network and no FastMCP internals in the way.
    """
    spec = _spec(jurisdiction)
    names = _derive_mcp_names(spec)
    components = spec.get("components", {}).get("schemas", {})
    out: dict[str, dict] = {}
    for item in spec["paths"].values():
        for op in item.values():
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if not op_id:
                continue
            properties: dict[str, Any] = {}
            for param in op.get("parameters") or []:
                schema = dict(param.get("schema") or {})
                if param.get("description"):
                    schema.setdefault("description", param["description"])
                properties[param["name"]] = schema
            body = (
                (op.get("requestBody") or {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            ref = body.get("$ref", "").split("/")[-1]
            if ref and ref in components:
                properties.update(components[ref].get("properties") or {})
            out[names.get(op_id, op_id)] = {"type": "object", "properties": properties}
    return out


_CATALOGUES = {j: _tool_schemas(j) for j in ("US", "IN")}
_ALL_TOOLS = [(j, t) for j, tools in _CATALOGUES.items() for t in sorted(tools)]

# The keywords `schema_slim` is allowed to touch. Everything else is contract.
_ANNOTATION_KEYS = {"description", "title"}


def _contract_only(node: Any) -> Any:
    """Strip annotation keywords, leaving only what affects a call.

    Schema-aware in the same way `_strip_titles` is: a property genuinely NAMED
    "description" is part of the contract and must survive.
    """
    if isinstance(node, list):
        return [_contract_only(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _ANNOTATION_KEYS:
            continue
        if key in ("properties", "$defs", "definitions", "patternProperties") and isinstance(
            value, dict
        ):
            out[key] = {name: _contract_only(sub) for name, sub in value.items()}
        else:
            out[key] = _contract_only(value)
    return out


def test_the_guard_has_something_to_guard() -> None:
    """A catalogue that silently emptied would make every test below vacuous."""
    assert len(_CATALOGUES["US"]) >= 20, _CATALOGUES["US"].keys()
    assert len(_CATALOGUES["IN"]) >= 5, _CATALOGUES["IN"].keys()


@pytest.mark.parametrize(("jurisdiction", "tool"), _ALL_TOOLS)
def test_call_contract_is_unchanged_for_every_tool(jurisdiction: str, tool: str) -> None:
    """THE regression guard: slimming may not alter what the tool accepts."""
    original = _CATALOGUES[jurisdiction][tool]
    slimmed = slim_input_schema(tool, original)
    assert _contract_only(slimmed) == _contract_only(original), (
        f"{jurisdiction} {tool}: slimming changed the call contract, not just its "
        "prose. Compare enums, required, defaults and bounds."
    )


@pytest.mark.parametrize(("jurisdiction", "tool"), _ALL_TOOLS)
def test_no_parameter_is_added_or_removed(jurisdiction: str, tool: str) -> None:
    """Stated separately from the deep-equality check because it is the failure
    a reader will look for first, and a set diff names the parameter."""
    original = _CATALOGUES[jurisdiction][tool]
    slimmed = slim_input_schema(tool, original)
    assert set(slimmed.get("properties") or {}) == set(original.get("properties") or {})


@pytest.mark.parametrize(("jurisdiction", "tool"), _ALL_TOOLS)
def test_every_parameter_still_has_a_description(jurisdiction: str, tool: str) -> None:
    """Compression, never deletion.

    Dropping parameter descriptions outright saves 56.9% and was rejected: an
    agent that cannot tell `excludeRepealed` from `actStatus` picks wrong, and on
    this API picking wrong means a wrong answer about whether a statute is still
    in force.
    """
    original = _CATALOGUES[jurisdiction][tool]
    slimmed = slim_input_schema(tool, original)
    for name, param in (slimmed.get("properties") or {}).items():
        if not isinstance(param, dict):
            continue
        had = (original["properties"][name] or {}).get("description")
        if had:
            assert param.get("description"), f"{tool}.{name} lost its description"


@pytest.mark.parametrize(("jurisdiction", "tool"), _ALL_TOOLS)
def test_no_uncurated_parameter_exceeds_the_budget(jurisdiction: str, tool: str) -> None:
    """The direction that catches FUTURE bloat.

    A new endpoint, or a new filter on an existing one, arrives with whatever
    prose the API reference needed. Without this it ships straight into every
    caller's context on every turn and nothing says so.
    """
    overruns = uncurated_overruns(tool, _CATALOGUES[jurisdiction][tool])
    assert not overruns, (
        f"{jurisdiction} {tool}: parameters inherit descriptions over "
        f"{_DESCRIPTION_BUDGET} chars with no curated entry: {overruns}. Add one to "
        "PARAM_DESCRIPTIONS_BY_TOOL in descriptions.py."
    )


def test_no_curated_entry_describes_a_parameter_that_does_not_exist() -> None:
    """The other direction, which is how dead prose survives a retirement.

    `descriptions.py` has already carried entries for tools deleted months
    earlier. A curated parameter entry can rot exactly the same way, silently,
    because an entry that never matches simply never fires.
    """
    live: set[tuple[str, str]] = set()
    for tools in _CATALOGUES.values():
        for tool, schema in tools.items():
            for param in schema.get("properties") or {}:
                live.add((tool, param))

    orphans = sorted(key for key in PARAM_DESCRIPTIONS_BY_TOOL if key not in live)
    assert not orphans, (
        f"PARAM_DESCRIPTIONS_BY_TOOL describes parameters no document publishes: "
        f"{orphans}"
    )

    live_names = {param for _, param in live}
    name_orphans = sorted(set(PARAM_DESCRIPTIONS) - live_names)
    assert not name_orphans, (
        f"PARAM_DESCRIPTIONS describes parameters no document publishes: {name_orphans}"
    )


def test_a_tool_scoped_entry_beats_a_bare_name_entry() -> None:
    """`corpusType` means three different things on three tools, so the scoped
    entry has to win or two of them get a description that is simply wrong."""
    assert curated_description("search_us_statutes", "corpusType") != curated_description(
        "create_watch", "corpusType"
    )
    # `act_id` is the shared case: identical on all seven tools that take it.
    assert curated_description("get_section_changes", "act_id") == PARAM_DESCRIPTIONS[
        "act_id"
    ]


def test_title_stripping_never_eats_a_parameter_named_title() -> None:
    """The subtle bug the schema-aware walker exists to prevent.

    `{"properties": {"title": {...}}}` declares a parameter NAMED title. A blind
    key filter would delete it from the tool's contract, and the tool would then
    reject a call that used to work.
    """
    schema = {
        "title": "Annotation, must go",
        "type": "object",
        "properties": {
            "title": {"type": "string", "title": "Title"},
            "nested": {
                "type": "object",
                "properties": {"title": {"type": "integer", "title": "Title"}},
            },
        },
    }
    out = _strip_titles(schema)
    assert "title" not in out
    assert out["properties"]["title"] == {"type": "string"}
    assert out["properties"]["nested"]["properties"]["title"] == {"type": "integer"}


def test_slimming_does_not_mutate_its_input() -> None:
    """`slim_input_schema` is pure. The single in-place write is the assignment
    onto the FastMCP component, which that callback's contract requires."""
    original = _CATALOGUES["US"]["search_us_statutes"]
    before = json.dumps(original, sort_keys=True)
    slim_input_schema("search_us_statutes", original)
    assert json.dumps(original, sort_keys=True) == before


def test_the_optimisation_actually_pays() -> None:
    """A guard on the point of the exercise.

    Without this every curated entry could be quietly reverted to its inherited
    text and every other test here would still pass.
    """
    def size(schema: dict) -> int:
        return len(json.dumps(schema, separators=(",", ":")))

    before = sum(size(s) for s in _CATALOGUES["US"].values())
    after = sum(size(slim_input_schema(t, s)) for t, s in _CATALOGUES["US"].items())
    assert after < before * 0.75, (
        f"US input schemas only fell from {before:,} to {after:,} bytes; the "
        "curated parameter descriptions have stopped being applied."
    )
