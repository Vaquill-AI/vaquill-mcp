"""Tests for vaquill_mcp.server module."""

import pathlib
import re

import httpx  # respx builds mock RESPONSES from the old httpx types
import httpx2  # ...but the code under test raises and catches httpx2 ones
import pytest

from vaquill_mcp.descriptions import TOOL_DESCRIPTIONS
from vaquill_mcp.server import (
    _FUNC_OVERRIDES,
    _ROUTE_MAPS,
    _build_tool_costs,
    _derive_mcp_names,
    _fetch_openapi_spec,
    _format_cost,
    _make_customize_component,
    _pricing_endpoint_for_route,
    create_server,
)

# A synthetic OpenAPI spec exercising the real operationId shapes: FastAPI's
# verbose auto id, an explicit hand-set id, a cross-router collision, and the
# /us country-prefix migration.
_SAMPLE_SPEC = {
    "paths": {
        "/api/v1/us/statutes/coverage": {
            "get": {"operationId": "list_statutes_coverage_api_v1_us_statutes_coverage_get"}
        },
        "/api/v1/us/statutes/search": {
            "post": {"operationId": "search_statutes_api_v1_us_statutes_search_post"}
        },
        "/api/v1/us/statutes/resolve": {
            "get": {"operationId": "resolve_statute_citation"}  # explicit id, collision fix
        },
        "/api/v1/citations/resolve": {
            "get": {"operationId": "resolve_citation_api_v1_citations_resolve_get"}
        },
        "/api/v1/research/search": {
            "post": {"operationId": "external_search_api_v1_research_search_post"}
        },
    }
}

# A no-cost customizer for tests that only exercise description/tag rewriting.
_customize_component = _make_customize_component({})


# The production external surface, as operationId -> path. Mirrors
# GET https://api.vaquill.ai/external/openapi.json; refresh it when the API
# gains or loses an endpoint (the drift guards below will tell you).
def _load_prod_operations() -> dict[str, str]:
    """operationId -> path, READ FROM THE PUBLISHED DOCUMENT.

    This was a hand-written dict and it drifted, in both directions at once:
    it still listed `list_statutes_laws` (whose route is `include_in_schema=
    False` and so is not published) and had never gained
    `resolve_statute_citations_batch`. Both slipped through because the list
    was the thing every other check in this file measured against, so a
    mistake in it defined its own correctness.

    Regenerate `fixtures/openapi_us.json` from the backend when the published
    surface changes; see `test_derived_catalogue.py` for the command.
    """
    import json

    spec = json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "openapi_us.json").read_text()
    )
    out: dict[str, str] = {}
    for path, item in spec["paths"].items():
        for op in item.values():
            if isinstance(op, dict) and (oid := op.get("operationId")):
                out[oid] = path
    return out


_PROD_OPERATIONS: dict[str, str] = _load_prod_operations()

# Methods matter only for building a spec-shaped dict; one op per (path, method).
_METHOD_BY_OP = {
    "create_watch_api_v1_watches_post": "post",
    "update_watch_api_v1_watches__watch_id__patch": "patch",
    "delete_watch_api_v1_watches__watch_id__delete": "delete",
    "search_statutes_api_v1_us_statutes_search_post": "post",
    "get_sections_batch_api_v1_us_statutes_sections_post": "post",
    "test_watch_api_v1_watches__watch_id__test_post": "post",
}


def _prod_spec() -> dict:
    """The published US document itself, not a reconstruction of it.

    This used to rebuild a synthetic spec from `_PROD_OPERATIONS` plus a
    hand-kept `_METHOD_BY_OP`, and the reconstruction lost information: two
    operations share `/us/statutes/resolve` (GET resolve_statute_citation and
    POST resolve_statute_citations_batch), an operation missing from the method
    map defaulted to "get", and the second one silently overwrote the first.
    Reading the real document removes both the second hand-kept list and the
    class of bug where the fixture disagrees with production.
    """
    import json

    return json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "openapi_us.json").read_text()
    )


def _production_tool_names() -> set[str]:
    """Tool names FastMCP will publish for the production spec."""
    spec = _prod_spec()
    names = _derive_mcp_names(spec)
    # An explicit operation_id has no `_api_v1_` marker, so it is absent from
    # the derived map and FastMCP uses the raw id as the tool name.
    return {names.get(op_id, op_id) for op_id in _PROD_OPERATIONS}


