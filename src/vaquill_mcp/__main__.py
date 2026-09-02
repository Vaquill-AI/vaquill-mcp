"""Entry point for `python -m vaquill_mcp` and `uvx vaquill-mcp`.

Jurisdiction can be selected two ways, flag first:

    uvx vaquill-mcp --jurisdiction IN
    VAQUILL_JURISDICTION=IN uvx vaquill-mcp

The flag exists because this is the STDIO server, whose configuration lives in
someone else's MCP client file. Adding a second `env` entry there is easy to
forget and easy to typo, and a wrong value would otherwise surface as "the
tools are missing" rather than as an error. Both forms default to US, so an
existing config keeps working untouched.
"""

from __future__ import annotations

import argparse

from vaquill_mcp.config import _SPEC_PATHS
from vaquill_mcp.server import create_server


def main() -> None:
    """Create the Vaquill MCP server and run it on stdio transport."""
    parser = argparse.ArgumentParser(
        prog="vaquill-mcp",
        description="MCP server for the Vaquill legal research API.",
    )
    parser.add_argument(
        "--jurisdiction",
        choices=sorted(_SPEC_PATHS),
        default=None,
        help=(
            "Which corpus to serve: US (default) or IN. Falls back to "
            "VAQUILL_JURISDICTION, then to US."
        ),
    )
    args = parser.parse_args()

    server = create_server(args.jurisdiction)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
