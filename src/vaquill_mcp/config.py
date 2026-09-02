"""Configuration for the Vaquill MCP server.

Reads settings from environment variables:
- VAQUILL_API_KEY (required) - Your API key (sign up at https://www.vaquill.ai)
- VAQUILL_BASE_URL (optional) - API base URL, defaults to https://api.vaquill.ai
- VAQUILL_TIMEOUT (optional) - Request timeout in seconds, defaults to 120
- VAQUILL_JURISDICTION (optional) - "US" (default) or "IN"
"""

import os

# The two jurisdictions Vaquill publishes an API for, mapped to the OpenAPI
# document that defines each one's tool set.
#
# ONE SERVER SERVES ONE JURISDICTION, and that is the whole design. The
# documents are disjoint by construction (see the guard at
# app/tests/unit/test_jurisdiction_openapi_separation.py in the backend), so a
# server pointed at the US document CANNOT expose an Indian tool even by
# accident: there is no India path in the spec it derives from. Serving both
# from one process would put every US statute tool into an Indian user's
# context window and vice versa, which is the cost an MCP client pays per tool.
_SPEC_PATHS: dict[str, str] = {
    "US": "/external/openapi.json",
    "IN": "/in/openapi.json",
}


def get_api_key() -> str:
    """Read API key from environment.

    Raises:
        ValueError: If VAQUILL_API_KEY is not set or empty.
    """
    key = os.environ.get("VAQUILL_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "VAQUILL_API_KEY environment variable is required.\n"
            "Sign up at https://www.vaquill.ai to get your API key.\n\n"
            "Set it in your MCP client config:\n"
            '  "env": { "VAQUILL_API_KEY": "vq_key_..." }'
        )
    return key


def get_base_url() -> str:
    """Read base URL from environment or use production default.

    Raises:
        ValueError: If the URL scheme is not http or https.
    """
    url = os.environ.get("VAQUILL_BASE_URL", "https://api.vaquill.ai").rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(
            f"VAQUILL_BASE_URL must start with http:// or https://, got: {url!r}"
        )
    return url


def get_timeout() -> float:
    """Read timeout from environment or use default.

    Default is 120 seconds -- the /ask endpoint in deep mode can take
    up to 90 seconds (RAG retrieval + LLM planning + generation + refinement).

    Raises:
        ValueError: If the timeout is not a positive number.
    """
    raw = os.environ.get("VAQUILL_TIMEOUT", "120")
    try:
        timeout = float(raw)
    except ValueError:
        raise ValueError(f"VAQUILL_TIMEOUT must be a number, got: {raw!r}") from None
    if timeout <= 0:
        raise ValueError(f"VAQUILL_TIMEOUT must be positive, got: {timeout}")
    return timeout


def get_jurisdiction() -> str:
    """Read the jurisdiction this server serves. "US" (default) or "IN".

    Raises:
        ValueError: If set to anything other than a jurisdiction we publish.
    """
    raw = os.environ.get("VAQUILL_JURISDICTION", "US").strip().upper()
    if raw not in _SPEC_PATHS:
        supported = ", ".join(sorted(_SPEC_PATHS))
        raise ValueError(
            f"VAQUILL_JURISDICTION must be one of: {supported}. Got: {raw!r}"
        )
    return raw


def get_spec_url(base_url: str, jurisdiction: str | None = None) -> str:
    """The OpenAPI document this server derives its tools from.

    Derived from the jurisdiction rather than configured separately: a spec URL
    that could disagree with the jurisdiction is a server that reports one
    jurisdiction and serves another.
    """
    jur = jurisdiction or get_jurisdiction()
    return f"{base_url.rstrip('/')}{_SPEC_PATHS[jur]}"
