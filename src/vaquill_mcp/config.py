"""Configuration for the Vaquill MCP server.

Reads settings from environment variables:
- VAQUILL_API_KEY (required) - Your API key (sign up at https://www.vaquill.ai)
- VAQUILL_BASE_URL (optional) - API base URL, defaults to https://api.vaquill.ai
- VAQUILL_TIMEOUT (optional) - Request timeout in seconds, defaults to 120
"""

import os


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
