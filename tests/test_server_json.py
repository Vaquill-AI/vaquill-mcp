"""server.json is what the MCP Registry publishes, and directories copy it.

Glama, PulseMCP and mcp.directory import their listings from the Registry, so a
wrong field here is copied into every one of them. Two ways it has gone wrong:

* The version drifted from the package: nothing tied the two together, and the
  Registry refuses to overwrite a published version, so a forgotten bump means
  the fix never ships.
* The US remote kept `isRequired: true` on its API-key header after OAuth went
  live, telling every client a key was mandatory on a URL whose default sign-in
  needs none. India has no OAuth (see remote_main.py), so its key stays required.
"""

from __future__ import annotations

import json
import pathlib

from vaquill_mcp import __version__

_SERVER_JSON = pathlib.Path(__file__).resolve().parent.parent / "server.json"
_US = "https://mcp.vaquill.ai/mcp"
_IN = "https://mcp.vaquill.ai/in/mcp"


def _remotes() -> dict[str, dict]:
    data = json.loads(_SERVER_JSON.read_text())
    return {r["url"]: r for r in data["remotes"]}


def _auth_header(remote: dict) -> dict:
    return next(h for h in remote.get("headers", []) if h["name"] == "Authorization")


def test_version_matches_the_package() -> None:
    assert json.loads(_SERVER_JSON.read_text())["version"] == __version__


def test_us_remote_does_not_require_a_key() -> None:
    """OAuth is the default on the root mount, so the key header is optional."""
    assert _auth_header(_remotes()[_US])["isRequired"] is False


def test_india_remote_still_requires_a_key() -> None:
    """The India mount has no OAuth; without a key it cannot authenticate."""
    assert _auth_header(_remotes()[_IN])["isRequired"] is True
