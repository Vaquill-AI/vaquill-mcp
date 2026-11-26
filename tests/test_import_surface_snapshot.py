"""Pin which `vaquill_mcp.<module>` names resolve, private ones included.

The package ships no public API contract beyond its entry points, but its tests,
its console scripts and a few external callers import module attributes by name,
including underscore-prefixed ones (`_SPEC_PATHS`, `_PerRequestBearerAuth`,
`_build_tool_costs`). Moving code into another package silently removes a name
unless each old module keeps resolving it. This records, for every module that
may be moved, the sorted attribute names it exposes today.

Interpreter metadata (`__file__`, `__spec__`, `__builtins__` and the like) is not
surface and is left out; `__all__` is recorded as written where a module has one.

A failure is not necessarily a bug. A removed name is a break for whoever imports
it. An added name only needs the golden regenerated. Read the diff first:

    UPDATE_IMPORT_SURFACE=1 .venv/bin/python -m pytest tests/test_import_surface_snapshot.py
"""

from __future__ import annotations

import importlib
import json

import pytest

from tests.surface_helpers import FIXTURES, check_golden

_GOLDEN = FIXTURES / "import_surface.json"
_ENV = "UPDATE_IMPORT_SURFACE"

_MODULES = (
    "config",
    "server",
    "remote",
    "remote_main",
    "oauth",
    "aliases",
    "prompts",
    "resources",
    "instructions",
    "schema_slim",
    "ordering",
    "brand_assets",
    "client_identity",
    "connector_channel",
    "log_redaction",
    "path_key_deprecation",
    "registration_guard",
)
# Set by the interpreter or the import system, never by this package.
_INTERPRETER = {
    "__builtins__",
    "__cached__",
    "__doc__",
    "__file__",
    "__loader__",
    "__name__",
    "__package__",
    "__path__",
    "__spec__",
}


def _surface(name: str) -> dict:
    module = importlib.import_module(f"vaquill_mcp.{name}")
    out: dict = {"attributes": sorted(set(dir(module)) - _INTERPRETER)}
    if hasattr(module, "__all__"):
        out["__all__"] = list(module.__all__)
    return out


def test_the_module_attributes_match_the_snapshot() -> None:
    check_golden({name: _surface(name) for name in _MODULES}, _GOLDEN, _ENV)


@pytest.mark.parametrize("name", _MODULES)
def test_every_pinned_module_still_imports(name: str) -> None:
    assert _surface(name)["attributes"], f"vaquill_mcp.{name} exposes nothing"


def test_the_snapshot_covers_every_module_and_the_private_names_are_in_it() -> None:
    golden = json.loads(_GOLDEN.read_text())
    assert set(golden) == set(_MODULES)
    assert "_SPEC_PATHS" in golden["config"]["attributes"]
    assert "_FUNC_OVERRIDES" in golden["server"]["attributes"]
    assert "_PerRequestBearerAuth" in golden["remote"]["attributes"]
