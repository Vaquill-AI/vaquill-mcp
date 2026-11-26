"""Pin the shape of what `create_server` publishes, not the prose of it.

`test_tool_contract_snapshot.py` builds an `OpenAPIProvider` directly and strips
annotations, tags and the two code-registered aliases. `test_model_facing_text_snapshot.py`
goes through `create_server` but sorts tools by name and records only the
annotation TITLE and the strings inside each schema. That leaves four things a
refactor could move without a red test:

* the server name (a user running both servers tells them apart by it);
* the `tools/list` order a client is actually served, which the provider
  prompt-prefix cache is keyed on;
* the four annotation hints and the tags on every tool, which clients use to
  decide what to auto-approve and how to group the catalogue;
* the full `inputSchema` of `search` and `fetch`, the generic aliases that
  research-style clients match on. They are registered in code, so they appear
  in no OpenAPI fixture and no other golden holds their types or `required`.

Everything here comes from the committed OpenAPI fixtures; nothing touches the
network. The tool list is read through an in-memory MCP client, so it is the
list a client receives after the middleware has run, not the provider's own.

A failure is not necessarily a bug: it means the published surface moved and a
person should read the diff. Regenerate deliberately:

    UPDATE_SERVER_SURFACE=1 .venv/bin/python -m pytest tests/test_server_surface_snapshot.py
"""

from __future__ import annotations

import difflib
import json
import os
import pathlib

import pytest
from fastmcp import Client, FastMCP

from tests.test_resources_and_prompts import _server

_GOLDEN = pathlib.Path(__file__).resolve().parent / "fixtures" / "server_surface.json"
_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
_ALIASES = ("search", "fetch")


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _unified(label: str, golden: object, current: object) -> str:
    return "\n".join(
        difflib.unified_diff(
            _dump(golden).splitlines(),
            _dump(current).splitlines(),
            fromfile=f"golden/{label}",
            tofile=f"current/{label}",
            lineterm="",
            n=2,
        )
    )


async def _surface(server: FastMCP) -> dict:
    """The served catalogue: name, ordered tools, hints and tags, alias schemas."""
    async with Client(server) as client:
        served = await client.list_tools()

    tools = []
    for tool in served:  # served order, deliberately not re-sorted
        annotations = tool.annotations.model_dump(by_alias=True) if tool.annotations else {}
        tools.append(
            {
                "name": tool.name,
                **{hint: annotations.get(hint) for hint in _HINTS},
                "tags": sorted(((tool.meta or {}).get("fastmcp") or {}).get("tags") or []),
            }
        )
    return {
        "serverName": server.name,
        "tools": tools,
        # MCP SDK v2 renamed `inputSchema` to `input_schema` and deprecates the
        # old spelling, which CI turns into an error (`-W error::DeprecationWarning`).
        # The py3.10 leg installs an SDK that only has the old name, so read
        # whichever exists rather than choosing one and failing the other leg.
        "aliasInputSchemas": {
            tool.name: (
                tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
            )
            for tool in served
            if tool.name in _ALIASES
        },
    }


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_the_published_surface_matches_the_snapshot(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    current = await _surface(_server(jurisdiction, monkeypatch, respx_mock))

    if os.environ.get("UPDATE_SERVER_SURFACE"):
        golden = json.loads(_GOLDEN.read_text()) if _GOLDEN.exists() else {}
        golden[jurisdiction] = current
        _GOLDEN.write_text(_dump(golden) + "\n")
        pytest.skip(f"wrote {jurisdiction} to {_GOLDEN.name}")

    golden = json.loads(_GOLDEN.read_text())[jurisdiction]

    current_names = [t["name"] for t in current["tools"]]
    golden_names = [t["name"] for t in golden["tools"]]
    assert set(current_names) == set(golden_names), (
        f"{jurisdiction}: the SET of tools changed. "
        f"Added={sorted(set(current_names) - set(golden_names))}, "
        f"removed={sorted(set(golden_names) - set(current_names))}"
    )
    assert current_names == golden_names, (
        f"{jurisdiction}: tools/list is served in a different ORDER. A reorder "
        "invalidates a client's cached prompt prefix.\n"
        + _unified("order", golden_names, current_names)
    )
    assert current["serverName"] == golden["serverName"], (
        f"{jurisdiction}: server name changed from {golden['serverName']!r} to "
        f"{current['serverName']!r}"
    )

    by_name = {t["name"]: t for t in golden["tools"]}
    drifted = [t["name"] for t in current["tools"] if t != by_name[t["name"]]]
    assert not drifted, (
        f"{jurisdiction}: annotation hints or tags changed on {drifted}.\n"
        + "\n".join(
            _unified(
                name,
                by_name[name],
                next(t for t in current["tools"] if t["name"] == name),
            )
            for name in drifted
        )
    )

    for alias in _ALIASES:
        assert current["aliasInputSchemas"].get(alias) == golden["aliasInputSchemas"].get(alias), (
            f"{jurisdiction}: the `{alias}` inputSchema changed.\n"
            + _unified(
                alias,
                golden["aliasInputSchemas"].get(alias),
                current["aliasInputSchemas"].get(alias),
            )
        )


def test_the_snapshot_is_not_empty() -> None:
    """A truncated golden would make the parametrized test vacuous."""
    golden = json.loads(_GOLDEN.read_text())
    assert set(golden) == {"US", "IN"}
    for jurisdiction, minimum in (("US", 50), ("IN", 20)):
        surface = golden[jurisdiction]
        assert surface["serverName"].startswith("Vaquill Legal Research")
        assert len(surface["tools"]) >= minimum
        assert set(surface["aliasInputSchemas"]) == set(_ALIASES)
        # The code-registered aliases carry no tags; the derived tools do.
        assert any(t["tags"] for t in surface["tools"]), f"{jurisdiction}: every tool lost its tags"
