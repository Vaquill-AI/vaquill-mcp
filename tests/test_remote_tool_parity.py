"""The hosted server must publish every tool the stdio server does.

The stdio server derives its catalogue from the live OpenAPI document, so it
cannot drift. `remote.py` declares tools by hand, and did drift: it shipped 9 of
28, missing every Law Change Alerts tool and every section-intelligence tool,
while `server.json` and the README pointed every Claude.ai, Cursor and ChatGPT
user at it. Nothing failed, because nothing compared the two.

`descriptions.py` is the shared vocabulary both servers key on, so it is the
honest thing to compare a hand-written catalogue against.
"""

from __future__ import annotations

import ast
import pathlib

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "vaquill_mcp"


def _declared_tool_names() -> set[str]:
    """Every function in remote.py carrying an @mcp.tool decorator."""
    tree = ast.parse((_SRC / "remote.py").read_text())
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                out.add(node.name)
    return out


def _description_keys() -> set[str]:
    """Keys of TOOL_DESCRIPTIONS, matched by NAME.

    Matching the first dict literal in the module instead picks up whatever
    helper mapping happens to be declared above it.
    """
    tree = ast.parse((_SRC / "descriptions.py").read_text())
    for node in ast.walk(tree):
        # TOOL_DESCRIPTIONS carries a type annotation, so it is an AnnAssign
        # (one `target`), never an Assign (a list of `targets`).
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "TOOL_DESCRIPTIONS" for t in targets
        ):
            continue
        return {
            k.value
            for k in node.value.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
    raise AssertionError("TOOL_DESCRIPTIONS dict not found in descriptions.py")


def test_hosted_server_publishes_every_described_tool() -> None:
    missing = _description_keys() - _declared_tool_names()
    assert not missing, (
        f"remote.py publishes {len(_declared_tool_names())} tools but "
        f"descriptions.py describes {len(_description_keys())}. Missing: "
        f"{sorted(missing)}. A described tool that is not declared is a "
        "capability the hosted server silently does not have."
    )


def test_no_tool_is_declared_without_a_description() -> None:
    extra = _declared_tool_names() - _description_keys()
    assert not extra, (
        f"remote.py declares tools with no entry in descriptions.py: "
        f"{sorted(extra)}. Add the description rather than inlining one."
    )


def test_statute_search_reaches_the_state_corpus() -> None:
    """The 50-state corpus is the product's differentiator.

    `corpus_type` was `Literal["USC", "CFR"]`, which schema-rejected every state
    token before the request left the client, while the tool description
    promised all 50 states. A Literal that omits a token is not a validation
    error the caller can read: the tool simply cannot express the request.
    """
    src = (_SRC / "remote.py").read_text()
    tree = ast.parse(src)
    literal = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_CorpusType" for t in node.targets
        ):
            literal = {
                e.value for e in node.value.slice.elts if isinstance(e, ast.Constant)
            }
    assert literal, "_CorpusType literal not found in remote.py"
    for token in (
        "STATE",
        "REGULATION",
        "STATE_CONSTITUTION",
        "STATE_RULES",
        "STATE_AGENCY_GUIDANCE",
    ):
        assert token in literal, f"{token} missing: the state corpus is unreachable"
