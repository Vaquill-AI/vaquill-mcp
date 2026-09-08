"""Vaquill MCP Server - Legal research tools powered by 20M+ court judgments.

Uses FastMCP with an OpenAPIProvider to auto-generate tools from the Vaquill
OpenAPI spec, with custom tool names and descriptions optimized for LLM agents.

The OpenAPI spec is fetched from the live API at startup, so tools automatically
reflect any API changes without a package update.
"""

import contextlib
import logging
import re
import time
from collections import Counter
from collections.abc import AsyncIterator

# httpx2, not httpx2. fastmcp 4 deprecated passing an `httpx2.AsyncClient` to
# `OpenAPIProvider` ("temporarily accepted via duck typing... will be rejected in
# a future release") and ships httpx2 as a hard dependency. httpx2 is a drop-in
# fork with the same public API, so this is an import swap, not a rewrite.
import httpx2
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import (
    MCPType,
    OpenAPIProvider,
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
    RouteMap,
)
from fastmcp.utilities.openapi.models import HTTPRoute
from mcp.types import ToolAnnotations

from vaquill_mcp import __version__
from vaquill_mcp.aliases import register_aliases
from vaquill_mcp.config import (
    get_api_key,
    get_base_url,
    get_jurisdiction,
    get_spec_url,
    get_timeout,
)
from vaquill_mcp.descriptions import TOOL_DESCRIPTIONS, TOOL_TITLES
from vaquill_mcp.ordering import DeterministicToolOrder
from vaquill_mcp.prompts import register_prompts
from vaquill_mcp.resources import register_resources
from vaquill_mcp.schema_slim import slim_input_schema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAPI operationId -> desired MCP tool name
# ---------------------------------------------------------------------------
# These map the auto-generated operationIds from FastAPI (which include the
# full path) to clean, LLM-friendly tool names.

# Tool names are AUTO-DERIVED from the live OpenAPI (see _derive_mcp_names), so
# new/renamed/re-prefixed endpoints need no change here. This map is only the
# handful of SEMANTIC renames where the raw handler-function name is not the
# clearest tool name. Keyed on the FUNCTION NAME (the part of FastAPI's
# operationId before `_api_v1_`) so it survives path/prefix changes such as the
# `/us` country-prefix migration.
#
# Entries for retired surfaces were removed on 2026-08-20: `/acts/*` went with
# the India-market exit (router unmounted) and `/citations/cases/search` no
# longer exists. An override for a route the spec no longer carries is inert
# but misleading, and it makes the description drift-guard pass vacuously.
_FUNC_OVERRIDES: dict[str, str] = {
    # `external_search` -> search_legal_cases and `bot_search` -> quick_search
    # were removed on 2026-09-01. They renamed operations on /research/*, which
    # was retired and deleted with the case-law routers on 2026-08-31, so the
    # overrides could never fire against the live spec and their descriptions
    # were dead weight that `test_all_tools_have_descriptions` still demanded.
    "search_statutes": "search_us_statutes",
    "get_section": "get_us_statute_section",
    "get_section_body": "get_us_statute_section_text",
}


def _derive_mcp_names(spec: dict) -> dict[str, str]:
    """Map each operationId to a clean tool name, derived from the OpenAPI.

    FastAPI auto-generates operationIds as ``{func}_api_v1_{path}_{method}``, so
    the text before ``_api_v1_`` is the clean handler-function name -- that is
    the default tool name for every endpoint (turning the ugly
    ``list_statutes_coverage_api_v1_statutes_coverage_get`` into
    ``list_statutes_coverage``). ``_FUNC_OVERRIDES`` refines a few, and genuine
    cross-router name collisions (e.g. ``resolve_citation`` under both
    ``/citations`` and ``/statutes``) are disambiguated by the resource segment.
    """
    entries: list[tuple[str, str, str]] = []  # (operation_id, resource, base_name)
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        segs = [
            s
            for s in path.strip("/").split("/")
            if s not in ("api", "v1", "us") and "{" not in s
        ]
        resource = segs[0] if segs else ""
        for op in item.values():
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if not op_id or "_api_v1_" not in op_id:
                continue
            func = op_id.split("_api_v1_", 1)[0]
            entries.append((op_id, resource, _FUNC_OVERRIDES.get(func, func)))
    counts = Counter(base for _, _, base in entries)
    names: dict[str, str] = {}
    for op_id, resource, base in entries:
        if counts[base] > 1 and resource and not base.startswith(f"{resource}_"):
            base = f"{resource}_{base}"
        names[op_id] = base
    return names


