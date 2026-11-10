"""Keep customers' API keys out of the logs.

`/s/{api_key}` is the simple-paste mount for clients that cannot send a header,
so the key travels in the URL, and uvicorn's access log prints the whole path.
Read from the production container log on 2026-10-02: live `vq_key_` values in
plain text on every call. Anything that can read the container's stdout (the
deploy platform's log viewer, a log shipper) could spend them.

The key has to stay in the URL, since that is how those clients authenticate, so
the log line is where it is removed.

QUERY TEXT, NOT ONLY KEYS
=========================

Zero data retention: the text a customer researched never reaches a log. Four
routes carried it before `install_query_redaction` existed, each read from the
installed libraries rather than assumed:

1. httpx2 logs `HTTP Request: GET <full URL>` at INFO for every upstream call,
   so `/us/statutes/resolve?cite=<the citation>` was printed per `fetch`. The
   HTTP client loggers are held at WARNING.
2. uvicorn's access line prints the request target with its query string. The
   target is cut at `?`, keeping method, path and status.
3. FastMCP's `Error calling tool` is a `logger.exception`, and the traceback
   prints the exception's message: an upstream 422 body that echoes the input,
   httpx's `HTTPStatusError` (whose text is the full URL), pydantic's
   `input_value=...`, our own `Could not resolve '<id>'`. Tracebacks keep their
   frames and exception classes and lose every message.
4. FastMCP's `Invalid arguments for tool %r: %s` logs pydantic's error list,
   whose `input` key IS the argument. It is cut to location and error type.
"""

from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Iterable
from types import TracebackType

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
    created on that logger, not ones propagated up from its children. FastMCP's
    handlers are covered too, since its loggers do not propagate to the root.

    Also installs the query-text redaction (`install_query_redaction`), so the
    one call `remote_main.main` makes covers keys and query text alike.
    """
    _attach_once(KeyRedactionFilter(), _redaction_targets())
    install_query_redaction()


# ---------------------------------------------------------------------------
# Query text: request targets, URLs, tool arguments and exception messages
# ---------------------------------------------------------------------------

#: Loggers that print one line per outbound request with the FULL URL. httpx2
#: and httpcore2 are what the package uses; httpx and httpcore are still
#: installed transitively and are held down too, so nothing that imports them
#: reopens the leak.
_HTTP_CLIENT_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2")

#: Loggers whose records are filtered where they are CREATED. A logger filter
#: survives uvicorn's `dictConfig` (it replaces handlers, never logger filters),
#: so these hold whatever handlers uvicorn builds at startup. `uvicorn.error`
#: carries "Exception in ASGI application" tracebacks, and
#: `fastmcp.server.server` carries the tool-call failures.
_FILTERED_LOGGERS = ("uvicorn.access", "uvicorn.error", "fastmcp.server.server")

#: A request target inside a request line: `"GET /path?query HTTP/1.1"`.
_REQUEST_LINE_QUERY_RE = re.compile(r'("(?:[A-Z]+) [^\s"?]*)\?[^\s"]*')

#: An absolute URL's query string, wherever it appears in a message.
_URL_QUERY_RE = re.compile(r"(https?://[^\s?#\"'<>]*)\?[^\s\"'<>#]*")
_URL_QUERY_REPLACEMENT = r"\1?[redacted]"

#: FastMCP's argument-validation warning (fastmcp/server/server.py). The second
#: argument is pydantic's error list, whose `input` key is the argument itself.
_INVALID_ARGUMENTS_MSG = "Invalid arguments for tool %r: %s"

_MESSAGE_REDACTED = "[message redacted]"

#: Set on a record once `QueryTextRedactionFilter` has rewritten it.
_DONE_ATTR = "_vaquill_query_text_redacted"


def strip_query_string(target: str) -> str:
    """`target` without its query string. On the Data API the query IS the research."""
    return target.split("?", 1)[0]


def _strip_url_queries(value: object) -> object:
    """`value` with every absolute URL's query string redacted.

    Same type discipline as `_redact`: an object (an `httpx2.URL`) is replaced by
    its text only when that text changed, so `%d` and friends keep working. The
    `?` and `://` guards keep the regex off the common line that has neither.
    """
    if isinstance(value, str):
        if "?" not in value or "://" not in value:
            return value
        return _URL_QUERY_RE.sub(_URL_QUERY_REPLACEMENT, value)
    if value is None or isinstance(value, (bool, int, float, bytes)):
        return value
    try:
        text = str(value)
    except Exception:
        return value
    redacted = _strip_url_queries(text)
    return redacted if redacted != text else value


def _summarize_validation_errors(detail: object) -> str:
    """Location and error type of each pydantic error, never its `input`."""
    if isinstance(detail, list) and all(isinstance(e, dict) for e in detail):
        parts = [
            f"{'.'.join(str(p) for p in e.get('loc') or ()) or '<root>'}: {e.get('type', 'error')}"
            for e in detail
        ]
        return "; ".join(parts) or _MESSAGE_REDACTED
    return _MESSAGE_REDACTED


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """`exc` and the exceptions it was raised from, outermost LAST, as Python prints them."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        if current.__cause__ is not None:
            current = current.__cause__
        elif current.__suppress_context__:
            current = None
        else:
            current = current.__context__
    chain.reverse()
    return chain


def _class_name(exc: BaseException) -> str:
    cls = type(exc)
    module = cls.__module__
    return cls.__qualname__ if module == "builtins" else f"{module}.{cls.__qualname__}"


