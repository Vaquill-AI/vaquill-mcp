"""Keep customers' API keys out of the logs.

`/s/{api_key}` is the simple-paste mount for clients that cannot send a header,
so the key travels in the URL, and uvicorn's access log prints the whole path.
Read from the production container log on 2026-10-02: live `vq_key_` values in
plain text on every call. Anything that can read the container's stdout (the
deploy platform's log viewer, a log shipper) could spend them.

The key has to stay in the URL, since that is how those clients authenticate, so
the log line is where it is removed.
"""

from __future__ import annotations

import logging
import re

#: A Vaquill key: the prefix plus a long token. At least eight token characters,
#: so prose that merely NAMES the format ("Keys must start with vq_key_") is left
#: readable and only something shaped like a credential is touched.
_KEY_RE = re.compile(r"vq_key_[A-Za-z0-9_\-]{8,}")
_REPLACEMENT = "vq_key_[redacted]"


def _redact(value: object) -> object:
    """`value` with any key removed; objects are rewritten only when they contain one.

    Libraries log URL OBJECTS (httpx logs `HTTP Request: %s %s` with an
    `httpx.URL`), so a str-only pass would leave exactly those lines leaking.
    Numbers and None are returned as they are so `%d` and `%s` keep working, and
    an object is replaced by its redacted text only when that text differs, so
    nothing else changes type under a formatter.
    """
    if isinstance(value, str):
        return _KEY_RE.sub(_REPLACEMENT, value)
    if value is None or isinstance(value, (bool, int, float, bytes)):
        return value
    try:
        text = str(value)
    except Exception:
        return value
    redacted = _KEY_RE.sub(_REPLACEMENT, text)
    return redacted if redacted != text else value


class KeyRedactionFilter(logging.Filter):
    """Rewrites a record in place so no formatter downstream can print a key.

    The message and every argument are redacted separately and `args` keeps its
    shape. uvicorn's access formatter unpacks `record.args` as a 5-tuple, so
    folding everything into `msg` and clearing `args` would pass a message-level
    test and then crash the logger on the first request.

    Never drops a record: this layer redacts, it does not decide what is logged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_redact(a) for a in args)
        elif isinstance(args, dict):
            record.args = {k: _redact(v) for k, v in args.items()}
        return True


def install_key_redaction() -> None:
    """Attach the filter to the loggers and handlers a key can reach. Idempotent.

    `uvicorn.access` is where the path is printed. The root handlers cover the
    application's own loggers, because a filter on a LOGGER sees only records
    created on that logger, not ones propagated up from its children.
    """
    redactor = KeyRedactionFilter()
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, KeyRedactionFilter) for f in access.filters):
        access.addFilter(redactor)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, KeyRedactionFilter) for f in handler.filters):
            handler.addFilter(redactor)