def published_tool_names(spec: dict) -> set[str]:
    """Every tool name the OpenAPI provider will publish for this document.

    Two sources, and the second is easy to forget: `_derive_mcp_names` returns
    RENAMES only. An operation carrying an explicit `operation_id` has no
    `_api_v1_` marker, is deliberately absent from that map, and FastMCP then
    uses the operationId verbatim as the tool name. Counting only the map
    under-reports the catalogue by exactly those tools
    (`resolve_statute_citation` and `resolve_statute_citations_batch` on the live
    US document).
    """
    mapped = _derive_mcp_names(spec)
    names = set(mapped.values())
    for item in (spec.get("paths") or {}).values():
        if not isinstance(item, dict):
            continue
        for op in item.values():
            if (
                isinstance(op, dict)
                and (op_id := op.get("operationId"))
                and op_id not in mapped
            ):
                names.add(op_id)
    return names


# ---------------------------------------------------------------------------
# Route exclusions
# ---------------------------------------------------------------------------
# The /ask/stream endpoint uses SSE streaming which MCP tools cannot support.
# Exclude it so only the synchronous /ask endpoint becomes a tool.

_ROUTE_MAPS: list[RouteMap] = [
    RouteMap(pattern=r".*/ask/stream$", mcp_type=MCPType.EXCLUDE),
]


# ---------------------------------------------------------------------------
# Credit-cost injection
# ---------------------------------------------------------------------------
# Credit costs are NEVER hardcoded in descriptions.py. Instead they are fetched
# once at startup from the live API (the same CREDIT_PRICING source of truth the
# billing system uses) and appended to each tool description. This makes it
# structurally impossible for the advertised cost to drift from the real charge.
#
# The tool -> pricing-endpoint link is DERIVED from the OpenAPI path, never
# hand-maintained. The pricing matrix names an endpoint as the routable path
# with the version prefix and every path parameter removed, so the mapping is a
# pure function of the spec (`_pricing_endpoint_for_route`) and a tool cannot
# drift away from its price by being renamed, re-prefixed or moved.
#
# It used to be a hand-keyed dict, and that is exactly how this broke: the
# 2026-08 `/us` country-prefix migration moved every statutes price from
# `/statutes/search` to `/us/statutes/search`, the dict still said the old
# spelling, and `_build_tool_costs` silently matched nothing. Combined with the
# India-market exit retiring the ask/acts/research/citations tools the dict was
# mostly built around, the result was that EVERY tool shipped with no cost line
# at all -- a "cannot drift" mechanism that had quietly stopped running.
#
# `descriptions.py` still may not state a credit number; see the drift guard in
# tests/test_server.py.

_PATH_PARAM_RE = re.compile(r"/\{[^}]+\}")
_VERSION_PREFIX_RE = re.compile(r"^/api/v\d+")


def _pricing_endpoint_for_route(path: str) -> str:
    """The pricing-matrix `endpoint` string for an OpenAPI path.

    `/api/v1/us/statutes/section/{act_id}/body` -> `/us/statutes/section/body`.

    This mirrors, deliberately, the same normalization the web console applies
    in `frontend/src/lib/playground/cost-endpoint.ts`. Both sides join prices to
    routes this way; keep them in step.
    """
    return _PATH_PARAM_RE.sub("", _VERSION_PREFIX_RE.sub("", path)).rstrip("/")


def _fmt_credits(credits: float) -> str:
    """Render a credit count without a trailing '.0' for whole numbers."""
    return str(int(credits)) if float(credits).is_integer() else str(credits)


def _credit_noun(credits_str: str) -> str:
    """'credit' for exactly one, 'credits' otherwise."""
    return "credit" if credits_str == "1" else "credits"


def _short_label(operation: str) -> str:
    """Extract the disambiguating tier label from a pricing operation name.

    'Ask (Standard)' -> 'standard'; 'US Case Law Search (21-50 results)' ->
    '21-50 results'; 'US Statutes Search' -> '' (no tier to disambiguate).
    """
    start = operation.rfind("(")
    end = operation.rfind(")")
    if start != -1 and end > start:
        return operation[start + 1 : end].strip().lower()
    return ""


