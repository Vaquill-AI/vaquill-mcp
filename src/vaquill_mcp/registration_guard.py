"""Rate limit on OAuth Dynamic Client Registration (`POST /register`).

WHY
===

`/register` is open on purpose: clients that do not use Client ID Metadata
Documents can only connect through RFC 7591 registration, so requiring a token
would break them. What an open endpoint should not allow is unbounded writes,
because every call persists a client record in the OAuth store. A researcher
reported the endpoint as unauthenticated on 2026-10-01; the exposure that
matters is storage growth and registration churn, and a per-address ceiling
closes it without changing who can connect.

The limit is generous by default (30 registrations per 10 minutes per address),
far above what a person connecting clients by hand reaches, and tunable with
`VAQUILL_OAUTH_REGISTER_LIMIT` / `VAQUILL_OAUTH_REGISTER_WINDOW_S`. A limit of
0 disables it.

The same middleware caps the SIZE of a registration. Before this, a 2 MB
`client_name` or 5,000 `redirect_uris` returned 201 and were stored, so the
per-address count was not a bound on storage. A real registration is a few
hundred bytes. The body is capped at 16 KiB (`VAQUILL_OAUTH_REGISTER_MAX_BYTES`,
413 above it) and `redirect_uris` at 20 entries (400 `invalid_client_metadata`).

DELIBERATE PROPERTIES
=====================

* Pure ASGI, not `BaseHTTPMiddleware`: it reads a body ONLY for `POST /register`
  (bounded by the size cap), so the MCP stream and every other route pass
  through untouched.
* Acts ONLY on `POST` to a path ending in `/register`. Everything else is
  forwarded without inspection.
* Fails OPEN. Any internal error forwards the request, because a broken limiter
  must never take the connect flow down.
* Per process and in memory, with a bounded table. A multi-replica deployment
  gets the limit per replica, which is the intended trade: this is abuse
  damping, not billing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 30
_DEFAULT_WINDOW_S = 600
#: Hard cap on tracked addresses so a spray of distinct sources cannot grow the
#: table without bound. Past it, the oldest-seen addresses are evicted.
_MAX_TRACKED = 10_000
_DEFAULT_MAX_BYTES = 16 * 1024
_MAX_REDIRECT_URIS = 20


class _BodyTooLarge(Exception):
    """The request body went past the registration size cap."""


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _client_ip(scope: dict) -> str:
    """Best available caller address.

    `CF-Connecting-IP` is overwritten by Cloudflare, so it is trusted first.
    Otherwise the RIGHTMOST `X-Forwarded-For` entry, which is the one our own
    reverse proxy appended; the leftmost is client-controlled.
    """
    headers = {k.lower(): v for k, v in scope.get("headers", [])}
    cf = headers.get(b"cf-connecting-ip", b"").decode("latin-1").strip()
    if cf:
        return cf
    xff = headers.get(b"x-forwarded-for", b"").decode("latin-1")
    if xff.strip():
        return xff.split(",")[-1].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


class RegistrationRateLimit:
    """ASGI middleware: 429 a source that over-registers OAuth clients."""

    def __init__(
        self,
        app,
        limit: int | None = None,
        window_s: int | None = None,
        max_bytes: int | None = None,
    ):
        self.app = app
        self.limit = (
            _int_env("VAQUILL_OAUTH_REGISTER_LIMIT", _DEFAULT_LIMIT)
            if limit is None
            else limit
        )
        self.window_s = (
            _int_env("VAQUILL_OAUTH_REGISTER_WINDOW_S", _DEFAULT_WINDOW_S)
            if window_s is None
            else window_s
        )
        self.max_bytes = (
            _int_env("VAQUILL_OAUTH_REGISTER_MAX_BYTES", _DEFAULT_MAX_BYTES)
            if max_bytes is None
            else max_bytes
        )
        self._hits: dict[str, deque[float]] = {}

    def _retry_after(self, key: str, now: float) -> int:
        """0 if the call is allowed (and counted), else seconds until it is."""
        window = self._hits.get(key)
        if window is None:
            if len(self._hits) >= _MAX_TRACKED:
                self._hits.pop(next(iter(self._hits)))
            window = self._hits[key] = deque()
        while window and now - window[0] >= self.window_s:
            window.popleft()
        if len(window) >= self.limit:
            return max(1, int(self.window_s - (now - window[0])) + 1)
        window.append(now)
        return 0

    async def _read_capped(self, scope, receive) -> bytes:
        """The request body, or `_BodyTooLarge` once it passes the cap."""
        declared = dict(scope.get("headers", [])).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            raise _BodyTooLarge
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return b"".join(chunks)
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                raise _BodyTooLarge
            chunks.append(chunk)
            if not message.get("more_body"):
                return b"".join(chunks)

    @staticmethod
    def _too_many_redirect_uris(body: bytes) -> bool:
        """True only for a parseable object whose `redirect_uris` is over the cap.

        Anything else (bad JSON, other shapes) is the library's to reject.
        """
        try:
            data = json.loads(body)
        except ValueError:
            return False
        uris = data.get("redirect_uris") if isinstance(data, dict) else None
        return isinstance(uris, list) and len(uris) > _MAX_REDIRECT_URIS

    async def _refuse(self, send, status: int, payload: dict, retry: int = 0) -> None:
        body = json.dumps(payload).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ]
        if retry:
            headers.append((b"retry-after", str(retry).encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").rstrip("/").endswith("/register")
        ):
            await self.app(scope, receive, send)
            return
        retry = 0
        if self.limit > 0:
            try:
                retry = self._retry_after(_client_ip(scope), time.monotonic())
            except Exception:
                logger.exception("registration limiter failed; allowing the request")
        if retry:
            await self._refuse(
                send,
                429,
                {
                    "error": "too_many_requests",
                    "error_description": (
                        "Too many client registrations from this address. "
                        "Retry later."
                    ),
                },
                retry,
            )
            return
        if self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return
        try:
            body = await self._read_capped(scope, receive)
        except _BodyTooLarge:
            await self._refuse(
                send,
                413,
                {
                    "error": "invalid_client_metadata",
                    "error_description": "Registration request is too large.",
                },
            )
            return
        if self._too_many_redirect_uris(body):
            await self._refuse(
                send,
                400,
                {
                    "error": "invalid_redirect_uri",
                    "error_description": (
                        f"At most {_MAX_REDIRECT_URIS} redirect URIs are accepted."
                    ),
                },
            )
            return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
