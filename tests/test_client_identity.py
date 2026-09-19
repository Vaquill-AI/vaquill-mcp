"""The calling MCP client reaches `api_request_logs`, or it is unknowable.

This server is a proxy, and it used to stamp ONE fixed `User-Agent` on every
outbound call. Claude, Codex, Gemini and a curl script all landed in the same
bucket, so the client mix was unanswerable: not uninstrumented, overwritten.

The assertions that matter here are on the OUTBOUND request, because that is
the only place the value can be verified to have survived the proxy. A unit
test of the formatter would pass just as happily while the hook never fired.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
from collections.abc import AsyncIterator

import httpx
import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import Implementation

from vaquill_mcp.client_identity import _clean, calling_client, with_client

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_BASE = "https://api.vaquill.ai"
_FREE_TOOL = "list_statutes_coverage"


def _spec(jurisdiction: str) -> dict:
    name = "openapi_us.json" if jurisdiction == "US" else "openapi_in.json"
    return json.loads((_FIXTURES / name).read_text())


@pytest.fixture
def _live_api(monkeypatch: pytest.MonkeyPatch, respx_mock) -> None:
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    for jurisdiction, path in (("US", "/external/openapi.json"), ("IN", "/in/openapi.json")):
        respx_mock.get(f"{_BASE}{path}").mock(
            return_value=httpx.Response(200, json=_spec(jurisdiction))
        )
    respx_mock.get(url__regex=rf"{_BASE}/api/v1/api-credits/pricing.*").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )


@pytest.fixture
def _captured_agent(respx_mock) -> list[str | None]:
    """Record the User-Agent on each outgoing call to the API."""
    seen: list[str | None] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent"))
        return httpx.Response(200, json={"states": [], "totalSections": 0})

    respx_mock.get(url__regex=rf"{_BASE}/api/v1/us/statutes/coverage.*").mock(
        side_effect=_record
    )
    return seen


@contextlib.asynccontextmanager
async def _serving() -> AsyncIterator[object]:
    from vaquill_mcp.remote_main import build_app

    app = build_app()
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(app.router.lifespan_context(app))
        yield app


def _client(app: object, client_info: Implementation | None) -> Client:
    def factory(**kwargs) -> httpx2.AsyncClient:
        kwargs.pop("transport", None)
        kwargs.pop("base_url", None)
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="https://mcp.vaquill.ai",
            **kwargs,
        )

    return Client(
        StreamableHttpTransport(
            url="https://mcp.vaquill.ai/s/vq_key_test",
            httpx_client_factory=factory,
        ),
        client_info=client_info,
    )


async def test_the_calling_client_reaches_the_api(
    _live_api: None, _captured_agent: list[str | None]
) -> None:
    """End to end: a real handshake declaring a client, through to the outbound
    header the backend will store in `api_request_logs.user_agent`."""
    async with _serving() as app:
        async with _client(app, Implementation(name="claude-ai", version="1.2.3")) as c:
            await c.call_tool(_FREE_TOOL, {})

    assert _captured_agent, "the tool never reached the API"
    agent = _captured_agent[-1]
    assert agent is not None
    assert agent.startswith("vaquill-mcp-remote/"), agent
    assert agent.endswith(" (client=claude-ai/1.2.3)"), agent


async def test_two_clients_are_told_apart(
    _live_api: None, _captured_agent: list[str | None]
) -> None:
    """The whole point. One bucket per client, not one bucket for all of them."""
    for name, version in (("claude-ai", "1.2.3"), ("codex", "0.9"), ("gemini-cli", "2.0")):
        async with _serving() as app:
            async with _client(app, Implementation(name=name, version=version)) as c:
                await c.call_tool(_FREE_TOOL, {})

    stamped = [a for a in _captured_agent if a and "(client=" in a]
    assert len(stamped) == 3, _captured_agent
    assert len({a.split("(client=")[1] for a in stamped}) == 3, stamped


async def test_an_unknown_client_sends_the_plain_agent(
    _live_api: None, _captured_agent: list[str | None]
) -> None:
    """A client declaring nothing must not produce a half-built agent.

    `(client=)` or `(client=None)` would be worse than the unstamped value: it
    reads as a client named nothing rather than as an absence, and the
    analytics query would happily count it as one.
    """
    async with _serving() as app:
        async with _client(app, Implementation(name="", version="")) as c:
            await c.call_tool(_FREE_TOOL, {})

    agent = _captured_agent[-1]
    assert agent is not None
    assert "(client=" not in agent, agent
    assert agent.startswith("vaquill-mcp-remote/"), agent


async def test_a_hostile_client_name_cannot_forge_a_header(
    _live_api: None, _captured_agent: list[str | None]
) -> None:
    """`clientInfo` is self-declared and nothing verifies it.

    It travels into a header, a database column and an admin table, so the
    header is the checkpoint. What has to die here is STRUCTURE, not spelling:
    a CR or LF is header injection, a `)` closes the UA comment early so the
    rest can pose as its own token, a `:` or a space lets it read as a separate
    header or agent product, and an unbounded name bloats every request on the
    connection.

    The word "X-Admin" surviving as literal text inside the comment is fine and
    is asserted to be inert: it is characters in one header value, not a header.
    """
    hostile = Implementation(
        name="evil\r\nX-Admin: 1 (spoof) " + "A" * 200,
        version="9.9\r\nZ: 1",
    )
    async with _serving() as app:
        async with _client(app, hostile) as c:
            await c.call_tool(_FREE_TOOL, {})

    agent = _captured_agent[-1]
    assert agent is not None
    # Exactly one comment, correctly closed, with the real product first.
    assert agent.startswith("vaquill-mcp-remote/")
    assert agent.count("(client=") == 1
    assert agent.count("(") == 1 and agent.count(")") == 1
    assert agent.endswith(")")

    inner = agent.split("(client=")[1].rstrip(")")
    for forbidden in ("\r", "\n", ":", " ", "(", ")", ",", ";"):
        assert forbidden not in inner, (forbidden, agent)

    name, _, version = inner.partition("/")
    assert len(name) <= 40, name
    assert len(version) <= 20, version
    # Inert, not absent: the point is that it cannot act, not that it is unsaid.
    assert "X-Admin" in name


def test_cleaning_keeps_distinct_names_distinct() -> None:
    """Dropping bad characters instead of collapsing them would merge clients.

    "claude ai" and "claudeai" are two names; if the space simply vanished they
    would become one row in the client mix, and the merge would be invisible.
    """
    assert _clean("claude ai", 40) != _clean("claudeai", 40)
    assert _clean("claude ai", 40) == "claude-ai"
    assert _clean("...", 40) == ""
    assert _clean(None, 40) == ""
    assert _clean(12345, 40) == ""
    # A truncation must not leave the separator dangling at the end.
    assert not _clean("ab" + "-" * 10 + "cd", 5).endswith("-")


def test_no_request_context_is_an_absence_not_an_error() -> None:
    """The startup fetches run outside any MCP request.

    `calling_client()` is called on every outbound request including those, so
    "no context" has to be a quiet None. Raising here would fail a billable
    call to record who made it.
    """
    assert calling_client() is None
    assert with_client("vaquill-mcp/1.0", None) == "vaquill-mcp/1.0"
    assert with_client("vaquill-mcp/1.0", "x/1") == "vaquill-mcp/1.0 (client=x/1)"
