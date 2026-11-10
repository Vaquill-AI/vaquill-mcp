"""Five tools over the Indian regulatory corpus (`/api/v1/in/reg`).

WHY THIS MODULE DECLARES TOOLS BY HAND, WHEN `remote.py` SAYS NOT TO
===================================================================

Every other tool here is DERIVED from a published OpenAPI document, and that is
the right default. It is not available for this corpus yet: the proxy router
that serves `/in/reg` is not mounted, so no document describes it, and writing
a fixture of a spec the backend has not published would be a guess committed as
a fact. These five are therefore declared, in ONE place, as a closed set. The
tool count IS the endpoint count (docs/prd-regulatory-api/10-mcp-design.md,
section 1): adding a sixth is a deliberate edit that `test_regulatory_tools.py`
fails on, not something a convenience wrapper can slide in.

When the backend publishes the document, this module should shrink to the
descriptions and the error table, and the tools should come from the provider.

ONE SCOPE
=========

A key that holds `reg:read` can call all five tools: every publisher, every
operation, every document. There is no per-publisher scope and no per-tool
scope, and nothing here tests for one. A 403 means the key lacks `reg:read`.

WHAT THE TOOLS MUST NOT DO
==========================

* Substitute a default for an argument. An unset argument sends NOTHING, so the
  upstream's own default applies and is the only one. `facets=False` and
  `includePaused=False` are SENT as `false`: a truthiness test would drop them,
  and a dropped filter is a wrong answer with a 200.
* Join a list with commas. A list is a REPEATED key (`publisherId=a&publisherId=b`);
  the upstream rejects a comma with a 400, which the model reads and corrects.
* Collapse `content_not_archived` (404: we never held the bytes, never retry)
  into `archive_unavailable` (502: our archive failed, a retry is worth
  something). Reporting an outage as "the regulator published nothing" is the
  failure this corpus is sold to prevent.
* Relay an upstream 401 as a transport 401. Claude starts a sign-in on one and
  ChatGPT keys off `www-authenticate`, so a problem a firm cannot fix would send
  its client into a login loop. Every failure here is a tool error carrying
  text, never a protocol-level auth failure.
* Name the upstream host, the bearer, an archive storage key, or the service
  behind the proxy, in a tool result or a log line. Failures are raised OUTSIDE
  the `except` that caught the transport error, so no `__context__` carries an
  httpx message (which is the full URL) into a traceback.
* Log `q`, a cursor, an id or a response body. Fault lines carry the tool name,
  the status, the machine code and a request id, and nothing else.
* Cache a document or a file. Nothing here stores a response.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any, Literal

import httpx2
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import File
from mcp.types import EmbeddedResource
from pydantic import Field

from vaquill_mcp.descriptions import TOOL_TITLES
from vaquill_mcp.instructions import instructions_for
from vaquill_mcp.log_redaction import quiet_http_client_loggers
from vaquill_mcp.ordering import DeterministicToolOrder

logger = logging.getLogger(__name__)

#: The one corpus key `instructions_for` and the factory below use.
CORPUS = "REG"

#: Every route sits under the same prefix as the India legislation surface
#: (`/api/v1/in/...`). One constant, so a prefix change is one edit.
_PREFIX = "/api/v1/in/reg"

TOOL_NAMES: tuple[str, ...] = (
    "search_regulatory_documents",
    "get_regulatory_document",
    "get_regulatory_document_file",
    "list_regulatory_changes",
    "get_regulatory_coverage",
)

#: The only permission any of the five needs. Stated once so the 403 text and the
#: docs cannot drift apart.
SCOPE = "reg:read"

#: The file route returns archived bytes, which this server base64-encodes into
#: a single MCP message. 10 MiB is a safety rail on that message, not a product
#: statement about the corpus. A larger file is refused with text, never cut.
FILE_MAX_BYTES = 10 * 1024 * 1024

#: An error body is a small JSON envelope. Reading more than this is a sign the
#: response is not one.
_ERROR_BODY_MAX_BYTES = 64 * 1024

# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------
# What a model reads, and the product. Each is under 500 characters, states no
# credit number (costs are never hand-written here), and claims nothing the
# corpus cannot support: no "complete", no "in force", no amendment history.
# Every one is pinned by `test_model_facing_text_snapshot`-style tests in
# `test_regulatory_tools.py`, so a wording change is a reviewed change.

DESCRIPTIONS: dict[str, str] = {
    "search_regulatory_documents": (
        "Search Indian regulatory publications: circulars, orders, notices and "
        "press releases from named government regulators. Not case law, statute "
        "text or commentary. Capture began 2026-08-23, so an empty result for "
        "an earlier date does not mean nothing was published. Not an in-force "
        "view. Filters are lists. Pass nextCursor back unchanged while hasMore "
        "is true. total null means not measured; 0 means none."
    ),
    "get_regulatory_document": (
        "One regulatory document by id. Take the id from a search or change "
        "result, never guess one. include body for the extracted text (cheaper "
        "than the file), diff for what changed between the two most recent "
        "captures, versions for every capture. Check fileAvailability before "
        "asking for the file."
    ),
    "get_regulatory_document_file": (
        "The archived original of one document, returned as a file, not text; "
        "for text use get_regulatory_document with include body. Check "
        "fileAvailability first. content_not_archived means we never held the "
        "bytes: do not retry. archive_unavailable means our archive failed: "
        "retry later. Neither means the regulator did not publish it."
    ),
    "list_regulatory_changes": (
        "What is new, changed or retracted since a time or cursor. Use it to "
        "answer what moved since I last looked. The feed follows our capture "
        "order, not publishedAt, so an older document can appear after a newer "
        "one: that is correct, do not re-sort. Pass nextCursor back unchanged "
        "while hasMore is true."
    ),
    "get_regulatory_coverage": (
        "What the corpus covers and how fresh each source is. Use it before "
        "saying nothing was published. A stale or failing source means our "
        "crawl is behind, not that the publisher stopped: say so. A null time "
        "means not yet checked, never fine."
    ),
}

_PUBLISHER = (
    "Publisher ids, e.g. ['sebi', 'rbi']. A list of separate values, never "
    "one comma-joined string."
)
_SOURCE = (
    "Source ids (a publisher's sub-sources, as get_regulatory_coverage lists "
    "them). A list of separate values, never comma-joined."
)
_DOC_TYPE = "Document types as returned in results. A list of separate values."
_CURSOR = (
    "Opaque. Pass back exactly the nextCursor of the previous page. Do not "
    "parse, edit or construct one."
)
_LIMIT = "Page size, 1 to 100. Omit to use the default."
_DATE = "ISO date, YYYY-MM-DD, as the publisher dated it."

_Items = Annotated[str, Field(min_length=1, max_length=64)]
_Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RegulatoryToolError(ToolError):
    """A failure the model reads as text and can act on.

    `code` is the machine reason and is also the first word of the text, so a
    model and a test branch on the same thing. `retryable` says whether trying
    again can change the answer: it is the field that keeps `content_not_archived`
    (never) and `archive_unavailable` (later) apart.
    """

    def __init__(
        self, code: str, message: str, *, status: int = 0, retryable: bool = False
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.status = status
        self.retryable = retryable


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")

#: Text that must never reach a model, whatever the upstream put in a message.
_LEAK_MARKERS = (
    "vq_key_",
    "bearer ",
    "http://",
    "https://",
    "tracker-web",
    "v1_api_token",
    "reg_upstream",
    "x-powered-by",
    "next.js",
)

_DO_NOT_ANSWER = (
    "Do not report that the document or regulation does not exist, and do not "
    "answer from memory. Tell the user the lookup failed."
)


def _safe_message(response: httpx2.Response, message: object) -> str | None:
    """An upstream 400's message, or None when it should not be shown.

    The message names the field the caller got wrong, which is what lets a model
    fix its call, so it is the one piece of upstream text relayed. It is dropped
    whole when it carries anything that looks like a credential, a URL, a
    service name, or the exact bearer or host THIS request used: a message
    cannot be partly scrubbed with any confidence.
    """
    if not isinstance(message, str):
        return None
    text = _CONTROL_RE.sub(" ", message).strip()
    if not text:
        return None
    lowered = text.lower()
    request = response.request
    sensitive = [m for m in _LEAK_MARKERS]
    auth = request.headers.get("authorization", "")
    token = auth.split(" ", 1)[-1].strip() if auth else ""
    if token:
        sensitive.append(token.lower())
    if request.url.host:
        sensitive.append(request.url.host.lower())
    if any(s and s in lowered for s in sensitive):
        return None
    return text[:300]


def _envelope(body: bytes) -> tuple[str | None, object]:
    """`(code, message)` from the error envelope, whichever of its shapes arrived.

    The backend wraps `{"error": {...}}` in FastAPI's `{"detail": ...}` on some
    paths and not others, so all three spellings are read: `{"error": {}}`,
    `{"detail": {"error": {}}}` and `{"detail": {}}`.
    """
    import json

    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    node: object = parsed.get("error")
    if not isinstance(node, dict):
        detail = parsed.get("detail")
        node = detail.get("error") if isinstance(detail, dict) and isinstance(
            detail.get("error"), dict
        ) else detail
    if not isinstance(node, dict):
        return None, None
    code = node.get("code")
    return (code.lower() if isinstance(code, str) else None), node.get("message")


def _retry_after(response: httpx2.Response) -> int | None:
    raw = response.headers.get("retry-after", "").strip()
    return max(1, min(60, int(raw))) if raw.isascii() and raw.isdigit() else None


def _request_id(response: httpx2.Response, code_body: bytes) -> str | None:
    value = response.headers.get("x-request-id", "").strip()
    return value if _REQUEST_ID_RE.match(value) else None


def _again(seconds: int | None) -> str:
    return f"Retry in {seconds} seconds." if seconds else "Retry shortly."


def classify_failure(
    tool: str, response: httpx2.Response, body: bytes
) -> RegulatoryToolError:
    """The section 6 table of the design, as one pure function.

    Takes the response and its (capped) body and returns the error to raise. It
    never relays upstream text except a 400's message, after `_safe_message`.
    """
    status = response.status_code
    code, message = _envelope(body)
    is_file = tool == "get_regulatory_document_file"
    retry = _retry_after(response)
    ref = _request_id(response, body)
    incident = f" Incident reference: {ref}." if ref else ""

    if status == 400:
        shown = _safe_message(response, message)
        return RegulatoryToolError(
            "invalid_request",
            f"{shown} Fix the parameter and call again."
            if shown
            else "The request was not accepted. Check the parameters and call again.",
            status=400,
        )
    if status == 404:
        if is_file and code == "content_not_archived":
            return RegulatoryToolError(
                "content_not_archived",
                "We hold no archived file for this document: it may come from a "
                "metadata-only source, or the id may not exist. Do not retry. "
                "Check fileAvailability from get_regulatory_document, or use "
                "include body there for the text. This does not mean the "
                "regulator did not publish it.",
                status=404,
            )
        return RegulatoryToolError(
            "not_found",
            "No document with that id. Use an id from search_regulatory_documents "
            "or list_regulatory_changes; do not guess one.",
            status=404,
        )
    if status == 402:
        return RegulatoryToolError(
            "insufficient_credits",
            "The account behind this API key has no credits left for this call. "
            "Do not retry. Ask the account owner to add credits.",
            status=402,
        )
    if status == 401:
        return RegulatoryToolError(
            "unauthorized",
            "The API key was not accepted. Check the key configured for this "
            "connector. Do not retry.",
            status=401,
        )
    if status == 403:
        return RegulatoryToolError(
            "forbidden",
            f"This API key cannot read the regulatory corpus. It needs the "
            f"{SCOPE} permission. Do not retry.",
            status=403,
        )
    if status == 429 or (status == 503 and code == "busy"):
        return RegulatoryToolError(
            "busy",
            f"The corpus is busy. {_again(retry)}",
            status=status,
            retryable=True,
        )
    if status == 502 and is_file and code == "archive_unavailable":
        # NOT content_not_archived. Our archive failed; the document exists.
        return RegulatoryToolError(
            "archive_unavailable",
            "Our archive could not return this file just now. Retry in a little "
            "while. This does not mean the document is missing or was never "
            "published.",
            status=502,
            retryable=True,
        )
    if status == 502:
        return RegulatoryToolError(
            "bad_upstream_response",
            f"The corpus returned an unusable response. {_DO_NOT_ANSWER}{incident}",
            status=502,
            retryable=True,
        )
    return RegulatoryToolError(
        "upstream_unavailable",
        f"The corpus is temporarily unreachable. {_DO_NOT_ANSWER} {_again(retry)}",
        status=status,
        retryable=True,
    )


def _unreachable() -> RegulatoryToolError:
    return RegulatoryToolError(
        "upstream_unavailable",
        f"The corpus is temporarily unreachable. {_DO_NOT_ANSWER} Retry shortly.",
        retryable=True,
    )


def _too_large() -> RegulatoryToolError:
    return RegulatoryToolError(
        "response_too_large",
        "This file is larger than this connector returns. Use "
        "get_regulatory_document with include body for the text instead.",
        status=200,
    )


def _log_fault(tool: str, error: RegulatoryToolError, response: httpx2.Response | None) -> None:
    """Fields only. No URL, no host, no header, no parameter, no body."""
    logger.warning(
        "regulatory_fault tool=%s status=%s code=%s retryable=%s request_id=%s",
        tool,
        error.status or None,
        error.code,
        error.retryable,
        (
            _request_id(response, b"")
            if response is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Query building and the HTTP call
# ---------------------------------------------------------------------------


def build_query(**params: object) -> list[tuple[str, str]]:
    """Turn tool arguments into query pairs, with no default of our own.

    * `None` means the caller did not say: NOTHING is sent.
    * A bool is sent as `true` or `false`. It is tested with `is None`, never
      truthiness, because `False` is a real answer.
    * A list is a REPEATED key, in the order given, never a comma join.
    """
    pairs: list[tuple[str, str]] = []
    for name, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            pairs.append((name, "true" if value else "false"))
        elif isinstance(value, (list, tuple)):
            pairs.extend((name, str(item)) for item in value)
        else:
            pairs.append((name, str(value)))
    return pairs


async def _get(
    client: httpx2.AsyncClient,
    tool: str,
    path: str,
    pairs: list[tuple[str, str]],
    *,
    max_bytes: int,
) -> tuple[httpx2.Response, bytes]:
    """GET and return `(response, body)` for a 200, or raise a mapped error.

    The body is streamed and stopped at `max_bytes` so a file cannot be pulled
    into memory whole. Every raise happens AFTER the `try`/`except` has closed,
    so the exception carries no `__context__`: an httpx error's message is the
    full URL, and a traceback printing it would put the host in a log.
    """
    response: httpx2.Response | None = None
    body = b""
    over = False
    failed = False
    try:
        async with client.stream("GET", path, params=pairs) as streamed:
            response = streamed
            cap = max_bytes if streamed.status_code == 200 else _ERROR_BODY_MAX_BYTES
            chunks: list[bytes] = []
            size = 0
            async for chunk in streamed.aiter_bytes():
                size += len(chunk)
                if size > cap:
                    over = True
                    break
                chunks.append(chunk)
            body = b"".join(chunks)
    except httpx2.HTTPError:
        failed = True

    if failed or response is None:
        error = _unreachable()
        _log_fault(tool, error, None)
        raise error
    if response.status_code == 200:
        if over:
            error = _too_large()
            _log_fault(tool, error, response)
            raise error
        return response, body
    error = classify_failure(tool, response, b"" if over else body)
    _log_fault(tool, error, response)
    raise error


async def _get_json(
    client: httpx2.AsyncClient, tool: str, path: str, pairs: list[tuple[str, str]]
) -> dict[str, Any]:
    response, body = await _get(client, tool, path, pairs, max_bytes=16 * 1024 * 1024)
    parsed: object = None
    try:
        import json

        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        parsed = None
    if not isinstance(parsed, dict):
        # A 200 that is not a JSON object is the upstream failing, and must read
        # as that: passing it through would hand the model something it cannot use
        # and nothing to say about it.
        error = RegulatoryToolError(
            "bad_upstream_response",
            f"The corpus returned an unusable response. {_DO_NOT_ANSWER}",
            status=502,
            retryable=True,
        )
        _log_fault(tool, error, response)
        raise error
    return parsed


def _file_name(response: httpx2.Response, doc_id: int) -> str:
    match = _FILENAME_RE.search(response.headers.get("content-disposition", ""))
    raw = match.group(1) if match else ""
    name = _UNSAFE_NAME_RE.sub("_", raw.rsplit("/", 1)[-1]).strip("._")[:120]
    return name or f"regulatory-document-{doc_id}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
}


def _annotations(name: str) -> dict[str, object]:
    return {**_READ_ONLY, "title": TOOL_TITLES[name]}


def register_regulatory_tools(mcp: FastMCP, client: httpx2.AsyncClient) -> None:
    """Publish exactly the five tools in `TOOL_NAMES` on `mcp`.

    `client` carries the credential (a fixed header for one user, an `httpx2.Auth`
    that reads the caller's key per request for the hosted server). It never
    reaches a tool parameter: a model that can see a credential can print it.
    """

    @mcp.tool(
        name="search_regulatory_documents",
        title=TOOL_TITLES["search_regulatory_documents"],
        description=DESCRIPTIONS["search_regulatory_documents"],
        annotations=_annotations("search_regulatory_documents"),
    )
    async def search_regulatory_documents(
        q: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=200,
                description="Words to search for. Omit to list by filters alone.",
            ),
        ] = None,
        publisherId: Annotated[list[_Items] | None, Field(description=_PUBLISHER)] = None,
        sourceId: Annotated[list[_Items] | None, Field(description=_SOURCE)] = None,
        docType: Annotated[list[_Items] | None, Field(description=_DOC_TYPE)] = None,
        id: Annotated[  # noqa: A002 - the wire name
            list[Annotated[int, Field(ge=1)]] | None,
            Field(
                max_length=100,
                description="Fetch these document ids in one call, instead of searching.",
            ),
        ] = None,
        publishedFrom: Annotated[_Day | None, Field(description=_DATE)] = None,
        publishedTo: Annotated[_Day | None, Field(description=_DATE)] = None,
        sort: Annotated[
            Literal["relevance", "published", "first_seen"] | None,
            Field(description="Order of results. Omit for the default."),
        ] = None,
        facets: Annotated[
            bool | None,
            Field(description="True to also return the filter values available."),
        ] = None,
        limit: Annotated[int | None, Field(ge=1, le=100, description=_LIMIT)] = None,
        cursor: Annotated[
            str | None, Field(min_length=1, max_length=512, description=_CURSOR)
        ] = None,
    ) -> dict[str, Any]:
        return await _get_json(
            client,
            "search_regulatory_documents",
            f"{_PREFIX}/documents",
            build_query(
                q=q,
                publisherId=publisherId,
                sourceId=sourceId,
                docType=docType,
                id=id,
                publishedFrom=publishedFrom,
                publishedTo=publishedTo,
                sort=sort,
                facets=facets,
                limit=limit,
                cursor=cursor,
            ),
        )

    @mcp.tool(
        name="get_regulatory_document",
        title=TOOL_TITLES["get_regulatory_document"],
        description=DESCRIPTIONS["get_regulatory_document"],
        annotations=_annotations("get_regulatory_document"),
    )
    async def get_regulatory_document(
        id: Annotated[  # noqa: A002 - the wire name
            int,
            Field(ge=1, description="Document id from a search or change result."),
        ],
        include: Annotated[
            list[Literal["body", "versions", "diff"]] | None,
            Field(description="Extra parts to return. Omit for metadata only."),
        ] = None,
    ) -> dict[str, Any]:
        return await _get_json(
            client,
            "get_regulatory_document",
            f"{_PREFIX}/documents/{int(id)}/detail",
            build_query(include=include),
        )

    @mcp.tool(
        name="get_regulatory_document_file",
        title=TOOL_TITLES["get_regulatory_document_file"],
        description=DESCRIPTIONS["get_regulatory_document_file"],
        annotations=_annotations("get_regulatory_document_file"),
    )
    async def get_regulatory_document_file(
        id: Annotated[  # noqa: A002 - the wire name
            int,
            Field(ge=1, description="Document id from a search or change result."),
        ],
    ) -> EmbeddedResource:
        response, body = await _get(
            client,
            "get_regulatory_document_file",
            f"{_PREFIX}/documents/{int(id)}/file",
            [],
            max_bytes=FILE_MAX_BYTES,
        )
        mime = response.headers.get("content-type", "").split(";", 1)[0].strip()
        return File(data=body, name=_file_name(response, int(id))).to_resource_content(
            mime_type=mime or "application/octet-stream"
        )

    @mcp.tool(
        name="list_regulatory_changes",
        title=TOOL_TITLES["list_regulatory_changes"],
        description=DESCRIPTIONS["list_regulatory_changes"],
        annotations=_annotations("list_regulatory_changes"),
    )
    async def list_regulatory_changes(
        since: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=40,
                description="ISO date or timestamp. Omit to continue from a cursor.",
            ),
        ] = None,
        publisherId: Annotated[list[_Items] | None, Field(description=_PUBLISHER)] = None,
        sourceId: Annotated[list[_Items] | None, Field(description=_SOURCE)] = None,
        docType: Annotated[list[_Items] | None, Field(description=_DOC_TYPE)] = None,
        changeKind: Annotated[
            list[Literal["new", "rechanged", "retracted"]] | None,
            Field(description="Kinds of change to include. Omit for all."),
        ] = None,
        limit: Annotated[int | None, Field(ge=1, le=100, description=_LIMIT)] = None,
        cursor: Annotated[
            str | None, Field(min_length=1, max_length=512, description=_CURSOR)
        ] = None,
    ) -> dict[str, Any]:
        return await _get_json(
            client,
            "list_regulatory_changes",
            f"{_PREFIX}/changes",
            build_query(
                since=since,
                publisherId=publisherId,
                sourceId=sourceId,
                docType=docType,
                changeKind=changeKind,
                limit=limit,
                cursor=cursor,
            ),
        )

    @mcp.tool(
        name="get_regulatory_coverage",
        title=TOOL_TITLES["get_regulatory_coverage"],
        description=DESCRIPTIONS["get_regulatory_coverage"],
        annotations=_annotations("get_regulatory_coverage"),
    )
    async def get_regulatory_coverage(
        publisherId: Annotated[list[_Items] | None, Field(description=_PUBLISHER)] = None,
        includePaused: Annotated[
            bool | None,
            Field(description="True to include sources we have paused."),
        ] = None,
    ) -> dict[str, Any]:
        return await _get_json(
            client,
            "get_regulatory_coverage",
            f"{_PREFIX}/sources",
            build_query(publisherId=publisherId, includePaused=includePaused),
        )


def create_regulatory_server(client: httpx2.AsyncClient | None = None) -> FastMCP:
    """A server whose whole catalogue is the five regulatory tools.

    A SEPARATE server rather than five more tools on the US or India one, because
    one connection should see one corpus (a client pays for every tool in every
    turn) and because the live catalogues' exact-count tests and snapshots are
    what protect the connector people already use.

    `client` is supplied by a hosted entry point, which owns its credential and
    its lifetime. Without one, a client is built from the same environment the
    stdio server reads (`VAQUILL_API_KEY`, `VAQUILL_BASE_URL`,
    `VAQUILL_TIMEOUT`) and closed with the server.
    """
    # The httpx2 INFO line is the full request URL: host, path and `?q=`.
    # Held down here as well as in `remote_main`, because this server must not
    # depend on a different entry point having done it.
    quiet_http_client_loggers()

    lifespan = None
    if client is None:
        import contextlib

        from vaquill_mcp import __version__
        from vaquill_mcp.client_identity import make_client_stamp
        from vaquill_mcp.config import get_api_key, get_base_url, get_timeout

        user_agent = f"vaquill-mcp/{__version__}"
        owned = httpx2.AsyncClient(
            base_url=get_base_url(),
            headers={
                "Authorization": f"Bearer {get_api_key()}",
                "User-Agent": user_agent,
            },
            event_hooks={"request": [make_client_stamp(user_agent)]},
            timeout=httpx2.Timeout(get_timeout(), connect=10.0),
        )
        client = owned

        @contextlib.asynccontextmanager
        async def lifespan(_server: FastMCP):  # type: ignore[no-redef]
            try:
                yield
            finally:
                await owned.aclose()

    mcp = FastMCP(
        name="Vaquill Indian Regulatory",
        instructions=instructions_for(CORPUS),
        lifespan=lifespan,
    )
    register_regulatory_tools(mcp, client)
    mcp.add_middleware(DeterministicToolOrder())
    return mcp
