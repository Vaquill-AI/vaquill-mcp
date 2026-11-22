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

Reporting never blocks or fails a sign-in: errors are logged and swallowed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from urllib.parse import urlparse

import httpx2

logger = logging.getLogger("vaquill_mcp.connector_channel")

#: Mirrors the backend allow-list and the CHECK constraint on
#: `connector_client_connections.client_family`.
FAMILIES = ("claude", "chatgpt", "claude_code", "codex", "cursor", "vscode", "other")

_CLAUDE_HOSTS = ("claude.ai", "claude.com", "anthropic.com")
_CHATGPT_HOSTS = ("chatgpt.com", "openai.com")
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

        name = str(client_name or "").strip().lower()
        for fragment, family in _NAME_RULES:
            if fragment in name:
                return family

        if any(_host_matches(h, _CLAUDE_HOSTS) for h in hosts):
            return "claude"
        if "claude" in name:
            return "claude"
        return "other"
    except Exception:
        return "other"


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
    timeout: float = 3.0,
) -> bool:
    """Tell the backend which family this user connected through. Never raises."""
    try:
        async with httpx2.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/api/v1/internal/connector-keys/client-seen",
                json={"userId": user_id, "clientFamily": family, "clientId": client_id},
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


def schedule_report(user_id: str, family: str, client_id: str | None) -> None:
    """Fire-and-forget `report_client_seen` using the deployment's own env. Never raises."""
    secret = os.environ.get("VAQUILL_INTERNAL_SECRET", "").strip()
    if not secret:
        return
    try:
        from vaquill_mcp.config import get_base_url

        task = asyncio.get_running_loop().create_task(
            report_client_seen(get_base_url(), secret, user_id, family, client_id)
        )
        _PENDING.add(task)
        task.add_done_callback(_PENDING.discard)
    except Exception:
        logger.warning("connector_client_report_not_scheduled", exc_info=True)