def _india_tool_names() -> set[str]:
    """Tool names the India document publishes.

    Was `_remote_tool_names()`, which scraped `@mcp.tool` decorators out of
    remote.py. remote.py stopped declaring tools on 2026-09-01 and now derives
    them, so that regex matched nothing and every check built on it passed
    vacuously. The descriptions it was guarding are still real; they just come
    from the other jurisdiction's document now.
    """
    import json
    import pathlib

    spec = json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "openapi_in.json").read_text()
    )
    names = _derive_mcp_names(spec)
    out: set[str] = set()
    for item in spec["paths"].values():
        for op in item.values():
            if isinstance(op, dict) and (oid := op.get("operationId")):
                out.add(names.get(oid, oid))
    return out


# The production pricing matrix, as endpoint -> credits. Mirrors
# GET /api/v1/api-credits/pricing/all. Only the endpoint strings are
# load-bearing here; the numbers are illustrative and are NOT asserted against
# the backend (they are fetched live at runtime, which is the whole design).
_PROD_PRICING_ENDPOINTS = {
    "/us/statutes/search",
    "/us/statutes/section",
    "/us/statutes/sections",
    "/us/statutes/section/related",
    "/us/statutes/section/body",
    "/us/statutes/section/cited-by",
    "/us/statutes/section/definitions",
    "/us/statutes/section/cross-state",
    "/us/statutes/section/changes",
    "/us/statutes/resolve",
    "/us/statutes/divisions",
    "/us/statutes/coverage",
    "/us/statutes/laws",
    "/boards",
    "/watches",
    "/watches/test",
    "/watches/changes",
    "/watches/deliveries",
    "/watches/changes/diff",
}


class TestMCPNames:
    """Tool names are AUTO-DERIVED from the live OpenAPI, not hand-maintained."""

    def test_verbose_operationid_reduced_to_function_name(self) -> None:
        names = _derive_mcp_names(_SAMPLE_SPEC)
        assert (
            names["list_statutes_coverage_api_v1_us_statutes_coverage_get"]
            == "list_statutes_coverage"
        )

    def test_no_verbose_suffix_leaks_into_names(self) -> None:
        for name in _derive_mcp_names(_SAMPLE_SPEC).values():
            assert "_api_v1_" not in name

    def test_semantic_override_applied(self) -> None:
        names = _derive_mcp_names(_SAMPLE_SPEC)
        assert names["search_statutes_api_v1_us_statutes_search_post"] == "search_us_statutes"
        # The `external_search` override went with the /research retirement, so
        # the operation now derives its name rather than being renamed.
        assert "search_legal_cases" not in names.values()

    def test_override_is_path_independent(self) -> None:
        """The /us country-prefix migration must not change tool names."""
        old = _derive_mcp_names(
            {
                "paths": {
                    "/api/v1/us/statutes/search": {
                        "post": {"operationId": "search_statutes_api_v1_statutes_search_post"}
                    }
                }
            }
        )
        new = _derive_mcp_names(
            {
                "paths": {
                    "/api/v1/us/statutes/search": {
                        "post": {"operationId": "search_statutes_api_v1_us_statutes_search_post"}
                    }
                }
            }
        )
        assert list(old.values()) == list(new.values()) == ["search_us_statutes"]

    def test_explicit_operation_id_is_left_verbatim(self) -> None:
        """A hand-set operation_id (no _api_v1_) is not remapped; FastMCP uses it."""
        names = _derive_mcp_names(_SAMPLE_SPEC)
        assert "resolve_statute_citation" not in names
        # the collision partner keeps its clean name because the statute side
        # was disambiguated via the explicit operation_id above
        assert names["resolve_citation_api_v1_citations_resolve_get"] == "resolve_citation"

    def test_no_duplicate_names(self) -> None:
        names = _derive_mcp_names(_SAMPLE_SPEC)
        assert len(set(names.values())) == len(names)

    def test_semantic_overrides_are_clean(self) -> None:
        for func, name in _FUNC_OVERRIDES.items():
            assert func and name and "_api_v1_" not in name


