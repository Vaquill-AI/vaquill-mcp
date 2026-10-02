"""A customer's API key must never reach the log.

`/s/{api_key}` is the simple-paste mount that clients without header support
use, so the key is IN THE URL, and uvicorn's access log prints the whole path.
Read on 2026-10-02 from the production container log: two distinct live
`vq_key_` values, in plain text, on every call. Dokploy's log viewer and
anything that ships the container's stdout can read them, and a leaked key is
spendable credit.

The key has to stay in the URL (it is how those clients authenticate), so the
log line is where it is removed.
"""

from __future__ import annotations

import io
import logging

import pytest

from vaquill_mcp.log_redaction import KeyRedactionFilter, install_key_redaction

_KEY = "vq_key_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


def _logger_with_filter() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(f"test.redaction.{id(stream)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addFilter(KeyRedactionFilter())
    return logger, stream


def test_a_key_in_a_uvicorn_style_access_line_is_removed() -> None:
    """uvicorn formats the path through `%s` args, so the key is not in `msg`."""
    logger, stream = _logger_with_filter()
    logger.info(
        '%s - "%s %s HTTP/%s" %d',
        "10.0.1.3:41456",
        "POST",
        f"/s/{_KEY}",
        "1.1",
        200,
    )
    out = stream.getvalue()
    assert _KEY not in out
    assert "AbCdEf" not in out
    assert "/s/vq_key_[redacted]" in out
    assert "200" in out and "POST" in out, "the rest of the line must survive"


def test_a_key_in_a_plain_message_is_removed() -> None:
    logger, stream = _logger_with_filter()
    logger.info(f"calling with {_KEY} now")
    assert _KEY not in stream.getvalue()


def test_the_india_mount_is_covered_too() -> None:
    logger, stream = _logger_with_filter()
    logger.info('%s - "%s %s"', "x", "POST", f"/in/s/{_KEY}")
    assert _KEY not in stream.getvalue()


def test_a_line_without_a_key_is_unchanged() -> None:
    logger, stream = _logger_with_filter()
    logger.info('%s - "%s %s HTTP/%s" %d', "10.0.1.3", "GET", "/health", "1.1", 200)
    assert stream.getvalue().strip() == '10.0.1.3 - "GET /health HTTP/1.1" 200'


def test_the_bare_prefix_is_not_mistaken_for_a_key() -> None:
    """Prose that names the format is not a credential and stays readable."""
    logger, stream = _logger_with_filter()
    logger.info("Keys must start with vq_key_")
    # Exact, not `in`: the replacement itself starts with the prefix, so a
    # substring check passes whether or not the bare prefix was rewritten.
    assert stream.getvalue().strip() == "Keys must start with vq_key_"


def test_a_key_with_dashes_and_underscores_is_fully_removed() -> None:
    logger, stream = _logger_with_filter()
    tail = "ab-cd_ef-GH_12-34_56"
    logger.info(f"/s/vq_key_{tail}/extra")
    out = stream.getvalue()
    assert tail not in out


def test_the_filter_never_drops_a_record() -> None:
    assert KeyRedactionFilter().filter(logging.makeLogRecord({"msg": "x"})) is True


def test_install_puts_the_filter_on_the_access_logger() -> None:
    access = logging.getLogger("uvicorn.access")
    before = [f for f in access.filters if isinstance(f, KeyRedactionFilter)]
    install_key_redaction()
    install_key_redaction()  # idempotent
    after = [f for f in access.filters if isinstance(f, KeyRedactionFilter)]
    assert len(after) == max(len(before), 1)


def test_main_installs_it_before_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    """A filter nobody installs protects nothing, and that fails silently."""
    import uvicorn

    from vaquill_mcp import remote_main

    calls: list[str] = []
    monkeypatch.setattr(remote_main, "install_key_redaction", lambda: calls.append("redact"))
    monkeypatch.setattr(remote_main, "build_app", lambda: object())
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append("serve"))
    remote_main.main()
    assert calls == ["redact", "serve"], "redaction must be installed BEFORE serving"


def test_the_real_uvicorn_access_formatter_still_works_after_redaction() -> None:
    """uvicorn unpacks `record.args` as a 5-tuple, so the filter must keep its shape.

    Collapsing the redacted line into `msg` and clearing `args` would pass every
    test above and then crash uvicorn's formatter on the first request.
    """
    from uvicorn.logging import AccessFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s'))
    logger = logging.getLogger("test.redaction.uvicorn_formatter")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addFilter(KeyRedactionFilter())

    logger.info('%s - "%s %s HTTP/%s" %d', "10.0.1.3:41456", "POST", f"/s/{_KEY}", "1.1", 200)
    out = stream.getvalue()
    assert _KEY not in out
    assert 'POST /s/vq_key_[redacted] HTTP/1.1' in out
    assert "200" in out


def test_a_url_object_argument_is_redacted_too() -> None:
    """httpx logs `HTTP Request: %s %s` with a `URL` OBJECT, not a string.

    Found by running a real server and client in one process: the access line
    was clean and httpx's own line still printed the key. Anything that logs a
    URL object would have leaked the same way.
    """
    import httpx

    logger, stream = _logger_with_filter()
    logger.info(
        'HTTP Request: %s %s "%s %d %s"',
        "POST",
        httpx.URL(f"http://127.0.0.1:8000/s/{_KEY}"),
        "HTTP/1.1",
        200,
        "OK",
    )
    out = stream.getvalue()
    assert _KEY not in out
    assert "vq_key_[redacted]" in out
    assert "200" in out, "numeric arguments must still format with %d"


def test_non_string_arguments_without_a_key_are_left_as_they_were() -> None:
    logger, stream = _logger_with_filter()
    logger.info("n=%d f=%.1f ok=%s none=%s", 7, 2.5, True, None)
    assert stream.getvalue().strip() == "n=7 f=2.5 ok=True none=None"


def test_an_object_whose_str_raises_does_not_break_logging() -> None:
    class Hostile:
        def __str__(self) -> str:
            raise RuntimeError("no")

    logger, stream = _logger_with_filter()
    logger.info("value %s", 1)
    logger.info("never formatted: %r", Hostile.__name__)
    assert KeyRedactionFilter().filter(logging.makeLogRecord({"msg": "m", "args": (Hostile(),)}))
