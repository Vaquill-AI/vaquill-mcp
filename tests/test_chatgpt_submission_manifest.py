"""The ChatGPT submission manifest must describe the server that is actually served.

On 2026-10-14 the catalogue grew from 18 to 26 tools while `chatgpt-app-submission.json`
still listed 18, and two of its five test cases named outputs the tools do not return.
OpenAI rejected the submission for test cases that did not produce the documented result.
These checks keep the file and the server in step.
"""

from __future__ import annotations

import json
from pathlib import Path

from vaquill_mcp.server import published_tool_names

_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST = json.loads((_ROOT / "chatgpt-app-submission.json").read_text(encoding="utf-8"))
_US_SPEC = json.loads((_ROOT / "tests" / "fixtures" / "openapi_us.json").read_text(encoding="utf-8"))
#: ChatGPT's deep research and company knowledge need these two names, so the server adds them.
_ALIASES = {"search", "fetch"}


def test_the_manifest_lists_exactly_the_published_tools() -> None:
    served = published_tool_names(_US_SPEC) | _ALIASES
    listed = set(_MANIFEST["tools"])
    assert listed == served, (
        f"only in the server: {sorted(served - listed)}; only in the manifest: {sorted(listed - served)}"
    )


def test_every_listed_tool_is_declared_read_only_with_all_three_justifications() -> None:
    for name, entry in _MANIFEST["tools"].items():
        assert entry["annotations"] == {
            "readOnlyHint": True,
            "openWorldHint": False,
            "destructiveHint": False,
        }, name
        assert set(entry["justifications"]) == {
            "read_only_justification",
            "open_world_justification",
            "destructive_justification",
        }, name
        assert all(entry["justifications"].values()), name


def test_every_test_case_names_tools_that_exist() -> None:
    for case in _MANIFEST["test_cases"]:
        for tool in str(case["tools_triggered"]).split(","):
            assert tool.strip() in _MANIFEST["tools"], (case["user_prompt"], tool)


def test_negative_cases_expect_no_tool_call() -> None:
    for case in _MANIFEST["negative_test_cases"]:
        assert case["tools_triggered"] is None, case["user_prompt"]
