"""The /register rate limit must damp abuse and never touch anything else."""

from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from vaquill_mcp.oauth import _allowed_redirect_uris
from vaquill_mcp.registration_guard import RegistrationRateLimit


async def _ok(_request):
    return JSONResponse({"ok": True})


def _client(limit: int = 3, window_s: int = 600) -> httpx.AsyncClient:
    app = Starlette(
        routes=[
            Route("/register", _ok, methods=["GET", "POST"]),
            Route("/mcp", _ok, methods=["GET", "POST"]),
        ]
    )
    guarded = RegistrationRateLimit(app, limit=limit, window_s=window_s)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guarded), base_url="https://mcp.test"
    )


async def test_register_is_limited_per_address() -> None:
    async with _client(limit=3) as c:
        codes = [(await c.post("/register", headers={"cf-connecting-ip": "1.1.1.1"})).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        blocked = await c.post("/register", headers={"cf-connecting-ip": "1.1.1.1"})
        assert int(blocked.headers["retry-after"]) >= 1
        other = await c.post("/register", headers={"cf-connecting-ip": "2.2.2.2"})
        assert other.status_code == 200


async def test_only_post_register_is_touched() -> None:
    async with _client(limit=1) as c:
        for _ in range(5):
            assert (await c.post("/mcp")).status_code == 200
            assert (await c.get("/register")).status_code == 200


async def test_spoofed_leftmost_forwarded_for_does_not_evade() -> None:
    async with _client(limit=1) as c:
        first = await c.post("/register", headers={"x-forwarded-for": "9.9.9.1, 5.5.5.5"})
        second = await c.post("/register", headers={"x-forwarded-for": "9.9.9.2, 5.5.5.5"})
        assert (first.status_code, second.status_code) == (200, 429)


async def test_zero_disables_the_limit() -> None:
    async with _client(limit=0) as c:
        for _ in range(10):
            assert (await c.post("/register")).status_code == 200


async def test_a_broken_limiter_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("limiter bug")

    monkeypatch.setattr(RegistrationRateLimit, "_retry_after", boom)
    async with _client(limit=1) as c:
        assert (await c.post("/register")).status_code == 200


def test_redirect_allowlist_is_off_unless_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VAQUILL_OAUTH_ALLOWED_REDIRECT_URIS", raising=False)
    assert _allowed_redirect_uris() is None
    monkeypatch.setenv("VAQUILL_OAUTH_ALLOWED_REDIRECT_URIS", " https://a/cb , ,http://localhost:* ")
    assert _allowed_redirect_uris() == ["https://a/cb", "http://localhost:*"]


async def _echo_len(request):
    return JSONResponse({"n": len(await request.body())})


def _sized_client(max_bytes: int = 1024) -> httpx.AsyncClient:
    app = Starlette(routes=[Route("/register", _echo_len, methods=["POST"])])
    guarded = RegistrationRateLimit(app, limit=1000, window_s=600, max_bytes=max_bytes)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guarded), base_url="https://mcp.test"
    )


async def test_oversized_registration_is_refused_with_413() -> None:
    async with _sized_client() as c:
        r = await c.post("/register", json={"client_name": "x" * 5000})
        assert r.status_code == 413
        assert r.json()["error"] == "invalid_client_metadata"


async def test_oversized_chunked_registration_is_refused() -> None:
    async def gen():
        for _ in range(10):
            yield b"x" * 512

    async with _sized_client() as c:
        r = await c.post("/register", content=gen())
        assert r.status_code == 413


async def test_normal_registration_body_reaches_the_app_intact() -> None:
    payload = {"client_name": "Claude", "redirect_uris": ["https://claude.ai/cb"]}
    async with _sized_client() as c:
        r = await c.post("/register", json=payload)
        assert r.status_code == 200
        assert r.json()["n"] == len(httpx.Request("POST", "http://x", json=payload).content)


async def test_too_many_redirect_uris_is_refused() -> None:
    uris = [f"https://a.example/{i}" for i in range(21)]
    async with _sized_client(max_bytes=100_000) as c:
        assert (await c.post("/register", json={"redirect_uris": uris})).status_code == 400
        ok = await c.post("/register", json={"redirect_uris": uris[:20]})
        assert ok.status_code == 200


async def test_malformed_json_is_left_to_the_library() -> None:
    async with _sized_client() as c:
        r = await c.post("/register", content=b"{not json")
        assert r.status_code == 200
