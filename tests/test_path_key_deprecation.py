"""The key-in-the-URL mounts keep working but say they are deprecated.

`/s/{api_key}` and `/in/s/{api_key}` put a credential in the URL path. They stay
for the customers who depend on them (a client that cannot send a header has no
other way in), and every response now carries `Deprecation` and `Link` headers
while each use is logged with a key PREFIX only, so the remaining dependents can
be counted before a sunset date is chosen.
"""

from __future__ import annotations

import logging

import httpx2
import pytest

from tests.test_remote_mcp_path import (  # noqa: F401  (fixtures)
    _live_api,
    _live_us,
    _oauth_on,
    _serving,
)
from vaquill_mcp.path_key_deprecation import DEPRECATION_DOC_URL, key_prefix

_KEY = "vq_key_abcdefghijklmnopqrstuvwxyz0123456789"
_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


async def _post(app, path: str) -> httpx2.Response:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="https://mcp.vaquill.ai"
    ) as raw:
        return await raw.post(path, json=_INIT, headers=_HEADERS)


@pytest.mark.usefixtures("_live_api")
@pytest.mark.parametrize(
    ("path", "mount"),
    [(f"/s/{_KEY}", "/s"), (f"/in/s/{_KEY}", "/in/s")],
)
async def test_path_key_mounts_still_work_and_are_marked_deprecated(
    path: str, mount: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="vaquill_mcp.path_key_deprecation")
    async with _serving() as app:
        response = await _post(app, path)

    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Link"] == f'<{DEPRECATION_DOC_URL}>; rel="deprecation"'

    events = [
        r.getMessage()
        for r in caplog.records
        if "path_key_mount_used" in r.getMessage()
    ]
    assert len(events) == 1
    assert f"mount={mount} " in events[0]
    assert "key_prefix=vq_key_abcd" in events[0]


@pytest.mark.usefixtures("_live_api")
async def test_the_log_event_never_carries_key_material(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    async with _serving() as app:
        await _post(app, f"/s/{_KEY}")

    # Only this module's records: the TEST's own httpx2 client logs the URL it
    # requested, which is not the server's behaviour.
    ours = "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name == "vaquill_mcp.path_key_deprecation"
    )
    assert "path_key_mount_used" in ours
    assert _KEY not in ours
    # Not even a long slice of it: only `vq_key_` plus four characters.
    assert _KEY[:16] not in ours


@pytest.mark.usefixtures("_live_api")
async def test_the_bearer_mount_is_not_marked_deprecated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`Authorization: Bearer` at `/mcp` is the recommended shape."""
    caplog.set_level(logging.INFO)
    async with _serving() as app:
        response = await _post(app, "/mcp")

    assert "Deprecation" not in response.headers
    assert "path_key_mount_used" not in caplog.text


def test_key_prefix_is_bounded_and_ignores_non_keys() -> None:
    assert key_prefix(f"/s/{_KEY}") == "vq_key_abcd"
    assert key_prefix(f"/in/s/{_KEY}/") == "vq_key_abcd"
    assert key_prefix("/s/_") == "other"
    # Caller text that is not a key is never echoed into the log.
    assert key_prefix("/s/some-secret-looking-thing") == "other"