class TestRouteExclusion:
    """Verify the streaming endpoint is excluded."""

    def test_stream_route_excluded(self) -> None:
        assert len(_ROUTE_MAPS) == 1
        route_map = _ROUTE_MAPS[0]
        assert route_map.pattern is not None

        pattern = re.compile(route_map.pattern)
        assert pattern.search("/api/v1/ask/stream") is not None
        assert pattern.search("/api/v1/ask") is None

    def test_non_stream_routes_not_excluded(self) -> None:
        """Regular endpoints should not match the exclusion pattern."""
        pattern = re.compile(_ROUTE_MAPS[0].pattern)
        assert pattern.search("/api/v1/research/search") is None
        assert pattern.search("/api/v1/citations/resolve") is None


class TestDescriptions:
    """Verify all mapped tools have custom descriptions."""

    def test_all_tools_have_descriptions(self) -> None:
        for tool_name in _FUNC_OVERRIDES.values():
            assert tool_name in TOOL_DESCRIPTIONS, (
                f"Tool '{tool_name}' is missing a description in descriptions.py"
            )

    def test_every_production_tool_has_a_description(self) -> None:
        """The real surface, not just the handful of semantic renames.

        The old version of this test walked ``_FUNC_OVERRIDES.values()``, which
        is five entries, so fifteen real tools could ship with the raw
        multi-paragraph OpenAPI description and nothing failed.
        """
        for name in sorted(_production_tool_names()):
            assert name in TOOL_DESCRIPTIONS, (
                f"Tool '{name}' exists in the API but has no description in "
                f"descriptions.py, so it ships the verbose OpenAPI prose"
            )

    def test_no_description_is_for_a_retired_tool(self) -> None:
        """A description for a route that no longer exists is dead weight.

        Every key must be reachable either from the OpenAPI surface (the stdio
        server) or from a hand-declared tool in remote.py (the hosted server).
        """
        known = _production_tool_names() | _india_tool_names()
        orphans = sorted(set(TOOL_DESCRIPTIONS) - known)
        assert not orphans, (
            f"descriptions.py describes tools that no longer exist: {orphans}"
        )

    def test_only_india_tools_claim_india_coverage(self) -> None:
        """A description may mention India only if it describes an India tool.

        This used to forbid the word outright, which was right while the India
        corpus was retired and every mention was a stale claim. India came back
        on 2026-09-01 with its own document, so the rule inverts rather than
        disappears: the failure worth catching now is a US tool advertising a
        corpus its jurisdiction cannot reach.
        """
        india_tools = _india_tool_names()
        for name, desc in TOOL_DESCRIPTIONS.items():
            if "india" in desc.lower():
                assert name in india_tools, (
                    f"Description for '{name}' claims India coverage, but it is "
                    "not a tool the India document publishes"
                )

    def test_descriptions_are_concise(self) -> None:
        """Descriptions should be under 500 characters for efficient LLM context."""
        for name, desc in TOOL_DESCRIPTIONS.items():
            assert len(desc) < 500, (
                f"Description for '{name}' is {len(desc)} chars (max 500)"
            )

    def test_descriptions_are_non_empty(self) -> None:
        for name, desc in TOOL_DESCRIPTIONS.items():
            assert len(desc) > 20, f"Description for '{name}' is too short"

    def test_no_orphan_descriptions(self) -> None:
        """Every custom description keys a clean tool name (no verbose suffix)."""
        for desc_name in TOOL_DESCRIPTIONS:
            assert desc_name and "_api_v1_" not in desc_name, (
                f"Description key '{desc_name}' is not a clean tool name"
            )


class TestCustomizeComponent:
    """Verify the mcp_component_fn callback works correctly."""

    def test_overrides_description(self) -> None:
        """Component description should be replaced with our custom one."""

        class MockComponent:
            name = "search_us_statutes"
            description = "Original very long OpenAPI description..."

            def __init__(self):
                self.tags: set[str] = set()

        component = MockComponent()
        _customize_component(None, component)  # type: ignore[arg-type]

        assert component.description == TOOL_DESCRIPTIONS["search_us_statutes"]
        assert "legal-research" in component.tags
        assert "vaquill" in component.tags

    def test_unknown_tool_keeps_original_description(self) -> None:
        """Tools not in our descriptions dict keep their original description."""

        class MockComponent:
            name = "unknown_tool"
            description = "Original description"

            def __init__(self):
                self.tags: set[str] = set()

        component = MockComponent()
        _customize_component(None, component)  # type: ignore[arg-type]

        assert component.description == "Original description"
        assert "legal-research" in component.tags

    def test_adds_both_tags(self) -> None:
        """Both 'legal-research' and 'vaquill' tags should always be added."""

        class MockComponent:
            name = "get_pricing"
            description = "original"

            def __init__(self):
                self.tags: set[str] = set()

        component = MockComponent()
        _customize_component(None, component)  # type: ignore[arg-type]
        assert component.tags == {"legal-research", "vaquill"}


