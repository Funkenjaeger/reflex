"""Physical-wiring permutation configs + a section-aware TOML config patcher.

See .hermes/plans/2026-07-09_emulator-backed-system-tests.md, Task 4, for the
full design context. Summary: the emulator now models four independent
PHYSICAL wiring axes via config knobs (all +-1, default +1), which the
firmware physics consumes directly (Path B):

- ``spindle.scale_dir``     -- spindle encoder wiring   (scales[0])
- ``z_axis.scale_dir``      -- Z encoder wiring          (scales[1])
- ``cross_slide.scale_dir`` -- X encoder wiring          (scales[2])
- ``servo.dir``             -- servo motor wiring

For a "valid configuration" (the one this suite's matrix exercises), the
operator-facing UI reversing toggle must be set to CANCEL whatever physical
wiring is in play:

- ``z_axis.scale_dir``      <-> Z InputDispatcher.reverse       (scales[1])
- ``spindle.scale_dir``     <-> spindle InputDispatcher.reverse (scales[0])
- ``cross_slide.scale_dir`` <-> X InputDispatcher.reverse       (scales[2])
- ``servo.dir``             <-> ServoDispatcher.reverse

Canceling rule: ``reverse = (wiring_sign == -1)`` -- i.e. wiring +1 means the
toggle stays False (un-reversed); wiring -1 means the toggle must be True.

This module has two exports the rest of the suite consumes:

- :func:`make_config` -- writes a patched copy of the base ``lathe.toml``
  with exactly the requested ``section.key`` overrides changed, byte-for-byte
  identical everywhere else.
- :data:`WIRING_PERMUTATIONS` -- the 16 physical-wiring permutations (2**4),
  each pre-computed with its ``overrides`` (for :func:`make_config`) and
  matching canceling ``toggles`` (for the harness's UI toggle setters).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

# ---------------------------------------------------------------------------
# Config patching
# ---------------------------------------------------------------------------

# Matches a TOML section header line, e.g. "[z_axis]" -- captures the name.
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")


def _key_line_re(key: str) -> re.Pattern[str]:
    """Regex matching a ``key = value`` assignment line for exactly ``key``.

    Anchored so e.g. key "dir" does not match "scale_dir". Captures the
    leading whitespace, the value token, and any trailing content (comment)
    separately so replacement can preserve formatting.
    """
    return re.compile(
        r"^(?P<indent>\s*)"
        + re.escape(key)
        + r"(?P<eq>\s*=\s*)"
        + r"(?P<value>[^#\s]+)"
        + r"(?P<rest>.*)$"
    )


def make_config(base_toml_path: Path, overrides: Mapping[str, int], out_path: Path) -> Path:
    """Write a copy of ``base_toml_path`` with ``overrides`` applied, to ``out_path``.

    ``overrides`` is keyed by dotted ``"section.key"`` (e.g.
    ``{"z_axis.scale_dir": -1, "servo.dir": -1}``); values are ints. Patching
    is section-aware: the same key name (e.g. ``scale_dir``) appears under
    multiple sections ([spindle], [z_axis], [cross_slide]), so only the line
    within the INTENDED section is rewritten. Every other line -- including
    comments, blank lines, and unrelated keys -- is preserved exactly.

    Raises ``KeyError`` if a requested ``section.key`` is never found in the
    base file (so a typo in an override can't silently no-op).

    Returns ``out_path``.
    """
    base_toml_path = Path(base_toml_path)
    out_path = Path(out_path)

    # section -> {key -> value} remaining to apply, so we can detect misses.
    pending: dict[str, dict[str, int]] = {}
    for dotted_key, value in overrides.items():
        section, sep, key = dotted_key.partition(".")
        if not sep or not key:
            raise ValueError(
                f"override key {dotted_key!r} must be dotted 'section.key'"
            )
        pending.setdefault(section, {})[key] = value

    lines = base_toml_path.read_text().splitlines(keepends=True)

    current_section: str | None = None
    out_lines: list[str] = []
    for line in lines:
        section_match = _SECTION_RE.match(line)
        if section_match:
            current_section = section_match.group(1)
            out_lines.append(line)
            continue

        section_pending = pending.get(current_section) if current_section else None
        if section_pending:
            replaced = False
            for key, value in list(section_pending.items()):
                key_match = _key_line_re(key).match(line)
                if key_match:
                    # 'rest' is matched with a non-newline-matching '.', so it
                    # excludes the line's trailing newline (if any) -- restore
                    # it explicitly so the file's line endings are preserved.
                    line_ending = "\n" if line.endswith("\n") else ""
                    new_line = (
                        f"{key_match.group('indent')}{key}{key_match.group('eq')}"
                        f"{value}{key_match.group('rest')}{line_ending}"
                    )
                    out_lines.append(new_line)
                    del section_pending[key]
                    if not section_pending:
                        del pending[current_section]
                    replaced = True
                    break
            if replaced:
                continue

        out_lines.append(line)

    if pending:
        missing = [f"{section}.{key}" for section, keys in pending.items() for key in keys]
        raise KeyError(
            f"override key(s) not found in {base_toml_path}: {', '.join(sorted(missing))}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(out_lines))
    return out_path


# ---------------------------------------------------------------------------
# Wiring permutations
# ---------------------------------------------------------------------------

# The four physical wiring axes, each independently +-1 (default +1). Order
# is fixed and used both for id generation and the nested-loop enumeration
# below, so WIRING_PERMUTATIONS is deterministic and stable across runs.
_AXES = (
    # (dotted override key, short id label, toggle name)
    ("spindle.scale_dir", "sp", "spindle_reverse"),
    ("z_axis.scale_dir", "z", "z_reverse"),
    ("cross_slide.scale_dir", "x", "x_reverse"),
    ("servo.dir", "srv", "servo_reverse"),
)


def canceling_toggles(overrides: Mapping[str, int]) -> dict[str, bool]:
    """Return the UI toggle values that cancel the given wiring ``overrides``.

    Canceling rule: ``reverse = (wiring_sign == -1)``. Only recognizes the
    four known axes in ``_AXES``; raises ``KeyError`` for anything else so a
    typo doesn't silently produce a missing/false toggle.
    """
    key_to_toggle = {dotted_key: toggle for dotted_key, _label, toggle in _AXES}
    toggles: dict[str, bool] = {}
    for dotted_key, sign in overrides.items():
        toggle = key_to_toggle[dotted_key]
        toggles[toggle] = sign == -1
    return toggles


# The real machine (elspi) commissions a FORWARD spindle that cuts correctly only
# with the SERVO reversed (servo_reverse=true) — verified on the real lathe and in
# the Task 9 retract test. The matrix can run against that real commissioning
# (EMU_RPM=+30) instead of the EMU_RPM=-30 spindle band-aid; when it does, the
# servo reverse toggle does DOUBLE DUTY: it cancels the physical servo wiring AND
# carries this commissioning polarity. The three INPUT axes have no commissioning
# offset (all inputs un-reversed at commissioning), so only the servo toggle is
# XOR'd with the baseline. This keeps the pure `canceling_toggles` model (and its
# tests) intact for the band-aid path.
SERVO_COMMISSION_REVERSE = True


def real_commissioning_toggles(overrides: Mapping[str, int]) -> dict[str, bool]:
    """UI toggles that cancel ``overrides`` under the REAL machine commissioning
    (forward +30 spindle + ``servo_reverse`` baseline), for the EMU_RPM=+30 path.

    Identical to :func:`canceling_toggles` for the spindle/Z/X input toggles; the
    servo toggle is the pure cancel XOR the commissioning reverse. So all-normal
    wiring → ``servo_reverse=True`` (the commissioned baseline), and a flipped
    ``servo.dir`` → ``servo_reverse=False`` (the flip cancels the commissioning).
    """
    toggles = canceling_toggles(overrides)
    if SERVO_COMMISSION_REVERSE:
        toggles["servo_reverse"] = not toggles["servo_reverse"]
    return toggles


@dataclass(frozen=True)
class WiringPermutation:
    """One of the 16 physical-wiring permutations for the Task 8/9 matrix."""

    id: str
    overrides: dict[str, int] = field(default_factory=dict)
    toggles: dict[str, bool] = field(default_factory=dict)


def _build_permutations() -> list[WiringPermutation]:
    permutations: list[WiringPermutation] = []
    # Nested loop over (+1, -1)^4, most-significant axis (spindle) first --
    # deterministic, no randomness, stable ordering across runs.
    for sp_sign in (1, -1):
        for z_sign in (1, -1):
            for x_sign in (1, -1):
                for srv_sign in (1, -1):
                    signs = (sp_sign, z_sign, x_sign, srv_sign)
                    overrides = {
                        dotted_key: sign
                        for (dotted_key, _label, _toggle), sign in zip(_AXES, signs)
                    }
                    id_parts = [
                        f"{label}{'+' if sign == 1 else '-'}"
                        for (_dotted_key, label, _toggle), sign in zip(_AXES, signs)
                    ]
                    permutation = WiringPermutation(
                        id="_".join(id_parts),
                        overrides=overrides,
                        toggles=canceling_toggles(overrides),
                    )
                    permutations.append(permutation)
    return permutations


WIRING_PERMUTATIONS: list[WiringPermutation] = _build_permutations()
