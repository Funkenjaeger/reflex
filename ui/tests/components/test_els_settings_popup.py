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
