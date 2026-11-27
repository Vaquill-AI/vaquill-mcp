"""A deprecated REST alias is never published as an MCP tool.

The backend keeps a retired route alive with FastAPI's `deprecated=True` so old
callers do not break, and the OpenAPI document then carries
`"deprecated": true` on that operation. The MCP catalogue must drop it without
any per-route edit here, otherwise every consolidation on the API side doubles
the tool list instead of shrinking it.
"""

import copy

import httpx
import pytest

from vaquill_mcp.server import (
    _build_tool_costs,
    _derive_mcp_names,
    create_server,
    is_deprecated_operation,
    published_tool_names,
    without_deprecated_operations,
)

_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "t", "version": "1"},
    "paths": {
        "/api/v1/in/acts/{act_id}": {
            "get": {
                "operationId": "get_act_record_api_v1_in_acts__act_id__get",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/api/v1/in/acts/{act_id}/status": {
            "get": {
                "operationId": "get_act_status_api_v1_in_acts__act_id__status_get",
                "deprecated": True,
                "parameters": [
                    {"name": "act_id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/api/v1/in/acts/coverage": {
            "get": {
                "operationId": "get_acts_coverage_api_v1_in_acts_coverage_get",
                "responses": {"200": {"description": "ok"}},
            },
            "post": {
                "operationId": "post_acts_coverage_api_v1_in_acts_coverage_post",
                "deprecated": True,
                "responses": {"200": {"description": "ok"}},
            },
        },
        "/api/v1/in/acts/odd": {
            "get": {
                # A STRING is not the boolean the spec defines; it must not
                # silently remove a tool.
                "operationId": "odd_api_v1_in_acts_odd_get",
                "deprecated": "true",
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
    "components": {"schemas": {"Keep": {"type": "object"}}},
}


def test_predicate_only_accepts_the_boolean() -> None:
    assert is_deprecated_operation({"deprecated": True})
    assert not is_deprecated_operation({"deprecated": "true"})
    assert not is_deprecated_operation({"deprecated": False})
    assert not is_deprecated_operation({})


def test_pruned_copy_drops_only_deprecated_operations() -> None:
    original = copy.deepcopy(_SPEC)
    pruned = without_deprecated_operations(_SPEC)

    assert _SPEC == original, "the input document must not be mutated"
    assert "/api/v1/in/acts/{act_id}/status" not in pruned["paths"], (
        "a path left with no operation is dropped"
    )
    assert set(pruned["paths"]["/api/v1/in/acts/coverage"]) == {"get"}
    assert "/api/v1/in/acts/odd" in pruned["paths"]
    assert "/api/v1/in/acts/{act_id}" in pruned["paths"]
    assert pruned["components"] == _SPEC["components"]


def test_helpers_skip_deprecated_operations_on_the_raw_document() -> None:
    names = published_tool_names(_SPEC)
    assert "get_act_status" not in names
    assert "post_acts_coverage" not in names
    assert {"get_act_record", "get_acts_coverage", "odd"} <= names
    assert published_tool_names(_SPEC) == published_tool_names(
        without_deprecated_operations(_SPEC)
    )
    assert "get_act_status_api_v1_in_acts__act_id__status_get" not in _derive_mcp_names(_SPEC)


def test_a_deprecated_route_gets_no_cost_line() -> None:
    costs = _build_tool_costs(
        _SPEC,
        [
            {"endpoint": "/in/acts/status", "operation": "Act status", "credits": 1},
            {"endpoint": "/in/acts", "operation": "Act record", "credits": 1},
        ],
    )
    assert "get_act_status" not in costs
    assert "get_act_record" in costs


async def test_server_does_not_publish_a_deprecated_operation(
    monkeypatch: pytest.MonkeyPatch, respx_mock
) -> None:
    monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test123")
    monkeypatch.setenv("VAQUILL_BASE_URL", "https://api.vaquill.ai")
    monkeypatch.setenv("VAQUILL_JURISDICTION", "IN")
    respx_mock.get("https://api.vaquill.ai/in/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC)
    )
    respx_mock.get(url__regex=r"https://api\.vaquill\.ai/api/v1(/in)?/api-credits/pricing/all$").mock(
        return_value=httpx.Response(200, json={"costs": []})
    )

    server = create_server()
    tools = {t.name for t in await server.list_tools()}

    assert "get_act_status" not in tools
    assert "post_acts_coverage" not in tools
    assert {"get_act_record", "get_acts_coverage"} <= tools
