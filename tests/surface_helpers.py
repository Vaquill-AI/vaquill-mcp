"""Shared helpers for the whole-surface golden snapshots.

Not a test module (no `test_` prefix), so pytest never collects it. It holds the
two things every surface snapshot needs and that must behave identically:

* `strings`, the walker that lists every model-visible value inside a JSON schema;
* `check_golden`, which compares a fresh dump with a committed fixture, fails with
  a readable unified diff, and regenerates ONLY when the caller's environment
  variable is set.

A golden here is a statement that today's behaviour is the baseline. A failure is
not necessarily a bug. It means a person has to read the diff and decide.
"""

from __future__ import annotations

import difflib
import json
import os
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
MODEL_KEYS = {"description", "title", "examples", "enum", "default"}
_DIFF_LINES = 120


def strings(node: object, path: str = "") -> dict[str, object]:
    """Every model-visible value inside a JSON schema, keyed by its path."""
    out: dict[str, object] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key in MODEL_KEYS:
                out[here] = value
            elif key in ("properties", "$defs", "definitions"):
                for name, sub in (value or {}).items():
                    out.update(strings(sub, f"{here}.{name}"))
            else:
                out.update(strings(value, here))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            out.update(strings(item, f"{path}[{index}]"))
    return out


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def check_golden(
    current: object,
    golden: pathlib.Path,
    env_var: str,
    *,
    key: str | None = None,
) -> None:
    """Compare `current` with the committed fixture, or rewrite it on request.

    `key` stores the value under one entry of a larger object, so a parametrized
    test can own one row of a shared file. The comparison is on parsed JSON, so
    only a change of VALUE fails, never a change of formatting.
    """
    # Round-trip so tuples and sets compare as the lists they are written as.
    fresh = json.loads(json.dumps(current, sort_keys=True))

    if os.environ.get(env_var):
        whole = json.loads(golden.read_text()) if key is not None and golden.exists() else {}
        if key is None:
            whole = fresh
        else:
            whole[key] = fresh
        golden.write_text(_dump(whole))
        pytest.skip(f"wrote {golden.name}" + (f" [{key}]" if key else ""))

    stored = json.loads(golden.read_text())
    expected = stored if key is None else stored.get(key)
    if expected == fresh:
        return

    diff = list(
        difflib.unified_diff(
            _dump(expected).splitlines(),
            _dump(fresh).splitlines(),
            fromfile=f"golden {golden.name}" + (f" [{key}]" if key else ""),
            tofile="current",
            lineterm="",
            n=2,
        )
    )
    shown = "\n".join(diff[:_DIFF_LINES])
    more = f"\n... {len(diff) - _DIFF_LINES} more diff lines" if len(diff) > _DIFF_LINES else ""
    pytest.fail(
        f"{golden.name}{' [' + key + ']' if key else ''} no longer matches current behaviour. "
        f"Read the diff. If the change is intended, regenerate with {env_var}=1.\n{shown}{more}",
        pytrace=False,
    )


async def wire_surface(server: object) -> dict:
    """What a connecting client is actually told, over the in-process wire.

    Goes through `Client`, not `server.list_tools()`, so the middleware that
    orders `tools/list` is in the path and the order recorded is the order a
    client receives. The version is withheld: it moves with every release.
    """
    from fastmcp import Client

    async with Client(server) as client:
        tools = await client.list_tools()
        info = client.server_info.model_dump(mode="json", by_alias=True, exclude_none=True)
        info.pop("version", None)
        return {
            "server": {"info": info, "instructions": client.instructions},
            "toolOrder": [t.name for t in tools],
            "tools": {
                t.name: t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools
            },
            "schemaStrings": {t.name: strings(t.input_schema) for t in tools},
        }
