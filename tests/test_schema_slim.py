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


# ---------------------------------------------------------------------------
# A curated description that RESTATES an enum must restate all of it
# ---------------------------------------------------------------------------
#
# `schema_slim` never touches `enum`, so the accepted values are always correct
# and always derived from the published document. What drifts is the PROSE
# beside it, and a curated description is hand-written by definition.
#
# Measured 2026-09-03: the `corpusType` description spelled out fifteen of the
# seventeen tokens and omitted `AGENCY_ADJUDICATION` and `STATUTE_COMPILATION`,
# both of which had already shipped. The enum accepted them; every agent reading
# the description believed they did not exist. That is worse than an outdated
# sentence, because the tool advertises a corpus boundary that is not real.
#
# 🔴 The hard part is that "name every value" is the WRONG rule in general.
# `source` has 46 values and `state` has 53, and spelling either out is exactly
# the bloat this module exists to remove: the file's own header records `source`
# spending 3,529 characters glossing values the enum already lists. So the guard
# has to tell a description that CLAIMS to enumerate from one that gives
# examples, and it does that two ways, both derived from the text itself rather
# than from a hand-kept list of exceptions:
#
#   1. a HEDGE marker ("and the rest", "~34", "e.g.") means the author said out
#      loud that the list is partial; and
#   2. naming fewer than most of the values means it never was a list.
#
# A description that names 88% of an enum with no hedge is making a completeness
# claim, and this fails until it is true.
#
# ⚠️ Known limit: adding many values at once can drop coverage under the
# threshold and silence the guard. That is the loud case, not the quiet one, and
# the quiet one is what has actually bitten.
_HEDGES = (
    "and the rest",
    "among",
    "e.g.",
    "for example",
    "such as",
    "lists them all",
    "~",
    "...",
)
_ENUMERATION_THRESHOLD = 0.6


def _enum_values(param: dict) -> list[str]:
    """Every enum value a parameter accepts, through `anyOf`/`items` wrappers."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.get("enum") or ():
                if isinstance(value, str):
                    found.append(value)
            for key in ("items", "anyOf", "oneOf", "allOf"):
                child = node.get(key)
                if isinstance(child, list):
                    for entry in child:
                        walk(entry)
                elif child is not None:
                    walk(child)

    walk(param)
    return sorted(set(found))


@pytest.mark.parametrize(("jurisdiction", "tool"), _ALL_TOOLS)
def test_a_description_that_lists_enum_values_lists_all_of_them(
    jurisdiction: str, tool: str
) -> None:
    schema = _CATALOGUES[jurisdiction][tool]
    for name, param in (schema.get("properties") or {}).items():
        if not isinstance(param, dict):
            continue
        curated = PARAM_DESCRIPTIONS_BY_TOOL.get((tool, name))
        if not curated:
            continue
        values = _enum_values(param)
        if not values:
            continue
        named = [v for v in values if f"`{v}`" in curated]
        if len(named) < len(values) * _ENUMERATION_THRESHOLD:
            continue  # examples, not an enumeration
        if any(hedge in curated for hedge in _HEDGES):
            continue  # the author said the list is partial
        missing = sorted(set(values) - set(named))
        assert not missing, (
            f"{jurisdiction} {tool}.{name}: the curated description spells out "
            f"{len(named)} of {len(values)} enum values but omits {missing}. The "
            "enum accepts them, so the tool advertises a boundary that is not "
            "real. Add them to PARAM_DESCRIPTIONS_BY_TOOL in descriptions.py, or "
            "hedge the sentence if the list is deliberately partial."
        )
