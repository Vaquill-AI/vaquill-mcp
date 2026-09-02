"""What the hosted and stdio servers actually publish, derived from the spec.

`remote.py` stopped declaring tools by hand on 2026-09-01 and now builds its
catalogue from the published OpenAPI document, exactly as `server.py` does. The
tests that used to read `@mcp.tool` decorators out of `remote.py` therefore had
nothing left to read, which is the intended outcome: the drift they guarded
against is no longer expressible.

What still needs asserting is the DERIVED catalogue:

* every tool it produces has a curated description, so nothing ships with a raw
  multi-paragraph OpenAPI blurb;
* the US and India catalogues do not overlap, because one deployment serves one
  jurisdiction and a shared tool name would mean a document leaked;
* the search tool can still express a state-scoped query, which is the specific
  capability a previous hand-written `Literal["USC", "CFR"]` silently removed.

The fixtures are the real published documents, captured from the running app.
Regenerate them from the backend repo with:

    python -c "import json,pathlib; from app.main import external_docs_app, india_docs_app; \
      pathlib.Path('openapi_us.json').write_text(json.dumps(external_docs_app.openapi(), indent=2, sort_keys=True)); \
      pathlib.Path('openapi_in.json').write_text(json.dumps(india_docs_app.openapi(), indent=2, sort_keys=True))"
"""

from __future__ import annotations

import json
import pathlib

import pytest

from vaquill_mcp.descriptions import TOOL_DESCRIPTIONS
from vaquill_mcp.server import _derive_mcp_names

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _spec(jurisdiction: str) -> dict:
    return json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())


def _catalogue(spec: dict) -> set[str]:
    """Every tool name the OpenAPIProvider will publish for this document.

    Two sources, and the second one is easy to forget: `_derive_mcp_names`
    returns renames only. An operation carrying an explicit `operation_id` has
    no `_api_v1_` marker, is deliberately left out of that map, and FastMCP then
    uses the operationId verbatim. Counting only the map under-reports the
    catalogue by exactly those tools (`resolve_statute_citation` and
    `resolve_statute_citations_batch` on the live US document).
    """
    mapped = _derive_mcp_names(spec)
    names = set(mapped.values())
    for item in spec["paths"].values():
        for op in item.values():
            if isinstance(op, dict) and (oid := op.get("operationId")):
                if oid not in mapped:
                    names.add(oid)
    return names


US = _catalogue(_spec("US"))
IN = _catalogue(_spec("IN"))


def test_both_catalogues_are_populated() -> None:
    """Guard the guard: an empty catalogue makes everything below vacuous."""
    assert US, "US document produced no tools"
    assert IN, "India document produced no tools"


def test_catalogues_do_not_overlap() -> None:
    """One deployment serves one jurisdiction.

    A shared tool name would mean the two documents overlap, which the backend
    asserts they do not. Catching it here as well is cheap, and this is the side
    that would actually hand a user the wrong jurisdiction's tool.
    """
    assert not (US & IN), f"tool name published by both jurisdictions: {sorted(US & IN)}"


@pytest.mark.parametrize("tool", sorted(US | IN))
def test_every_derived_tool_has_a_curated_description(tool: str) -> None:
    assert tool in TOOL_DESCRIPTIONS, (
        f"{tool!r} is derived from the OpenAPI but has no entry in "
        "descriptions.py, so it would ship with the raw endpoint description."
    )


def test_no_description_describes_a_tool_that_no_longer_exists() -> None:
    """The other direction, which is how the five dead tools survived.

    `search_legal_cases` and friends kept their descriptions long after
    `/research/*` was deleted. A description with no tool is the residue of a
    retirement someone only half-finished.
    """
    orphans = sorted(set(TOOL_DESCRIPTIONS) - (US | IN))
    assert not orphans, (
        f"descriptions.py describes tools no document produces: {orphans}. "
        "Delete them, or the endpoint they described is missing from the spec."
    )


def test_statute_search_can_still_scope_to_a_state() -> None:
    """A state-scoped search must remain expressible.

    A hand-written `Literal["USC", "CFR"]` once narrowed this to the two federal
    corpora, so no state corpus was reachable and the failure had no error the
    caller could read: the tool simply could not express the request. Deriving
    from the spec removes the chance to retype the enum, so this asserts the
    SPEC still carries it.
    """
    spec = _spec("US")
    search = spec["paths"]["/api/v1/us/statutes/search"]["post"]
    body = json.dumps(search)
    assert "corpusType" in body or "corpus_type" in body, (
        "the statutes search operation no longer accepts a corpus type"
    )
    assert "state" in body, "the statutes search operation no longer accepts a state"
