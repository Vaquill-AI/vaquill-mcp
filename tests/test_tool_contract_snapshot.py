"""A golden snapshot of the contract the PROVIDER actually publishes.

`test_schema_slim.py` guards our own transformation, and it does that well, but
it builds its schemas from the OpenAPI document. That leaves a hole exactly one
layer down: everything FastMCP's `OpenAPIProvider` does between the document and
the published tool is invisible to it.

The hole is not hypothetical. During the fastmcp 3 -> 4 upgrade a probe reported
that all 29 tools had lost their input schemas. It turned out to be the probe
(MCP SDK v2 renamed `inputSchema`, so `model_dump()` without `by_alias=True`
returns nothing), but nothing in the suite could have told the difference
between that and a real break, because no test compared provider OUTPUT across
versions.

So this file pins it. The golden file is the argument contract, annotations
stripped, generated from the live provider. Regenerate deliberately:

    python tests/test_tool_contract_snapshot.py --update

A diff here means a dependency upgrade changed what our tools accept. That is
sometimes fine and never automatic: read the diff before regenerating.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

import httpx2
import pytest
from fastmcp.server.providers.openapi import OpenAPIProvider

from vaquill_mcp.server import (
    _ROUTE_MAPS,
    _derive_mcp_names,
    _make_customize_component,
)

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_GOLDEN = _FIXTURES / "tool_contract.json"

# Annotation keywords, which this snapshot deliberately ignores: they are what
# the optimisation is allowed to rewrite. Everything else is the contract.
_ANNOTATIONS = {"description", "title"}


def _contract_only(node):
    if isinstance(node, list):
        return [_contract_only(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in _ANNOTATIONS:
            continue
        if key in ("properties", "$defs", "definitions", "patternProperties") and isinstance(
            value, dict
        ):
            out[key] = {name: _contract_only(sub) for name, sub in value.items()}
        else:
            out[key] = _contract_only(value)
    return out


async def _published_contract(jurisdiction: str) -> dict:
    spec = json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())
    provider = OpenAPIProvider(
        openapi_spec=spec,
        client=httpx2.AsyncClient(base_url="https://api.vaquill.ai"),
        mcp_names=_derive_mcp_names(spec),
        route_maps=_ROUTE_MAPS,
        mcp_component_fn=_make_customize_component({}),
        validate_output=False,
    )
    return {
        tool.name: {
            "method": tool._route.method,
            "path": tool._route.path,
            "inputSchema": _contract_only(tool.parameters),
        }
        for tool in sorted(await provider._list_tools(), key=lambda t: t.name)
    }


async def _current() -> dict:
    return {j: await _published_contract(j) for j in ("US", "IN")}


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_published_contract_matches_the_golden_snapshot(jurisdiction: str) -> None:
    golden = json.loads(_GOLDEN.read_text())
    current = await _published_contract(jurisdiction)

    assert set(current) == set(golden[jurisdiction]), (
        f"{jurisdiction}: the provider publishes a different SET of tools than the "
        f"snapshot. Added={sorted(set(current) - set(golden[jurisdiction]))}, "
        f"removed={sorted(set(golden[jurisdiction]) - set(current))}"
    )
    for tool in sorted(current):
        assert current[tool] == golden[jurisdiction][tool], (
            f"{jurisdiction} {tool}: the argument contract the provider publishes "
            "changed. If this followed a dependency upgrade, read the diff before "
            "regenerating: it is what every existing integration calls."
        )


def test_the_snapshot_is_not_empty() -> None:
    """A truncated golden file would make the parametrized tests vacuous."""
    golden = json.loads(_GOLDEN.read_text())
    assert len(golden["US"]) >= 20 and len(golden["IN"]) >= 5


if __name__ == "__main__":
    if "--update" in sys.argv:
        _GOLDEN.write_text(json.dumps(asyncio.run(_current()), indent=2, sort_keys=True) + "\n")
        print(f"wrote {_GOLDEN}")
    else:
        print(__doc__)
