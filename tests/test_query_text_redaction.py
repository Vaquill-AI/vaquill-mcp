"""The text a customer researched must never reach the hosted server's log.

Zero data retention. Four routes printed it before this guard, each read from
the installed libraries (see `vaquill_mcp/log_redaction.py`): httpx2's INFO line
with the full upstream URL (`/resolve?cite=...`), uvicorn's access line with the
query string, FastMCP's `Error calling tool` traceback whose exception messages
carry upstream bodies, URLs and our own `Could not resolve '<id>'`, and FastMCP's
argument-validation warning, whose pydantic detail includes the `input`.

The end-to-end tests drive the REAL code paths (uvicorn's own logging config,
FastMCP's own `call_tool`) rather than a hand-built record, because a filter on
the wrong logger passes every unit test and protects nothing.
"""

from __future__ import annotations

import io
import logging
import sys
from collections.abc import Iterator
from typing import Annotated

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from vaquill_mcp.log_redaction import (
    KeyRedactionFilter,
    QueryTextRedactionFilter,
    install_key_redaction,
    quiet_http_client_loggers,
    safe_exception_text,
    strip_query_string,
)

_SECRET = "Tex. Gov't Code 552.001 confidential-matter-zebra"
_SECRET_WORD = "zebra"
_KEY = "vq_key_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
_ACCESS_FMT = '%s - "%s %s HTTP/%s" %d'

_TOUCHED_LOGGERS = (
    "",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "fastmcp",
    "fastmcp.server.server",
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
)


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Put every logger these tests touch back exactly as it was."""
    saved = []
    for name in _TOUCHED_LOGGERS:
        lg = logging.getLogger(name)
        handler_filters = [(h, list(h.filters)) for h in lg.handlers]
        saved.append(
            (
                lg,
                lg.level,
                list(lg.filters),
                list(lg.handlers),
                lg.propagate,
                handler_filters,
            )
        )
    yield
    for lg, level, filters, handlers, propagate, handler_filters in saved:
        lg.setLevel(level)
        lg.filters = filters
        lg.handlers = handlers
        lg.propagate = propagate
        for handler, hf in handler_filters:
            handler.filters = hf


def _capture(name: str, *filters: logging.Filter) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger(name)
    lg.addHandler(handler)
    lg.setLevel(logging.INFO)
    for f in filters:
        lg.addFilter(f)
    return lg, stream


# ---------------------------------------------------------------------------
# 1. HTTP client loggers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["httpx", "httpx2", "httpcore", "httpcore2"])
def test_http_client_loggers_are_held_at_warning(name: str) -> None:
    logging.getLogger(name).setLevel(logging.DEBUG)
    quiet_http_client_loggers()
    assert logging.getLogger(name).level == logging.WARNING
    assert not logging.getLogger(name).isEnabledFor(logging.INFO)


def test_the_httpcore2_children_inherit_the_warning_level() -> None:
    """httpcore2 logs from `httpcore2.http11` etc., which must inherit, not override."""
    quiet_http_client_loggers()
    assert logging.getLogger("httpcore2.http11").getEffectiveLevel() == logging.WARNING


# ---------------------------------------------------------------------------
# 2. uvicorn's access line
# ---------------------------------------------------------------------------


def test_strip_query_string() -> None:
    assert strip_query_string("/s/x/mcp?cite=42+U.S.C.+1983") == "/s/x/mcp"
    assert strip_query_string("/health") == "/health"


def test_the_access_line_loses_its_query_and_keeps_method_path_and_status() -> None:
    lg, stream = _capture("test.qtr.uvicorn.access", QueryTextRedactionFilter())
    lg.info(_ACCESS_FMT, "10.0.1.3:41456", "GET", f"/mcp?cite={_SECRET}", "1.1", 200)
    assert stream.getvalue().strip() == '10.0.1.3:41456 - "GET /mcp HTTP/1.1" 200'


def test_the_access_record_keeps_its_five_tuple_shape() -> None:
    """uvicorn's formatter unpacks `args` positionally; changing the shape crashes it."""
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        _ACCESS_FMT,
        ("1.2.3.4:5", "POST", "/s/k?q=secret", "1.1", 404),
        None,
    )
    assert QueryTextRedactionFilter().filter(record) is True
    assert record.args == ("1.2.3.4:5", "POST", "/s/k", "1.1", 404)


