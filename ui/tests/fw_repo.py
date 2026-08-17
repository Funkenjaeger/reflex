"""Locate the adjacent reflex-fw checkout for cross-repo tests.

WHY THIS EXISTS. Two test modules compare this repo against reflex-fw: the
Modbus register-map contract and the wiring-config permutations. Both used to
resolve the firmware through a hardcoded default of
``/mnt/c/projects/embedded/reflex-fw`` -- a path that exists on exactly one
machine. Everywhere else, including CI and Evan's laptop, the path missed and
the modules skipped wholesale.

That mattered more than 23 skipped tests. The register-map contract is the guard
against the most dangerous change in this project -- firmware and UI disagreeing
about the register layout, where every read past the divergence silently returns
the wrong field. It caught exactly that on 2026-08-15 (a 112-byte truncation from
a trailing-array bug in the layout engine). It had never run in CI, because CI's
`test` job checks out reflex-ui alone. A green run on a map-breaking change
looked identical to a green run on a safe one.

TWO MODES, and the difference is the whole point:

- **Optional** (default). No firmware checkout found, the modules skip. This is
  correct for a fresh clone with no sibling repo -- the rest of the suite is
  still worth running, and failing would just teach people to ignore it.
- **Required** (``REFLEX_FW_REQUIRED=1``). Not finding the firmware is a
  COLLECTION ERROR, not a skip. CI's cross-repo job sets this, because a job
  whose entire purpose is the cross-repo check must not be able to pass by
  quietly checking nothing. Without it, a wrong checkout path in the workflow
  would reproduce the original failure exactly: green, and blind.
"""

import os
from pathlib import Path

# Canonical marker: the header the register-map contract parses. A directory
# without it is not a usable firmware checkout no matter what it is called.
_MARKER = Path("Core") / "Inc" / "Ramps.h"


def _candidates():
    """Search order: explicit override first, then layouts we actually use."""
    override = os.environ.get("REFLEX_FW_DIR")
    if override:
        # An explicit override is a statement of intent. Do not fall back past
        # it -- silently searching elsewhere after being told where to look is
        # how you end up testing against a repo you did not mean to.
        yield Path(override).expanduser()
        return

    here = Path(__file__).resolve().parent.parent      # the reflex-ui checkout
    yield here.parent / "reflex-fw"                    # CI, and any flat layout
    yield here.parent.parent / "embedded" / "reflex-fw"  # desktop: rpi/ + embedded/
    yield Path.home() / "projects" / "reflex-fw"       # laptop


def fw_dir():
    """Return the reflex-fw checkout, or None if there isn't a usable one."""
    for c in _candidates():
        if (c / _MARKER).is_file():
            return c
    return None


def require_or_skip_reason():
    """Return (fw_dir_or_None, skip_reason_or_None).

    Raises instead of returning None when REFLEX_FW_REQUIRED=1, so a misconfigured
    CI job fails loudly rather than passing with everything skipped.
    """
    found = fw_dir()
    if found is not None:
        return found, None

    searched = ", ".join(str(c) for c in _candidates())
    if os.environ.get("REFLEX_FW_REQUIRED") == "1":
        raise RuntimeError(
            "REFLEX_FW_REQUIRED=1 but no reflex-fw checkout was found. This job "
            "exists to run the cross-repo checks, so skipping them is a failure, "
            f"not an outcome. Searched: {searched}"
        )
    return None, (
        f"reflex-fw checkout not found (searched: {searched}); set REFLEX_FW_DIR "
        f"to run the cross-repo tests"
    )
