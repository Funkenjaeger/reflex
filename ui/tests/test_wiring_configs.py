"""Verification for Task 4 of the emulator system-test plan: the physical-
wiring permutation configs + section-aware TOML config patcher.

Pure file generation / dict-shape checks -- does NOT launch the emulator, so
this lives at the top-level ``tests/`` (not ``tests/system/``) and is NOT
marked ``system``; it runs in the default suite.

See .hermes/plans/2026-07-09_emulator-backed-system-tests.md, Task 4, and
``tests/system/wiring.py`` for the design this exercises.
"""

import os
import tomllib
from pathlib import Path

import pytest

from tests.system.wiring import (
    WIRING_PERMUTATIONS,
    canceling_toggles,
    make_config,
)

REFLEX_FW_DIR = Path(os.environ.get("REFLEX_FW_DIR", "/mnt/c/projects/embedded/reflex-fw"))
BASE_TOML_PATH = REFLEX_FW_DIR / "emulator" / "config" / "lathe.toml"

pytestmark = pytest.mark.skipif(
    not BASE_TOML_PATH.is_file(),
    reason=f"reflex-fw base emulator config not found at {BASE_TOML_PATH}",
)


# ---------------------------------------------------------------------------
# WIRING_PERMUTATIONS shape
# ---------------------------------------------------------------------------

def test_exactly_16_permutations():
    assert len(WIRING_PERMUTATIONS) == 16


def test_permutation_ids_are_unique():
    ids = [permutation.id for permutation in WIRING_PERMUTATIONS]
    assert len(ids) == len(set(ids))


def test_every_permutation_pins_all_four_axes():
    expected_keys = {
        "spindle.scale_dir",
        "z_axis.scale_dir",
        "cross_slide.scale_dir",
        "servo.dir",
    }
    for permutation in WIRING_PERMUTATIONS:
        assert set(permutation.overrides.keys()) == expected_keys
        assert set(v for v in permutation.overrides.values()) <= {1, -1}
        assert set(permutation.toggles.keys()) == {
            "spindle_reverse",
            "z_reverse",
            "x_reverse",
            "servo_reverse",
        }


def test_default_all_normal_permutation_present():
    all_normal = [p for p in WIRING_PERMUTATIONS if all(v == 1 for v in p.overrides.values())]
    assert len(all_normal) == 1
    permutation = all_normal[0]
    assert permutation.id == "sp+_z+_x+_srv+"
    assert all(toggle is False for toggle in permutation.toggles.values())


def test_all_inverted_permutation_present():
    all_inverted = [p for p in WIRING_PERMUTATIONS if all(v == -1 for v in p.overrides.values())]
    assert len(all_inverted) == 1
    permutation = all_inverted[0]
    assert permutation.id == "sp-_z-_x-_srv-"
    assert all(toggle is True for toggle in permutation.toggles.values())


# ---------------------------------------------------------------------------
# canceling_toggles() rule: reverse = (wiring_sign == -1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "overrides, expected_toggles",
    [
        (
            {
                "spindle.scale_dir": 1,
                "z_axis.scale_dir": -1,
                "cross_slide.scale_dir": 1,
                "servo.dir": -1,
            },
            {
                "spindle_reverse": False,
                "z_reverse": True,
                "x_reverse": False,
                "servo_reverse": True,
            },
        ),
        (
            {
                "spindle.scale_dir": -1,
                "z_axis.scale_dir": -1,
                "cross_slide.scale_dir": -1,
                "servo.dir": -1,
            },
            {
                "spindle_reverse": True,
                "z_reverse": True,
                "x_reverse": True,
                "servo_reverse": True,
            },
        ),
    ],
)
def test_canceling_toggles_rule(overrides, expected_toggles):
    assert canceling_toggles(overrides) == expected_toggles


def test_every_permutation_toggles_match_canceling_rule():
    for permutation in WIRING_PERMUTATIONS:
        assert permutation.toggles == canceling_toggles(permutation.overrides)


