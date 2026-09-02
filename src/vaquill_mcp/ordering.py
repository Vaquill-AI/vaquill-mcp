"""Deterministic `tools/list` ordering.

The MCP client specification (2026-07-28) is explicit that adding, removing or
REORDERING tools invalidates a provider's cached prompt prefix. A catalogue that
comes back in a different order on two consecutive calls therefore pays the full
definition cost on every single call, which for this server is the entire
optimisation in `schema_slim.py` given back.

Nothing sorted before this. `OpenAPIProvider._list_tools` returns
`list(self._tools.values())`, a dict populated while walking `spec["paths"]`, and
`FastMCP.list_tools` aggregates providers without ordering them either. Python
dicts preserve insertion order and FastAPI emits paths deterministically, so the
order was stable in practice -- but stable BY ACCIDENT. A route reordering in the
backend, a FastAPI upgrade, or (the one actually coming) mounting a second
provider alongside the OpenAPI one would silently start reordering the list, with
no error and no test to catch it.

Sorting is done as middleware rather than by subclassing the provider because the
alias tools in `aliases.py` come from a different provider. Middleware sees the
aggregated list, so the guarantee covers the whole catalogue rather than one
provider's slice of it.
"""

from __future__ import annotations

from collections.abc import Sequence

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

# `fastmcp.tools`, not `fastmcp.tools.tool`: the submodule was removed in
# fastmcp 4.0, and this package declares `fastmcp>=3.0.0`, so a fresh install
# resolves to 4.x. The package path here works on both.
from fastmcp.tools import Tool


class DeterministicToolOrder(Middleware):
    """Return `tools/list` sorted by name, always.

    Sorting by name rather than preserving spec order on purpose: spec order is
    the thing that cannot be relied on. A name sort is reproducible from the
    catalogue alone, so two processes built from the same document emit the same
    list, which is what the prompt-prefix cache is keyed on.
    """

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        return sorted(await call_next(context), key=lambda tool: tool.name)