def _format_cost(entries: list[dict]) -> str:
    """Build a 'Cost: ...' sentence from the pricing entries for one tool.

    Single price -> 'Cost: 4 credits.' Multiple tiers -> each tier labelled,
    e.g. 'Cost: 2 credits (1-20 results), 4 credits (21-50 results).'

    A priced-at-zero endpoint renders 'Free.' rather than 'Cost: 0 credits.'
    Several endpoints are deliberately free (coverage discovery, and every
    law-change alert route except the diff), and an agent choosing between
    tools benefits more from knowing one is free than from parsing a zero.
    """
    tiers: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        credits = _fmt_credits(entry["credits"])
        label = _short_label(entry.get("operation", ""))
        key = (credits, label)
        if key in seen:
            continue
        seen.add(key)
        tiers.append((credits, label))

    if not tiers:
        return ""
    if len(tiers) == 1:
        credits = tiers[0][0]
        if credits == "0":
            return "Free."
        return f"Cost: {credits} {_credit_noun(credits)}."
    rendered = ", ".join(
        f"{credits} {_credit_noun(credits)} ({label})"
        if label
        else f"{credits} {_credit_noun(credits)}"
        for credits, label in tiers
    )
    return f"Cost: {rendered}."


def _build_tool_costs(spec: dict, cost_entries: list[dict]) -> dict[str, str]:
    """Map each MCP tool name to its injected 'Cost: ...' sentence.

    ``cost_entries`` is the ``costs`` array from /api/v1/api-credits/pricing/all.
    Both the tool name and the pricing endpoint are derived from the same
    OpenAPI operation, so they cannot fall out of step with each other.

    A tool whose route has no pricing row (``get_pricing``) or whose fetch
    failed is omitted, so it carries no cost line rather than a wrong one.
    """
    by_endpoint: dict[str, list[dict]] = {}
    for entry in cost_entries:
        endpoint = entry.get("endpoint")
        if endpoint:
            by_endpoint.setdefault(endpoint, []).append(entry)

    names = _derive_mcp_names(spec)
    tool_costs: dict[str, str] = {}
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        endpoint = _pricing_endpoint_for_route(path)
        matches = by_endpoint.get(endpoint)
        if not matches:
            continue
        line = _format_cost(matches)
        if not line:
            continue
        for op in item.values():
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if not op_id:
                continue
            # An explicit hand-set operation_id is not in `names` (it has no
            # `_api_v1_` marker); FastMCP uses it verbatim as the tool name.
            tool_costs[names.get(op_id, op_id)] = line
    return tool_costs


def _fetch_public_costs(base_url: str, region: str) -> list[dict]:
    """Fetch one jurisdiction's credit-cost matrix WITHOUT an API key.

    The remote server has no key at startup -- keys arrive per request -- so it
    cannot use the authenticated `/pricing/all` below. It does not need to:
    `PUBLIC_HIDDEN_CATEGORIES` is empty and is asserted empty by
    `test_all_matrix_equals_public_while_nothing_is_hidden` on the API side, so
    the public matrix and the full matrix are the same set for a region. If that
    invariant ever breaks, this returns fewer rows and some tools lose a cost
    line; it cannot return a WRONG price, which is the failure that matters.

    🔴 `region` is REQUIRED, and it is the caller's jurisdiction, not a
    preference. This hits the MAIN app route rather than the one mounted inside
    a jurisdiction's OpenAPI document, so it gets no scoping from the mount and
    the API defaults it to US. An India server that omitted it would silently
    label its India tools with United States prices.
    """
    url = f"{base_url}/api/v1/api-credits/pricing"
    try:
        response = httpx2.get(url, params={"region": region}, timeout=15.0)
        response.raise_for_status()
        return response.json().get("costs", [])
    except (httpx2.HTTPError, ValueError) as exc:
        logger.warning(
            "Could not fetch public credit pricing from %s; tool descriptions "
            "will omit cost lines this session: %s",
            url,
            exc,
        )
        return []


