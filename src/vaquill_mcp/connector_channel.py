"""Which AI client a user connected through, reported to the backend.

WHY THIS LIVES HERE
===================

This server is ONE OAuth client as far as Supabase is concerned, so Supabase can
never say whether a sign-in came from Claude or ChatGPT. This server can: the
client that registered (DCR) or published its metadata (CIMD) told it, in its
redirect URIs and its name. The family is derived at the end of the
authorization-code exchange, when the Supabase user id is also known, and sent to
the backend, which stores one row per (user, family).

SELF-DECLARED, SO ANALYTICS ONLY
================================

Redirect hosts are checked by the real client's own OAuth flow (a code only goes
to the registered redirect), but the NAME is whatever the client typed. A hostile
client can claim to be Claude. That is fine for counting and never fine for
authorizing, so nothing may branch on the result.

WHERE THE SIGN-IN CAME FROM
===========================

The report also carries two sets of request evidence (`request_evidence`): the
person's BROWSER at `/auth/callback` (the step after they sign in on
app.vaquill.ai, so its address is theirs, not the AI platform's), kept in the
OAuth store against the code for a few minutes, and the PLATFORM that then
calls `/token` with that code. The backend stores both in
`connector_connect_events`. Same rule: analytics only.

Reporting never blocks or fails a sign-in: errors are logged and swallowed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from contextvars import ContextVar
from urllib.parse import urlparse

import httpx2

logger = logging.getLogger("vaquill_mcp.connector_channel")

#: Mirrors the backend allow-list and the CHECK constraint on
#: `connector_client_connections.client_family`.
FAMILIES = (
    "claude",
    "chatgpt",
    "gemini",
    "harvey",
    "claude_code",
    "codex",
    "cursor",
    "vscode",
    "other",
)

_CLAUDE_HOSTS = ("claude.ai", "claude.com", "anthropic.com")
_CHATGPT_HOSTS = ("chatgpt.com", "openai.com")
#: Gemini Enterprise's two OAuth redirect hosts, as its own registration declared
#: them on 2026-11-23. Exact hosts, not `google.com`: many unrelated Google
#: products could register a client, and only these two are the connector.
_GEMINI_HOSTS = ("vertexaisearch.cloud.google.com", "enterprise.gemini.google.com")
_HARVEY_HOSTS = ("harvey.ai",)
_LOOPBACK = ("localhost", "127.0.0.1", "::1", "[::1]")

#: Name fragments for clients that redirect to a loopback port, where the host
#: says nothing. Checked in order, first hit wins.
_NAME_RULES = (
    ("claude code", "claude_code"),
    ("claude-code", "claude_code"),
    ("codex", "codex"),
    ("cursor", "cursor"),
    ("visual studio code", "vscode"),
    ("vscode", "vscode"),
    ("vs code", "vscode"),
)


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def classify_client(
    client_id: object, client_name: object, redirect_uris: object
) -> str:
    """The client family for a registered OAuth client. Never raises."""
    try:
        hosts: list[str] = []
        for uri in list(redirect_uris or []):
            host = urlparse(str(uri)).hostname
            if host:
                hosts.append(host)
        # A CIMD client_id IS a URL on the client's own domain.
        cid_host = urlparse(str(client_id or "")).hostname
        if cid_host:
            hosts.append(cid_host)

        if any(_host_matches(h, _CHATGPT_HOSTS) for h in hosts):
            return "chatgpt"
        if any(_host_matches(h, _GEMINI_HOSTS) for h in hosts):
            return "gemini"
        if any(_host_matches(h, _HARVEY_HOSTS) for h in hosts):
            return "harvey"

        name = str(client_name or "").strip().lower()
        for fragment, family in _NAME_RULES:
            if fragment in name:
                return family

        if any(_host_matches(h, _CLAUDE_HOSTS) for h in hosts):
            return "claude"
        if "claude" in name:
            return "claude"
        if "harvey" in name:
            return "harvey"
        return "other"
    except Exception:
        return "other"


def redirect_hosts(redirect_uris: object) -> list[str]:
    """The distinct hosts a client registered as redirects, in order. Never raises."""
    out: list[str] = []
    try:
        for uri in list(redirect_uris or []):
            host = urlparse(str(uri)).hostname
            if host and host not in out:
                out.append(host)
    except Exception:
        pass
    return out[:10]


#: Request headers kept as evidence of where a sign-in came from. Cloudflare sets
#: `cf-ipcountry` always and the city and region headers only when the zone's
#: "Add visitor location headers" managed transform is on; absent ones are
#: simply left out.
_EVIDENCE_HEADERS = {
    "country": b"cf-ipcountry",
    "region": b"cf-region",
    "city": b"cf-ipcity",
    "userAgent": b"user-agent",
    "acceptLanguage": b"accept-language",
}


def request_evidence(scope: object) -> dict[str, str]:
    """Address, Cloudflare location and browser of one ASGI request. Never raises.

    The address comes from `registration_guard._client_ip`, which trusts
    CF-Connecting-IP first. Values are clipped and never logged here.
    """
    try:
        from vaquill_mcp.registration_guard import _client_ip

        headers = {
            k.lower(): v
            for k, v in (scope.get("headers") or [])  # type: ignore[union-attr]
        }
        evidence: dict[str, str] = {}
        ip = _client_ip(scope)  # type: ignore[arg-type]
        if ip and ip != "unknown":
            evidence["ip"] = ip[:64]
        for field, header in _EVIDENCE_HEADERS.items():
            value = headers.get(header, b"").decode("latin-1").strip()
            if value:
                evidence[field] = value[:300]
        return evidence
    except Exception:
        return {}


#: The /token request's evidence, set by `AuthDiagnosticsMiddleware` around the
#: request so `exchange_authorization_code` (which is given no request) can
#: read who exchanged the code.
TOKEN_REQUEST: ContextVar[dict[str, str] | None] = ContextVar(
    "vaquill_token_request", default=None
)


def subject_from_access_token(token: object) -> str | None:
    """The `sub` of a JWT, read WITHOUT verifying it. Never raises.

    Safe here because the token came straight from Supabase's token endpoint over
    our own back channel, and the value only keys an analytics row. It is never
    used to authorize anything.
    """
    try:
        payload = str(token).split(".")[1]
        payload += "=" * (-len(payload) % 4)
        sub = json.loads(base64.urlsafe_b64decode(payload)).get("sub")
        return sub if isinstance(sub, str) and sub else None
    except Exception:
        return None


async def report_client_seen(
    base_url: str,
    secret: str,
    user_id: str,
    family: str,
    client_id: str | None,
    details: dict | None = None,
    timeout: float = 3.0,
) -> bool:
    """Tell the backend which family this user connected through. Never raises.

    `details` adds `clientName`, `redirectHosts`, `browser` and `platform`
    (see `request_evidence`) when the caller has them.
    """
    try:
        payload: dict = {
            "userId": user_id,
            "clientFamily": family,
            "clientId": client_id,
        }
        for key, value in (details or {}).items():
            if value:
                payload[key] = value
        async with httpx2.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/api/v1/internal/connector-keys/client-seen",
                json=payload,
                # Lowercase header name on purpose; see ConnectorKeyResolver.resolve.
                headers={"x-vaquill-internal": secret},
            )
        if response.status_code != 200:
            logger.warning(
                "connector_client_report_rejected status=%s", response.status_code
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "connector_client_report_failed error_type=%s", type(exc).__name__
        )
        return False


#: Strong references to in-flight report tasks, so the loop cannot drop one early.
_PENDING: set[asyncio.Task] = set()


def schedule_report(
    user_id: str, family: str, client_id: str | None, details: dict | None = None
) -> None:
    """Fire-and-forget `report_client_seen` using the deployment's own env. Never raises."""
    secret = os.environ.get("VAQUILL_INTERNAL_SECRET", "").strip()
    if not secret:
        return
    try:
        from vaquill_mcp.config import get_base_url

        task = asyncio.get_running_loop().create_task(
            report_client_seen(
                get_base_url(), secret, user_id, family, client_id, details
            )
        )
        _PENDING.add(task)
        task.add_done_callback(_PENDING.discard)
    except Exception:
        logger.warning("connector_client_report_not_scheduled", exc_info=True)
