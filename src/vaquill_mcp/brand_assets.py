"""Square icon and favicon, served from the MCP host itself.

WHY THE SERVER CARRIES ITS OWN ICON
===================================

The only icon this server published was the wide wordmark lockup on
www.vaquill.ai: 512x191, 37 KB, no `sizes`. Clients that list an app by icon
need something else on all three counts:

* SQUARE. An app tile is square, and a 512x191 lockup either letterboxes to
  an unreadable sliver or is rejected outright.
* SMALL. OpenAI's app tooling refuses a logo over 10,240 bytes, and ChatGPT
  has refused to add a server over a 13 KB icon in the field.
* SAME HOST. The MCP icon spec (SEP-973) says clients MUST check that an icon
  URL is on the server's own domain or a trusted one. www.vaquill.ai is a
  different host from mcp.vaquill.ai, so a strict client may drop it.

So the square icon is a packaged file served at `/icon.png` on this host,
128x128 and well under the 10 KB ceiling, and `/favicon.ico` stops 404ing on
the consent page.

THE LOCKUP STAYS FIRST
======================

FastMCP's consent screen renders `icons[0]` and nothing else, and the brand
skin in oauth.py sizes that slot for the wide lockup. The square icon is
therefore APPENDED. A client choosing by `sizes` finds it; the consent page is
unchanged.

The bytes are read once, at import, because they never change for the life of
a process and a missing asset should fail the boot rather than the first
browser that asks for it.
"""

from __future__ import annotations

import os
from importlib.resources import files
from urllib.parse import urlsplit

from mcp.types import Icon
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

SQUARE_ICON_PATH = "/icon.png"
FAVICON_PATH = "/favicon.ico"
SQUARE_ICON_SIZE = "128x128"

# The production origin, used only when VAQUILL_PUBLIC_URL is unset (a local
# checkout). The icon is a public brand asset, so a dev server pointing at the
# production copy is correct rather than a leak.
_DEFAULT_ORIGIN = "https://mcp.vaquill.ai"

# A day in browser caches, a week in shared ones. Long enough that a client
# re-rendering an app list does not refetch it, short enough that a rebrand
# lands within the week without a cache-busting rename.
_CACHE_CONTROL = "public, max-age=86400, s-maxage=604800"

_STATIC = files("vaquill_mcp") / "static"
_SQUARE_ICON = (_STATIC / "icon-128.png").read_bytes()
_FAVICON = (_STATIC / "favicon.ico").read_bytes()


def public_origin() -> str:
    """This server's public origin, `scheme://host`, with no path.

    VAQUILL_PUBLIC_URL is the OAuth base URL. Only its origin is taken, so the
    icon URL is right whether the variable is set with or without a path.
    """
    raw = os.environ.get("VAQUILL_PUBLIC_URL", "").strip()
    parts = urlsplit(raw)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return _DEFAULT_ORIGIN


def square_icon() -> Icon:
    """The square app icon, on this server's own host."""
    return Icon(
        src=f"{public_origin()}{SQUARE_ICON_PATH}",
        mime_type="image/png",
        sizes=[SQUARE_ICON_SIZE],
    )


async def _square_icon(_request: Request) -> Response:
    return Response(
        _SQUARE_ICON,
        media_type="image/png",
        headers={"Cache-Control": _CACHE_CONTROL},
    )


async def _favicon(_request: Request) -> Response:
    return Response(
        _FAVICON,
        media_type="image/x-icon",
        headers={"Cache-Control": _CACHE_CONTROL},
    )


def asset_routes() -> list[Route]:
    """Routes for the two assets, to be declared BEFORE the root mount.

    The US app mounts at "/" and Starlette never falls through to a later
    route, so anything declared after it is unreachable.
    """
    return [
        Route(SQUARE_ICON_PATH, _square_icon, methods=["GET", "HEAD"]),
        Route(FAVICON_PATH, _favicon, methods=["GET", "HEAD"]),
    ]
