"""Entry point for the remote Vaquill MCP server.

Runs over Streamable HTTP transport, serving at ``/s/{api_key}``.
Users paste ``https://mcp.vaquill.ai/s/{their_key}`` into Claude.ai.

Environment variables:
    HOST            Listen address (default: 0.0.0.0)
    PORT            Listen port (default: 8000)
    VAQUILL_BASE_URL  API base URL (default: https://api.vaquill.ai)
    VAQUILL_TIMEOUT   Request timeout in seconds (default: 120)
"""

from __future__ import annotations

import logging
import os


def main() -> None:
    """Start the remote MCP server on Streamable HTTP transport."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from vaquill_mcp.remote import mcp

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        path="/s/{api_key}",
    )


if __name__ == "__main__":
    main()
