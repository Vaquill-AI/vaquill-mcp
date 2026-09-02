"""Shared test fixtures for vaquill-mcp tests."""

import pytest


@pytest.fixture
def respx_mock(httpx2_mock):
    """Override respx's own `respx_mock` fixture to intercept httpx2.

    THIS IS THE ONE THAT BITES. respx patches `httpcore`; the package moved to
    httpx2 with fastmcp 4, and httpx2 sits on `httpcore2`. Stock respx therefore
    matches NOTHING against our clients, and because an unmatched route falls
    through rather than failing, the suite would keep passing while making REAL
    network calls to api.vaquill.ai -- the exact opposite of what this fixture
    exists to guarantee.

    `httpx2_mock` (from pytest-httpx2) is respx configured with
    `using="httpcore2"`. Shadowing the well-known fixture NAME rather than
    renaming it at ~20 call sites means every existing test keeps its signature
    and silently gets the correct target.
    """
    return httpx2_mock


@pytest.fixture(autouse=True)
def _block_real_http(respx_mock):
    """Ensure no test accidentally makes real HTTP requests.

    Autouse, so a test that forgets to mock something raises instead of
    reaching the network. Depends on the override above, not on stock respx.
    """


@pytest.fixture
def sample_openapi_spec() -> dict:
    """Minimal OpenAPI spec that mirrors the Vaquill external API structure.

    Contains enough endpoints to verify tool naming, exclusion of the
    streaming endpoint, and description customization.
    """
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Vaquill Developer API",
            "version": "1.0.0",
        },
        "servers": [{"url": "https://api.vaquill.ai"}],
        "paths": {
            "/api/v1/ask": {
                "post": {
                    "operationId": "ask_legal_question_api_v1_ask_post",
                    "summary": "Ask a legal question",
                    "description": "Very long description that should be overridden...",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["question"],
                                    "properties": {
                                        "question": {
                                            "type": "string",
                                            "description": "The legal question to ask",
                                        },
                                        "mode": {
                                            "type": "string",
                                            "enum": ["standard", "deep"],
                                            "default": "standard",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/api/v1/ask/stream": {
                "post": {
                    "operationId": "ask_legal_question_stream_api_v1_ask_stream_post",
                    "summary": "Ask a legal question (streaming)",
                    "description": "SSE streaming variant - should be EXCLUDED",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["question"],
                                    "properties": {
                                        "question": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "SSE stream"}},
                }
            },
            "/api/v1/research/search": {
                "post": {
                    "operationId": "external_search_api_v1_research_search_post",
                    "summary": "Search the legal corpus",
                    "description": "Long description...",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["query"],
                                    "properties": {
                                        "query": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/api/v1/research/quick": {
                "post": {
                    "operationId": "bot_search_api_v1_research_quick_post",
                    "summary": "Quick search for bots",
                    "description": "Long description...",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["query"],
                                    "properties": {
                                        "query": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/api/v1/citations/resolve": {
                "get": {
                    "operationId": "resolve_citation_api_v1_citations_resolve_get",
                    "summary": "Resolve a citation",
                    "description": "Long description...",
                    "parameters": [
                        {
                            "name": "citation",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/api/v1/citations/cases/search": {
                "get": {
                    "operationId": "search_cases_api_v1_citations_cases_search_get",
                    "summary": "Search cases",
                    "description": "Long description...",
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/api/v1/citations/cases/lookup": {
                "get": {
                    "operationId": "lookup_case_api_v1_citations_cases_lookup_get",
                    "summary": "Look up case details",
                    "description": "Long description...",
                    "parameters": [
                        {
                            "name": "citation",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/api/v1/citations/cases/network": {
                "get": {
                    "operationId": "get_citation_network_api_v1_citations_cases_network_get",
                    "summary": "Get citation network",
                    "description": "Long description...",
                    "parameters": [
                        {
                            "name": "citation",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/api/v1/api-credits/pricing": {
                "get": {
                    "operationId": "get_pricing_api_v1_api_credits_pricing_get",
                    "summary": "Get pricing",
                    "description": "Long description...",
                    "parameters": [],
                    "responses": {"200": {"description": "Success"}},
                }
            },
        },
    }
