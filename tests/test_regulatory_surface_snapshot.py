"""Pin the whole surface of the hosted Indian-regulatory server.

`regulatory_model_facing_text.json` already pins the instructions and each tool's
text. It does not pin what a client also depends on, and that is what this adds:

* the server name, `websiteUrl` and icons a consent screen shows;
* the order `tools/list` returns, as the wire delivers it;
* every tool's full wire object: annotations, `inputSchema` structure, tags;
* the descriptions with the live price lines appended;
* the root app's `/health` body.

A failure is not necessarily a bug. It means something a client reads or relies
on has changed, and a person should read the diff. Regenerate deliberately:

    UPDATE_REGULATORY_SURFACE=1 .venv/bin/python -m pytest tests/test_regulatory_surface_snapshot.py
"""

from __future__ import annotations

import json

import httpx
import httpx2
import pytest

from tests.surface_helpers import FIXTURES, check_golden, wire_surface
from vaquill_mcp import __version__
from vaquill_mcp.regulatory import TOOL_NAMES, create_regulatory_server, regulatory_costs

_GOLDEN = FIXTURES / "regulatory_surface.json"
_ENV = "UPDATE_REGULATORY_SURFACE"
_BASE = "https://api.vaquill.ai"
# IMPORTED, not copied. This was a hand-kept duplicate of the list in
# test_regulatory_remote.py until 2026-11-26, when the two account routes landed
# in one copy and not the other: the surface snapshot then rendered
# `get_india_credit_balance` with NO cost line, logged the unpriced warning, and
# would have shipped a snapshot asserting a price the live matrix does supply.
# A table two files keep by hand is one edit from disagreeing, and the
# disagreement renders as a plausible snapshot rather than an error.
from tests.test_regulatory_remote import _PRICES
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


def _server(**kwargs: object):
    client = httpx2.AsyncClient(base_url=_BASE, headers={"Authorization": "Bearer vq_key_test"})
    return create_regulatory_server(client, **kwargs)


async def _health(respx_mock) -> dict:
    from vaquill_mcp.regulatory_main import build_app

    respx_mock.get(f"{_BASE}/api/v1/in/api-credits/pricing").mock(
        return_value=httpx.Response(200, json={"costs": _PRICES})
    )
    app = build_app()
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://reg-mcp.vaquill.ai"
        ) as raw:
            response = await raw.get("/health")
    body = response.json()
    assert body.pop("version") == __version__, "the /health version must be the package version"
    return {
        "status": response.status_code,
        "contentType": response.headers.get("content-type"),
        "body": body,
    }


async def test_the_regulatory_surface_matches_the_snapshot(respx_mock) -> None:
    surface = await wire_surface(_server())
    priced = await _server(costs=regulatory_costs(_PRICES)).list_tools()
    surface["descriptionsWithPrices"] = {t.name: t.description for t in priced}
    surface["health"] = await _health(respx_mock)

    check_golden(surface, _GOLDEN, _ENV)


def test_the_snapshot_is_not_empty() -> None:
    """A truncated golden would make the comparison vacuous."""
    golden = json.loads(_GOLDEN.read_text())
    # Counted from TOOL_NAMES, not written out: this said `5` and had to be
    # hunted down by hand when the two account tools landed, which is the
    # fourth place that literal was hiding. The floor keeps the derivation
    # from passing on an emptied table.
    assert len(golden["toolOrder"]) == len(TOOL_NAMES) >= 7
    assert golden["server"]["instructions"]
    assert golden["server"]["info"]["name"] == "Vaquill Indian Regulatory"
    assert golden["health"]["body"]["corpus"] == "regulatory"
    assert all(t["annotations"] and t["inputSchema"] for t in golden["tools"].values())
    assert set(golden["descriptionsWithPrices"]) == set(golden["tools"])