class TestCostInjection:
    """Verify credit costs are injected from the live API, never hardcoded."""

    def test_descriptions_have_no_hardcoded_credit_numbers(self) -> None:
        """No description may state a credit cost; costs are injected at boot.

        A hardcoded 'N credit(s)' string is exactly the drift bug this design
        removes: the number would go stale the next time backend pricing moves.

        get_pricing is exempt: its description states the fixed conversion peg
        ('1 credit = $0.01 USD'), which is a definitional rate, not a per-call
        cost that can drift.
        """
        for name, desc in TOOL_DESCRIPTIONS.items():
            if name == "get_pricing":
                continue
            assert not re.search(r"\d+\s*credit", desc, re.IGNORECASE), (
                f"Description for '{name}' hardcodes a credit cost; costs must "
                f"be injected from /api-credits/pricing/all, not written here"
            )

    def test_pricing_endpoint_derived_from_path(self) -> None:
        """The version prefix and every path parameter come off.

        These are the exact pairs the live API serves. Getting this wrong is
        silent: the cost line just never appears.
        """
        cases = {
            "/api/v1/us/statutes/search": "/us/statutes/search",
            "/api/v1/us/statutes/section/{act_id}": "/us/statutes/section",
            "/api/v1/us/statutes/section/{act_id}/body": "/us/statutes/section/body",
            "/api/v1/us/statutes/section/{act_id}/cited-by": "/us/statutes/section/cited-by",
            "/api/v1/watches": "/watches",
            "/api/v1/watches/{watch_id}/test": "/watches/test",
            "/api/v1/watches/{watch_id}/changes": "/watches/changes",
            "/api/v1/watches/{watch_id}/changes/{change_id}/diff": "/watches/changes/diff",
        }
        for path, expected in cases.items():
            assert _pricing_endpoint_for_route(path) == expected

    def test_every_priced_production_route_gets_a_cost_line(self) -> None:
        """The guard that would have caught the /us country-prefix break.

        For months every tool shipped with NO cost line: the hand-keyed map
        still said `/statutes/search` after pricing moved to
        `/us/statutes/search`, and a miss is silent by design. Deriving the
        endpoint from the path makes that impossible, and this asserts it.
        """
        spec = _prod_spec()
        entries = [
            {"endpoint": e, "operation": "X", "credits": 4, "regions": ["US"]}
            for e in _PROD_PRICING_ENDPOINTS
        ]
        costs = _build_tool_costs(spec, entries)

        priced_tools = {
            name
            for op_id, path in _PROD_OPERATIONS.items()
            if _pricing_endpoint_for_route(path) in _PROD_PRICING_ENDPOINTS
            for name in [_derive_mcp_names(spec).get(op_id, op_id)]
        }
        missing = sorted(priced_tools - set(costs))
        assert not missing, f"priced routes with no injected cost line: {missing}"

    def test_the_metered_alerts_route_is_priced_and_the_rest_are_free(self) -> None:
        """Law-change alerts are free except the diff, which serves section text."""
        spec = _prod_spec()
        entries = [
            {"endpoint": "/boards", "operation": "Boards", "credits": 0, "regions": ["US"]},
            {"endpoint": "/watches", "operation": "Watches", "credits": 0, "regions": ["US"]},
            {
                "endpoint": "/watches/changes",
                "operation": "Change List",
                "credits": 0,
                "regions": ["US"],
            },
            {
                "endpoint": "/watches/changes/diff",
                "operation": "Change Diff",
                "credits": 4,
                "regions": ["US"],
            },
        ]
        costs = _build_tool_costs(spec, entries)
        assert costs["get_watch_change_diff"] == "Cost: 4 credits."
        assert costs["list_watch_changes"] == "Free."
        assert costs["list_boards"] == "Free."
        assert costs["create_watch"] == "Free."

    def test_explicit_operation_id_route_still_gets_its_cost(self) -> None:
        """`resolve_statute_citation` has a hand-set operation_id.

        It is therefore absent from the derived-name map, and a cost builder
        that only walked that map would skip it.
        """
        costs = _build_tool_costs(
            _prod_spec(),
            [
                {
                    "endpoint": "/us/statutes/resolve",
                    "operation": "Resolver",
                    "credits": 2,
                    "regions": ["US"],
                }
            ],
        )
        assert costs["resolve_statute_citation"] == "Cost: 2 credits."

    def test_format_cost_single_tier(self) -> None:
        entries = [{"credits": 4, "operation": "US Statutes Search", "regions": ["US"]}]
        assert _format_cost(entries) == "Cost: 4 credits."

    def test_format_cost_multi_tier_labelled(self) -> None:
        entries = [
            {"credits": 15, "operation": "Ask (Standard)", "regions": ["US", "IN"]},
            {"credits": 30, "operation": "Ask (Deep)", "regions": ["US", "IN"]},
        ]
        assert _format_cost(entries) == (
            "Cost: 15 credits (standard), 30 credits (deep)."
        )

    def test_format_cost_empty(self) -> None:
        assert _format_cost([]) == ""

    def test_build_tool_costs_maps_the_real_spellings(self) -> None:
        costs = _build_tool_costs(
            _prod_spec(),
            [
                {
                    "endpoint": "/us/statutes/search",
                    "operation": "US Statutes Search",
                    "credits": 4,
                    "regions": ["US"],
                },
                {
                    "endpoint": "/us/statutes/section/body",
                    "operation": "US Statute Section (Full Text)",
                    "credits": 6,
                    "regions": ["US"],
                },
            ],
        )
        assert costs["search_us_statutes"] == "Cost: 4 credits."
        assert costs["get_us_statute_section_text"] == "Cost: 6 credits."

    def test_build_tool_costs_ignores_a_stale_pricing_spelling(self) -> None:
        """The pre-/us spellings must resolve to nothing, not to a wrong tool."""
        costs = _build_tool_costs(
            _prod_spec(),
            [
                {
                    "endpoint": "/statutes/search",
                    "operation": "Old",
                    "credits": 99,
                    "regions": ["US"],
                }
            ],
        )
        assert costs == {}

    def test_build_tool_costs_empty_on_no_data(self) -> None:
        """A failed fetch (empty list) yields no cost lines, not wrong ones."""
        assert _build_tool_costs(_prod_spec(), []) == {}

    def test_free_endpoint_renders_as_free(self) -> None:
        assert _format_cost([{"credits": 0, "operation": "Coverage", "regions": ["US"]}]) == "Free."

    def test_injection_appends_cost_to_description(self) -> None:
        customize = _make_customize_component(
            {"search_us_statutes": "Cost: 4 credits."}
        )

        class MockComponent:
            name = "search_us_statutes"
            description = "orig"

            def __init__(self):
                self.tags: set[str] = set()

        component = MockComponent()
        customize(None, component)  # type: ignore[arg-type]
        assert component.description == (
            f"{TOOL_DESCRIPTIONS['search_us_statutes']} Cost: 4 credits."
        )


