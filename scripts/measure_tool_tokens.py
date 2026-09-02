#!/usr/bin/env python3
"""Measure what this server's tool catalogue actually costs a model.

    python scripts/measure_tool_tokens.py                  # both jurisdictions
    python scripts/measure_tool_tokens.py --jurisdiction US --per-tool
    python scripts/measure_tool_tokens.py --live           # against the live API

WHY THIS EXISTS
===============

Every published per-tool figure is somebody else's harness: ~403 tokens for
Anthropic's trivial `get_weather`, ~470 measured on a verbose GitHub-style
server, 550-1,400 in MCP issue tracking, ~534 derived internally from the
workspace API's 133 operations. Spreads like that decide nothing, and the
question they are meant to decide -- whether this catalogue needs deferred
loading -- has a hard threshold attached (10+ tools, or definitions over 10K
tokens).

So measure it. With `ANTHROPIC_API_KEY` set this makes exactly two
`count_tokens` calls, one with the `tools` array and one without, and the
difference is the real resident cost of the catalogue. `count_tokens`
deliberately ignores prompt caching, which is the right basis here: caching
hides the cost from the invoice, never from the model's working memory or from
its tool-selection accuracy.

With no key it still reports the structural breakdown, which is what actually
drives the optimisation: bytes are a fine proxy for WHERE the cost is even
though they are a poor one for HOW MUCH.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import httpx2  # noqa: E402
from fastmcp.server.providers.openapi import OpenAPIProvider  # noqa: E402

from vaquill_mcp.config import _SPEC_PATHS, get_base_url  # noqa: E402
from vaquill_mcp.server import (  # noqa: E402
    _ROUTE_MAPS,
    _derive_mcp_names,
    _fetch_openapi_spec,
    _make_customize_component,
)

_FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures"

# Anthropic's threshold for reaching for progressive disclosure at all.
_DEFER_THRESHOLD_TOKENS = 10_000


def _load_spec(jurisdiction: str, live: bool) -> dict:
    if live:
        return _fetch_openapi_spec(get_base_url(), jurisdiction)
    return json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())


async def _catalogue(spec: dict, slim: bool) -> list[dict]:
    """The tool definitions this server would publish, in Anthropic wire shape.

    `slim=False` rebuilds the catalogue with the schema optimisation disabled,
    which is how the before/after column is produced from one run.
    """
    if slim:
        tools = await _provider(spec, _make_customize_component({}))._list_tools()
    else:
        # Patch the name in `server`, NOT in `schema_slim`. server.py does
        # `from vaquill_mcp.schema_slim import slim_input_schema`, which binds
        # the function into server's own namespace at import time, so replacing
        # the attribute on the defining module is a no-op that silently reports
        # a 0.0% saving instead of failing.
        import vaquill_mcp.server as server_module

        original = server_module.slim_input_schema
        server_module.slim_input_schema = lambda _name, schema: schema
        try:
            tools = await _provider(
                spec, server_module._make_customize_component({})
            )._list_tools()
        finally:
            server_module.slim_input_schema = original

    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.parameters,
        }
        for tool in sorted(tools, key=lambda t: t.name)
    ]


def _provider(spec: dict, component_fn) -> OpenAPIProvider:
    return OpenAPIProvider(
        openapi_spec=spec,
        client=httpx2.AsyncClient(base_url="https://api.vaquill.ai"),
        mcp_names=_derive_mcp_names(spec),
        route_maps=_ROUTE_MAPS,
        mcp_component_fn=component_fn,
        validate_output=False,
    )


def _count_tokens_live(tools: list[dict], model: str) -> int | None:
    """Exact catalogue cost via two `count_tokens` calls, or None with no key.

    The catalogue cost is the DIFFERENCE, not the with-tools number: the second
    call carries the same one-word message plus the system overhead, so
    subtracting removes both and leaves the tools.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    url = "https://api.anthropic.com/v1/messages/count_tokens"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    message = [{"role": "user", "content": "hi"}]

    def _count(payload: dict) -> int:
        response = httpx2.post(url, headers=headers, json=payload, timeout=60.0)
        response.raise_for_status()
        return response.json()["input_tokens"]

    with_tools = _count({"model": model, "messages": message, "tools": tools})
    without = _count({"model": model, "messages": message})
    return with_tools - without


def _bytes(obj: object) -> int:
    return len(json.dumps(obj, separators=(",", ":")))


def _report(jurisdiction: str, slim: list[dict], fat: list[dict], args) -> None:
    slim_bytes = _bytes(slim)
    fat_bytes = _bytes(fat)
    print(f"\n=== {jurisdiction}: {len(slim)} tools ===")
    print(f"  definition bytes   {fat_bytes:>8,}  ->  {slim_bytes:>8,}   "
          f"({(slim_bytes - fat_bytes) / fat_bytes:+.1%})")

    measured = _count_tokens_live(slim, args.model)
    if measured is None:
        estimate = slim_bytes // 4
        print(f"  tokens             ~{estimate:,} ESTIMATED at 4 bytes/token.")
        print("                     Set ANTHROPIC_API_KEY for the exact figure; do not")
        print("                     make the deferred-loading call on this number.")
    else:
        print(f"  tokens (measured)   {measured:>8,}   "
              f"({measured / max(len(slim), 1):.0f}/tool, model={args.model})")
    if measured is not None and measured > _DEFER_THRESHOLD_TOKENS:
        print(f"  -> OVER the {_DEFER_THRESHOLD_TOKENS:,}-token threshold. Mark the 3-5")
        print("     highest-traffic tools resident and defer the rest AT THE CLIENT /")
        print("     API layer. Do not build discovery into this server: GitHub deleted")
        print("     exactly that (PR #2512) as superseded by client-level tool search.")
    elif measured is not None:
        print(f"  -> under the {_DEFER_THRESHOLD_TOKENS:,}-token threshold; deferred "
              "loading not warranted yet.")

    if args.per_tool:
        print(f"\n  {'tool':<36} {'bytes':>8} {'share':>7}")
        for tool in sorted(slim, key=lambda t: -_bytes(t)):
            size = _bytes(tool)
            print(f"  {tool['name']:<36} {size:>8,} {size / slim_bytes:>6.1%}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jurisdiction", choices=sorted(_SPEC_PATHS), default=None)
    parser.add_argument("--live", action="store_true", help="fetch the published spec")
    parser.add_argument("--per-tool", action="store_true")
    parser.add_argument("--model", default="claude-sonnet-5")
    args = parser.parse_args()

    for jurisdiction in [args.jurisdiction] if args.jurisdiction else sorted(_SPEC_PATHS):
        spec = _load_spec(jurisdiction, args.live)
        _report(
            jurisdiction,
            await _catalogue(spec, slim=True),
            await _catalogue(spec, slim=False),
            args,
        )
    print()


if __name__ == "__main__":
    asyncio.run(main())
