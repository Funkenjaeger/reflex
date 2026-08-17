"""Import every module under reflex/ and assert it imports cleanly.

This exists because of a live outage on 2026-08-03: a refactor removed
`from pathlib import Path` from reflex/components/setup/profiling_panel.py,
where `Path` was still referenced by a return annotation evaluated at
class-definition time. Nothing imported that module in the test suite, so 556
green tests AND both CI jobs passed — and the app crash-looped on the lathe the
moment it started, because the real app DOES import it.

An import-time NameError/SyntaxError/missing-dependency is the cheapest class of
bug to catch and the most expensive to catch in production. This test is
deliberately dumb: it does not exercise behavior, only that every module the
app can reach is importable at all.

Modules that genuinely cannot import headless are skipped BY NAME with a
reason, so the skip list is a visible, reviewable inventory rather than a
silent hole.
"""
import importlib
import pkgutil

import pytest

import reflex

# Modules that cannot be imported in a headless test process, with the reason.
# Keep this list SHORT and justified — every entry is a module this guard does
# not protect. Prefer fixing the module over adding to this list.
SKIP = {
    # (none currently — add as "module.path": "reason" if a real one appears)
}


def _all_module_names():
    names = []
    for info in pkgutil.walk_packages(reflex.__path__, prefix="reflex."):
        if info.ispkg:
            continue
        names.append(info.name)
    return sorted(names)


ALL_MODULES = _all_module_names()


def test_module_discovery_found_a_plausible_number():
    """Guard the guard: if walk_packages silently returned nothing (bad path,
    renamed package), every parametrized case below would vacuously pass."""
    assert len(ALL_MODULES) > 40, (
        f"only discovered {len(ALL_MODULES)} modules under reflex/ — "
        f"discovery is probably broken, so the import sweep proves nothing"
    )


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_module_imports_cleanly(module_name):
    if module_name in SKIP:
        pytest.skip(SKIP[module_name])
    importlib.import_module(module_name)
