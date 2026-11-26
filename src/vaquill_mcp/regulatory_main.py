"""Entry point for the hosted Indian-regulatory MCP server.

Serves the regulatory tools (the table in `regulatory.TOOL_NAMES`) over
Streamable HTTP at the ROOT of its own origin:

    https://<regulatory host>/mcp             bearer `vq_key_...` or OAuth
    https://<regulatory host>/s/{api_key}     key in the path, for authless clients

WHY ITS OWN ORIGIN, AND NOT A PATH ON mcp.vaquill.ai
====================================================

OAuth only works at an origin root. RFC 9728 discovery is served from the
origin's `/.well-known/*`, but a FastMCP app knows only its own `path` and
nothing about a Mount above it, so an app under a prefix advertises the WRONG
`resource` URL and serves its document at the WRONG location (measured, and
recorded in `remote_main.build_app`: `mount_auth = auth if prefix == "" else
None`). Anthropic requires `resource` to match the URL the user typed, path
included. This corpus has to be connectable from Harvey and Claude over OAuth,
so it gets a subdomain, mounted at "/", and the problem does not arise.

This module is SEPARATE from `remote_main` on purpose. `remote_main._MOUNTS`
drives the live US and India connectors, which exact tool-count tests protect;
a third entry there would put a different corpus behind the same process, the
same tool budget and the same failure domain. A separate process also lets this
one be deployed, scaled and rolled back alone.

ONE DEPLOYMENT PER ORIGIN
=========================

OAuth is configured by `VAQUILL_PUBLIC_URL`, and that is THIS origin, so the
`VAQUILL_OAUTH_*` group on this deployment is its own: it names this host, and
the upstream authorization server must list this host's callback. Absent the
group the server behaves as bearer-only, exactly as `remote_main` does.

Environment variables (a subset of `remote_main`'s, with the same meaning):
    HOST                     Listen address (default: 0.0.0.0)
    PORT                     Listen port (default: 8000)
    VAQUILL_BASE_URL         API base URL (default: https://api.vaquill.ai)
    VAQUILL_TIMEOUT          Request timeout in seconds (default: 120)
    VAQUILL_PUBLIC_URL       This server's public URL, for OAuth metadata
    VAQUILL_INTERNAL_SECRET  Shared secret for connector-key resolution
    VAQUILL_OAUTH_*          Authorization server details; see oauth.py
"""

from __future__ import annotations

import logging
import os

from starlette.applications import Starlette

from vaquill_mcp.log_redaction import install_key_redaction
from vaquill_mcp.origin import ORIGINS
from vaquill_mcp.origin import build_app as _build_origin_app

logger = logging.getLogger(__name__)

#: This origin's registry row. The app's shape, its mount ORDER and its OAuth
#: wiring live in `vaquill_mcp.origin`, shared by every single-corpus origin,
#: because this module and `tribunals_main` were the same 180 lines twice and
#: Australia, Canada and further jurisdictions are each another copy. This
#: module stays so the console script `vaquill-mcp-regulatory`, the import path and
#: the golden-file tests keep naming what the deployments already name.
ORIGIN = ORIGINS["regulatory"]


def build_app() -> Starlette:
    """Compose the ASGI app for the regulatory origin."""
    return _build_origin_app(ORIGIN)


def main() -> None:
    """Start the regulatory MCP server on Streamable HTTP transport."""
    # This entry point IS the regulatory origin, so it does not require the
    # deployment to say so a second time. A DISAGREEING value is refused rather
    # than ignored: `VAQUILL_ORIGIN` reaches `origin.get_origin()` from
    # anywhere else in the process, so a container running this script with
    # another origin's slug set would be one import away from serving the wrong
    # corpus under this host name, with every log line naming this one.
    current = os.environ.get("VAQUILL_ORIGIN", "").strip().lower()
    if current and current != ORIGIN.slug:
        raise ValueError(
            f"vaquill-mcp-regulatory serves the {ORIGIN.slug!r} origin, but "
            f"VAQUILL_ORIGIN is {current!r}. Unset it, or run "
            f"`vaquill-mcp-origin` instead."
        )
    os.environ["VAQUILL_ORIGIN"] = ORIGIN.slug

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # AFTER `basicConfig`, and before anything is served: `/s/{api_key}` puts a
    # customer's key in the URL and uvicorn's access log prints the URL. This
    # also holds the httpx2/httpcore2 loggers at WARNING (their INFO line is the
    # full upstream URL, `?q=` included).
    #
    # Kept HERE rather than deferred to `origin.main`, and the ordering test
    # patches this module's name: the guarantee is "this entry point redacts
    # before it serves", so each entry point must be able to fail that test on
    # its own. A shared `main` would make one test stand for every origin.
    install_key_redaction()

    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    logger.info(
        "Regulatory MCP serving /mcp and /s/{api_key} on %s:%s",
        host,
        port,
    )
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
