"""Who is CALLING this server, carried through to the API behind it.

WHY THIS EXISTS
===============

This server is a proxy. A tool call arrives over MCP from Claude, Codex,
Gemini, Cursor or somebody's script, and leaves as an HTTP request to
`api.vaquill.ai`. Both hops were logged, but the outbound one stamped a FIXED
`User-Agent` of `vaquill-mcp-remote/<version>`, so every client on earth landed
in `api_request_logs` as one string. "How many people use the connector, and
from which client" was therefore unanswerable, and not for want of
instrumentation: the proxy was overwriting the answer on its way past.

MCP already carries it. `initialize` sends `clientInfo {name, version}` and the
SDK keeps it on the session for the life of the connection. This module reads
it and appends it to the outbound agent as an ordinary UA comment:

    vaquill-mcp-remote/0.4.0 (client=claude-ai/1.2.3)

THE USER AGENT, NOT A NEW HEADER
================================

`api_request_logs.user_agent` already exists, is already written on every
metered call, and is already what the admin client-mix panel reads. A dedicated
header would need a backend middleware change, a column and a migration to
arrive at the same table. This reaches it with no backend change at all, and
the value stays legible to anyone reading a raw access log.

`clientInfo` IS SELF-DECLARED, SO IT IS TREATED AS HOSTILE
==========================================================

Any client may send any string in `initialize` and nothing verifies it. (The
OAuth `client_id` IS domain-verified through CIMD, but it covers only OAuth
callers, and most traffic here arrives on the key-in-path mount, which has no
client identity at all.) This value therefore goes into a header, then a
database column, then an admin table, so it is reduced to a conservative
charset, length-capped, and dropped entirely when nothing survives.

Good enough to COUNT clients. Never good enough to authorise one: no
entitlement, billing or access decision may read it.
"""

from __future__ import annotations

import re

#: Deliberately narrow. This value reaches an HTTP header, a database column
#: and an admin table, so anything that could terminate a header, close the UA
#: comment early, or confuse the `client=` extraction the analytics query does
#: is gone before it travels.
_DISALLOWED = re.compile(r"[^A-Za-z0-9._-]+")

#: Long enough for every real client name and version seen in the wild, short
#: enough that a hostile one cannot bloat every outbound request.
_MAX_NAME = 40
_MAX_VERSION = 20

#: A cleaned value has to say something. See `_clean`.
_ALPHANUMERIC = re.compile(r"[A-Za-z0-9]")


def _clean(value: object, limit: int) -> str:
    """One `clientInfo` field, reduced to something safe to put in a header.

    Disallowed runs collapse to a single `-` rather than vanishing, so two
    different names cannot silently become the same one. Stripped again after
    the truncation, because the cut itself can leave a trailing separator.
    """
    if not isinstance(value, str):
        return ""
    cleaned = _DISALLOWED.sub("-", value).strip("-")[:limit].strip("-")
    # `.` and `-` survive the charset because real versions need them, which
    # means a name of nothing but punctuation ("...") survives it too. That is
    # not a client, so it is an absence, not a bucket in the client mix.
    return cleaned if _ALPHANUMERIC.search(cleaned) else ""


def calling_client() -> str | None:
    """`name/version` for the MCP client driving this request, or None.

    None whenever the answer is not knowable, rather than guessed at: no
    request context at all (the OpenAPI and pricing fetches at startup), a
    client that sent no `clientInfo`, or a name with nothing printable left
    after cleaning.

    Never raises. This is telemetry riding on a billable request, and it must
    not be able to fail one.
    """
    try:
        from fastmcp.server.dependencies import get_context

        params = getattr(get_context().session, "client_params", None)
        # `client_info`, NOT `clientInfo`. The wire field is camelCase and the
        # SDK model is snake_case with that as a serialization alias, so the
        # camelCase spelling reads as a plain missing attribute: no error, no
        # warning, every request simply unstamped. Caught only because the test
        # asserts the OUTBOUND header rather than the formatter.
        info = getattr(params, "client_info", None)
        name = _clean(getattr(info, "name", None), _MAX_NAME)
        if not name:
            return None
        version = _clean(getattr(info, "version", None), _MAX_VERSION)
        return f"{name}/{version}" if version else name
    except Exception:
        return None


def with_client(user_agent: str, client: str | None) -> str:
    """The outbound agent, with the calling client appended when it is known."""
    return f"{user_agent} (client={client})" if client else user_agent


def make_client_stamp(user_agent: str):
    """An httpx request hook that stamps the calling MCP client onto the agent.

    An event hook rather than the auth flow, because both surfaces need this
    and only the remote one HAS an auth flow: the stdio server puts a fixed key
    on the client at construction and never runs one. The hook also sits
    outside authentication entirely, which keeps a telemetry concern from
    sharing a code path with the credential that decides who gets billed.

    Falls back to the unstamped agent whenever the client is unknown, so a
    request is never blocked and never carries a half-built value.
    """

    async def stamp(request) -> None:
        client = calling_client()
        if client:
            request.headers["User-Agent"] = with_client(user_agent, client)

    return stamp
