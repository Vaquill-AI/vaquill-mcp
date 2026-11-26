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
#:
#: A 200 there is necessary and NOT sufficient: the upstream OAuth client must
#: also list the host's `/auth/callback`, or sign-in dies at the END of the flow,
#: after the user has authenticated. On 2026-11-26 `in-mcp` answered 200 with the
#: callback unregistered and the upstream returned 400 on the redirect. Measure
#: it by registering a throwaway client, approving consent, and following the
#: redirect: a 302 to the product's sign-in page is accepted, a 400 is not.
#: (`in-mcp` and `reg-mcp` callbacks were added to the client on 2026-11-26.)
#:
#: Adding a host here is the LAST step of the checklist in `origin.py`'s
#: docstring, after its durable store is proven by a write landing in its own
#: Redis db. The same change sets `supportsOAuth: true` on the host's row in the
#: console (vaquill-qdrant-python, `mcp-connect/data-api-servers.ts`).
_OAUTH_LIVE_HOSTS = frozenset({"mcp.vaquill.ai", "in-mcp.vaquill.ai", "reg-mcp.vaquill.ai"})


def _required_flag_violations(remotes: dict, live: frozenset) -> list[str]:
    """Hosts whose `isRequired` disagrees with whether OAuth is live there.

    A function, not a loop in the test, so the rule can be canaried against
    synthetic remotes. It has to be: once every listed host is OAuth-live there
    is no key-only host left in the real file to exercise the `false` direction,
    and an assertion that one exists would fail on the outcome we want.
    """
    bad = []
    for url, remote in remotes.items():
        host = urlsplit(url).hostname or ""
        required = _auth_header(remote)["isRequired"]
        live_here = host in live
        if required is live_here:  # required where live, or optional where not
            bad.append(
                f"{host}: isRequired={required} but OAuth "
                f"{'IS' if live_here else 'is NOT'} live there"
            )
    return bad


def test_a_key_is_optional_exactly_where_oauth_is_live() -> None:
    """`isRequired` is the deployment's state, not the URL's shape.

    Both directions matter and both have shipped wrong. `true` on an OAuth host
    tells clients to demand a key nobody needs; `false` on a host without the
    `VAQUILL_OAUTH_*` group tells them a key is optional when it is the only
    credential accepted, and the client then offers a Connect button that cannot
    work. Keyed on `_OAUTH_LIVE_HOSTS`, so enabling OAuth on a host is one edit
    here and the file and the measurement cannot drift apart.
    """
    remotes = _remotes()
    assert _required_flag_violations(remotes, _OAUTH_LIVE_HOSTS) == []
    # Anti-vacuous: every measured-live host is really listed, and there is
    # something to check. A typo in the set would otherwise shrink the loop.
    hosts = {urlsplit(u).hostname for u in remotes}
    assert _OAUTH_LIVE_HOSTS <= hosts, _OAUTH_LIVE_HOSTS - hosts
    assert len(hosts) >= 3


def test_the_required_flag_rule_fails_in_both_directions() -> None:
    """Canary for the rule above, on synthetic remotes, in both directions."""
    optional = {"headers": [{"name": "Authorization", "isRequired": False}]}
    required = {"headers": [{"name": "Authorization", "isRequired": True}]}
    live = frozenset({"live.example"})
    # Optional where OAuth is NOT live: a Connect button that cannot work.
    assert _required_flag_violations({"https://dead.example/mcp": optional}, live)
    # Required where OAuth IS live: demands a key nobody needs.
    assert _required_flag_violations({"https://live.example/mcp": required}, live)
    # And the two correct pairings are clean.
    assert _required_flag_violations({"https://live.example/mcp": optional}, live) == []
    assert _required_flag_violations({"https://dead.example/mcp": required}, live) == []


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


# THE PYPI PACKAGE ENTRY. `packages` is what tells a Registry-driven client
# that this server also installs locally (`uvx vaquill-mcp`), not only the
# three hosted URLs. The Registry VERIFIES it at publish time: it reads that
# exact version's description on PyPI and looks for `mcp-name: <name>`, so the
# README line, the package version and PyPI having that version are one
# contract. publish-mcp.yml waits for PyPI for the same reason.
_ROOT = _SERVER_JSON.parent


def _package_violations(server: dict, version: str, readme: str, project: str) -> list[str]:
    """Every way the PyPI entry can disagree with what will be on PyPI."""
    from vaquill_mcp.config import _SPEC_PATHS

    problems: list[str] = []
    pypi = [p for p in server.get("packages", []) if p.get("registryType") == "pypi"]
    if len(pypi) != 1:
        return [f"expected exactly one pypi package entry, found {len(pypi)}"]
    pkg = pypi[0]
    if pkg.get("identifier") != project:
        problems.append(f"identifier {pkg.get('identifier')!r} is not the project {project!r}")
    if pkg.get("version") != version or server.get("version") != version:
        problems.append(f"package {pkg.get('version')!r} / server {server.get('version')!r} != {version!r}")
    if pkg.get("transport", {}).get("type") != "stdio":
        problems.append("the PyPI package is the stdio server")
    env = {e["name"]: e for e in pkg.get("environmentVariables", [])}
    key = env.get("VAQUILL_API_KEY", {})
    if not (key.get("isRequired") and key.get("isSecret")):
        problems.append("VAQUILL_API_KEY must be required and secret: config.py refuses to start without it")
    # Derived from the code, so a third jurisdiction cannot be left out here.
    if sorted(env.get("VAQUILL_JURISDICTION", {}).get("choices", [])) != sorted(_SPEC_PATHS):
        problems.append("VAQUILL_JURISDICTION choices differ from config._SPEC_PATHS")
    if f"mcp-name: {server.get('name')}" not in readme:
        problems.append("README lacks the mcp-name line the Registry checks on PyPI")
    return problems


def _project_name() -> str:
    import re

    match = re.search(r'(?m)^name\s*=\s*"([^"]+)"', (_ROOT / "pyproject.toml").read_text())
    assert match, "pyproject.toml has no project name"
    return match.group(1)


def test_the_pypi_package_entry_matches_what_will_be_published() -> None:
    server = json.loads(_SERVER_JSON.read_text())
    readme = (_ROOT / "README.md").read_text()
    assert _package_violations(server, __version__, readme, _project_name()) == []


def test_the_package_rule_catches_each_kind_of_drift() -> None:
    """Canary: a check that cannot fail would pass the release that breaks."""
    server = json.loads(_SERVER_JSON.read_text())
    readme = (_ROOT / "README.md").read_text()
    project = _project_name()

    stale = json.loads(json.dumps(server))
    stale["packages"][0]["version"] = "0.0.1"
    no_choice = json.loads(json.dumps(server))
    no_choice["packages"][0]["environmentVariables"][1]["choices"] = ["US"]
    for broken, why in (
        (stale, "a package version left behind by a bump"),
        (no_choice, "a jurisdiction missing from the choices"),
        ({**server, "packages": []}, "the entry removed"),
    ):
        assert _package_violations(broken, __version__, readme, project), why
    assert _package_violations(server, __version__, readme.replace("mcp-name:", "mcp name:"), project)
    assert _package_violations(server, __version__, readme, "vaquill_mcp_typo")
