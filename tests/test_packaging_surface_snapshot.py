"""Pin the console scripts and the `vaquill-mcp --help` text.

Users start this package through four installed commands and, for stdio, through
the flags `vaquill-mcp` accepts. A refactor that renames a module an entry point
names, or moves the argument parser, breaks installs without a failing import
test. This records:

* the console scripts from the INSTALLED metadata (`importlib.metadata`), name to
  `module:function`;
* the exact `vaquill-mcp --help` output, at a fixed terminal width.

NOT pinned here: the wheel's `Requires-Dist` set. That needs `uv build --wheel`
and is left to a build job.

The help text is produced by argparse, whose layout is Python-version dependent
in places (3.13 changed how an option with several names prints its metavar).
This parser has one name per option and a `choices` list, which prints the same
on 3.10 to 3.13, but the golden was generated on a single interpreter.

Regenerate deliberately, after reading the diff:

    UPDATE_PACKAGING_SURFACE=1 .venv/bin/python -m pytest tests/test_packaging_surface_snapshot.py
"""

from __future__ import annotations

import importlib.metadata
import json
import sys

import pytest

from tests.surface_helpers import FIXTURES, check_golden

_GOLDEN = FIXTURES / "packaging_surface.json"
_ENV = "UPDATE_PACKAGING_SURFACE"


def _console_scripts() -> dict[str, str]:
    dist = importlib.metadata.distribution("vaquill-mcp")
    return {e.name: e.value for e in dist.entry_points if e.group == "console_scripts"}


def _help(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> dict:
    from vaquill_mcp.__main__ import main

    monkeypatch.setenv("COLUMNS", "80")  # argparse wraps on the terminal width
    monkeypatch.setattr(sys, "argv", ["vaquill-mcp", "--help"])
    with pytest.raises(SystemExit) as stopped:
        main()
    captured = capsys.readouterr()
    return {"exitCode": stopped.value.code, "stdout": captured.out, "stderr": captured.err}


def test_the_packaging_surface_matches_the_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    check_golden(
        {"consoleScripts": _console_scripts(), "help": _help(monkeypatch, capsys)},
        _GOLDEN,
        _ENV,
    )


def test_the_four_console_scripts_are_the_documented_ones() -> None:
    golden = json.loads(_GOLDEN.read_text())
    assert sorted(golden["consoleScripts"]) == [
        "vaquill-mcp",
        "vaquill-mcp-regulatory",
        "vaquill-mcp-remote",
        "vaquill-mcp-tribunals",
    ]
    assert golden["help"]["exitCode"] == 0 and "--jurisdiction {IN,US}" in golden["help"]["stdout"]
