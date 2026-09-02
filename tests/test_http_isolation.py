"""The suite must never reach the real API. Asserted, not assumed.

This is infrastructure, not behaviour, and it earns a test because the failure
is invisible. respx patches `httpcore`; the package moved to httpx2 (which sits
on `httpcore2`) when fastmcp 4 deprecated passing an `httpx.AsyncClient` to
`OpenAPIProvider`. Stock respx matches nothing against an httpx2 client, and an
unmatched request is not an error by default -- it is simply forwarded. The
suite would have stayed green while every test hammered api.vaquill.ai with a
real key, and the only signal would have been the bill.

`tests/conftest.py` shadows the `respx_mock` fixture with pytest-httpx2's
httpcore2-targeting router to close that. These two tests are what prove it is
still closed.
"""

from __future__ import annotations

import httpx
import httpx2
import pytest


async def test_an_unmocked_request_raises_instead_of_being_sent() -> None:
    """The autouse guard in conftest, exercised end to end."""
    with pytest.raises(Exception, match="not mocked"):
        async with httpx2.AsyncClient() as client:
            await client.get("https://api.vaquill.ai/external/openapi.json")


async def test_a_mocked_httpx2_request_is_intercepted(respx_mock) -> None:
    """The other half: interception actually works for the client we now use.

    Note the asymmetry, which is easy to trip over when writing a new test:
    the request is httpx2, but respx still constructs the RESPONSE from the old
    `httpx.Response` type.
    """
    route = respx_mock.get("https://api.vaquill.ai/ping").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    async with httpx2.AsyncClient() as client:
        response = await client.get("https://api.vaquill.ai/ping")

    assert route.called, "respx did not intercept an httpx2 request"
    assert response.json() == {"ok": True}
