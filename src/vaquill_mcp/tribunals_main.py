"""Entry point for the hosted Indian-tribunals MCP server.

Serves the six tribunal tools over Streamable HTTP at the ROOT of its own
origin:

    https://<tribunals host>/mcp             bearer `vq_key_...` or OAuth
    https://<tribunals host>/s/{api_key}     key in the path, for authless clients

This is `regulatory_main` for a different corpus, and every structural choice
here is made for the reason recorded there: its own origin because RFC 9728
discovery is served from an origin's `/.well-known/*` and a FastMCP app under a
Mount prefix advertises the wrong `resource`; separate from `remote_main._MOUNTS`
so a third corpus does not land behind the same process, tool budget and failure
domain; and TWO servers rather than one, because FastMCP reads `self.auth` when
it builds each HTTP app and `RequireAuthMiddleware` 401s a request carrying no
Authorization header, which is exactly what a `/s/{api_key}` caller sends.

WHY A SEPARATE ORIGIN FROM THE REGULATORY ONE
=============================================

One connection should see one corpus. These two are easy to confuse in a way
that produces a wrong answer rather than a missing one: the regulatory corpus
holds what a regulator ISSUED, and this one holds matters BEFORE tribunals,
regulators and boards. A model with both catalogues on one connection can answer
a question about an order with a circular, or report that a tribunal has not
considered something because it searched the wrong corpus. `instructions.py`
gives each origin its own corpus sentence (`TRIB` here), which only works when
the origins are distinct.

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
#: module stays so the console script `vaquill-mcp-tribunals`, the import path and
#: the golden-file tests keep naming what the deployments already name.
ORIGIN = ORIGINS["tribunals"]


def build_app() -> Starlette:
    """Compose the ASGI app for the tribunals origin."""
    return _build_origin_app(ORIGIN)


def main() -> None:
    """Start the tribunals MCP server on Streamable HTTP transport."""
    # This entry point IS the tribunals origin, so it does not require the
    # deployment to say so a second time. A DISAGREEING value is refused rather
    # than ignored: `VAQUILL_ORIGIN` reaches `origin.get_origin()` from
    # anywhere else in the process, so a container running this script with
    # another origin's slug set would be one import away from serving the wrong
    # corpus under this host name, with every log line naming this one.
    current = os.environ.get("VAQUILL_ORIGIN", "").strip().lower()
    if current and current != ORIGIN.slug:
        raise ValueError(
            f"vaquill-mcp-tribunals serves the {ORIGIN.slug!r} origin, but "
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
        "Tribunals MCP serving /mcp and /s/{api_key} on %s:%s",
        host,
        port,
    )
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