# ---------------------------------------------------------------------------
# make_config(): section-aware patching
# ---------------------------------------------------------------------------

def _load(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _flatten(d: dict, prefix: str = "") -> dict:
    """Flatten a nested dict to {"section.key": value, ...} for easy diffing."""
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


@pytest.mark.parametrize(
    "permutation",
    [p for p in WIRING_PERMUTATIONS if p.id in ("sp+_z+_x+_srv+", "sp-_z+_x-_srv+", "sp+_z-_x+_srv-")],
    ids=lambda p: p.id,
)
def test_make_config_changes_only_intended_keys(tmp_path, permutation):
    out_path = tmp_path / f"{permutation.id}.toml"
    result = make_config(BASE_TOML_PATH, permutation.overrides, out_path)
    assert result == out_path
    assert out_path.is_file()

    base_flat = _flatten(_load(BASE_TOML_PATH))
    generated_flat = _flatten(_load(out_path))

    # Same set of keys -- patching must not add/remove any fields.
    assert set(base_flat.keys()) == set(generated_flat.keys())

    # Any key that actually differs from base must be one of the intended
    # overrides (note: an override whose value equals the base default, e.g.
    # +1, legitimately produces no diff -- so this is a subset check, not
    # equality; the loop below separately confirms every override landed).
    changed_keys = {k for k in base_flat if base_flat[k] != generated_flat[k]}
    assert changed_keys <= set(permutation.overrides.keys())

    for dotted_key, expected_value in permutation.overrides.items():
        assert generated_flat[dotted_key] == expected_value
    for dotted_key in base_flat.keys() - permutation.overrides.keys():
        assert generated_flat[dotted_key] == base_flat[dotted_key]


def test_make_config_is_section_aware_scale_dir_disambiguation(tmp_path):
    """`scale_dir` lives under three different sections; patching one must not
    touch the others, and the section-scoped regex must not accidentally
    match `servo.dir` (a different key name, but same suffix)."""
    out_path = tmp_path / "z_only.toml"
    make_config(BASE_TOML_PATH, {"z_axis.scale_dir": -1}, out_path)

    base_flat = _flatten(_load(BASE_TOML_PATH))
    generated_flat = _flatten(_load(out_path))

    assert generated_flat["z_axis.scale_dir"] == -1
    assert generated_flat["spindle.scale_dir"] == base_flat["spindle.scale_dir"]
    assert generated_flat["cross_slide.scale_dir"] == base_flat["cross_slide.scale_dir"]
    assert generated_flat["servo.dir"] == base_flat["servo.dir"]

    changed_keys = {k for k in base_flat if base_flat[k] != generated_flat[k]}
    assert changed_keys == {"z_axis.scale_dir"}


def test_make_config_preserves_untouched_lines_byte_for_byte(tmp_path):
    """Lines outside the overridden section.key must be byte-identical,
    including comments and formatting."""
    out_path = tmp_path / "servo_only.toml"
    make_config(BASE_TOML_PATH, {"servo.dir": -1}, out_path)

    base_lines = BASE_TOML_PATH.read_text().splitlines()
    generated_lines = out_path.read_text().splitlines()

    assert len(base_lines) == len(generated_lines)
    diffs = [
        (i, b, g)
        for i, (b, g) in enumerate(zip(base_lines, generated_lines))
        if b != g
    ]
    assert len(diffs) == 1
    i, base_line, generated_line = diffs[0]
    assert base_line.strip().startswith("dir = 1")
    assert generated_line.strip().startswith("dir = -1")


def test_make_config_raises_on_unknown_key(tmp_path):
    out_path = tmp_path / "bad.toml"
    with pytest.raises(KeyError):
        make_config(BASE_TOML_PATH, {"servo.not_a_real_key": -1}, out_path)


def test_make_config_raises_on_unknown_section(tmp_path):
    out_path = tmp_path / "bad_section.toml"
    with pytest.raises(KeyError):
        make_config(BASE_TOML_PATH, {"not_a_real_section.dir": -1}, out_path)
