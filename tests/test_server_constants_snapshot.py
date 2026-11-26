"""Pin the hand-kept tables that decide which tools exist and what they are called.

`test_tool_contract_snapshot.py` pins the tools the two OpenAPI fixtures produce.
That cannot notice an entry nobody's fixture route exercises: a lost
`_FUNC_OVERRIDES` rename, a dropped `_ROUTE_MAPS` exclusion, a path missing from
`_READ_ONLY_POSTS`. A test of the output passes, and the next backend document
that carries the route publishes the wrong tool or the wrong annotation. This
dumps each table whole, so a lost entry is a diff.

Recorded, from `vaquill_mcp.server` and `vaquill_mcp.config`:

* `_FUNC_OVERRIDES`, `_READ_ONLY_POSTS`, `_ACKNOWLEDGED_WRITE_POSTS`,
  `_HIDDEN_PARAMS`, `_SPEC_PATHS`;
* `_ROUTE_MAPS` in order (the first matching map wins, so order is behaviour);
* the path segments `_derive_mcp_names` ignores when it picks a resource name.
  That tuple is an inline literal, not a constant, so it is MEASURED: two
  colliding operations are derived and the resulting name shows whether the
  candidate segment was skipped.

Regenerate deliberately, after reading the diff:

    UPDATE_SERVER_CONSTANTS=1 .venv/bin/python -m pytest tests/test_server_constants_snapshot.py
"""

from __future__ import annotations

import json

from tests.surface_helpers import FIXTURES, check_golden
from vaquill_mcp import server
from vaquill_mcp.config import _SPEC_PATHS

_GOLDEN = FIXTURES / "server_constants.json"
_ENV = "UPDATE_SERVER_CONSTANTS"
# Every segment that could plausibly be skipped, plus a few that must not be.
_CANDIDATE_SEGMENTS = ("api", "v1", "us", "in", "external", "statutes", "cases", "citations")


def _ignored_segments() -> list[str]:
    """Which candidate path segments `_derive_mcp_names` drops from a resource name.

    Two operations share the handler name `probe`, which forces the resource
    prefix. The first sits under `/api/v1/<segment>/leaf`: if the segment is
    skipped the resource is `leaf`, otherwise it is the segment itself.
    """
    ignored = []
    for segment in _CANDIDATE_SEGMENTS:
        spec = {
            "paths": {
                f"/api/v1/{segment}/leaf": {
                    "get": {"operationId": "probe_api_v1_a_get", "responses": {}}
                },
                "/api/v1/zzz/other": {
                    "get": {"operationId": "probe_api_v1_b_get", "responses": {}}
                },
            }
        }
        names = server._derive_mcp_names(spec)
        if names["probe_api_v1_a_get"] == "leaf_probe":
            ignored.append(segment)
    return ignored


def _route_maps() -> list[dict]:
    return [
        {
            "methods": sorted(rm.methods) if not isinstance(rm.methods, str) else rm.methods,
            "pattern": rm.pattern if isinstance(rm.pattern, str) else rm.pattern.pattern,
            "tags": sorted(rm.tags),
            "mcpType": str(getattr(rm.mcp_type, "value", rm.mcp_type)),
            "mcpTags": sorted(rm.mcp_tags),
        }
        for rm in server._ROUTE_MAPS
    ]


def _current() -> dict:
    return {
        "_ACKNOWLEDGED_WRITE_POSTS": sorted(server._ACKNOWLEDGED_WRITE_POSTS),
        "_FUNC_OVERRIDES": dict(sorted(server._FUNC_OVERRIDES.items())),
        "_HIDDEN_PARAMS": {k: sorted(v) for k, v in sorted(server._HIDDEN_PARAMS.items())},
        "_READ_ONLY_POSTS": sorted(server._READ_ONLY_POSTS),
        "_ROUTE_MAPS": _route_maps(),
        "_SPEC_PATHS": dict(sorted(_SPEC_PATHS.items())),
        "ignoredSegments": _ignored_segments(),
    }


def test_the_server_constants_match_the_snapshot() -> None:
    check_golden(_current(), _GOLDEN, _ENV)


def test_the_snapshot_is_not_empty() -> None:
    golden = json.loads(_GOLDEN.read_text())
    assert len(golden["_FUNC_OVERRIDES"]) >= 10 and golden["_ROUTE_MAPS"]
    assert golden["_READ_ONLY_POSTS"] and golden["_HIDDEN_PARAMS"]
    assert set(golden["_SPEC_PATHS"]) == {"IN", "US"}
    # The measurement must be able to tell skipped from kept.
    assert "api" in golden["ignoredSegments"]
    assert "statutes" not in golden["ignoredSegments"]
