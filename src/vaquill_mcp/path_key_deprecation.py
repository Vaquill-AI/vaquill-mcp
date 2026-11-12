"""Mark the key-in-the-URL mounts (`/s/{api_key}`, `/in/s/{api_key}`) deprecated.

A credential in the URL path leaks through proxies, browser history and any log
that prints the target. `log_redaction` strips it from OUR logs, but the shape
itself is the defect, so the mounts are kept working for the customers who
depend on them while every response says they are on the way out.

Per request this middleware:

* logs one `path_key_mount_used` event carrying the mount prefix and the first
  few characters of the key, NEVER the key. Enough to tell which customers still
  depend on the shape, useless to replay;
* adds `Deprecation: true` and a `Link: <...>; rel="deprecation"` header
  (RFC 9745 and RFC 8288) pointing at the migration docs.

There is deliberately no `Sunset` header: no date has been decided, and a
made-up date in a header is a promise to customers.

Pure ASGI, fails OPEN: an error here is never a failed MCP call.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Where the migration to `Authorization: Bearer` or OAuth is described.
DEPRECATION_DOC_URL = "https://www.vaquill.ai/docs/integrations/mcp/vaquill"

#: `vq_key_` plus this many characters. Short enough to be useless as a
#: credential, long enough to match a customer's key list by eye.
_PREFIX_CHARS = 4
_KEY_PREFIX = "vq_key_"


def key_prefix(path: str) -> str:
    """The loggable prefix of the key in `path`, never more than 11 characters."""
    segment = path.rstrip("/").rsplit("/", 1)[-1]
    if segment.startswith(_KEY_PREFIX):
        return segment[: len(_KEY_PREFIX) + _PREFIX_CHARS]
    # Not a Vaquill-shaped key (a placeholder like `_`, or junk): say so
    # instead of echoing arbitrary caller text into the log.
    return "other"


class PathKeyDeprecation:
    """ASGI middleware for the path-key mounts. Install on that app only."""

    def __init__(self, app, mount: str = "/s") -> None:
        self.app = app
        self.mount = mount

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        try:
            logger.info(
                "path_key_mount_used mount=%s key_prefix=%s method=%s",
                self.mount,
                key_prefix(scope.get("path") or ""),
                scope.get("method"),
            )
        except Exception:
            pass

        async def deprecating_send(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"deprecation", b"true"))
                headers.append(
                    (b"link", f'<{DEPRECATION_DOC_URL}>; rel="deprecation"'.encode())
                )
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, deprecating_send)