def safe_exception_text(
    exc_info: tuple[
        type[BaseException] | None, BaseException | None, TracebackType | None
    ],
) -> str:
    """A traceback with every frame and exception class, and no exception message.

    The message is where arguments live (see the module docstring), and the
    frames are what locates a bug, so the frames stay. Sub-exceptions of an
    exception group are named, since anyio wraps task failures in one and the
    group alone would hide which error it was.
    """
    exc = exc_info[1]
    if exc is None:
        cls = exc_info[0]
        return f"{cls.__qualname__ if cls else 'Exception'}: {_MESSAGE_REDACTED}"
    lines: list[str] = []
    for i, link in enumerate(_exception_chain(exc)):
        if i:
            lines.append("\nThe above exception led to the following exception:\n\n")
        lines.append("Traceback (most recent call last):\n")
        lines.extend(traceback.format_list(traceback.extract_tb(link.__traceback__)))
        lines.append(f"{_class_name(link)}: {_MESSAGE_REDACTED}\n")
        members = getattr(link, "exceptions", None)
        if isinstance(members, tuple) and members:
            names = ", ".join(
                _class_name(m) for m in members if isinstance(m, BaseException)
            )
            lines.append(f"  (group of: {names})\n")
    return "".join(lines).rstrip("\n")


class QueryTextRedactionFilter(logging.Filter):
    """Removes query text from a record in place. Never drops a record.

    - An access record (a logger named `*.access`) is uvicorn's 5-tuple
      `(client, method, target, http_version, status)`. The target loses its
      query string and `args` keeps its shape, because uvicorn's formatter
      unpacks it positionally. A string-only request line is cut by regex.
    - Every other string and argument has absolute URLs' query strings redacted.
    - FastMCP's argument-validation warning keeps location and error type only.
    - A record carrying an exception keeps its frames and classes, loses every
      message, and loses `exc_info` so no handler (Rich included, which renders
      from `exc_info` and ignores `exc_text`) can print the original.

    Per record this is an `isinstance`, two substring checks and, only on a
    line that has a `?`, one regex. Tracebacks are built only for records that
    carry one.

    Fails CLOSED: a filter that raises propagates into the code that logged,
    and one that gives up silently would print the original, so a record this
    cannot rewrite is replaced by a fixed line naming only its logger.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # One pass per record. A record can meet this filter twice (on the
        # logger that made it, then on a handler), and a second pass would
        # re-summarize an already-summarized validation detail into nothing.
        if getattr(record, _DONE_ATTR, False):
            return True
        setattr(record, _DONE_ATTR, True)
        try:
            self._rewrite(record)
        except Exception:
            record.msg = "log record withheld: query-text redaction failed"
            record.args = None
            record.exc_info = None
            record.exc_text = None
        return True

    @staticmethod
    def _rewrite(record: logging.LogRecord) -> None:
        args = record.args
        if args and isinstance(args, tuple):
            name = record.name or ""
            if name.endswith(".access") and len(args) == 5 and isinstance(args[2], str):
                if "?" in args[2]:
                    record.args = (*args[:2], strip_query_string(args[2]), *args[3:])
            elif record.msg == _INVALID_ARGUMENTS_MSG and len(args) == 2:
                record.args = (args[0], _summarize_validation_errors(args[1]))
            else:
                record.args = tuple(_strip_url_queries(a) for a in args)
        elif isinstance(args, dict):
            record.args = {k: _strip_url_queries(v) for k, v in args.items()}
        if isinstance(record.msg, str) and "?" in record.msg:
            msg = _REQUEST_LINE_QUERY_RE.sub(r"\1", record.msg)
            record.msg = _strip_url_queries(msg)
        if record.exc_info:
            record.exc_text = safe_exception_text(record.exc_info)
            record.exc_info = None


def quiet_http_client_loggers() -> None:
    """Hold the HTTP client loggers at WARNING. Their INFO line is the full URL.

    Called AFTER `basicConfig`, which sets only the root, and safe against
    uvicorn's `dictConfig`, which names only its own loggers and runs with
    `disable_existing_loggers: False`.
    """
    for name in _HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _redaction_targets() -> list[logging.Logger | logging.Handler]:
    """Every logger and handler a filter must sit on to see the server's records.

    Root handlers cover the application and the `mcp` SDK, which propagate.
    FastMCP does NOT propagate (`fastmcp/utilities/logging.py` sets
    `propagate = False` and installs its own handlers at import), so its
    handlers are covered directly, which is why fastmcp is imported here.
    """
    try:
        import fastmcp  # noqa: F401  # configures the `fastmcp` handlers on import
    except ImportError:  # pragma: no cover - fastmcp is a hard dependency
        pass
    targets: list[logging.Logger | logging.Handler] = [
        logging.getLogger(name) for name in _FILTERED_LOGGERS
    ]
    targets.extend(logging.getLogger().handlers)
    targets.extend(logging.getLogger("fastmcp").handlers)
    return targets


def _attach_once(
    log_filter: logging.Filter, targets: Iterable[logging.Logger | logging.Handler]
) -> None:
    for target in targets:
        if not any(type(f) is type(log_filter) for f in target.filters):
            target.addFilter(log_filter)


def install_query_redaction() -> None:
    """Keep query text out of every log line the hosted server writes. Idempotent.

    Called by `install_key_redaction`, so the one call in `remote_main.main`
    installs both before anything is served.
    """
    quiet_http_client_loggers()
    _attach_once(QueryTextRedactionFilter(), _redaction_targets())
