"""Shared test fixtures for vaquill-mcp tests."""

import pytest


@pytest.fixture(autouse=True)
def _block_real_http(respx_mock):
    """Ensure no test accidentally makes real HTTP requests.

    The respx_mock fixture intercepts all httpx requests. Unmocked routes
    will raise an error instead of hitting the network.
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
