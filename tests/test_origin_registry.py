"""The origin registry, and the two guarantees that keep it from lying.

`vaquill_mcp.origin` replaced two near-identical `*_main` modules so that
Australia, Canada and further jurisdictions are a REGISTRY ROW rather than a
fifth copy of a `build_app`. Two things have to stay true for that to be safe:

* every row is deployable -- a jurisdiction row names a jurisdiction the package
  publishes a spec for, and a corpus row's factory and cost fetcher actually
  import. A row that cannot be built is a deployment that fails at boot, under a
  host name customers already have.
* `remote_main` is NOT in the registry. It is the live US connector; its route
  table and tool count are pinned elsewhere, and folding it in here would put
  the one surface customers already depend on behind this refactor.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # stdlib only from 3.11; `tomli` is the same parser and ships with
    # pytest on 3.10. A bare `import tomllib` here aborted collection on the
    # py3.10 CI leg from a688185 to the commit that added this split.
    import tomli as tomllib

import pytest

from vaquill_mcp.config import _SPEC_PATHS
from vaquill_mcp.origin import ORIGINS, Origin, get_origin

_ROOT = Path(__file__).resolve().parents[1]


def test_every_jurisdiction_row_names_a_published_spec() -> None:
    """A jurisdiction origin derives its catalogue from that jurisdiction's
    OpenAPI document, so a row naming one we do not publish would boot, serve an
    empty catalogue and look healthy."""
    for origin in ORIGINS.values():
        if origin.jurisdiction is not None:
            assert origin.jurisdiction in _SPEC_PATHS, (
                f"origin {origin.slug!r} names jurisdiction {origin.jurisdiction!r}, "
                f"which has no spec path; add it to config._SPEC_PATHS"
            )


def test_every_corpus_row_resolves_its_factory_and_cost_fetcher() -> None:
    for origin in ORIGINS.values():
        if origin.jurisdiction is None:
            module = importlib.import_module(origin.module)
            assert callable(getattr(module, origin.factory))
            assert callable(getattr(module, origin.costs))


def test_the_slug_is_the_key_so_health_cannot_disagree_with_the_deployment() -> None:
    """`/health` reports `corpus: <slug>`, and VAQUILL_ORIGIN selects by key."""
    for key, origin in ORIGINS.items():
        assert key == origin.slug


@pytest.mark.parametrize(
    "kwargs",
    [
        {"jurisdiction": "IN", "module": "m", "factory": "f", "costs": "c"},
        {},
        {"module": "m", "factory": "f"},
        {"jurisdiction": None, "module": "m"},
    ],
)
def test_a_row_that_is_neither_or_both_is_refused(kwargs: dict) -> None:
    """Canaried: each of these was confirmed to raise before the guard was
    trusted. A row with both shapes set would silently take one branch."""
    with pytest.raises(ValueError, match="EITHER jurisdiction OR"):
        Origin(slug="canary", description="d", **kwargs)


def test_an_unknown_or_empty_origin_is_refused_rather_than_defaulted() -> None:
    """A process that cannot tell which corpus it serves would serve the WRONG
    corpus under the right host name."""
    with pytest.raises(ValueError, match="must be one of"):
        get_origin("atlantis")
    with pytest.raises(ValueError, match="must be set to one of"):
        get_origin("")


def test_remote_main_is_not_in_the_registry() -> None:
    """The live US connector keeps its own module. See this file's docstring."""
    assert "remote" not in ORIGINS
    assert "us" not in ORIGINS
    for origin in ORIGINS.values():
        assert origin.module != "vaquill_mcp.remote_main"


def test_the_named_entry_points_survive_beside_the_generic_one() -> None:
    """The deployments and `test_regulatory_remote` name the per-corpus scripts;
    the generic one is what a new jurisdiction uses."""
    scripts = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["vaquill-mcp-remote"] == "vaquill_mcp.remote_main:main"
    assert scripts["vaquill-mcp-regulatory"] == "vaquill_mcp.regulatory_main:main"
    assert scripts["vaquill-mcp-tribunals"] == "vaquill_mcp.tribunals_main:main"
    assert scripts["vaquill-mcp-origin"] == "vaquill_mcp.origin:main"


def test_the_delegates_build_the_app_for_their_own_origin() -> None:
    for slug in ("regulatory", "tribunals"):
        module = importlib.import_module(f"vaquill_mcp.{slug}_main")
        assert module.ORIGIN is ORIGINS[slug]


def test_a_delegate_refuses_a_contradicting_environment(monkeypatch) -> None:
    """`VAQUILL_ORIGIN=tribunals` with the regulatory script is a deployment
    that would serve one corpus while every log line named the other."""
    from vaquill_mcp import regulatory_main

    monkeypatch.setenv("VAQUILL_ORIGIN", "tribunals")
    monkeypatch.setattr(regulatory_main, "build_app", lambda: object())
    with pytest.raises(ValueError, match="but VAQUILL_ORIGIN is"):
        regulatory_main.main()
