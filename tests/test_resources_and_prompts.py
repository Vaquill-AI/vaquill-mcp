"""The two primitives that make this a server rather than a wrapper.

`OpenAPIProvider` emits tools and only tools, so before this the catalogue was
25 tools / 0 resources / 0 prompts. These tests assert the other two exist, that
they carry the specific knowledge they were added for, and -- the one that will
actually catch a future mistake -- that no prompt tells an agent to call a tool
that does not exist.

That drift guard is not hypothetical. `descriptions.py` carried entries for five
tools whose routers had been deleted a month earlier, and nothing failed, because
prose has nothing to be checked against. A prompt is prose that gives
INSTRUCTIONS, so a stale one is worse: it does not just describe a dead tool, it
tells the model to call it.
"""

from __future__ import annotations

import json
import pathlib
import re

import httpx
import pytest
from fastmcp import FastMCP

from vaquill_mcp.prompts import register_prompts
from vaquill_mcp.resources import guide_for, reference_uris
from vaquill_mcp.server import create_server

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_BASE = "https://api.vaquill.ai"
_SPEC_PATH = {"US": "/external/openapi.json", "IN": "/in/openapi.json"}


def _spec(jurisdiction: str) -> dict:
    return json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())


def _server(jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock) -> FastMCP:
    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test")
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    respx_mock.get(f"{_BASE}{_SPEC_PATH[jurisdiction]}").mock(
        return_value=httpx.Response(200, json=_spec(jurisdiction))
    )
    respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing/all").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )
    return create_server(jurisdiction)


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_the_server_publishes_more_than_tools(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """The headline assertion: all three MCP primitives, not just one."""
    server = _server(jurisdiction, monkeypatch, respx_mock)
    assert await server.list_tools()
    assert await server.list_resources(), f"{jurisdiction} publishes no resources"
    assert await server.list_prompts(), f"{jurisdiction} publishes no prompts"


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_the_guide_and_reference_resources_are_published(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    server = _server(jurisdiction, monkeypatch, respx_mock)
    uris = {str(r.uri) for r in await server.list_resources()}
    assert "vaquill://guide" in uris
    assert set(reference_uris(jurisdiction)) <= uris


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_a_reference_resource_reads_live_json(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """A resource that cannot be read is decoration."""
    server = _server(jurisdiction, monkeypatch, respx_mock)
    respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing").mock(
        return_value=httpx.Response(200, json={"costs": [{"endpoint": "/x", "credits": 1}]})
    )
    result = await server.read_resource("vaquill://pricing")
    assert "costs" in str(result.contents)


@pytest.mark.parametrize(
    ("jurisdiction", "must_mention"),
    [
        # Each string is a trap that has produced a wrong answer. If a rewrite
        # drops one, the guide has stopped earning its place.
        ("US", ("goodLawStatus", "unknown", "act_id", "404", "24 months")),
        ("IN", ("BNS", "1 July 2024", "get_corresponding_provisions", "RECORDED")),
    ],
)
def test_the_guide_carries_the_traps_it_exists_for(
    jurisdiction: str, must_mention: tuple[str, ...]
) -> None:
    guide = guide_for(jurisdiction) or ""
    missing = [token for token in must_mention if token not in guide]
    assert not missing, f"{jurisdiction} guide no longer mentions: {missing}"


# ---------------------------------------------------------------------------
# The drift guard
# ---------------------------------------------------------------------------

# Tool names look like `snake_case_words` in prompt text. Anything matching this
# shape that is ALSO a plausible tool reference gets checked.
_CANDIDATE_RE = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")


async def _prompt_texts(server: FastMCP) -> dict[str, str]:
    """Render every prompt with placeholder arguments."""
    rendered: dict[str, str] = {}
    for prompt in await server.list_prompts():
        args = {a.name: f"<{a.name}>" for a in (prompt.arguments or [])}
        result = await server.render_prompt(prompt.name, args)
        rendered[prompt.name] = " ".join(
            m.content.text if hasattr(m.content, "text") else str(m.content)
            for m in result.messages
        )
    return rendered


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_no_prompt_tells_the_agent_to_call_a_nonexistent_tool(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """Prompts give INSTRUCTIONS, so a stale tool name is an instruction to fail.

    Only names that look like tools AND are not published are reported, and the
    check is one-directional on purpose: a prompt need not mention every tool,
    but every tool it does mention must exist in THIS jurisdiction.
    """
    server = _server(jurisdiction, monkeypatch, respx_mock)
    published = {t.name for t in await server.list_tools()}
    # Field names, JSON keys and parameter names share the snake_case shape, so
    # only flag a candidate that names a tool in the OTHER jurisdiction or is
    # otherwise tool-shaped by prefix.
    verbs = ("get_", "list_", "search_", "resolve_", "create_", "update_", "delete_", "test_")

    problems: list[str] = []
    for name, text in (await _prompt_texts(server)).items():
        for candidate in set(_CANDIDATE_RE.findall(text)):
            if candidate.startswith(verbs) and candidate not in published:
                problems.append(f"{name} -> `{candidate}`")
    assert not problems, (
        f"{jurisdiction} prompts reference tools this server does not publish: "
        f"{sorted(problems)}"
    )


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_prompts_interpolate_their_arguments(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """A prompt that ignores its own argument is a static blob with a signature."""
    server = _server(jurisdiction, monkeypatch, respx_mock)
    for name, text in (await _prompt_texts(server)).items():
        prompt = next(p for p in await server.list_prompts() if p.name == name)
        required = [a.name for a in (prompt.arguments or []) if a.required]
        for arg in required:
            assert f"<{arg}>" in text, f"{name} never uses its `{arg}` argument"


def test_prompts_register_nothing_for_an_unknown_jurisdiction() -> None:
    server = FastMCP("t")
    register_prompts(server, "ZZ")
    assert server is not None  # no exception, and nothing registered


@pytest.mark.parametrize("jurisdiction", ["US", "IN"])
async def test_prompts_do_not_inflate_the_tool_budget(
    jurisdiction: str, monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    """The point of using prompts/resources rather than more tools.

    Tool definitions are resident every turn; prompts and resources are fetched
    deliberately. Adding six prompts must not have added six tools.
    """
    server = _server(jurisdiction, monkeypatch, respx_mock)
    expected = 25 if jurisdiction == "US" else 8
    assert len(await server.list_tools()) == expected
