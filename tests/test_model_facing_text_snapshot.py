"""Pin every string a model reads from this server.

`test_tool_contract_snapshot.py` pins the argument CONTRACT and deliberately
ignores `description` and `title`. That leaves the prose a model reads
unguarded, and most of it is not ours: parameter descriptions, `examples` and
enum text are fetched from the live OpenAPI document at start-up. A docstring
edit in the backend therefore changes what every connected agent reads, with no
release of this package, no review and no alert. That is the tool-poisoning
"rug pull" pattern with a legitimate owner.

This snapshot makes that change visible. It captures, for each jurisdiction:

* the server `instructions` and the `vaquill://guide` text;
* every tool's title, description and annotation title, plus every `description`,
  `title`, `examples`, `enum` and `default` inside its input schema;
* every prompt and resource description.

A failure here is not necessarily a bug. It means the text a model reads has
changed, and a person should read the diff. Regenerate deliberately:

    UPDATE_MODEL_FACING_TEXT=1 .venv/bin/python -m pytest tests/test_model_facing_text_snapshot.py
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest
from fastmcp import FastMCP

from tests.test_resources_and_prompts import _server
from vaquill_mcp.resources import guide_for

_GOLDEN = pathlib.Path(__file__).resolve().parent / "fixtures" / "model_facing_text.json"
_MODEL_KEYS = {"description", "title", "examples", "enum", "default"}


def _strings(node, path: str = "") -> dict[str, object]:
    """Every model-visible value inside a JSON schema, keyed by its path."""
    out: dict[str, object] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key in _MODEL_KEYS:
                out[here] = value
            elif key in ("properties", "$defs", "definitions"):
                for name, sub in (value or {}).items():
                    out.update(_strings(sub, f"{here}.{name}"))
            else:
                out.update(_strings(value, here))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            out.update(_strings(item, f"{path}[{index}]"))
    return out


async def _model_facing(server: FastMCP, jurisdiction: str) -> dict:
    tools = {}
    for tool in sorted(await server.list_tools(), key=lambda t: t.name):
        annotation_title = getattr(tool.annotations, "title", None)
        tools[tool.name] = {
            "title": tool.title,
            "annotationTitle": annotation_title,
            "description": tool.description,
            "schema": _strings(tool.parameters),
        }
    prompts = {
        p.name: {
            "description": p.description,
            "arguments": {a.name: a.description for a in (p.arguments or [])},
        }
        for p in sorted(await server.list_prompts(), key=lambda p: p.name)
    }
    resources = {
        str(r.uri): {"name": r.name, "description": r.description}
        for r in sorted(await server.list_resources(), key=lambda r: str(r.uri))
    }
    return {
        "instructions": server.instructions,
        "guide": guide_for(jurisdiction),
        "tools": tools,
        "prompts": prompts,
        "resources": resources,
    }


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_the_text_a_model_reads_matches_the_snapshot(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    current = await _model_facing(_server(jurisdiction, monkeypatch, respx_mock), jurisdiction)

    if os.environ.get("UPDATE_MODEL_FACING_TEXT"):
        golden = json.loads(_GOLDEN.read_text()) if _GOLDEN.exists() else {}
        golden[jurisdiction] = current
        _GOLDEN.write_text(json.dumps(golden, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        pytest.skip(f"wrote {jurisdiction} to {_GOLDEN.name}")

    golden = json.loads(_GOLDEN.read_text())[jurisdiction]
    assert set(current["tools"]) == set(golden["tools"]), (
        f"{jurisdiction}: the SET of tools changed. "
        f"Added={sorted(set(current['tools']) - set(golden['tools']))}, "
        f"removed={sorted(set(golden['tools']) - set(current['tools']))}"
    )
    changed = [
        f"tool {name}"
        for name in sorted(current["tools"])
        if current["tools"][name] != golden["tools"][name]
    ]
    changed += [f"prompt {n}" for n in sorted(current["prompts"]) if current["prompts"][n] != golden["prompts"].get(n)]
    changed += [f"resource {u}" for u in sorted(current["resources"]) if current["resources"][u] != golden["resources"].get(u)]
    if current["instructions"] != golden["instructions"]:
        changed.append("server instructions")
    if current["guide"] != golden["guide"]:
        changed.append("the vaquill://guide text")
    assert not changed, (
        f"{jurisdiction}: text a model reads has changed: {changed}. If the change is "
        "intended (or came from the backend's OpenAPI document), read the diff against "
        "tests/fixtures/model_facing_text.json for anything that steers the model, then "
        "regenerate with UPDATE_MODEL_FACING_TEXT=1."
    )


def test_the_snapshot_is_not_empty() -> None:
    """A truncated golden file would make the parametrized test vacuous."""
    golden = json.loads(_GOLDEN.read_text())
    assert len(golden["US"]["tools"]) >= 20 and len(golden["IN"]["tools"]) >= 5
    assert golden["US"]["instructions"] and golden["US"]["prompts"] and golden["US"]["resources"]
