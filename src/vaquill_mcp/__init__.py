"""Vaquill MCP Server - Legal research tools powered by 13M+ court judgments."""

import logging

__version__ = "0.1.0"
__all__ = ["__version__", "create_server"]

# PEP 593: Libraries should add NullHandler to prevent "No handler found"
# warnings. The application (CLI entry point) configures actual handlers.
logging.getLogger(__name__).addHandler(logging.NullHandler())


def create_server():  # noqa: ANN201
    """Create and configure the Vaquill MCP server.

    Lazy import to avoid loading FastMCP when only ``__version__`` is needed.
    """
    from vaquill_mcp.server import create_server as _create_server

    return _create_server()