class TestFetchOpenAPISpec:
    """Verify OpenAPI spec fetching with retry logic."""

    def test_fetches_spec_successfully(self, respx_mock) -> None:
        """Should fetch and parse the OpenAPI JSON spec."""
        spec = {"openapi": "3.1.0", "info": {"title": "Test"}, "paths": {}}
        respx_mock.get("https://api.vaquill.ai/external/openapi.json").mock(
            return_value=httpx.Response(200, json=spec)
        )

        result = _fetch_openapi_spec("https://api.vaquill.ai")
        assert result == spec

    def test_raises_on_http_error(self, respx_mock) -> None:
        """Should raise on non-2xx status (no retry for HTTP errors)."""
        respx_mock.get("https://api.vaquill.ai/external/openapi.json").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        with pytest.raises(httpx2.HTTPStatusError):
            _fetch_openapi_spec("https://api.vaquill.ai")

    def test_raises_on_connect_error_after_retries(self, respx_mock) -> None:
        """Should retry on ConnectError and raise after all attempts fail."""
        respx_mock.get("https://api.vaquill.ai/external/openapi.json").mock(
            side_effect=httpx2.ConnectError("Connection refused")
        )

        with pytest.raises(httpx2.ConnectError):
            _fetch_openapi_spec("https://api.vaquill.ai")

    def test_raises_on_timeout_after_retries(self, respx_mock) -> None:
        """Should retry on TimeoutException and raise after all attempts fail."""
        respx_mock.get("https://api.vaquill.ai/external/openapi.json").mock(
            side_effect=httpx2.ReadTimeout("Read timed out")
        )

        with pytest.raises(httpx2.TimeoutException):
            _fetch_openapi_spec("https://api.vaquill.ai")

    def test_raises_on_invalid_json(self, respx_mock) -> None:
        """Should raise ValueError when response is not valid JSON."""
        respx_mock.get("https://api.vaquill.ai/external/openapi.json").mock(
            return_value=httpx.Response(200, text="<html>Not JSON</html>")
        )

        with pytest.raises(ValueError, match="Failed to parse OpenAPI spec"):
            _fetch_openapi_spec("https://api.vaquill.ai")

    def test_retries_on_transient_failure_then_succeeds(self, respx_mock) -> None:
        """Should succeed if a retry attempt works."""
        spec = {"openapi": "3.1.0", "info": {"title": "Test"}, "paths": {}}
        route = respx_mock.get("https://api.vaquill.ai/external/openapi.json")
        route.side_effect = [
            httpx2.ConnectError("Connection refused"),
            httpx.Response(200, json=spec),
        ]

        result = _fetch_openapi_spec("https://api.vaquill.ai")
        assert result == spec


