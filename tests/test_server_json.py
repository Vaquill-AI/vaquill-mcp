"""server.json is what the MCP Registry publishes, and directories copy it.

Glama, PulseMCP and mcp.directory import their listings from the Registry, so a
wrong field here is copied into every one of them. Two ways it has gone wrong:

* The version drifted from the package: nothing tied the two together, and the
  Registry refuses to overwrite a published version, so a forgotten bump means
  the fix never ships.
* The US remote kept `isRequired: true` on its API-key header after OAuth went
  live, telling every client a key was mandatory on a URL whose default sign-in
  needs none.
* The reverse, which is what 2026-11-26 cost: `isRequired: false` was set on the
  two India origins because they had MOVED to their own hostnames, and an origin
  root is where OAuth discovery CAN be served from. It is not where it is turned
  on. That needs a `VAQUILL_OAUTH_*` group naming the host plus a callback
  registered upstream, which is per deployment, so the file told every directory
  a key was optional on hosts that accept nothing else. Measured the same day:
  `/.well-known/oauth-protected-resource/mcp` answers 200 on mcp.vaquill.ai and
  404 on both in-mcp and reg-mcp.
  `isRequired` therefore tracks the DEPLOYMENT, and `_OAUTH_LIVE_HOSTS` below is
  the one list to edit when a host's group lands. The origin-root rule is still
  asserted, because it is the precondition: a remote under a path prefix can
  never offer OAuth however it is configured (see regulatory_main.py).
"""

from __future__ import annotations

import json
import pathlib
from urllib.parse import urlsplit

from vaquill_mcp import __version__

_SERVER_JSON = pathlib.Path(__file__).resolve().parent.parent / "server.json"
_US = "https://mcp.vaquill.ai/mcp"
_IN = "https://in-mcp.vaquill.ai/mcp"
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


#: Hosts whose OAuth group is configured and whose discovery document answers.
#: MEASURED, not inferred from the URL shape: an origin root is what makes OAuth
#: possible, and this list is what makes it true. Verify before editing:
#:   curl -o /dev/null -w '%{http_code}' \
#:     https://<host>/.well-known/oauth-protected-resource/mcp
#: 200 means it belongs here, 404 means it does not. Last measured 2026-11-26.
_OAUTH_LIVE_HOSTS = frozenset({"mcp.vaquill.ai"})


def test_a_key_is_optional_exactly_where_oauth_is_live() -> None:
    """`isRequired` is the deployment's state, not the URL's shape.

    Both directions matter and both have shipped wrong. `true` on an OAuth host
    tells clients to demand a key nobody needs; `false` on a host without the
    `VAQUILL_OAUTH_*` group tells them a key is optional when it is the only
    credential accepted, and the client then offers a Connect button that cannot
    work. Keyed on `_OAUTH_LIVE_HOSTS`, so enabling OAuth on a host is one edit
    here and the file and the measurement cannot drift apart.
    """
    for url, remote in _remotes().items():
        host = urlsplit(url).hostname or ""
        header = _auth_header(remote)
        live = host in _OAUTH_LIVE_HOSTS
        assert header["isRequired"] is (not live), (
            f"{host}: isRequired={header['isRequired']} but OAuth "
            f"{'IS' if live else 'is NOT'} live there"
        )
    # Anti-vacuous: at least one host of each kind, or the loop proves nothing.
    hosts = {urlsplit(u).hostname for u in _remotes()}
    assert hosts & _OAUTH_LIVE_HOSTS, "no OAuth host listed"
    assert hosts - _OAUTH_LIVE_HOSTS, "no key-only host listed"


def test_every_india_remote_sits_at_an_origin_root() -> None:
    """The PRECONDITION for OAuth, which is separate from having it.

    India used to be `https://mcp.vaquill.ai/in/mcp`, a PATH mount, where
    sign-in could never work at all: a FastMCP app knows only its own `path`, so
    it advertises the origin's `resource` URL and serves its document at the
    wrong location (`remote_main._MOUNTS` records the measurement). Moving to
    `in-mcp.vaquill.ai` removed that permanent blocker. It did not configure
    OAuth, which is why the assertion above is keyed on a measured list instead.
    """
    assert urlsplit(_IN).path == "/mcp", "OAuth needs the origin root, not a path"
    assert urlsplit(_IN).hostname == "in-mcp.vaquill.ai"


def test_the_regulatory_remote_is_listed_with_its_scope() -> None:
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


def test_every_remote_host_carries_the_mcp_suffix_the_edge_rule_matches() -> None:
    """The zone's bot-check skip rule keys on the NAME, so the name is infra.

    Cloudflare Super Bot Fight Mode blocks MCP clients on sight: a JSON POST
    with no browser fingerprint and no cookies is a bot by every heuristic it
    applies. A host the skip rule does not cover answers HTTP 403 with a
    Cloudflare interstitial AT THE EDGE while the container behind it is
    healthy, so the deployment logs are empty and the symptom reads as a broken
    build. That rule was forgotten on three origins out of three: reg-mcp
    2026-11-11, tribunals-mcp 2026-11-12, in-mcp 2026-11-26.

    On 2026-11-26 the hand-maintained host list was replaced with
    `(http.host eq "mcp.vaquill.ai") or (ends_with(http.host, "-mcp.vaquill.ai"))`
    so a new jurisdiction needs no Cloudflare change at all. Canaried the same
    day: `au-mcp.vaquill.ai`, absent from both DNS and Dokploy, answers 530 (no
    origin) rather than 403 (blocked).

    The cost of that convenience is that the SPELLING is now load-bearing. This
    test is the half of the rule that lives in the repo: a remote added as
    `au.vaquill.ai` or `mcp-au.vaquill.ai` satisfies every other check here,
    including the one-label certificate test above, and still 403s in
    production. If a host must be spelled otherwise, edit the zone rule first.
    """
    for remote in json.loads(_SERVER_JSON.read_text())["remotes"]:
        host = urlsplit(remote["url"]).hostname or ""
        assert host == "mcp.vaquill.ai" or host.endswith("-mcp.vaquill.ai"), (
            f"{host} is not matched by the Cloudflare bot-check skip rule "
            f'(`mcp.vaquill.ai` or `*-mcp.vaquill.ai`); it will answer 403 at '
            f"the edge with a healthy origin behind it"
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
