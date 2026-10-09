"""Tests for ElsSettingsPopup's mm<->steps backlash conversion
(reflex/components/home/els_settings_popup.py).

ElsSettingsPopup subclasses kivy.uix.popup.Popup directly (unlike CustomPopup,
which WRAPS a Popup and can substitute a MagicMock for it). Its own kv rule
(els_settings_popup.kv) builds a ScrollView > GridLayout > DropDownItem/
BooleanItem/NumberItem tree, and constructing a real Popup subclass headlessly
risks the same mock-GL-backend texture crash documented in
test_keypad.py/test_custom_popup.py.

The methods under test here (_servo_mm_per_step / _steps_to_mm / _mm_to_steps)
are pure: they only ever read `self.app`. So rather than construct a real
ElsSettingsPopup (patching Popup.__init__ and/or apply_class_lang_rules), these
tests bind the three real methods onto a bare stand-in class that only carries
`.app.servo.ratioNum/ratioDen` -- no Kivy widget is built at all, so there is
no crash risk and no kv/Popup machinery to fight with. (A plain
`types.SimpleNamespace` doesn't work here: `_steps_to_mm`/`_mm_to_steps` call
`self._servo_mm_per_step()`, an ordinary bound-method dispatch through
`self`'s own class, which a namespace object has no class-level method for --
so the stand-in needs to actually own the methods, not just be duck-typed
kwargs.)
"""
from types import SimpleNamespace

import pytest

from reflex.components.home.els_settings_popup import ElsSettingsPopup

# Servo scale the converters below are exercised at: 127/64000 mm/step. That
# is not a machine property in its own right -- it FOLLOWS from an 8 TPI
# leadscrew (0.125 in pitch) driven at 1600 steps/rev, which is the REAL elspi
# machine's commissioned step count. The EMULATOR REFERENCE machine uses 800
# steps/rev, i.e. 127/32000 mm/step (the defaults in tests/system/harness.py
# commission_geometry) -- so 127/64000 here is the real-machine figure and
# 127/32000 is the emulator's, not the other way round.
# elspi's full commissioned geometry: AGENTS.md ("elspi commissioned
# geometry"), proven end to end by tests/system/test_els_elspi_geometry.py.
REAL_RATIO_NUM = 127
REAL_RATIO_DEN = 64000


class _BarePopup:
    """Owns the real converter methods (bound to a real instance of this
    class, so their internal `self._servo_mm_per_step()` calls resolve
    normally) without ever running ElsSettingsPopup.__init__ / Popup
    construction / kv rules."""
    _servo_mm_per_step = ElsSettingsPopup._servo_mm_per_step
    _steps_to_mm = ElsSettingsPopup._steps_to_mm
    _mm_to_steps = ElsSettingsPopup._mm_to_steps

    def __init__(self, ratio_num=REAL_RATIO_NUM, ratio_den=REAL_RATIO_DEN):
        self.app = SimpleNamespace(servo=SimpleNamespace(ratioNum=ratio_num, ratioDen=ratio_den))


def test_steps_to_mm_matches_real_machine_ratio():
    popup = _BarePopup()
    result = popup._steps_to_mm(403)
    expected = 403 * REAL_RATIO_NUM / REAL_RATIO_DEN
    assert result == pytest.approx(expected)
    assert result == pytest.approx(0.7997, abs=1e-4)


def test_mm_to_steps_round_trips_with_steps_to_mm():
    popup = _BarePopup()
    mm = popup._steps_to_mm(403)
    assert popup._mm_to_steps(mm) == 403


def test_mm_to_steps_zero_mm_is_zero_steps():
    popup = _BarePopup()
    assert popup._mm_to_steps(0) == 0


def test_mm_to_steps_negative_mm_clamps_to_zero_steps():
    popup = _BarePopup()
    assert popup._mm_to_steps(-5.0) == 0


def test_ratio_den_zero_yields_zero_without_crashing():
    popup = _BarePopup(ratio_num=REAL_RATIO_NUM, ratio_den=0)
    assert popup._servo_mm_per_step() == 0.0
    assert popup._steps_to_mm(403) == 0.0
    assert popup._mm_to_steps(5.0) == 0


# ── The gearbox rows ────────────────────────────────────────────────────

from kivy.event import EventDispatcher  # noqa: E402
from kivy.properties import (BooleanProperty, ListProperty,  # noqa: E402
                             ObjectProperty, StringProperty)
from unittest.mock import MagicMock  # noqa: E402

from reflex.fsms.els_gear import NO_GEARBOX, GearTable  # noqa: E402


class _GearServo(EventDispatcher):
    """The servo surface the gearbox rows read: real Kivy properties, so the
    popup's binding to gear_table is a real binding."""
    gear_table = ObjectProperty(NO_GEARBOX)
    gearbox_table = StringProperty("")
    gearbox_position = StringProperty("")
    gearbox_engaged = StringProperty("")
    ratioNum = 127
    ratioDen = 64000


