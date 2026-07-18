"""Tests for vaquill_mcp.server module."""

import re

import httpx
import pytest

from vaquill_mcp.descriptions import TOOL_DESCRIPTIONS
from vaquill_mcp.server import (
    _MCP_NAMES,
    _ROUTE_MAPS,
    _TOOL_COST_ENDPOINTS,
    _build_tool_costs,
    _fetch_openapi_spec,
    _format_cost,
    _make_customize_component,
    create_server,
)

# A no-cost customizer for tests that only exercise description/tag rewriting.
_customize_component = _make_customize_component({})


class TestMCPNames:
    """Verify the operationId -> tool name mapping is complete and correct."""

    def test_all_expected_tools_mapped(self) -> None:
        expected_tools = {
            # Cross-jurisdiction
            "ask_legal_question",
            "get_pricing",
            # Indian case law
            "search_legal_cases",
            "quick_search",
            "resolve_citation",
            "search_cases_by_citation",
            "lookup_case",
            "get_citation_network",
            # Indian acts & legislation
            "search_legislation",
            "list_legislation",
            "get_act_text",
            "get_amendments",
            # US statutes (USC + CFR)
            "search_us_statutes",
            "get_us_statute_section",
            "get_us_statute_section_text",
        }
        assert set(_MCP_NAMES.values()) == expected_tools

    def test_streaming_endpoint_not_in_names(self) -> None:
        """The streaming endpoint should be excluded via RouteMap, not named."""
        for operation_id in _MCP_NAMES:
            assert "stream" not in operation_id

    def test_expected_tool_count(self) -> None:
        assert len(_MCP_NAMES) == 15

    def test_no_duplicate_tool_names(self) -> None:
        """Each tool name should be unique."""
        names = list(_MCP_NAMES.values())
        assert len(names) == len(set(names))


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
        for tool_name in _MCP_NAMES.values():
            assert tool_name in TOOL_DESCRIPTIONS, (
                f"Tool '{tool_name}' is missing a description in descriptions.py"
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
        """Every description should map to a real tool name."""
        tool_names = set(_MCP_NAMES.values())
        for desc_name in TOOL_DESCRIPTIONS:
            assert desc_name in tool_names, (
                f"Description for '{desc_name}' has no matching tool"
            )


class TestCustomizeComponent:
    """Verify the mcp_component_fn callback works correctly."""

    def test_overrides_description(self) -> None:
        """Component description should be replaced with our custom one."""

        class MockComponent:
            name = "ask_legal_question"
            description = "Original very long OpenAPI description..."

            def __init__(self):
                self.tags: set[str] = set()

        component = MockComponent()
        _customize_component(None, component)  # type: ignore[arg-type]

        assert component.description == TOOL_DESCRIPTIONS["ask_legal_question"]
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

    def test_every_paid_tool_has_a_cost_endpoint(self) -> None:
        """Every tool except the free get_pricing maps to a pricing endpoint."""
        paid_tools = set(_MCP_NAMES.values()) - {"get_pricing"}
        assert paid_tools == set(_TOOL_COST_ENDPOINTS)

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

    def test_build_tool_costs_maps_and_region_filters(self) -> None:
        """Costs map to the right tool, and region-scoped tools pick their region."""
        cost_entries = [
            {"endpoint": "/statutes/search", "operation": "US Statutes Search",
             "credits": 4, "regions": ["US"]},
            {"endpoint": "/statutes/section/body", "operation": "US Statute Section (Full Text)",
             "credits": 6, "regions": ["US"]},
            # /research/search serves BOTH regions at different prices; the
            # India-scoped search_legal_cases tool must pick the India tiers.
            {"endpoint": "/research/search", "operation": "US Case Law Search (1-20 results)",
             "credits": 2, "regions": ["US"]},
            {"endpoint": "/research/search", "operation": "Indian Case Law Search (1-20 results)",
             "credits": 1, "regions": ["IN"]},
        ]
        costs = _build_tool_costs(cost_entries)
        assert costs["search_us_statutes"] == "Cost: 4 credits."
        assert costs["get_us_statute_section_text"] == "Cost: 6 credits."
        # Only the India tier (1), never the US tier (2), for this tool.
        assert costs["search_legal_cases"] == "Cost: 1 credit."

    def test_build_tool_costs_empty_on_no_data(self) -> None:
        """A failed fetch (empty list) yields no cost lines, not wrong ones."""
        assert _build_tool_costs([]) == {}

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

        with pytest.raises(httpx.HTTPStatusError):
            _fetch_openapi_spec("https://api.vaquill.ai")

    def test_raises_on_connect_error_after_retries(self, respx_mock) -> None:
        """Should retry on ConnectError and raise after all attempts fail."""
        respx_mock.get("https://api.vaquill.ai/external/openapi.json").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with pytest.raises(httpx.ConnectError):
            _fetch_openapi_spec("https://api.vaquill.ai")

    def test_raises_on_timeout_after_retries(self, respx_mock) -> None:
        """Should retry on TimeoutException and raise after all attempts fail."""
        respx_mock.get("https://api.vaquill.ai/external/openapi.json").mock(
            side_effect=httpx.ReadTimeout("Read timed out")
        )

        with pytest.raises(httpx.TimeoutException):
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
            httpx.ConnectError("Connection refused"),
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
