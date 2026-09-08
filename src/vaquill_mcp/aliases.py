"""The generic `search` / `fetch` pair OpenAI's deep-research clients require.

WHY A SERVER WITH 23 GOOD TOOLS IS UNUSABLE WITHOUT THESE TWO
=============================================================

ChatGPT's deep research and company-knowledge connectors match a corpus server
BY NAME AND SHAPE: exactly `search(query: str)` and `fetch(id: str)`, each taking
a single string. A server that does not publish both is listable there and then
refuses to work, with no error a user can act on.

`search_us_statutes` and `get_us_statute_section_text` are the same two
operations with better parameters, and that does not help: the interface is
matched on the name and the one-string signature, not on capability.

So both are published. The typed tools stay exactly as they were, and these two
say in their own descriptions that a client able to call the typed ones should.
The pair is deliberately thin: one string in, the documented envelope out, no
filters. Anything richer belongs on the tools that already have it.

THE TWO RULES THAT ARE EASY TO GET WRONG
========================================

**`url` must be a non-empty string or no citation is rendered.** ChatGPT creates
a citation only when the field is populated, so a result with a null
`externalUrl` silently becomes uncitable. Every result here therefore falls back
through the publisher link, then our own HTML link, then the canonical API URL
for the section, which always exists.

**`fetch` must be lenient about what it is handed.** Models pass back whatever
they saw: the citation URL from a previous result, a bare path with or without a
leading slash, or (on a legal corpus, most often) the human citation itself
rather than any id at all. Refusing those is technically correct and wastes a
turn, so `_coerce_act_id` unwraps URLs and paths, and a string that reads like a
citation rather than an act_id is resolved through the citation resolver first.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# httpx2, not httpx2. fastmcp 4 deprecated passing an `httpx2.AsyncClient` to
# `OpenAPIProvider` ("temporarily accepted via duck typing... will be rejected in
# a future release") and ships httpx2 as a hard dependency. httpx2 is a drop-in
# fork with the same public API, so this is an import swap, not a rewrite.
import httpx2
from fastmcp import FastMCP

from vaquill_mcp.descriptions import TOOL_TITLES

# An act_id is a single token of uppercase-prefixed, underscore-joined segments
# (`USC_T42_C21_S1983`, `STATE_TX_Cpr_C93_S93.005`, `IND_central_2065`). A
# Bluebook citation is prose with spaces and periods ("42 U.S.C. 1983"). The
# split is therefore whitespace: anything containing it cannot be an act_id, and
# is worth a trip through the resolver rather than a guaranteed 404.
_ACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:_[A-Za-z0-9._-]+)+$")

# `.../section/USC_T42_C21_S1983` and `.../section/USC_T42_C21_S1983/body` both
# appear in text we hand back, so the id is the segment after `section/`.
_SECTION_PATH_RE = re.compile(r"/section/([^/?#]+)")
_ACTS_PATH_RE = re.compile(r"/acts/([^/?#]+)")

_SEARCH_LIMIT = 10

# Result fields the US search needs to build the envelope. Requested explicitly
# because a StatuteResult carries 100+ fields and the alias uses five of them.
_US_SEARCH_FIELDS = [
    "sectionTitle",
    "excerpt",
    "externalUrl",
    "htmlUrl",
    "stateHtmlUrl",
]


def _first_url(*candidates: Any) -> str | None:
    """The first candidate that is a non-empty string."""
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _coerce_act_id(raw: str) -> tuple[str | None, str | None]:
    """Turn whatever the model passed into `(act_id, citation_to_resolve)`.

    Exactly one side is populated. A recognizable id (bare, or embedded in a URL
    or path we previously emitted) comes back as an act_id; anything else is
    returned as a citation for the resolver to try, because failing over to a
    resolve is strictly better than answering a 404 to a caller who handed us a
    perfectly good citation.
    """
    text = (raw or "").strip().strip("<>").strip()
    if not text:
        return None, None

    if text.startswith(("http://", "https://")):
        text = urlparse(text).path or text

    for pattern in (_SECTION_PATH_RE, _ACTS_PATH_RE):
        match = pattern.search(text)
        if match:
            return match.group(1), None

    if "/" in text:
        # A bare path with no marker segment: take the last meaningful segment.
        segments = [
            seg for seg in text.split("/") if seg and seg not in ("body", "text")
        ]
        text = segments[-1] if segments else text

    if _ACT_ID_RE.match(text):
        return text, None
    return None, text


def _register_us(mcp: FastMCP, client: httpx2.AsyncClient, base_url: str) -> None:
    """`search` / `fetch` over US primary law."""

    def _canonical_url(act_id: str) -> str:
        return f"{base_url.rstrip('/')}/api/v1/us/statutes/section/{act_id}"

    @mcp.tool(
        name="search",
        title=TOOL_TITLES["search"],
        description=(
            "Generic corpus search over US primary law, returning `{id, title, url}` "
            "records for citation. Present so this server works in clients that "
            "require the standard search/fetch pair. If you can call "
            "`search_us_statutes`, prefer it: it filters by jurisdiction, corpus, "
            "date and status, which this cannot. Pair with `fetch` to read a result."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": TOOL_TITLES["search"]},
    )
    async def search(query: str) -> dict[str, list[dict[str, str]]]:
        """Search US statutes, regulations, constitutions and court rules."""
        response = await client.post(
            "/api/v1/us/statutes/search",
            json={"query": query, "limit": _SEARCH_LIMIT, "fields": _US_SEARCH_FIELDS},
        )
        response.raise_for_status()
        results = []
        for hit in response.json().get("results") or []:
            act_id = hit.get("actId")
            if not act_id:
                continue
            results.append(
                {
                    "id": act_id,
                    "title": _first_url(hit.get("sectionTitle"), hit.get("citation"))
                    or act_id,
                    # Never null: a null url renders no citation at all.
                    "url": _first_url(
                        hit.get("externalUrl"),
                        hit.get("stateHtmlUrl"),
                        hit.get("htmlUrl"),
                    )
                    or _canonical_url(act_id),
                    "snippet": hit.get("excerpt") or "",
                }
            )
        return {"results": results}

    @mcp.tool(
        name="fetch",
        title=TOOL_TITLES["fetch"],
        description=(
            "Fetch the full text of one US law section by the `id` from a `search` "
            "result, returning `{id, title, text, url, metadata}`. Also accepts a "
            "citation URL, a bare path, or a Bluebook citation such as "
            "'42 U.S.C. 1983'. Present for clients that require the standard "
            "search/fetch pair; prefer `get_us_statute_section_text` if available. "
            "Charged as a section lookup plus a body read."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": TOOL_TITLES["fetch"]},
    )
    async def fetch(id: str) -> dict[str, Any]:  # noqa: A002 - the spec names it `id`
        """Retrieve one US law section by act_id, URL, path or citation."""
        act_id, citation = _coerce_act_id(id)

        if act_id is None and citation:
            resolved = await client.get(
                "/api/v1/us/statutes/resolve", params={"cite": citation}
            )
            resolved.raise_for_status()
            payload = resolved.json()
            if payload.get("resolved"):
                act_id = (payload.get("section") or {}).get("actId")
        if not act_id:
            raise ValueError(
                f"Could not resolve {id!r} to a section. Pass an `id` from a "
                "`search` result, or a citation such as '42 U.S.C. 1983'."
            )

        meta_response = await client.get(f"/api/v1/us/statutes/section/{act_id}")
        meta_response.raise_for_status()
        section = meta_response.json().get("section") or {}

        body_response = await client.get(f"/api/v1/us/statutes/section/{act_id}/body")
        body_response.raise_for_status()
        body = body_response.json()

        return {
            "id": act_id,
            "title": _first_url(section.get("sectionTitle"), section.get("citation"))
            or act_id,
            "text": _first_url(
                body.get("plain"), body.get("markdown"), body.get("html")
            )
            or "",
            "url": _first_url(
                body.get("sourceUrl"),
                section.get("externalUrl"),
                section.get("stateHtmlUrl"),
                section.get("htmlUrl"),
            )
            or _canonical_url(act_id),
            "metadata": {
                key: str(value)
                for key, value in (
                    ("citation", section.get("citation")),
                    ("corpusType", section.get("corpusType")),
                    ("state", section.get("state")),
                    ("actStatus", section.get("actStatus")),
                    ("goodLawStatus", section.get("goodLawStatus")),
                )
                if value
            },
        }


def _register_in(mcp: FastMCP, client: httpx2.AsyncClient, base_url: str) -> None:
    """`search` / `fetch` over Indian legislation."""

    def _canonical_url(act_id: str) -> str:
        return f"{base_url.rstrip('/')}/api/v1/in/acts/{act_id}/text"

    @mcp.tool(
        name="search",
        title=TOOL_TITLES["search_in"],
        description=(
            "Generic corpus search over Indian Central and State legislation, "
            "returning `{id, title, url}` records for citation. Present so this "
            "server works in clients that require the standard search/fetch pair. "
            "Prefer `search_acts` if you can call it: it filters by category, "
            "state, year and status. Pair with `fetch` to read a result."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": TOOL_TITLES["search_in"]},
    )
    async def search(query: str) -> dict[str, list[dict[str, str]]]:
        """Search Indian legislation down to the individual section."""
        response = await client.post("/api/v1/in/acts/search", json={"query": query})
        response.raise_for_status()
        data = response.json().get("data") or {}
        results = []
        for hit in data.get("results") or []:
            act_id = hit.get("actId")
            if not act_id:
                continue
            results.append(
                {
                    "id": act_id,
                    "title": _first_url(
                        hit.get("sectionTitle"), hit.get("title"), hit.get("longTitle")
                    )
                    or act_id,
                    "url": _first_url(
                        hit.get("sourceUrl"), hit.get("textUrl"), hit.get("pdfUrl")
                    )
                    or _canonical_url(act_id),
                    "snippet": (hit.get("content") or "")[:800],
                }
            )
        return {"results": results}

    @mcp.tool(
        name="fetch",
        title=TOOL_TITLES["fetch_in"],
        description=(
            "Fetch one Indian enactment by the `id` from a `search` result, "
            "returning `{id, title, text, url, metadata}`. Also accepts a source "
            "URL or a bare path. NOTE: the India corpus serves an enactment as "
            "publisher links rather than inline text, so `text` carries the title "
            "and `metadata` carries the PDF, HTML and plain-text URLs to read. "
            "Prefer `get_act_text` if you can call it."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": TOOL_TITLES["fetch_in"]},
    )
    async def fetch(id: str) -> dict[str, Any]:  # noqa: A002 - the spec names it `id`
        """Retrieve one Indian enactment's source links by act_id, URL or path."""
        act_id, fallback = _coerce_act_id(id)
        act_id = act_id or fallback
        if not act_id:
            raise ValueError(
                f"Could not resolve {id!r} to an enactment. Pass an `id` from a "
                "`search` result."
            )

        response = await client.get(f"/api/v1/in/acts/{act_id}/text")
        response.raise_for_status()
        act = response.json()
        title = _first_url(act.get("title")) or act_id
        return {
            "id": act_id,
            "title": title,
            # The endpoint returns links, not prose. Say so rather than
            # returning "" and looking like an empty document.
            "text": (
                f"{title}. Full text is published at the source links in "
                "`metadata`; this corpus serves enactments by reference."
            ),
            "url": _first_url(act.get("htmlUrl"), act.get("textUrl"), act.get("pdfUrl"))
            or _canonical_url(act_id),
            "metadata": {
                key: str(value)
                for key, value in (
                    ("category", act.get("category")),
                    ("year", act.get("year")),
                    ("chunksCount", act.get("chunksCount")),
                    ("pdfUrl", act.get("pdfUrl")),
                    ("textUrl", act.get("textUrl")),
                    ("htmlUrl", act.get("htmlUrl")),
                )
                if value
            },
        }


_REGISTRARS = {"US": _register_us, "IN": _register_in}


def register_aliases(
    mcp: FastMCP,
    client: httpx2.AsyncClient,
    jurisdiction: str,
    base_url: str,
    existing: set[str],
) -> None:
    """Add the `search` / `fetch` pair for one jurisdiction.

    `existing` is the catalogue the OpenAPI provider already published. If the
    backend ever publishes an operation that derives to `search` or `fetch`, the
    alias is skipped rather than shadowing it: a real endpoint losing its tool to
    a convenience shim would be a silent regression in what the server can do.
    """
    registrar = _REGISTRARS.get(jurisdiction)
    if registrar is None:
        return
    if {"search", "fetch"} & existing:
        return
    registrar(mcp, client, base_url)