def _fetch_full_costs(base_url: str, api_key: str) -> list[dict]:
    """Fetch the full per-endpoint credit-cost matrix from the live API.

    Uses the authenticated /pricing/all endpoint (research:read scope) so the
    hidden ask/research/citations/acts prices are included. Best-effort: any
    failure returns an empty list, and tools then carry no cost line rather
    than a stale one.
    """
    url = f"{base_url}/api/v1/api-credits/pricing/all"
    try:
        response = httpx2.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json().get("costs", [])
    except (httpx2.HTTPError, ValueError) as exc:
        logger.warning(
            "Could not fetch credit pricing from %s; tool descriptions will "
            "omit cost lines this session: %s",
            url,
            exc,
        )
        return []


# ---------------------------------------------------------------------------
# Tool annotations (readOnlyHint / destructiveHint)
# ---------------------------------------------------------------------------
# Without these a client cannot tell `search_us_statutes` from `delete_watch`,
# so a user must either auto-approve everything including the deletes or
# hand-approve every read. The catalogue mixes both, so neither is acceptable.
#
# WHY NOT JUST THE HTTP VERB. That was the obvious rule and it is wrong on this
# API. Three of the read tools are POST, because their input is too large for a
# query string:
#
#     POST /us/statutes/search      POST /us/statutes/sections
#     POST /us/statutes/resolve
#
# Deriving read-only from `method == "GET"` would mark all three as writes, and
# the most-used tool in the catalogue would lose auto-approval. Deriving it from
# `method != "GET"` being a write is equally wrong in the other direction.
#
# WHAT IS ACTUALLY DERIVABLE. Most of it:
#
#     GET              -> read
#     DELETE           -> write, destructive
#     PATCH / PUT      -> write, not destructive
#     POST with a 201  -> write, not destructive (it says it created something)
#
# POST without a 201 is genuinely ambiguous, and that ambiguity belongs to REST
# rather than to us. So it FAILS CLOSED: an unrecognized POST is treated as a
# write unless its path is on `_READ_ONLY_POSTS` below.
#
# That direction is the whole point. A stale allow-list costs a user one extra
# approval prompt on a read. The opposite default would auto-approve a write
# nobody classified. `test_every_post_route_is_classified` then makes even the
# cheap failure loud: a new POST that is neither 201 nor listed fails the suite
# until somebody decides which it is.

# Paths (path-parameters stripped, as `_pricing_endpoint_for_route` renders
# them) whose POST reads rather than writes. POST-as-query, never a mutation.
_READ_ONLY_POSTS: frozenset[str] = frozenset(
    {
        "/us/statutes/search",
        "/us/statutes/sections",
        "/us/statutes/resolve",
        "/in/acts/search",
    }
)

# POSTs that DO act but return 200 rather than 201, so the response-code rule
# cannot see them. `test_watch` sends a real signed delivery to the customer's
# endpoint: a side effect on the outside world, and nothing this server should
# ever let a client auto-approve as a read.
#
# The runtime does not consult this set -- fail-closed already treats these as
# writes -- and that is deliberate. It exists so the CLASSIFICATION is
# exhaustive: `test_every_post_route_is_classified` requires every published
# POST to be a 201, a listed read, or a listed write, so a new endpoint cannot
# arrive and quietly inherit a default nobody looked at.
_ACKNOWLEDGED_WRITE_POSTS: frozenset[str] = frozenset({"/watches/test"})

# Emitted only where the value differs from the MCP default, because every
# annotation is bytes in a definition that is resident for the whole
# conversation. `readOnlyHint` defaults to false and `destructiveHint` to TRUE,
# so a non-destructive write MUST say so explicitly or a client is entitled to
# treat `create_watch` as if it were `delete_watch`.
# snake_case because MCP SDK v2 (which fastmcp 4 depends on) renamed these
# fields; the camelCase spellings still work through a warning bridge. The WIRE
# format is unaffected and stays camelCase, as the MCP spec requires -- verified
# by `test_annotations_serialize_as_camel_case_on_the_wire`.
_READ_ONLY = ToolAnnotations(read_only_hint=True)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)


def _is_read_only(route: HTTPRoute) -> bool:
    """Whether this route only reads. See the note above for why not the verb."""
    method = route.method.upper()
    if method == "GET":
        return True
    if method != "POST":
        return False
    if "201" in {str(code) for code in (route.responses or {})}:
        return False
    return _pricing_endpoint_for_route(route.path) in _READ_ONLY_POSTS


