"""The square icon and favicon the hosted server serves from its own host.

Asserted on what a client actually receives: the bytes at `/icon.png` must be
a square PNG under the 10,240-byte ceiling OpenAI's app tooling enforces, the
favicon must be a real ICO, both must be reachable past the root Mount, and the
icon list must keep the lockup FIRST so the consent screen is unchanged.
"""

from __future__ import annotations

import json
import pathlib
import struct

import httpx
import pytest
from starlette.routing import Mount, Route

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_BASE = "https://api.vaquill.ai"
_LOCKUP = "https://www.vaquill.ai/brand/lockup/vaquill-lockup-color-512w.png"
# OpenAI rejects a logo over this many bytes ("Logo is too large").
_ICON_BYTE_CEILING = 10_240


def _spec(jurisdiction: str) -> dict:
    return json.loads((_FIXTURES / f"openapi_{jurisdiction.lower()}.json").read_text())


@pytest.fixture
def _live_api(monkeypatch: pytest.MonkeyPatch, respx_mock) -> None:
    """Mock the startup fetches, as test_remote_dual_mount does."""
    monkeypatch.setenv("VAQUILL_BASE_URL", _BASE)
    respx_mock.get(f"{_BASE}/external/openapi.json").mock(
        return_value=httpx.Response(200, json=_spec("US"))
    )
    respx_mock.get(f"{_BASE}/in/openapi.json").mock(
        return_value=httpx.Response(200, json=_spec("IN"))
    )
    respx_mock.get(f"{_BASE}/api/v1/api-credits/pricing").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Width and height from the IHDR chunk, which a valid PNG puts first."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert data[12:16] == b"IHDR", "IHDR is not the first chunk"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_icon_is_a_small_square_png(_live_api: None) -> None:
    from starlette.testclient import TestClient

    from vaquill_mcp.remote_main import build_app

    with TestClient(build_app()) as client:
        r = client.get("/icon.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert "max-age" in r.headers["cache-control"]
    assert len(r.content) <= _ICON_BYTE_CEILING, len(r.content)
    assert _png_dimensions(r.content) == (128, 128)


def test_favicon_is_a_real_ico(_live_api: None) -> None:
    """The consent page's browser asked for /favicon.ico and got 404."""
    from starlette.testclient import TestClient

    from vaquill_mcp.remote_main import build_app

    with TestClient(build_app()) as client:
        r = client.get("/favicon.ico")
        head = client.head("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/x-icon"
    # ICONDIR: reserved 0, type 1 (icon), then at least one image.
    reserved, kind, count = struct.unpack("<HHH", r.content[:6])
    assert (reserved, kind) == (0, 1)
    assert count >= 1
    assert head.status_code == 200


def test_assets_are_routed_before_the_root_mount(_live_api: None) -> None:
    """Mount("/") never falls through, so a later route is unreachable."""
    from vaquill_mcp.remote_main import build_app

    routes = build_app().routes
    first_mount = next(i for i, r in enumerate(routes) if isinstance(r, Mount))
    asset_positions = [
        i
        for i, r in enumerate(routes)
        if isinstance(r, Route) and r.path in ("/icon.png", "/favicon.ico")
    ]
    assert len(asset_positions) == 2
    assert max(asset_positions) < first_mount


async def test_lockup_stays_first_and_the_square_icon_follows(
    _live_api: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The consent screen renders icons[0] only, sized for the wide lockup."""
    monkeypatch.setenv("VAQUILL_PUBLIC_URL", "https://mcp.example.test")
    from vaquill_mcp.remote import create_remote_server

    for jurisdiction in ("US", "IN"):
        icons = create_remote_server(jurisdiction).icons
        assert icons[0].src == _LOCKUP, jurisdiction
        square = icons[1]
        assert square.src == "https://mcp.example.test/icon.png", jurisdiction
        assert square.mime_type == "image/png"
        assert square.sizes == ["128x128"]


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("https://mcp.example.test", "https://mcp.example.test"),
        ("https://mcp.example.test/mcp", "https://mcp.example.test"),
        ("https://mcp.example.test/", "https://mcp.example.test"),
        ("", "https://mcp.vaquill.ai"),
        ("not a url", "https://mcp.vaquill.ai"),
    ],
)
def test_public_origin_takes_only_scheme_and_host(
    monkeypatch: pytest.MonkeyPatch, configured: str, expected: str
) -> None:
    from vaquill_mcp.brand_assets import public_origin

    monkeypatch.setenv("VAQUILL_PUBLIC_URL", configured)
    assert public_origin() == expected
