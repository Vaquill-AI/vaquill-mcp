"""Compress the OpenAPI-derived input schemas WITHOUT touching the call contract.

WHY THIS EXISTS
===============

`descriptions.py` rewrites the TOOL description, and that was long assumed to be
the main token lever. Measured against the published documents on 2026-09-02 it
is not, and not by a little:

    US catalogue, 23 tools, 51,854 bytes of tool definition
      tool descriptions    6,654  (12.8%)   <- what TOOL_DESCRIPTIONS controls
      input schemas       44,615  (86.0%)   <- what nothing controlled
      output schemas         585  ( 1.1%)

and 65% of that input-schema mass is per-PARAMETER prose inherited verbatim from
the OpenAPI. One parameter, `search_us_statutes.source`, carries a 3,529-char
description; the whole `search_us_statutes` tool is 19,242 bytes, 38% of the
entire catalogue on its own.

That prose is not wasted in its own home. The published API reference is
generated from those same OpenAPI descriptions, so they have to stay long there.
The MCP layer is simply the wrong place to pay for them: a tool definition is
resident in the model's working memory for every turn of an agentic loop, and
`source` spends 3,529 characters glossing values that the `enum` sitting beside
it already lists machine-readably.

WHAT IS AND IS NOT TOUCHED
==========================

The rule that makes this safe: **the call contract is annotation-free.**

    NEVER touched -- property names, `type`, `enum`, `anyOf`/`oneOf`/`allOf`,
    `required`, `default`, `items`, `$ref`/`$defs`, `format`, and every
    validation bound (`minLength`, `maxLength`, `minimum`, `maximum`,
    `pattern`, `additionalProperties`).

    Rewritten -- `description` on top-level parameters, from a curated map.

    Dropped -- `title`, which JSON Schema defines as a pure annotation and
    which FastAPI generates for every field as a restatement of the property
    name (`"query"` -> `"Query"`).

So a tool accepts exactly the arguments it accepted before, rejects exactly what
it rejected before, and returns exactly what it returned before. The guard in
`tests/test_schema_slim.py` proves it structurally rather than by inspection: it
strips annotations from the before and after schemas and asserts deep equality
across every tool of both published documents.

WHY CURATED RATHER THAN MECHANICAL
==================================

Mechanical truncation was measured first and it does not work here. Taking the
first paragraph of any over-long description saves 18.7% of the input schemas,
because most of these are one enormous paragraph rather than a short lede
followed by detail. Dropping parameter descriptions outright saves 56.9% and is
not acceptable: an agent that cannot tell `excludeRepealed` from `actStatus`
picks wrong, and this is a paid legal API where picking wrong means a wrong
answer about whether a statute is still in force.

So the compression is hand-written, exactly as `TOOL_DESCRIPTIONS` is, and
carries the same two-directional drift guard: a curated entry naming a parameter
no document publishes fails the suite, and a published parameter whose inherited
description exceeds `_DESCRIPTION_BUDGET` and has no curated entry ALSO fails.
The second direction is the one that matters. Without it a newly bloated
parameter ships silently, which is precisely how the tool descriptions this
module was written to complement went stale twice.
"""

from __future__ import annotations

from typing import Any

from vaquill_mcp.descriptions import PARAM_DESCRIPTIONS, PARAM_DESCRIPTIONS_BY_TOOL

# A parameter description longer than this must be curated. The number is not
# magic: it is comfortably above the ~250 chars an unremarkable parameter needs
# and far below the 340+ where the published documents start restating the API
# reference. `tests/test_schema_slim.py` fails on any uncurated parameter above
# it, so raising this to silence a failure is a deliberate, reviewable act.
_DESCRIPTION_BUDGET = 260

# JSON Schema keywords whose value is a MAPPING OF NAME -> SUBSCHEMA. The names
# are arbitrary and author-controlled, so a walker must recurse into the values
# and never interpret a key here as a schema keyword. This is the subtle bug the
# split exists to prevent: `{"properties": {"title": {...}}}` declares a
# parameter NAMED "title", and treating that as the annotation keyword would
# delete a real parameter from the tool's contract.
_SUBSCHEMA_MAPS = ("properties", "$defs", "definitions", "patternProperties")

