"""The input screen's scale-entry wiring: resolution vs ratio.

The conversion itself is covered in tests/utils/test_scale_resolution.py. What
is here is the SCREEN behaviour: which form it opens on, that typing a
resolution writes both registers, and that the kv actually gates the two field
groups against each other. None of that is reachable through the conversion
tests, and all of it is what the operator meets.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def screen_factory(running_app):
    from reflex.components.screens.input_screen import InputScreen

    def _make(ratio_num=1, ratio_den=1000, index=2, with_input=True):
        with patch.object(InputScreen, "apply_class_lang_rules"):
            s = InputScreen()
        if with_input:
            s.input = SimpleNamespace(inputIndex=index, ratioNum=ratio_num,
                                      ratioDen=ratio_den, spindleMode=False)
        return s

    return _make


# ── which form it opens on ──────────────────────────────────────────────────

def test_it_opens_on_resolution_for_a_real_scale(screen_factory):
    s = screen_factory(1, 1000)          # a 1 um head
    s._pick_entry_mode()
    assert s.entry_mode == "Resolution"


def test_it_opens_on_ratio_when_resolution_cannot_carry_the_value(screen_factory):
    """So the screen never opens showing a number that would change the
    setting if the operator simply accepted it."""
    s = screen_factory(1, 2000003)
    s._pick_entry_mode()
    assert s.entry_mode == "Ratio"


def test_picking_the_mode_survives_a_missing_input(screen_factory):
    """on_pre_enter runs before `input` is necessarily populated.

    Exercised by NOT assigning input rather than by assigning None: the
    property is allownone=False, so None is only ever the initial value -- and
    that is precisely the state the guard exists for.
    """
    s = screen_factory(with_input=False)
    assert s.input is None, "the guarded state is unreachable -- test is moot"
    s._pick_entry_mode()                 # must not raise
    assert s.entry_mode in ("Resolution", "Ratio")


# ── typing a resolution ─────────────────────────────────────────────────────

@pytest.mark.parametrize("microns,num,den", [
    (1.0, 1, 1000),
    (2.0, 1, 500),
    (2.5, 1, 400),
    (5.0, 1, 200),
])
def test_typing_a_resolution_writes_both_registers(screen_factory, microns, num, den):
    s = screen_factory(1, 1)
    s.set_resolution(microns)
    assert (s.input.ratioNum, s.input.ratioDen) == (num, den)


def test_a_nonsense_resolution_never_writes_a_zero_numerator(screen_factory):
    """A zero numerator is a DEAD SCALE -- the axis would read a constant.
    Worth its own assertion because the fallback is the only thing between a
    fat-fingered entry and that."""
    s = screen_factory(1, 1000)
    for bad in (0, -1, "", "abc", None):
        s.set_resolution(bad)
        assert s.input.ratioNum > 0
        assert s.input.ratioDen > 0


def test_setting_a_resolution_is_a_no_op_without_an_input(screen_factory):
    s = screen_factory(with_input=False)
    assert s.input is None, "the guarded state is unreachable -- test is moot"
    s.set_resolution(1.0)                # must not raise


def test_the_displayed_resolution_follows_the_stored_ratio(screen_factory):
    s = screen_factory(1, 400)
    assert s.get_resolution(s.input.ratioNum, s.input.ratioDen) == pytest.approx(2.5)


# ── the kv gates the two groups against each other ──────────────────────────

def test_the_kv_shows_exactly_one_form_at_a_time():
    """Both groups keyed off entry_mode, and neither reachable in spindle mode.

    A behavioural test cannot see this -- the widget tree is never built here --
    so it is read out of the kv, the same way the safe-diameter button is.
    """
    from pathlib import Path
    import reflex.components.screens.input_screen as mod

    kv = (Path(mod.__file__).parent / "input_screen.kv").read_text(encoding="utf-8")

    assert 'root.entry_mode != "Resolution"' in kv, "resolution field is not gated"
    assert 'root.entry_mode != "Ratio"' in kv, "ratio fields are not gated"
    assert kv.count('root.entry_mode != "Ratio"') >= 6, \
        "both ratio fields need opacity, height and disabled gated"
    assert "root.set_resolution" in kv and "root.get_resolution" in kv


def test_the_units_are_on_the_labels():
    """The trap that started this: the ratio was secretly millimetres and
    nothing said so, on a machine otherwise run in inches."""
    from pathlib import Path
    import reflex.components.screens.input_screen as mod

    kv = (Path(mod.__file__).parent / "input_screen.kv").read_text(encoding="utf-8")
    assert "Resolution (um/count)" in kv
    assert "Ratio numerator (mm)" in kv
    assert "Ratio denominator (counts)" in kv


# ── the choice sticks (Evan, 2026-09-01) ────────────────────────────────────
#
# It was per-visit at first and reverted every time the screen was left.
# "If a user prefers ratio, honor that and stick to it." A preference that does
# not survive leaving the screen is not a preference.

def test_choosing_ratio_is_written_back_to_the_input(screen_factory):
    s = screen_factory(1, 1000)
    s.entry_mode = "Ratio"
    assert s.input.scale_entry_mode == "Ratio"


def test_a_stored_preference_is_honoured_on_the_next_visit(screen_factory):
    s = screen_factory(1, 1000)
    s.input.scale_entry_mode = "Ratio"
    s._pick_entry_mode()
    assert s.entry_mode == "Ratio", "the preference was ignored on re-entry"


def test_the_preference_is_overridden_when_it_would_lie(screen_factory):
    """The one case the preference does NOT win: a stored value that cannot be
    expressed as a resolution opens on Ratio no matter what, because honouring
    a preference is not worth showing a number that would change the setting
    if the operator accepted it."""
    s = screen_factory(1, 2000003)
    s.input.scale_entry_mode = "Resolution"
    s._pick_entry_mode()
    assert s.entry_mode == "Ratio"


def test_a_resolution_preference_is_honoured_too(screen_factory):
    """The negative control on the override -- it must not force Ratio always."""
    s = screen_factory(1, 1000)
    s.input.scale_entry_mode = "Resolution"
    s._pick_entry_mode()
    assert s.entry_mode == "Resolution"


def test_a_garbage_stored_preference_falls_back(screen_factory):
    s = screen_factory(1, 1000)
    s.input.scale_entry_mode = "Furlongs"
    s._pick_entry_mode()
    assert s.entry_mode == "Resolution"


def test_the_preference_is_persisted_on_the_input_not_the_screen():
    """It rides the input's SavingDispatcher YAML, so it survives a restart.
    Held on the Screen it would die with the widget on every navigation."""
    from reflex.dispatchers.input import InputDispatcher
    assert hasattr(InputDispatcher, "scale_entry_mode"), \
        "the preference is no longer on the persisted dispatcher"


def test_writing_back_is_a_no_op_without_an_input(screen_factory):
    s = screen_factory(with_input=False)
    s.entry_mode = "Ratio"               # must not raise


def test_the_resolution_field_asks_for_decimals():
    """A bare "2" reads as a count; "2.000" reads as a measurement."""
    from pathlib import Path
    import reflex.components.screens.input_screen as mod

    kv = (Path(mod.__file__).parent / "input_screen.kv").read_text(encoding="utf-8")
    block = kv[kv.index("Resolution (um/count)"):]
    block = block[:block.index("Ratio numerator")]
    assert "decimals:" in block, "the resolution field lost its fixed decimals"
