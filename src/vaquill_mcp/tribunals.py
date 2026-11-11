"""Six tools over the Indian tribunals corpus (`/api/v1/in/tribunals`).

WHY THIS MODULE DECLARES TOOLS BY HAND
======================================

The same reason `regulatory.py` does, and the comment there is the long version:
tools are normally DERIVED from a published OpenAPI document, and no document
describes this surface yet. So these six are declared, in ONE place, as a closed
set. The tool count IS the endpoint count, and `test_tribunals_tools.py` fails on
a seventh, so a convenience wrapper cannot slide in beside them.

WHY A SEPARATE SERVER AND NOT SIX MORE TOOLS ON THE INDIA ONE
=============================================================

`create_regulatory_server` gives the rule: one connection should see one corpus,
because a client pays for every tool in every turn. This corpus is also a
different PERMISSION (`cases:read`, not `reg:read`) and a different kind of
material: 813,168 tribunal, regulator and board MATTERS, which are case records,
where `/in/reg` holds published documents and `/in/acts` holds legislation.

WHAT THIS CORPUS IS NOT, AND WHY EVERY DESCRIPTION SAYS SO
==========================================================

🔴 **There is no judgment text anywhere in it.** No `full_text`, `body`,
`content`, `ocr` or `markdown` column exists on any relation behind this API.
Search covers `title`, `petitioner` and `respondent`, and nothing else. A model
that reads "search Indian tribunal decisions" and infers it can find a matter by
a phrase in its reasoning will report a false negative on a corpus that holds the
document and serves it through `get_india_tribunal_matter_file`. Every search
result carries `searchScope` and `searchedFields` as FIELDS so a model can read
what was actually matched rather than infer it, and the description says it in
words as well.

It is also not a citator: nothing here says whether a matter is still good law,
and no description may imply it.

WHAT THE TOOLS MUST NOT DO
==========================

The `regulatory.py` list applies in full, and this corpus adds three:

* Infer the forum from the caseId. A caseId carries a forum prefix in practice
  (`APTEL_APPEALNO62OF2010`), and parsing it would make the primary key depend on
  a naming habit of fifteen separate scrapers. Identity is the PAIR
  `(tribunal, caseId)`, and both are separate arguments.
* Report "no orders" when the answer is unknown. Twelve of the fifteen forums
  publish no order list at all (`not_applicable`), and 2,694 DRT matters have no
  join key (`unjoinable`). Both are distinct from "the list ran and was empty",
  and flattening them asserts something the lookup never established.
* Report a missing document as a missing matter. `matter_file_unavailable` means
  the matter exists and we hold no file for it, which is routine in one forum
  (27,407 of NCLT's 63,487) and rare elsewhere.

ONE SCOPE
=========

A key that holds `cases:read` can call all six. There is no per-forum scope: the
corpus is public regulatory and judicial material from one country, and a second
scope would make an existing customer re-issue a key to reach it without
protecting anything new.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import httpx2
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.utilities.types import File
from mcp.types import EmbeddedResource
from pydantic import Field

from vaquill_mcp.instructions import instructions_for
from vaquill_mcp.log_redaction import quiet_http_client_loggers
from vaquill_mcp.ordering import DeterministicToolOrder

# The transport, error mapping and query building are SHARED with the regulatory
# server, deliberately. They encode decisions that are not corpus-specific (never
# relay an upstream 401 as a protocol 401, never log a query or a cursor, raise
# outside the `except` so no httpx URL reaches a traceback, send nothing for an
# unset argument, a list is a repeated key). Copying them would mean two places
# to fix the next leak, and the leak tests only cover the copy they import.
from vaquill_mcp.regulatory import (
    _ERROR_BODY_MAX_BYTES,  # noqa: F401  (re-exported for the tests that pin the cap)
    _flatten_optional,
    _get,
    _get_json,
    build_query,
)

logger = logging.getLogger(__name__)

#: The corpus key `instructions_for` uses. Listed in `instructions.py`; an
#: unlisted key silently tells every client it serves United States law.
CORPUS = "TRIB"

#: One constant, so a prefix change is one edit.
_PREFIX = "/api/v1/in/tribunals"

TOOL_NAMES: tuple[str, ...] = (
    "list_india_tribunal_forums",
    "search_india_tribunal_matters",
    "browse_india_tribunal_matters",
    "get_india_tribunal_matter",
    "list_india_tribunal_matter_orders",
    "get_india_tribunal_matter_file",
)

#: Where each tool goes, with `{tribunal}` and `{caseId}` left in. ONE table,
#: read by the tools and by the price lookup, so a route cannot be changed in one
#: place and still be priced against its old spelling.
TOOL_ROUTES: dict[str, str] = {
    "list_india_tribunal_forums": f"{_PREFIX}/forums",
    "search_india_tribunal_matters": f"{_PREFIX}/matters/search",
    "browse_india_tribunal_matters": f"{_PREFIX}/matters",
    "get_india_tribunal_matter": f"{_PREFIX}/matters/{{tribunal}}/{{caseId}}/detail",
    "list_india_tribunal_matter_orders": f"{_PREFIX}/matters/{{tribunal}}/{{caseId}}/orders",
    "get_india_tribunal_matter_file": f"{_PREFIX}/matters/{{tribunal}}/{{caseId}}/file",
}

#: The only permission any of the six needs. Stated once so the 403 text and the
#: docs cannot drift apart.
SCOPE = "cases:read"

#: A matter's document, base64-encoded into a single MCP message. A safety rail on
#: that message, not a product statement. A larger file is refused with text.
FILE_MAX_BYTES = 10 * 1024 * 1024

TOOL_TITLES: dict[str, str] = {
    "list_india_tribunal_forums": "Indian Tribunals and Forums",
    "search_india_tribunal_matters": "Search Indian Tribunal Matters",
    "browse_india_tribunal_matters": "Browse Indian Tribunal Matters",
    "get_india_tribunal_matter": "Indian Tribunal Matter",
    "list_india_tribunal_matter_orders": "Orders in an Indian Tribunal Matter",
    "get_india_tribunal_matter_file": "Indian Tribunal Matter Document",
}

# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------
# What a model reads, and the product. No credit number is written here (costs
# arrive from the live pricing matrix; a hand-keyed price is what drifted
# silently for a month), nothing claims completeness or "in force", and the
# metadata-only limit is stated wherever a model could otherwise assume text.

_NO_TEXT = (
    "Searches case METADATA only: title, petitioner and respondent. This corpus "
    "holds no judgment text, so a matter that does not match your words may still "
    "discuss them at length in a document we hold. Never report that no Indian "
    "tribunal has considered something because this returned nothing."
)

DESCRIPTIONS: dict[str, str] = {
    "list_india_tribunal_forums": (
        "The Indian tribunals, regulators and boards whose matters are held, with each one's "
        "record count, earliest decision year, judgment split and how many of its documents are "
        "available. Free. Call it first: it carries the forum code every other tool takes, and "
        "the per-forum document coverage, which ranges from complete to 43 percent missing."
    ),
    "search_india_tribunal_matters": (
        "Find Indian tribunal matters by party name or case title, narrowed by forum, bench, "
        f"year or decision date. {_NO_TEXT} Returns searchScope and searchedFields on every page "
        "so you can state what was matched."
    ),
    "browse_india_tribunal_matters": (
        "List Indian tribunal matters by forum, bench, year, decision date or exact case number, "
        "newest decision first. Use this rather than search when you have a case number or want "
        "a forum's recent matters. Paged with a cursor; there is no total count."
    ),
    "get_india_tribunal_matter": (
        "Everything held about one matter: title, parties, bench, members, case number, decision "
        "date and how far that date can be trusted, document availability, the forum's own link, "
        "and the order list where the forum keeps one. Takes the forum code and the caseId "
        "together. This is the authority on the decision date and can differ from the same "
        "matter in a list result. It says nothing about whether the matter is still good law."
    ),
    "list_india_tribunal_matter_orders": (
        "The individual orders a forum recorded against one matter, newest first. Read status "
        "before the list: listed means the lookup ran and an empty list is a real answer; "
        "not_applicable means this forum publishes no order list, which is true of twelve of the "
        "fifteen; unjoinable means the key needed to find them is missing, so whether orders "
        "exist is UNKNOWN. Never report unjoinable or not_applicable as no orders."
    ),
    "get_india_tribunal_matter_file": (
        "The document held for one matter, as a file. Check hasFile on the matter first. A matter "
        "with no document is a settled answer, not an outage, and does not mean the forum "
        "published nothing: it is routine in NCLT and rare elsewhere. One document per matter in "
        "every forum, including the three that also publish an order list."
    ),
}

# ---------------------------------------------------------------------------
# Parameter text, shared so two tools cannot describe one filter differently
# ---------------------------------------------------------------------------

_TRIBUNAL_FILTER = (
    "Narrow to one forum, by the code from list_india_tribunal_forums. An unknown code is "
    "refused and not charged; do not guess one."
)
_TRIBUNAL_PATH = (
    "The forum's code, from list_india_tribunal_forums. Required, and never inferred from the "
    "caseId: the prefix a caseId carries is a scraper's naming habit, not the forum."
)
_CASE_ID = "The matter's id within its forum, exactly as a search or list returned it, prefix included."
_YEAR = (
    "Decision year. Matches the stored year, which is not derived from the decision date and can "
    "outlive it: some matters carry a year while serving no date at all, so read dateQuality on "
    "each result rather than assuming a filtered matter has a date."
)
_BENCH = (
    "The bench or seat, matched EXACTLY as the forum spells it, which is usually a slug rather "
    "than a readable name (cat_cis_guwahati, nclt_ahmedabad). Send back a value taken from a "
    "matter's bench field: a name you compose matches nothing."
)
_IS_JUDGMENT = (
    "True for judgments, false for everything else. READ PER FORUM: false means a case listing "
    "with no document in NCLT, and an order sheet we hold in CAT CIS."
)
_DATE = "ISO date, YYYY-MM-DD. Not a UNIX timestamp, which is refused."
_LIMIT = "Page size, 1 to 100. Omit to use the default."
_CURSOR = (
    "The nextCursor from the previous page, byte for byte. Omit for the first page. Do not build "
    "one, and do not use it as an offset."
)

_Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
_Code = Annotated[str, Field(min_length=1, max_length=32)]
_CaseId = Annotated[str, Field(min_length=1, max_length=1024)]

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
}


def _annotations(name: str) -> dict[str, object]:
    return {**_READ_ONLY, "title": TOOL_TITLES[name]}


def _described(tool: str, costs: dict[str, str] | None) -> str:
    """The description a model reads: the hand-written text plus a price line.

    The price is NEVER written in `DESCRIPTIONS` (a test fails on a number there). It arrives
    from the live pricing matrix, exactly as it does for every derived tool. No entry means no
    line, never a guessed one.
    """
    line = (costs or {}).get(tool, "")
    return f"{DESCRIPTIONS[tool]} {line}" if line else DESCRIPTIONS[tool]


def _file_name(tribunal: str, case_id: str) -> str:
    """A download name from the pair, with anything exotic dropped and the length capped.

    Built from the ARGUMENTS rather than the upstream `content-disposition`, because the longest
    caseId in this corpus is 635 characters and the upstream name is derived from it.
    """
    safe = "".join(ch for ch in case_id if ch.isalnum() or ch in "_-.")[:120]
    return f"{tribunal}_{safe or 'matter'}"


class _FlattenOptionalParams(Middleware):
    """Publish the flat parameter shape whatever the interpreter emits.

    The same py3.10 `anyOf` nesting `regulatory._flatten_optional` documents: on 3.10 every
    optional parameter publishes NO description where a client looks for one. Scoped to this
    module's `TOOL_NAMES` so a tool it did not define is left as its own server built it.
    """

    async def on_list_tools(self, context, call_next):  # type: ignore[no-untyped-def]
        tools = await call_next(context)
        for tool in tools:
            if tool.name in TOOL_NAMES:
                _flatten_optional(tool.parameters)
        return tools


def register_tribunals_tools(
    mcp: FastMCP, client: httpx2.AsyncClient, costs: dict[str, str] | None = None
) -> None:
    """Publish exactly the six tools in `TOOL_NAMES` on `mcp`.

    `costs` is `{tool: "Cost: 1 credit."}` from `tribunals_costs`. Omitted, the descriptions
    carry no price line, which is what the offline tests want.

    `client` carries the credential. It never reaches a tool parameter: a model that can see a
    credential can print it.
    """

    @mcp.tool(
        name="list_india_tribunal_forums",
        title=TOOL_TITLES["list_india_tribunal_forums"],
        description=_described("list_india_tribunal_forums", costs),
        annotations=_annotations("list_india_tribunal_forums"),
    )
    async def list_india_tribunal_forums() -> dict[str, Any]:
        # No parameters at all: the whole registry is one small page, and a filter here would
        # only let a model ask for a forum code it does not yet have.
        return await _get_json(
            client,
            "list_india_tribunal_forums",
            TOOL_ROUTES["list_india_tribunal_forums"],
            [],
        )

    @mcp.tool(
        name="search_india_tribunal_matters",
        title=TOOL_TITLES["search_india_tribunal_matters"],
        description=_described("search_india_tribunal_matters", costs),
        annotations=_annotations("search_india_tribunal_matters"),
    )
    async def search_india_tribunal_matters(
        q: Annotated[
            str,
            Field(
                min_length=2,
                max_length=200,
                description=(
                    "Party name or words from the case title. Matched against title, petitioner "
                    "and respondent only, never against judgment text."
                ),
            ),
        ],
        tribunal: Annotated[_Code | None, Field(description=_TRIBUNAL_FILTER)] = None,
        year: Annotated[int | None, Field(ge=1800, le=2100, description=_YEAR)] = None,
        decidedFrom: Annotated[_Day | None, Field(description=_DATE)] = None,
        decidedTo: Annotated[_Day | None, Field(description=_DATE)] = None,
        isJudgment: Annotated[bool | None, Field(description=_IS_JUDGMENT)] = None,
        bench: Annotated[
            Annotated[str, Field(min_length=1, max_length=200)] | None,
            Field(description=_BENCH),
        ] = None,
        limit: Annotated[int | None, Field(ge=1, le=100, description=_LIMIT)] = None,
        cursor: Annotated[
            str | None, Field(min_length=1, max_length=512, description=_CURSOR)
        ] = None,
    ) -> dict[str, Any]:
        return await _get_json(
            client,
            "search_india_tribunal_matters",
            TOOL_ROUTES["search_india_tribunal_matters"],
            build_query(
                q=q,
                tribunal=tribunal,
                year=year,
                decidedFrom=decidedFrom,
                decidedTo=decidedTo,
                isJudgment=isJudgment,
                bench=bench,
                limit=limit,
                cursor=cursor,
            ),
        )

    @mcp.tool(
        name="browse_india_tribunal_matters",
        title=TOOL_TITLES["browse_india_tribunal_matters"],
        description=_described("browse_india_tribunal_matters", costs),
        annotations=_annotations("browse_india_tribunal_matters"),
    )
    async def browse_india_tribunal_matters(
        tribunal: Annotated[_Code | None, Field(description=_TRIBUNAL_FILTER)] = None,
        year: Annotated[int | None, Field(ge=1800, le=2100, description=_YEAR)] = None,
        decidedFrom: Annotated[_Day | None, Field(description=_DATE)] = None,
        decidedTo: Annotated[_Day | None, Field(description=_DATE)] = None,
        isJudgment: Annotated[bool | None, Field(description=_IS_JUDGMENT)] = None,
        bench: Annotated[
            Annotated[str, Field(min_length=1, max_length=200)] | None,
            Field(description=_BENCH),
        ] = None,
        caseNumber: Annotated[
            Annotated[str, Field(min_length=1, max_length=200)] | None,
            Field(
                description=(
                    "The forum's own case number, matched EXACTLY. This is the filter to use when "
                    "you have a number; it is not a search target, because no index supports a "
                    "partial match on it."
                )
            ),
        ] = None,
        limit: Annotated[int | None, Field(ge=1, le=100, description=_LIMIT)] = None,
        cursor: Annotated[
            str | None, Field(min_length=1, max_length=512, description=_CURSOR)
        ] = None,
    ) -> dict[str, Any]:
        return await _get_json(
            client,
            "browse_india_tribunal_matters",
            TOOL_ROUTES["browse_india_tribunal_matters"],
            build_query(
                tribunal=tribunal,
                year=year,
                decidedFrom=decidedFrom,
                decidedTo=decidedTo,
                isJudgment=isJudgment,
                bench=bench,
                caseNumber=caseNumber,
                limit=limit,
                cursor=cursor,
            ),
        )

    @mcp.tool(
        name="get_india_tribunal_matter",
        title=TOOL_TITLES["get_india_tribunal_matter"],
        description=_described("get_india_tribunal_matter", costs),
        annotations=_annotations("get_india_tribunal_matter"),
    )
    async def get_india_tribunal_matter(
        tribunal: Annotated[_Code, Field(description=_TRIBUNAL_PATH)],
        caseId: Annotated[_CaseId, Field(description=_CASE_ID)],
    ) -> dict[str, Any]:
        return await _get_json(
            client,
            "get_india_tribunal_matter",
            _matter_path("get_india_tribunal_matter", tribunal, caseId),
            [],
        )

    @mcp.tool(
        name="list_india_tribunal_matter_orders",
        title=TOOL_TITLES["list_india_tribunal_matter_orders"],
        description=_described("list_india_tribunal_matter_orders", costs),
        annotations=_annotations("list_india_tribunal_matter_orders"),
    )
    async def list_india_tribunal_matter_orders(
        tribunal: Annotated[_Code, Field(description=_TRIBUNAL_PATH)],
        caseId: Annotated[_CaseId, Field(description=_CASE_ID)],
    ) -> dict[str, Any]:
        return await _get_json(
            client,
            "list_india_tribunal_matter_orders",
            _matter_path("list_india_tribunal_matter_orders", tribunal, caseId),
            [],
        )

    @mcp.tool(
        name="get_india_tribunal_matter_file",
        title=TOOL_TITLES["get_india_tribunal_matter_file"],
        description=_described("get_india_tribunal_matter_file", costs),
        annotations=_annotations("get_india_tribunal_matter_file"),
    )
    async def get_india_tribunal_matter_file(
        tribunal: Annotated[_Code, Field(description=_TRIBUNAL_PATH)],
        caseId: Annotated[_CaseId, Field(description=_CASE_ID)],
    ) -> EmbeddedResource:
        response, body = await _get(
            client,
            "get_india_tribunal_matter_file",
            _matter_path("get_india_tribunal_matter_file", tribunal, caseId),
            [],
            max_bytes=FILE_MAX_BYTES,
        )
        mime = response.headers.get("content-type", "").split(";", 1)[0].strip()
        return File(data=body, name=_file_name(tribunal, caseId)).to_resource_content(
            mime_type=mime or "application/octet-stream"
        )

    # Flatten every tool on the way out, in ONE place, so a seventh is covered the day it is
    # added. Middleware rather than inline because the registered Tool is only reachable through
    # an async accessor and this function is sync.
    mcp.add_middleware(_FlattenOptionalParams())


def _matter_path(tool: str, tribunal: str, case_id: str) -> str:
    """The route for one matter, with both segments percent-encoded.

    `quote` with an EMPTY `safe`, so a `/` or a `?` in either value cannot change which route is
    called. The upstream caps the caseId at 1024 and validates its characters, but a path built
    by string formatting must not depend on that: the pair arrives from a model, and a caseId
    containing `../` would otherwise reach a different endpoint entirely.
    """
    from urllib.parse import quote

    return TOOL_ROUTES[tool].format(
        tribunal=quote(tribunal, safe=""), caseId=quote(case_id, safe="")
    )


def tribunals_costs(entries: list[dict]) -> dict[str, str]:
    """`{tool: price line}` from the `costs` array of the public pricing matrix.

    The same derivation the OpenAPI-built tools use: the pricing endpoint is the route with the
    version prefix and every path parameter removed, so
    `/api/v1/in/tribunals/matters/{tribunal}/{caseId}/detail` is priced as
    `/in/tribunals/matters/detail`. A tool whose route has no row is OMITTED rather than priced
    wrongly, and `fetch_tribunals_costs` says so out loud.
    """
    from vaquill_mcp.server import _format_cost, _pricing_endpoint_for_route

    by_endpoint: dict[str, list[dict]] = {}
    for entry in entries:
        endpoint = entry.get("endpoint")
        if endpoint:
            by_endpoint.setdefault(endpoint, []).append(entry)

    costs: dict[str, str] = {}
    for tool, route in TOOL_ROUTES.items():
        matches = by_endpoint.get(_pricing_endpoint_for_route(route))
        line = _format_cost(matches) if matches else ""
        if line:
            costs[tool] = line
    return costs


def fetch_tribunals_costs(base_url: str) -> dict[str, str]:
    """Fetch the public pricing matrix for India and derive the six price lines.

    Region `IN`, and it must be passed: the route is not scoped by any mount and defaults to US,
    which would label these tools with United States prices.

    A tool left without a price is logged at WARNING by name. `list_india_tribunal_forums` is
    FREE, so it legitimately has no price line and is not warned about: a zero-credit route with
    a "Cost: 0 credits" line reads as a priced one.
    """
    from vaquill_mcp.server import _fetch_public_costs

    costs = tribunals_costs(_fetch_public_costs(base_url, "IN"))
    unpriced = [
        t for t in TOOL_NAMES if t not in costs and t != "list_india_tribunal_forums"
    ]
    if unpriced:
        logger.warning(
            "No price in the pricing matrix for: %s. Their descriptions carry no "
            "cost line this session.",
            ", ".join(unpriced),
        )
    return costs


def create_tribunals_server(
    client: httpx2.AsyncClient | None = None,
    *,
    auth: object | None = None,
    costs: dict[str, str] | None = None,
) -> FastMCP:
    """A server whose whole catalogue is the six tribunals tools.

    A SEPARATE server rather than six more tools on the India or regulatory one, for the reason
    `create_regulatory_server` gives: one connection should see one corpus, because a client pays
    for every tool in every turn. This corpus also needs a different permission (`cases:read`).

    `auth` is per-SERVER: FastMCP reads it when it builds each HTTP app, so the OAuth mount and
    the `/s/{api_key}` mount cannot share one instance.

    `client` is supplied by a hosted entry point, which owns its credential and its lifetime.
    Without one, a client is built from the same environment the stdio server reads.
    """
    # The httpx2 INFO line is the full request URL: host, path and `?q=`. Held down here as well
    # as in the entry point, because this server must not depend on another having done it.
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

    # `icons` and `website_url` are what the OAuth consent screen renders: without them the
    # screen a user checks before granting access shows FastMCP's own logo.
    from mcp.types import Icon

    from vaquill_mcp.brand_assets import square_icon

    mcp = FastMCP(
        name="Vaquill Indian Tribunals",
        instructions=instructions_for(CORPUS),
        lifespan=lifespan,
        auth=auth,
        icons=[
            Icon(
                src="https://www.vaquill.ai/brand/lockup/vaquill-lockup-color-512w.png",
                mime_type="image/png",
            ),
            square_icon(),
        ],
        website_url="https://www.vaquill.ai",
    )
    register_tribunals_tools(mcp, client, costs)
    mcp.add_middleware(DeterministicToolOrder())
    return mcp