class _GearPopup(EventDispatcher):
    """The real gearbox methods and properties, without Popup or kv."""
    has_gearbox = BooleanProperty(False)
    gear_names = ListProperty([])
    _sync_gearbox = ElsSettingsPopup._sync_gearbox
    commit_gear_table = ElsSettingsPopup.commit_gear_table
    select_gear = ElsSettingsPopup.select_gear
    _servo_mm_per_step = ElsSettingsPopup._servo_mm_per_step

    def __init__(self, servo):
        super().__init__()
        self.app = SimpleNamespace(servo=servo, els_uic=MagicMock())
        self._sync_gearbox()
        servo.fbind("gear_table", self._sync_gearbox)


def test_no_gearbox_hides_the_position_row():
    popup = _GearPopup(_GearServo())
    assert popup.has_gearbox is False
    assert popup.gear_names == []


def test_the_position_row_offers_the_owner_names_and_follows_the_table():
    servo = _GearServo()
    popup = _GearPopup(servo)
    servo.gear_table = GearTable.parse("Lo=1, Hi=2, 3=1/2")
    assert popup.has_gearbox is True
    assert popup.gear_names == ["Lo", "Hi", "3"]
    servo.gear_table = NO_GEARBOX
    assert popup.has_gearbox is False and popup.gear_names == []


def test_mm_per_step_divides_out_the_engaged_factor():
    """The step-valued fields are entered in mm and stored in reference steps;
    in a x2 position the servo ratio is doubled, and the mm must not be."""
    servo = _GearServo(gear_table=GearTable.parse("A=1, B=2"),
                       gearbox_position="B")
    servo.ratioNum, servo.ratioDen = 127, 32000          # x2 folded in
    popup = _GearPopup(servo)
    assert popup._servo_mm_per_step() == pytest.approx(127 / 64000)


def test_a_typed_table_goes_to_the_controller_and_shows_canonical():
    servo = _GearServo(gearbox_table="")
    popup = _GearPopup(servo)

    def apply(text):
        servo.gearbox_table = GearTable.parse(text).text()
        return True
    popup.app.els_uic.set_gear_table.side_effect = apply
    item = SimpleNamespace(value="Lo=1 Hi=1.5")
    popup.commit_gear_table(item)
    popup.app.els_uic.set_gear_table.assert_called_once_with("Lo=1 Hi=1.5")
    assert item.value == "Lo=1, Hi=3/2"


def test_a_refused_table_snaps_the_row_back():
    servo = _GearServo(gearbox_table="A=1, B=2")
    popup = _GearPopup(servo)
    popup.app.els_uic.set_gear_table.return_value = False
    item = SimpleNamespace(value="A=1, B=1.1")
    popup.commit_gear_table(item)
    assert item.value == "A=1, B=2"


def test_the_unchanged_table_is_not_resubmitted():
    """The row's value is set from the servo when the popup is built; that
    must not count as an edit."""
    popup = _GearPopup(_GearServo(gearbox_table="A=1, B=2"))
    popup.commit_gear_table(SimpleNamespace(value="A=1, B=2"))
    popup.app.els_uic.set_gear_table.assert_not_called()


def test_a_refused_position_snaps_back_to_the_engaged_one():
    servo = _GearServo(gear_table=GearTable.parse("A=1, B=2"),
                       gearbox_engaged="A")
    popup = _GearPopup(servo)
    popup.app.els_uic.select_gear.return_value = False
    item = SimpleNamespace(value="B")
    popup.select_gear(item)
    popup.app.els_uic.select_gear.assert_called_once_with("B")
    assert item.value == "A"


def _kv_block(anchor):
    from pathlib import Path
    import reflex.components.home.els_settings_popup as mod
    kv = (Path(mod.__file__).parent / "els_settings_popup.kv").read_text(
        encoding="utf-8")
    block = kv[kv.index(anchor):]
    return kv, block[:block.index("\n\n")]


def test_the_kv_rows_read_the_configured_table():
    """The popup is never built in this suite, so pin the kv bindings: rows
    bound to constants would pass every test above."""
    kv, table_row = _kv_block('name: "Gearbox table"')
    assert "value: app.servo.gearbox_table" in table_row
    assert "on_value: root.commit_gear_table(self)" in table_row
    _kv, pos_row = _kv_block('name: "Gearbox position"')
    assert "options: root.gear_names" in pos_row
    assert "value: app.servo.gearbox_engaged" in pos_row
    assert "on_value: root.select_gear(self)" in pos_row
    pos_rule = kv[kv.rindex("DropDownItem:", 0, kv.index('name: "Gearbox position"')):]
    pos_rule = pos_rule[:pos_rule.index("\n\n")]
    assert "height: 60 if root.has_gearbox else 0" in pos_rule
    assert "opacity: 1 if root.has_gearbox else 0" in pos_rule
    assert '["A", "B", "C"]' not in kv
