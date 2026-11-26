"""Pin the whole surface of the hosted Indian-tribunals server.

The tribunals server had no golden at all. This one holds the text a model reads
AND what a client also depends on:

* the instructions and every tool's text and schema strings;

* the server name, `websiteUrl` and icons a consent screen shows;
* the order `tools/list` returns, as the wire delivers it;
* every tool's full wire object: annotations, `inputSchema` structure, tags;
* the descriptions with the live price lines appended;
* the root app's `/health` body.

A failure is not necessarily a bug. It means something a client reads or relies
on has changed, and a person should read the diff. Regenerate deliberately:

    UPDATE_TRIBUNALS_SURFACE=1 .venv/bin/python -m pytest tests/test_tribunals_surface_snapshot.py
"""

from __future__ import annotations

import json

import httpx
import httpx2
import pytest

from tests.surface_helpers import FIXTURES, check_golden, wire_surface
from vaquill_mcp import __version__
from vaquill_mcp.tribunals import create_tribunals_server, tribunals_costs

_GOLDEN = FIXTURES / "tribunals_model_facing_text.json"
_ENV = "UPDATE_TRIBUNALS_SURFACE"
_BASE = "https://api.vaquill.ai"
# The six rows the startup fetch expects, as test_tribunals_remote.py serves them.
_PRICES = [
    {"endpoint": "/in/tribunals/forums", "operation": "Indian Tribunal Registry", "credits": 0},
    {"endpoint": "/in/tribunals/matters", "operation": "Indian Tribunal Matter Browse", "credits": 1},
    {
        "endpoint": "/in/tribunals/matters/search",
        "operation": "Indian Tribunal Matter Search",
        "credits": 2,
    },
    {
        "endpoint": "/in/tribunals/matters/detail",
        "operation": "Indian Tribunal Matter Detail",
        "credits": 1,
    },
    {
        "endpoint": "/in/tribunals/matters/orders",
        "operation": "Indian Tribunal Matter Orders",
        "credits": 1,
    },
    {
        "endpoint": "/in/tribunals/matters/file",
        "operation": "Indian Tribunal Matter Document",
        "credits": 1,
    },
]
_OAUTH_ENV = (
    "VAQUILL_OAUTH_UPSTREAM_JWKS_URI",
    "VAQUILL_OAUTH_UPSTREAM_ISSUER",
    "VAQUILL_OAUTH_AUTHORIZE_URL",
    "VAQUILL_OAUTH_TOKEN_URL",
    "VAQUILL_OAUTH_CLIENT_ID",
    "VAQUILL_OAUTH_CLIENT_SECRET",
    "VAQUILL_PUBLIC_URL",
    "VAQUILL_INTERNAL_SECRET",
)


@pytest.fixture(autouse=True)
def _bearer_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bearer-only and on the default public origin, whatever the shell holds.

    `VAQUILL_PUBLIC_URL` is cleared on purpose: the square icon is built from it,
    so a developer's own value would change the golden.
    """
    for name in _OAUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test")


def _server(**kwargs: object):
    client = httpx2.AsyncClient(base_url=_BASE, headers={"Authorization": "Bearer vq_key_test"})
    return create_tribunals_server(client, **kwargs)


async def _health(respx_mock) -> dict:
    from vaquill_mcp.tribunals_main import build_app

    respx_mock.get(f"{_BASE}/api/v1/in/api-credits/pricing").mock(
        return_value=httpx.Response(200, json={"costs": _PRICES})
    )
    app = build_app()
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://tribunals-mcp.vaquill.ai"
        ) as raw:
            response = await raw.get("/health")
    body = response.json()
    assert body.pop("version") == __version__, "the /health version must be the package version"
    return {
        "status": response.status_code,
        "contentType": response.headers.get("content-type"),
        "body": body,
    }


async def test_the_tribunals_surface_matches_the_snapshot(respx_mock) -> None:
    surface = await wire_surface(_server())
    priced = await _server(costs=tribunals_costs(_PRICES)).list_tools()
    surface["descriptionsWithPrices"] = {t.name: t.description for t in priced}
    surface["health"] = await _health(respx_mock)

    check_golden(surface, _GOLDEN, _ENV)


def test_the_snapshot_is_not_empty() -> None:
    """A truncated golden would make the comparison vacuous."""
    golden = json.loads(_GOLDEN.read_text())
    assert len(golden["toolOrder"]) == 6 and golden["server"]["instructions"]
    assert golden["server"]["info"]["name"] == "Vaquill Indian Tribunals"
    assert golden["health"]["body"]["corpus"] == "tribunals"
    assert all(t["annotations"] and t["inputSchema"] for t in golden["tools"].values())
    assert set(golden["descriptionsWithPrices"]) == set(golden["tools"])

