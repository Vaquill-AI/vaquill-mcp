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

DELIBERATE PROPERTIES
=====================

* Pure ASGI, not `BaseHTTPMiddleware`: it never reads or buffers a body, so the
  MCP stream and every other route pass through untouched.
* Acts ONLY on `POST` to a path ending in `/register`. Everything else is
  forwarded without inspection.
* Fails OPEN. Any internal error forwards the request, because a broken limiter
  must never take the connect flow down.
* Per process and in memory, with a bounded table. A multi-replica deployment
  gets the limit per replica, which is the intended trade: this is abuse
  damping, not billing.
"""

from __future__ import annotations

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

    def __init__(self, app, limit: int | None = None, window_s: int | None = None):
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

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or self.limit <= 0
            or scope.get("method") != "POST"
            or not scope.get("path", "").rstrip("/").endswith("/register")
        ):
            await self.app(scope, receive, send)
            return
        try:
            retry = self._retry_after(_client_ip(scope), time.monotonic())
        except Exception:
            logger.exception("registration limiter failed; allowing the request")
            retry = 0
        if not retry:
            await self.app(scope, receive, send)
            return
        body = (
            b'{"error":"too_many_requests","error_description":'
            b'"Too many client registrations from this address. Retry later."}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", str(retry).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