def test_the_real_uvicorn_access_formatter_prints_the_stripped_target() -> None:
    from uvicorn.logging import AccessFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s')
    )
    lg = logging.getLogger("test.qtr.real.access")
    lg.handlers = [handler]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    lg.addFilter(QueryTextRedactionFilter())
    lg.info(_ACCESS_FMT, "10.0.1.3:41456", "GET", "/mcp?cite=secret&x=1", "1.1", 200)
    out = stream.getvalue()
    assert "secret" not in out
    assert '"GET /mcp HTTP/1.1" 200' in out


def test_a_string_request_line_loses_its_query() -> None:
    lg, stream = _capture("test.qtr.string.access", QueryTextRedactionFilter())
    lg.info(f'1.2.3.4 - "GET /us/statutes/resolve?cite={_SECRET_WORD} HTTP/1.1" 200')
    assert (
        stream.getvalue().strip() == '1.2.3.4 - "GET /us/statutes/resolve HTTP/1.1" 200'
    )


def test_a_non_access_logger_tuple_is_not_treated_as_a_request_line() -> None:
    """Only `*.access` records are uvicorn's 5-tuple; others keep their third argument."""
    record = logging.makeLogRecord(
        {
            "name": "app",
            "msg": "%s %s %s %s %s",
            "args": ("a", "b", "/p?keep", "d", "e"),
        }
    )
    QueryTextRedactionFilter().filter(record)
    assert record.args[2] == "/p?keep"


def test_key_and_query_redaction_compose_on_one_line() -> None:
    lg, stream = _capture(
        "test.qtr.both.access", KeyRedactionFilter(), QueryTextRedactionFilter()
    )
    lg.info(_ACCESS_FMT, "10.0.1.3", "POST", f"/s/{_KEY}?cite=secret", "1.1", 200)
    out = stream.getvalue().strip()
    assert out == '10.0.1.3 - "POST /s/vq_key_[redacted] HTTP/1.1" 200'


