"""Entry point for `python -m vaquill_mcp` and `uvx vaquill-mcp`."""

from vaquill_mcp.server import create_server


def main() -> None:
    """Create the Vaquill MCP server and run it on stdio transport."""
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