def _annotations_for(route: HTTPRoute, name: str | None = None) -> ToolAnnotations:
    """Derive the MCP tool annotations for one route.

    🔴 `title` goes HERE, inside the annotations, and not only on the tool's own
    `title` field. Both exist in the schema and they are not interchangeable:
    the Anthropic connector directory reads `annotations.title` and reports
    "Missing annotations: title" when only the top-level one is set, then falls
    back to a name-derived label. Measured 2026-09-08 against the live server,
    which was serving `Tool.title = "Credit Pricing"` while the submission
    portal displayed "Get Pricing" and flagged all 25 tools.

    Setting only `annotations.title` would be the mirror mistake for clients
    that read the newer top-level field, so `_customize_component` writes both.
    """
    if _is_read_only(route):
        base = _READ_ONLY
    elif route.method.upper() == "DELETE":
        base = _DESTRUCTIVE
    else:
        base = _WRITE
    title = TOOL_TITLES.get(name or "")
    return base.model_copy(update={"title": title}) if title else base


# ---------------------------------------------------------------------------
# Component customization
# ---------------------------------------------------------------------------


def _make_customize_component(tool_costs: dict[str, str]):
    """Build the FastMCP ``mcp_component_fn`` bound to a live cost map."""

    def _customize_component(
        route: HTTPRoute,
        component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate,
    ) -> None:
        """Rewrite auto-generated descriptions to be concise and LLM-friendly,
        and append the live per-call credit cost.

        The OpenAPI spec descriptions are multi-paragraph markdown with tables,
        code examples, and SSE documentation -- far too verbose for an LLM tool
        description. We replace them with focused 50-100 word descriptions that
        tell the LLM WHEN to use the tool and WHAT it returns, then append the
        credit cost fetched from the live API at startup.

        Note: This callback mutates ``component`` in-place as required by
        FastMCP's ``mcp_component_fn`` contract.
        """
        if component.name in TOOL_DESCRIPTIONS:
            description = TOOL_DESCRIPTIONS[component.name]
            cost_line = tool_costs.get(component.name)
            if cost_line:
                description = f"{description} {cost_line}"
            component.description = description

        # A title is REQUIRED by the Anthropic connector directory and is what a
        # client shows in its tool list. Set before the OpenAPITool branch so a
        # resource gets one too.
        title = TOOL_TITLES.get(component.name)
        if title:
            component.title = title

        if isinstance(component, OpenAPITool):
            # The input schema, not the description, is where this catalogue's
            # tokens actually are: 86% of it against 12.8% for descriptions.
            # `slim_input_schema` is a pure function and returns a new schema, so
            # the in-place assignment here is the only mutation, as FastMCP's
            # contract requires. It rewrites annotations only; the arguments the
            # tool accepts are untouched, and `test_schema_slim.py` proves that
            # structurally against both published documents.
            component.parameters = slim_input_schema(
                component.name, component.parameters
            )
            component.annotations = _annotations_for(route, component.name)

        # Tag all components for discoverability
        component.tags.add("legal-research")
        component.tags.add("vaquill")

    return _customize_component


# ---------------------------------------------------------------------------
# Spec fetching (with retry)
# ---------------------------------------------------------------------------

_MAX_RETRIES = 2


