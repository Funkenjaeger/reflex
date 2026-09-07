"""NumberItem's optional fixed-decimal display.

Added for the scale-resolution field: a bare "2" reads as a count of
something, "2.000" reads as a measurement (Evan, 2026-09-01).

THE RISK IS THE SHARED WIDGET, not the formatting. NumberItem renders every
numeric setting in the app, so the default must be byte-identical to the
`str(value)` it has always used -- these tests exist mostly to pin that.
"""
from unittest.mock import patch

import pytest

from reflex.components.widgets.number_item import NumberItem


@pytest.fixture(autouse=True)
def _headless():
    """Same pattern as every other component suite here: the widget's own kv
    rule tree pulls in chrome the mock GL backend cannot service, and the
    Python under test does not need it."""
    with patch.object(NumberItem, "apply_class_lang_rules"):
        yield


@pytest.mark.parametrize("value,expected", [
    (2, "2"),
    (2.0, "2.0"),
    (2.5, "2.5"),
    # 0 renders "0.0", not "0": the property's default IS 0.0, so assigning
    # int 0 is not a change and the float default survives. Pinned as the
    # pre-existing behaviour rather than corrected -- every zero-valued field
    # in the app already reads this way.
    (0, "0.0"),
    (1000, "1000"),
])
def test_the_default_is_unchanged_str_rendering(value, expected):
    """Every existing NumberItem in the app goes through this path. If it
    moves, dozens of unrelated fields change how they read."""
    item = NumberItem()
    assert item.decimals == -1, "the opt-in default changed"
    item.value = value
    assert item.display == expected


@pytest.mark.parametrize("value,decimals,expected", [
    (2, 3, "2.000"),
    (2.5, 3, "2.500"),
    (1, 3, "1.000"),
    (0.1, 3, "0.100"),
    (5, 1, "5.0"),
    (2.5, 0, "2"),
])
def test_fixed_decimals_when_asked_for(value, decimals, expected):
    item = NumberItem(decimals=decimals)
    item.value = value
    assert item.display == expected


def test_display_re_derives_when_the_value_moves():
    """It is an AliasProperty with an explicit bind list; a missing entry
    leaves the field showing a stale number after an edit."""
    item = NumberItem(decimals=3)
    seen = []
    item.bind(display=lambda _i, v: seen.append(v))

    item.value = 2.0
    item.value = 5.0

    assert seen == ["2.000", "5.000"]


def test_display_re_derives_when_the_precision_moves():
    item = NumberItem()
    item.value = 2.0
    assert item.display == "2.0"
    item.decimals = 3
    assert item.display == "2.000"


def test_the_kv_renders_display_not_the_raw_value():
    """A behavioural test cannot see this -- the widget tree is not built
    here -- and reverting the kv to str(root.value) would silently undo the
    whole feature while every test above stayed green."""
    from pathlib import Path
    import reflex.components.widgets.number_item as mod

    kv = (Path(mod.__file__).parent / "number_item.kv").read_text(encoding="utf-8")
    assert "text: root.display" in kv
    assert "str(root.value)" not in kv