class TestCreateServer:
    """Integration tests for the server factory."""

    def test_creates_server_with_correct_name(
        self, monkeypatch: pytest.MonkeyPatch, sample_openapi_spec: dict, respx_mock
    ) -> None:
        """Server should be created with the correct name and tools."""
        monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test123")
        monkeypatch.setenv("VAQUILL_BASE_URL", "https://api.vaquill.ai")

        respx_mock.get("https://api.vaquill.ai/external/openapi.json").mock(
            return_value=httpx.Response(200, json=sample_openapi_spec)
        )
        respx_mock.get("https://api.vaquill.ai/api/v1/api-credits/pricing/all").mock(
            return_value=httpx.Response(200, json={"costs": []})
        )

        server = create_server()
        assert server.name == "Vaquill Legal Research"

    def test_raises_without_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise ValueError when API key is not set."""
        monkeypatch.delenv("VAQUILL_API_KEY", raising=False)

        with pytest.raises(ValueError, match="VAQUILL_API_KEY"):
            create_server()

    def test_raises_with_invalid_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise ValueError when base URL has invalid scheme."""
        monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test123")
        monkeypatch.setenv("VAQUILL_BASE_URL", "ftp://bad-scheme")

        with pytest.raises(ValueError, match="http:// or https://"):
            create_server()

    def test_raises_with_invalid_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise ValueError when timeout is not a valid number."""
        monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test123")
        monkeypatch.setenv("VAQUILL_TIMEOUT", "abc")

        with pytest.raises(ValueError, match="must be a number"):
            create_server()


class TestVersion:
    """Verify version is consistent and accessible."""

    def test_version_is_string(self) -> None:
        from vaquill_mcp import __version__

        assert isinstance(__version__, str)
        assert len(__version__) > 0

    def test_version_is_semver(self) -> None:
        from vaquill_mcp import __version__

        parts = __version__.split(".")
        assert len(parts) == 3
        assert all(part.isdigit() for part in parts)

    def test_public_api_exports(self) -> None:
        import vaquill_mcp

        assert hasattr(vaquill_mcp, "__version__")
        assert hasattr(vaquill_mcp, "create_server")
        assert callable(vaquill_mcp.create_server)


class TestMainEntryPoint:
    """Verify the __main__.py entry point."""

    def test_main_function_exists(self) -> None:
        from vaquill_mcp.__main__ import main

        assert callable(main)
