"""CHARACTERIZATION of three silent fallbacks. Deliberate CURRENT behaviour.

THE CORE EXTRACTION WILL NOT KEEP THESE. They are pinned so the step that changes
each one is a visible, reviewed edit of this file, not a side effect of moving
code. When one of these tests fails after a planned change, update it to state the
new rule (refuse, raise, or pass the region) and delete the fallback's test here.
Do not "fix" the code to make an old assertion pass.

1. An unknown jurisdiction key does not fail. `instructions_for` hands back the
   UNITED STATES text, so a mistyped or lowercase key (`"in"`) silently serves
   the wrong country's instructions.
2. `register_prompts`, `register_aliases`, `register_resources` and `guide_for`
   do nothing for an unknown key. The server comes up with an empty surface and
   no error.
3. The `vaquill://pricing` reference resource sends no `?region=`. It used to
   read the US route for BOTH jurisdictions, so the India server's pricing
   resource showed United States prices; each jurisdiction now reads its own
   route (US `/api/v1/api-credits/pricing`, India `/api/v1/in/api-credits/pricing`).

`create_server("ZZ")` itself still refuses an unknown key, in `config`; these
fallbacks are in the lower layers a refactor will rewire.
"""

from __future__ import annotations

import httpx
import httpx2
import pytest
from fastmcp import FastMCP

from tests.test_resources_and_prompts import _BASE, _server
from vaquill_mcp.aliases import register_aliases
from vaquill_mcp.instructions import instructions_for
from vaquill_mcp.prompts import register_prompts
from vaquill_mcp.resources import guide_for, register_resources


@pytest.mark.parametrize("unknown", ["ZZ", "", "in", "us", "India", "UK"])
def test_current_behaviour_an_unknown_key_gets_the_united_states_instructions(
    unknown: str,
) -> None:
    """Includes lowercase `in`: an India server asked in the wrong case reads US text."""
    assert instructions_for(unknown) == instructions_for("US")
    assert instructions_for(unknown) != instructions_for("IN")


async def test_current_behaviour_an_unknown_key_registers_nothing_and_raises_nothing() -> None:
    client = httpx2.AsyncClient(base_url=_BASE)
    mcp = FastMCP("fallback-probe")

    register_prompts(mcp, "ZZ")
    register_aliases(mcp, client, "ZZ", _BASE, set())
    register_resources(mcp, client, "ZZ")

    assert await mcp.list_prompts() == []
    assert await mcp.list_tools() == []
    assert await mcp.list_resources() == []
    assert guide_for("ZZ") is None
    await client.aclose()


@pytest.mark.parametrize(
    ("jurisdiction", "path"),
    [("US", "/api/v1/api-credits/pricing"), ("IN", "/api/v1/in/api-credits/pricing")],
)
async def test_the_pricing_resource_reads_its_own_jurisdictions_route(
    jurisdiction: str, path: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    server = _server(jurisdiction, monkeypatch, respx_mock)
    route = respx_mock.get(f"{_BASE}{path}").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )

    await server.read_resource("vaquill://pricing")

    assert route.call_count == 1
    sent = route.calls.last.request
    assert sent.url.path == path
    assert sent.url.query == b"", "the route is the jurisdiction; no region is sent"
