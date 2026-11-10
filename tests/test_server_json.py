"""server.json is what the MCP Registry publishes, and directories copy it.

Glama, PulseMCP and mcp.directory import their listings from the Registry, so a
wrong field here is copied into every one of them. Two ways it has gone wrong:

* The version drifted from the package: nothing tied the two together, and the
  Registry refuses to overwrite a published version, so a forgotten bump means
  the fix never ships.
* The US remote kept `isRequired: true` on its API-key header after OAuth went
  live, telling every client a key was mandatory on a URL whose default sign-in
  needs none. India has no OAuth (see remote_main.py), so its key stays required.
* The regulatory remote has OAuth, and only because it has its own ORIGIN: OAuth
  discovery is served from the origin root, so a remote under a path prefix can
  never offer it (see regulatory_main.py). The last test generalises that rule so
  a future remote cannot claim OAuth from a path.
"""

from __future__ import annotations

import json
import pathlib
from urllib.parse import urlsplit

from vaquill_mcp import __version__

_SERVER_JSON = pathlib.Path(__file__).resolve().parent.parent / "server.json"
_US = "https://mcp.vaquill.ai/mcp"
_IN = "https://mcp.vaquill.ai/in/mcp"
_REG = "https://reg-mcp.vaquill.ai/mcp"


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


def test_regulatory_remote_is_listed_and_oauth_capable() -> None:
    """Its own origin means OAuth works, so the key header is optional."""
    assert _auth_header(_remotes()[_REG])["isRequired"] is False
    assert "reg:read" in _auth_header(_remotes()[_REG])["description"]


def test_regulatory_remote_is_at_an_origin_root_not_under_the_main_host() -> None:
    """The reason it is a subdomain. A path on mcp.vaquill.ai would advertise the
    wrong OAuth `resource`; see remote_main.py's `mount_auth` comment.

    Read from server.json itself, not from the `_REG` constant alone: a test that
    only parses its own constant passes whatever the file says.
    """
    remotes = _remotes()
    assert _REG in remotes, f"server.json lists {sorted(remotes)}, not {_REG}"
    parts = urlsplit(_REG)
    assert parts.path == "/mcp"
    assert parts.hostname not in {"mcp.vaquill.ai", "www.vaquill.ai", "vaquill.ai"}
    # Every remote that is not on the main host is on an origin of its own, at
    # the root: the only shape OAuth discovery can be served from.
    for url in remotes:
        if urlsplit(url).hostname != "mcp.vaquill.ai":
            assert urlsplit(url).path == "/mcp", url


def test_every_remote_host_is_one_label_above_the_zone() -> None:
    """The Cloudflare edge certificate covers one level of subdomain, no more.

    Measured 2026-11-10: the edge certificate for this zone has the SANs
    `DNS:vaquill.ai, DNS:*.vaquill.ai` and nothing else. A wildcard matches
    exactly ONE label, so `mcp.vaquill.ai` and `reg-mcp.vaquill.ai` are covered
    and `regulatory.mcp.vaquill.ai` (two labels deep) is NOT: it would fail TLS at
    the edge, and a client would report it as the server being unreachable.
    Covering a deeper name needs Cloudflare Advanced Certificate Manager, a paid
    add-on nobody has bought.

    So do not "fix" a name by nesting it under `mcp.`. Use a single label with a
    hyphen, as `reg-mcp`, `canlii-mcp` and `courtlistener-mcp` do.
    """
    for remote in json.loads(_SERVER_JSON.read_text())["remotes"]:
        host = urlsplit(remote["url"]).hostname or ""
        assert host.endswith(".vaquill.ai"), remote["url"]
        assert len(host.split(".")) == 3, (
            f"{host} is not one label above vaquill.ai; the *.vaquill.ai edge "
            f"certificate does not cover it"
        )


def test_no_remote_is_listed_twice() -> None:
    """`_remotes` keys by URL, which would hide a duplicate silently."""
    urls = [r["url"] for r in json.loads(_SERVER_JSON.read_text())["remotes"]]
    assert len(urls) == len(set(urls)) == 3


def test_only_an_origin_root_remote_may_leave_its_key_optional() -> None:
    """OAuth is served from the origin's well-known path, so a remote at any
    other path cannot offer it and must say a key is required."""
    for remote in json.loads(_SERVER_JSON.read_text())["remotes"]:
        if urlsplit(remote["url"]).path != "/mcp":
            assert _auth_header(remote)["isRequired"] is True, remote["url"]