# Keywords whose value is a single subschema, or (for `items`/`additionalProperties`)
# either a subschema or a boolean.
_SUBSCHEMA_VALUES = (
    "items",
    "additionalProperties",
    "additionalItems",
    "contains",
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
    "unevaluatedItems",
    "unevaluatedProperties",
)

# Keywords whose value is a LIST of subschemas.
_SUBSCHEMA_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")


def _strip_titles(node: Any) -> Any:
    """Return `node` with every schema-level `title` removed.

    Schema-aware rather than a blind key filter, so a parameter or property
    genuinely NAMED "title" survives untouched. `title` is annotation-only in
    JSON Schema (it never participates in validation), and FastAPI emits one for
    every field as a capitalized restatement of the field name, so removing it
    costs nothing a client can observe and returns 1,637 bytes on the US
    catalogue alone.
    """
    if isinstance(node, list):
        return [_strip_titles(item) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title":
            continue
        if key in _SUBSCHEMA_MAPS and isinstance(value, dict):
            # Values are subschemas; the KEYS are author-chosen names and must
            # be passed through verbatim.
            out[key] = {name: _strip_titles(sub) for name, sub in value.items()}
        elif key in _SUBSCHEMA_VALUES or key in _SUBSCHEMA_LISTS:
            out[key] = _strip_titles(value)
        else:
            out[key] = value
    return out


def curated_description(tool_name: str, param_name: str) -> str | None:
    """The curated description for one parameter, or None to keep the original.

    A tool-scoped entry wins over a bare parameter-name entry. Both exist
    because the same name means different things on different tools
    (`corpusType` is a 15-value filter on `search_us_statutes` and a board
    selector on `create_watch`) while others are genuinely identical everywhere
    (`act_id` carries the same 340-char description on seven tools, so one entry
    collapses all seven).
    """
    scoped = PARAM_DESCRIPTIONS_BY_TOOL.get((tool_name, param_name))
    if scoped is not None:
        return scoped
    return PARAM_DESCRIPTIONS.get(param_name)


def slim_input_schema(tool_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Return a new input schema with annotations compressed and nothing else.

    Pure: the input is never mutated. The caller assigns the result onto the
    component, which is the one place FastMCP's `mcp_component_fn` contract
    requires mutation.
    """
    slimmed = _strip_titles(schema)
    properties = slimmed.get("properties")
    if not isinstance(properties, dict):
        return slimmed

    new_properties: dict[str, Any] = {}
    for param_name, param in properties.items():
        if not isinstance(param, dict):
            new_properties[param_name] = param
            continue
        replacement = curated_description(tool_name, param_name)
        if replacement is None:
            new_properties[param_name] = param
            continue
        # Rebuild rather than copy-and-assign so key ORDER stays stable: a
        # reordered schema serializes differently and, like a reordered
        # tools/list, needlessly invalidates a provider's prompt-prefix cache.
        new_properties[param_name] = {
            key: (replacement if key == "description" else value)
            for key, value in param.items()
        }
        if "description" not in param:
            new_properties[param_name]["description"] = replacement

    return {**slimmed, "properties": new_properties}


def uncurated_overruns(
    tool_name: str, schema: dict[str, Any]
) -> list[tuple[str, int]]:
    """Parameters that INHERIT a description longer than the budget.

    The drift guard reads this. A parameter with a curated entry is skipped
    however long that entry is, and deliberately: the budget exists to catch
    prose arriving unreviewed from the OpenAPI, not to cap a decision somebody
    made on purpose. `create_watch.scope` is the case that proves it. Three
    mutually exclusive scope forms cannot be explained in 260 characters, and a
    watch created with the wrong one is a subscription that can never fire, so
    the right length there is longer than the budget rather than shorter.

    Returns `(param_name, length)` for each offender, so a newly bloated
    parameter fails the suite instead of quietly costing every caller tokens on
    every turn of every conversation.
    """
    overruns: list[tuple[str, int]] = []
    for param_name, param in (schema.get("properties") or {}).items():
        if not isinstance(param, dict):
            continue
        if curated_description(tool_name, param_name) is not None:
            continue
        description = param.get("description") or ""
        if len(description) > _DESCRIPTION_BUDGET:
            overruns.append((param_name, len(description)))
    return sorted(overruns)