def _fetch_openapi_spec(base_url: str, jurisdiction: str | None = None) -> dict:
    """Fetch the OpenAPI spec from the Vaquill API server.

    Retries up to 2 times with exponential backoff for transient network
    errors (connect failures, timeouts). This is intentionally synchronous
    because it runs during server initialization before the async event
    loop starts.

    Raises:
        httpx2.HTTPStatusError: If the API returns a non-2xx status.
        httpx2.ConnectError: If the API is unreachable after retries.
        httpx2.TimeoutException: If all attempts time out.
        ValueError: If the response is not valid JSON.
    """
    # The document, and therefore the entire tool catalogue, follows from the
    # jurisdiction. US derives from /external/openapi.json, India from
    # /in/openapi.json, and the two are disjoint, so a server cannot leak a
    # tool from the jurisdiction it was not configured for.
    url = get_spec_url(base_url, jurisdiction)
    last_error: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = httpx2.get(url, timeout=15.0)
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                raise ValueError(
                    f"Failed to parse OpenAPI spec from {url} -- "
                    f"expected JSON but got: {response.text[:200]}"
                ) from exc
        except (httpx2.ConnectError, httpx2.TimeoutException) as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                delay = 2**attempt  # 1s, 2s
                logger.warning(
                    "OpenAPI spec fetch failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    delay,
                    exc,
                )
                time.sleep(delay)

    raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_server(jurisdiction: str | None = None) -> FastMCP:
    """Create and configure the Vaquill MCP server.

    Reads configuration from environment variables:
    - VAQUILL_API_KEY (required): API key for authentication
    - VAQUILL_BASE_URL (optional): API base URL (default: https://api.vaquill.ai)
    - VAQUILL_TIMEOUT (optional): Request timeout in seconds (default: 120)

    Returns:
        A configured FastMCP server ready to run.

    Raises:
        ValueError: If VAQUILL_API_KEY is not set.
        httpx2.HTTPError: If the OpenAPI spec cannot be fetched.
    """
    api_key = get_api_key()
    base_url = get_base_url()
    timeout = get_timeout()

    # Fetch OpenAPI spec from the live API for THIS server's jurisdiction.
    # An explicit argument (the `--jurisdiction` flag) beats the environment,
    # so a client config can select India without setting env vars at all.
    jurisdiction = jurisdiction or get_jurisdiction()
    openapi_spec = _fetch_openapi_spec(base_url, jurisdiction)

    # Fetch the live credit-cost matrix so each tool description carries an
    # accurate, never-drifting cost line (best-effort; empty on failure).
    tool_costs = _build_tool_costs(openapi_spec, _fetch_full_costs(base_url, api_key))

    # Create authenticated HTTP client.
    # The auth header is set on the client so ALL requests carry it.
    # The pricing endpoint ignores the extra header (it's unauthenticated).
    # Timeout is generous (120s default) because /ask in deep mode can take 90s.
    client = httpx2.AsyncClient(
        base_url=base_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": f"vaquill-mcp/{__version__}",
        },
        timeout=httpx2.Timeout(timeout, connect=10.0),
    )

    # Lifespan context manager to cleanly close the httpx client on shutdown.
    @contextlib.asynccontextmanager
    async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await client.aclose()

    # Build MCP server with lifespan for proper resource cleanup,
    # then add the OpenAPI provider for tool generation.
    # The name carries the jurisdiction so a user running both servers can
    # tell them apart in a client that lists them side by side.
    name = (
        "Vaquill Legal Research"
        if jurisdiction == "US"
        else f"Vaquill Legal Research ({jurisdiction})"
    )
    mcp = FastMCP(name=name, lifespan=_lifespan)

    provider = OpenAPIProvider(
        openapi_spec=openapi_spec,
        client=client,
        mcp_names=_derive_mcp_names(openapi_spec),
        route_maps=_ROUTE_MAPS,
        mcp_component_fn=_make_customize_component(tool_costs),
        # Disable output validation — the live API is the source of truth.
        # Some fields (e.g., citation network treatmentType) can be null in
        # practice even though the OpenAPI enum doesn't declare it nullable.
        validate_output=False,
    )
    mcp.add_provider(provider)

    # Sorted `tools/list`, so the provider prompt-prefix cache survives. See
    # ordering.py: nothing sorted before this and the stability was accidental.
    mcp.add_middleware(DeterministicToolOrder())

    # The generic pair OpenAI's deep-research clients match on. Additive: every
    # typed tool above is untouched, and the aliases stand down if the document
    # ever publishes an operation of the same name. See aliases.py.
    register_aliases(
        mcp,
        client,
        jurisdiction,
        base_url,
        published_tool_names(openapi_spec),
    )

    # The other two MCP primitives. `OpenAPIProvider` only ever emits tools, so
    # a server built purely from it publishes 25 tools, 0 resources and 0
    # prompts -- which is what "thin wrapper" means in practice. Resources carry
    # the reference data and the corpus guide; prompts carry the workflows and
    # the traps. Neither rides in the per-turn tool budget.
    register_resources(mcp, client, jurisdiction)
    register_prompts(mcp, jurisdiction)

    return mcp
