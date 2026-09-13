"""The tier contract. See reflex/utils/commissioning_scope.py's docstring for
why a re-tier is a contract change and not a tweak."""
import pytest

from reflex.utils.commissioning_scope import (COMMISSIONING, IGNORED,
                                              OPERATIONAL, tier)


# ── commissioning is the default, and that is the point ──────────────

@pytest.mark.parametrize("key", [
    "backlash", "axis_name", "axis_index", "diameter_mode",
    "spindle_axis_index", "z_axis_index", "counts_per_mm",
    "transform_config", "els_cal_measured_legs", "invert_direction",
    # A property nobody has written yet: it must land in the tier that gets
    # captured, with no edit to the module.
    "some_future_calibration_constant",
])
def test_unknown_and_identity_keys_are_commissioning(key):
    assert tier("Axis-0", key, {}) == COMMISSIONING


# ── the operational set, which is exhaustive ─────────────────────────

@pytest.mark.parametrize("stem", ["Axis-0", "Axis-3", "CoordBar-1", "Els-0"])
def test_offsets_is_operational_in_any_file(stem):
    assert tier(stem, "offsets", {}) == OPERATIONAL


@pytest.mark.parametrize("key", ["syncRatioNum", "syncRatioDen"])
def test_sync_ratio_is_operational_on_a_spindle(key):
    data = {"spindleMode": True, "syncRatioNum": 360, "syncRatioDen": 100}
    assert tier("Axis-0", key, data) == OPERATIONAL


@pytest.mark.parametrize("key", ["syncRatioNum", "syncRatioDen"])
@pytest.mark.parametrize("data", [
    {"spindleMode": False},
    {},                       # flag absent entirely (older file)
    {"spindleMode": None},
    {"spindleMode": "true"},  # a string is not the flag; only True is
])
def test_sync_ratio_is_commissioning_on_a_linear_axis(key, data):
    """Same key names, opposite tier. On a linear axis these two ARE the
    scale calibration, which is exactly what a card death loses."""
    assert tier("Axis-0", key, data) == COMMISSIONING


def test_spindleness_comes_from_the_data_not_the_filename():
    """The rule that stops a differently-wired machine being mis-tiered.

    `Axis-0` is the spindle on Evan's lathe and need not be on anyone else's;
    the classification must follow the flag in the file, both directions.
    """
    # A file named like the usual spindle, but not flagged: calibration.
    assert tier("Axis-0", "syncRatioNum", {"spindleMode": False}) == COMMISSIONING
    # A file named like a linear axis, but flagged: operator state.
    assert tier("Axis-2", "syncRatioNum", {"spindleMode": True}) == OPERATIONAL


def test_spindle_flag_itself_is_commissioning():
    """Which axis IS the spindle is machine identity, not job state."""
    assert tier("Axis-0", "spindleMode", {"spindleMode": True}) == COMMISSIONING


# ── the ignored set ──────────────────────────────────────────────────

def test_id_override_is_ignored():
    """It is the filename, not a value. See read_settings' comment."""
    assert tier("Axis-0", "id_override", {"id_override": "0"}) == IGNORED


@pytest.mark.parametrize("key", [
    "size_hint_x", "size_hint_y", "size_hint_min_x", "size_hint_max_y",
    "spacing", "padding", "pos", "size",
    "x", "y", "width", "height",
    "minimum_width", "minimum_height", "position",
    # The two documented as actually present in ElsAdvancedBar-*.yaml on elspi.
    "natural_height", "opacity",
])
def test_layout_geometry_is_ignored(key):
    assert tier("ElsAdvancedBar-2268", key, {}) == IGNORED


def test_a_none_data_mapping_does_not_raise():
    """read_settings returns None for an unparseable file; the caller may pass
    an empty mapping through. A classifier that raises here would take out a
    config save."""
    assert tier("Axis-0", "syncRatioNum", None) == COMMISSIONING