def test_main_installs_query_redaction_and_it_survives_uvicorns_logging_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real `main()`, then the real uvicorn `Config`, then a real access line.

    `uvicorn.run` applies `LOGGING_CONFIG` through `dictConfig` at startup, which
    replaces the access logger's handlers. The filter lives on the LOGGER, which
    `dictConfig` does not clear; this proves it on the installed uvicorn.
    """
    import uvicorn

    from vaquill_mcp import remote_main

    def fake_run(app: object, host: str, port: int) -> None:
        uvicorn.Config(app, host=host, port=port)  # runs configure_logging()
        logging.getLogger("uvicorn.access").info(
            _ACCESS_FMT, "10.0.1.3:41456", "GET", f"/s/{_KEY}/x?cite=secret", "1.1", 200
        )

    monkeypatch.setattr(remote_main, "build_app", lambda: object())
    monkeypatch.setattr(uvicorn, "run", fake_run)
    remote_main.main()

    out = capsys.readouterr().out
    assert "secret" not in out and _KEY not in out
    assert '"GET /s/vq_key_[redacted]/x HTTP/1.1" 200' in out
    for name in ("httpx", "httpx2", "httpcore", "httpcore2"):
        assert logging.getLogger(name).level == logging.WARNING, name
    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, QueryTextRedactionFilter) for f in access.filters)


def test_install_is_idempotent() -> None:
    install_key_redaction()
    install_key_redaction()
    for name in ("uvicorn.access", "uvicorn.error", "fastmcp.server.server"):
        filters = logging.getLogger(name).filters
        assert sum(isinstance(f, QueryTextRedactionFilter) for f in filters) == 1, name
        assert sum(isinstance(f, KeyRedactionFilter) for f in filters) == 1, name


def test_install_covers_fastmcps_own_handlers() -> None:
    """FastMCP does not propagate to the root, so root handlers never see its records."""
    install_key_redaction()
    handlers = logging.getLogger("fastmcp").handlers
    assert handlers, "fastmcp configures its handlers on import"
    for handler in handlers:
        assert any(isinstance(f, QueryTextRedactionFilter) for f in handler.filters)


# ---------------------------------------------------------------------------
# 3. URLs, validation detail and exceptions in ordinary records
# ---------------------------------------------------------------------------


def test_an_absolute_url_query_in_an_argument_is_redacted() -> None:
    import httpx2

    lg, stream = _capture("test.qtr.url", QueryTextRedactionFilter())
    lg.info(
        "HTTP Request: %s %s %d",
        "GET",
        httpx2.URL(
            f"https://api.vaquill.ai/api/v1/us/statutes/resolve?cite={_SECRET_WORD}"
        ),
        200,
    )
    out = stream.getvalue()
    assert _SECRET_WORD not in out
    assert "https://api.vaquill.ai/api/v1/us/statutes/resolve?[redacted]" in out
    assert out.strip().endswith("200"), "numeric arguments still format with %d"


def test_an_absolute_url_query_in_the_message_is_redacted() -> None:
    lg, stream = _capture("test.qtr.urlmsg", QueryTextRedactionFilter())
    lg.warning(f"failed for url 'https://h/p?q={_SECRET_WORD}'")
    assert _SECRET_WORD not in stream.getvalue()


def test_a_line_with_nothing_to_redact_is_unchanged() -> None:
    lg, stream = _capture("test.qtr.plain", QueryTextRedactionFilter())
    lg.info("n=%d ok=%s url=%s", 7, True, "https://api.vaquill.ai/health")
    assert stream.getvalue().strip() == "n=7 ok=True url=https://api.vaquill.ai/health"


def test_the_validation_warning_keeps_location_and_type_but_not_input() -> None:
    lg, stream = _capture("test.qtr.validation", QueryTextRedactionFilter())
    detail = [
        {
            "type": "string_too_long",
            "loc": ("query",),
            "msg": "too long",
            "input": _SECRET,
        }
    ]
    lg.warning("Invalid arguments for tool %r: %s", "search", detail)
    out = stream.getvalue()
    assert _SECRET_WORD not in out
    assert "'search'" in out and "query: string_too_long" in out


def test_the_filter_runs_once_per_record() -> None:
    """A record meets the filter on its logger AND on a handler; the second pass is a no-op."""
    f = QueryTextRedactionFilter()
    record = logging.makeLogRecord(
        {
            "msg": "Invalid arguments for tool %r: %s",
            "args": ("search", [{"type": "missing", "loc": ("query",), "input": {}}]),
        }
    )
    f.filter(record)
    f.filter(record)
    assert record.getMessage() == "Invalid arguments for tool 'search': query: missing"


def test_a_traceback_keeps_frames_and_classes_and_loses_every_message() -> None:
    lg, stream = _capture("test.qtr.exc", QueryTextRedactionFilter())
    try:
        try:
            raise ValueError(f"Could not resolve {_SECRET!r}")
        except ValueError as inner:
            raise RuntimeError(f"https://h/p?cite={_SECRET_WORD}") from inner
    except RuntimeError:
        lg.exception("Error calling tool %r", "fetch")
    out = stream.getvalue()
    assert _SECRET_WORD not in out
    assert "Error calling tool 'fetch'" in out
    assert "ValueError: [message redacted]" in out
    assert "RuntimeError: [message redacted]" in out
    assert "Traceback (most recent call last):" in out
    assert "test_a_traceback_keeps_frames_and_classes_and_loses_every_message" in out


def test_exc_info_is_cleared_so_no_handler_can_render_the_original() -> None:
    """Rich renders from `exc_info` and ignores `exc_text`; leaving it set would leak."""
    try:
        raise ValueError(_SECRET)
    except ValueError:
        record = logging.makeLogRecord({"msg": "x", "exc_info": sys.exc_info()})
    QueryTextRedactionFilter().filter(record)
    assert record.exc_info is None
    assert record.exc_text and _SECRET_WORD not in record.exc_text


@pytest.mark.skipif(sys.version_info < (3, 11), reason="ExceptionGroup is 3.11+")
def test_an_exception_group_names_its_members() -> None:
    import builtins

    group_cls = builtins.ExceptionGroup  # 3.11+; a bare name fails py310 lint
    try:
        raise group_cls("tg", [ValueError(_SECRET), KeyError(_SECRET)])
    except group_cls as group:
        text = safe_exception_text((type(group), group, group.__traceback__))
    assert _SECRET_WORD not in text
    assert "group of: ValueError, KeyError" in text


def test_the_filter_never_drops_a_record() -> None:
    assert (
        QueryTextRedactionFilter().filter(logging.makeLogRecord({"msg": "x"})) is True
    )


def test_a_record_that_cannot_be_rewritten_is_withheld_not_printed() -> None:
    """Fails closed: never raise into the caller, never fall back to the original."""

    class Hostile(tuple):
        def __len__(self) -> int:
            raise RuntimeError(_SECRET)

    record = logging.makeLogRecord({"msg": f"q {_SECRET}?x", "args": Hostile(("a",))})
    assert QueryTextRedactionFilter().filter(record) is True
    assert _SECRET_WORD not in record.getMessage()


# ---------------------------------------------------------------------------
# 4. FastMCP's own tool-call logging, end to end
# ---------------------------------------------------------------------------


async def test_fastmcp_tool_failure_log_carries_no_argument_text() -> None:
    """`fastmcp.server.server` logs `Error calling tool` with the full traceback."""
    install_key_redaction()
    lg, stream = _capture("fastmcp.server.server")
    server = FastMCP("t")

    @server.tool
    def lookup(citation: str) -> str:
        raise ValueError(f"Could not resolve {citation!r} to a section")

    with pytest.raises(ToolError):
        await server.call_tool("lookup", {"citation": _SECRET})
    out = stream.getvalue()
    assert "Error calling tool 'lookup'" in out, "the failure must still be logged"
    assert "ValueError: [message redacted]" in out
    assert _SECRET_WORD not in out


async def test_fastmcp_invalid_arguments_warning_carries_no_input() -> None:
    """The leak check comes FIRST, and the format check accepts both shapes.

    fastmcp changed what this warning carries. 4.0.1 logs pydantic's error
    list, which names the field; 4.1.7 logs a summary (`error_count`,
    `error_types`) that our filter does not recognise, so the whole message
    becomes `[message redacted]` and the field name goes with it. Measured
    2026-11-10: this test asserted `"query" in out` and FAILED on 4.1.7, which
    is the version a fresh resolve installs and therefore what production runs.

    4.1.7 logs LESS than 4.0.1, so the regression was in the assertion, never
    in the protection. Ordering the leak assertion first makes that impossible
    to confuse: the test fails loudly if the secret ever appears, and is
    tolerant only about how much detail fastmcp chooses to keep.
    """
    install_key_redaction()
    lg, stream = _capture("fastmcp.server.server")
    server = FastMCP("t")

    @server.tool
    def search(query: Annotated[str, Field(max_length=5)]) -> str:
        return query

    with pytest.raises(Exception):
        await server.call_tool("search", {"query": _SECRET})
    out = stream.getvalue()
    # 1. What this file exists for.
    assert _SECRET_WORD not in out
    assert _SECRET not in out
    # 2. The failure is still reported at all.
    assert "Invalid arguments for tool 'search'" in out
    # 3. Either fastmcp kept the field name, or the filter redacted the lot.
    assert "query" in out or "[message redacted]" in out, out


async def test_the_real_fetch_alias_upstream_error_leaks_no_citation(
    monkeypatch: pytest.MonkeyPatch, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    """The case that motivated this: `fetch` with a citation, upstream 422.

    httpx2's `HTTPStatusError` text is the full `/resolve?cite=...` URL, and the
    422 body echoes the input; both reached the log through FastMCP's traceback,
    and httpx2's own INFO line printed the URL on every call.
    """
    from tests.test_aliases import _BASE, _mount

    install_key_redaction()
    server = _mount("US", monkeypatch, respx_mock)
    respx_mock.get(f"{_BASE}/api/v1/us/statutes/resolve").mock(
        return_value=httpx.Response(422, json={"detail": [{"input": _SECRET}]})
    )
    lg, stream = _capture("fastmcp.server.server")
    caplog.set_level(logging.INFO)  # production's root level

    with pytest.raises(ToolError):
        await server.call_tool("fetch", {"id": _SECRET})

    out = stream.getvalue()
    # 1. What this file exists for: the citation reaches no log, by any route.
    assert _SECRET_WORD not in out
    assert _SECRET not in out
    leaked = [r for r in caplog.records if _SECRET_WORD in r.getMessage()]
    assert not leaked, [f"{r.name}: {r.getMessage()}" for r in leaked]
    assert not [r for r in caplog.records if r.name.startswith("httpx")]
    # 2. The failure is still reported.
    assert "Error calling tool 'fetch'" in out
    # 3. If a traceback was logged, its message must be redacted. fastmcp 4.1.7
    #    passes `exc_info=status_code is None`, so an upstream HTTP error gets
    #    NO traceback and this line is simply absent; 4.0.1 logs one. Measured
    #    2026-11-10: asserting the line unconditionally failed on 4.1.7, the
    #    version a fresh resolve installs. Absent is strictly safer than
    #    redacted, so both pass, but a traceback that is present and NOT
    #    redacted still fails here.
    if "HTTPStatusError" in out:
        assert "HTTPStatusError: [message redacted]" in out, out
