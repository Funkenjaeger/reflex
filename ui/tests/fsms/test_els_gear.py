"""The gearbox table (reflex/fsms/els_gear.py): parsing and validation, what
each position does to step counts and the cut-start margin, and the default of
no gearbox at all."""
from fractions import Fraction
from types import SimpleNamespace

import pytest

from reflex.fsms.els_gear import (
    MAX_FACTOR,
    MIN_FACTOR,
    NO_GEARBOX,
    GearTable,
    GearTableError,
    table_of,
)
from tests.fsms.test_els_fsm import _build_fsm, _make_controller, _make_servo

#: A lathe with a three-position lever: reference A, B twice as fast, C half.
ABC = GearTable.parse("A=1, B=2, C=1/2")


# ── Parsing and validation ──────────────────────────────────────────────

def test_empty_text_is_no_gearbox():
    for text in ("", "   ", "\t"):
        assert GearTable.parse(text) is NO_GEARBOX
    assert not NO_GEARBOX.has_gearbox
    assert NO_GEARBOX.names == ()
    assert NO_GEARBOX.text() == ""


def test_the_first_position_is_the_reference():
    assert ABC.has_gearbox
    assert ABC.names == ("A", "B", "C")
    assert ABC.reference == "A"
    assert ABC.factor("A") == 1
    assert ABC.factor("B") == 2
    assert ABC.factor("C") == Fraction(1, 2)


@pytest.mark.parametrize("text, factors", [
    ("A=1, B=2, C=0.5", [1, 2, Fraction(1, 2)]),
    ("A=1 B=2 C=1/2", [1, 2, Fraction(1, 2)]),
    ("A=1,B=1.5", [1, Fraction(3, 2)]),
    ("Lo=1, Hi=3/2", [1, Fraction(3, 2)]),
    ("1=1, 2=3/2, 3=4", [1, Fraction(3, 2), 4]),
    ("x=1, y=0.25, z=2.5, w=8", [1, Fraction(1, 4), Fraction(5, 2), 8]),
])
def test_decimals_and_fractions_parse_exactly(text, factors):
    t = GearTable.parse(text)
    assert [t.factor(n) for n in t.names] == factors


def test_the_reference_factor_may_be_left_out():
    assert GearTable.parse("Lo, Hi=2") == GearTable.parse("Lo=1, Hi=2")


def test_canonical_text_round_trips():
    t = GearTable.parse("  Lo=1 ,Hi=1.5,  Top=4 ")
    assert t.text() == "Lo=1, Hi=3/2, Top=4"
    assert GearTable.parse(t.text()) == t
    assert ABC.text() == "A=1, B=2, C=1/2"


@pytest.mark.parametrize("text, words", [
    ("A=1", "2 to 4 positions"),
    ("A=1, B=2, C=1/2, D=4, E=1/4", "2 to 4 positions"),
    ("A=1, BCDE=2", "1 to 3 characters"),
    ("=1, B=2", "1 to 3 characters"),
    ("A=1, A=2", "twice"),
    ("Lo=1, LO=2", "twice"),
    ("A=1, B=two", "a number"),
    ("A=1, B=", "a number"),
    ("A=1, B=1/0", "a number"),
    ("A=1, B", "name=factor"),
    ("A=1, B=0", "between"),
    ("A=1, B=-2", "between"),
    ("A=1, B=9", "between"),
    ("A=1, B=0.1", "between"),
    ("A=2, B=1", "reference"),
    ("A=1, B=1.1", "too close"),
    ("A=1, B=2, C=2.2", "too close"),
    ("A=1, B=2=3", "name=factor"),
])
def test_invalid_tables_are_refused_with_a_reason(text, words):
    with pytest.raises(GearTableError) as err:
        GearTable.parse(text)
    assert words in str(err.value)


def test_the_range_ends_are_allowed():
    t = GearTable.parse(f"A=1, B={MAX_FACTOR}, C={MIN_FACTOR}")
    assert t.fastest == MAX_FACTOR and t.slowest == MIN_FACTOR


def test_table_of_reads_a_servo_or_falls_back_to_no_gearbox():
    assert table_of(SimpleNamespace(gear_table=ABC)) is ABC
    assert table_of(SimpleNamespace()) is NO_GEARBOX
    assert table_of(SimpleNamespace(gear_table="A=1, B=2")) is NO_GEARBOX


# ── Positions ───────────────────────────────────────────────────────────

def test_an_unknown_position_reads_as_the_reference():
    assert ABC.resolve("Z") == "A"
    assert ABC.resolve("") == "A"
    assert ABC.factor("Z") == 1


def test_with_no_gearbox_every_position_is_ignored():
    assert NO_GEARBOX.resolve("B") == ""
    assert NO_GEARBOX.factor("B") == 1
    assert NO_GEARBOX.steps_in_gear(484, "B") == 484
    assert NO_GEARBOX.worst_case_travel_factor("C") == 1


@pytest.mark.parametrize("gear, expected", [("A", 484), ("B", 242), ("C", 968)])
def test_backlash_steps_follow_the_lever(gear, expected):
    """The play is on the leadscrew side: fast B needs half the steps, slow C
    twice as many. On 2026-10-03 the A-sized take-up was refused in C."""
    assert ABC.steps_in_gear(484, gear) == expected


def test_steps_follow_a_non_binary_factor():
    t = GearTable.parse("1=1, 2=3/2, 3=4")
    assert t.steps_in_gear(300, "2") == 200
    assert t.steps_in_gear(300, "3") == 75


def test_a_one_step_take_up_never_rounds_to_zero():
    assert ABC.steps_in_gear(1, "B") == 1
    assert ABC.steps_in_gear(-1, "B") == -1
    assert ABC.steps_in_gear(0, "B") == 0


@pytest.mark.parametrize("gear, expected", [("A", 2), ("B", 1), ("C", 4)])
def test_worst_case_is_the_fastest_position(gear, expected):
    assert ABC.worst_case_travel_factor(gear) == expected


def test_the_fast_slow_hint_comes_from_the_factor_not_the_name():
    t = GearTable.parse("Hi=1, Lo=4, B=1/3")
    assert t.describe("Hi") == "Hi"
    assert t.describe("Lo") == "Lo (fast)"
    assert t.describe("B") == "B (slow)"


def test_nearest_position_on_a_ratio_scale():
    t = GearTable.parse("1=1, 2=3/2, 3=4")
    assert t.nearest(1.02) == ("1", pytest.approx(0.02))
    assert t.nearest(1.45)[0] == "2"
    assert t.nearest(3.7)[0] == "3"
    assert t.nearest(0.0) == ("1", -1.0)


def test_with_no_gearbox_the_only_position_is_the_leadscrew():
    assert NO_GEARBOX.nearest(2.0) == ("", pytest.approx(1.0))
    assert NO_GEARBOX.nearest(0.5)[0] == ""


# ── The cut-start margin ────────────────────────────────────────────────

def _fsm(*, gear="A", table=ABC, backlash=0, threading=False,
         thread_pitch_mm=None):
    """An ElsFsm on an 8 TPI / 1600-step leadscrew in mm display units."""
    controller = _make_controller(is_threading=threading)
    fsm = _build_fsm(controller=controller,
                     els_extra={"els_backlash_steps": backlash})
    lead_mm = Fraction(254, 80)                      # 0.125 in
    ratio = lead_mm / 1600 * table.factor(gear)      # as configure_lead_screw_ratio
    servo = _make_servo(ratio_num=ratio.numerator, ratio_den=ratio.denominator,
                        lead_screw_pitch=0.125, lead_screw_pitch_in=True)
    servo.gear_table = table
    servo.gearbox_position = gear
    fsm.servo = servo
    if thread_pitch_mm is not None:
        pitch = Fraction(thread_pitch_mm).limit_denominator(10000)
        fsm.els.get_spindle_axis.return_value = SimpleNamespace(
            syncRatioNum=pitch.numerator, syncRatioDen=pitch.denominator)
    return fsm


def test_margin_in_A_covers_the_fast_gear():
    """Leadscrew pitch 3.175 mm, no backlash: 3.175 x 2 (fastest) x 1.1."""
    assert _fsm()._safety_margin_display() == pytest.approx(3.175 * 2 * 1.1)


def test_margin_in_B_is_not_inflated():
    """B is already the fastest position; one B revolution is 6.35 mm."""
    assert _fsm(gear="B")._safety_margin_display() == pytest.approx(6.35 * 1 * 1.1)


def test_margin_in_C_covers_a_lever_moved_to_B():
    """C believes 1.5875 mm per revolution, but B would move four times that."""
    assert _fsm(gear="C")._safety_margin_display() == pytest.approx(1.5875 * 4 * 1.1)


def test_margin_with_no_gearbox_is_one_leadscrew_pitch():
    """No lever, nothing faster to budget for."""
    got = _fsm(gear="B", table=NO_GEARBOX)._safety_margin_display()
    assert got == pytest.approx(3.175 * 1 * 1.1)


def test_margin_follows_a_configured_fastest_position():
    """Reference "1", fastest "3" at x4: in "2" (x3/2) the worst case is 4 / 1.5."""
    t = GearTable.parse("1=1, 2=3/2, 3=4")
    got = _fsm(gear="2", table=t)._safety_margin_display()
    assert got == pytest.approx(3.175 * 1.5 * (4 / 1.5) * 1.1)


def test_margin_includes_the_take_up_in_the_engaged_gear():
    """484 reference steps are 0.9604 mm of carriage in any gear: fewer steps
    in B, each moving further."""
    backlash_mm = 484 * 3.175 / 1600
    for gear, travel in (("A", 2), ("B", 1), ("C", 4)):
        lead = 3.175 * float(ABC.factor(gear))
        got = _fsm(gear=gear, backlash=484)._safety_margin_display()
        assert got == pytest.approx((lead + backlash_mm) * travel * 1.1, rel=1e-3), gear


def test_margin_uses_a_thread_coarser_than_the_leadscrew():
    """4 TPI (6.35 mm) on an 8 TPI leadscrew: the phase correction can move a
    whole thread pitch, so the thread pitch sets the correction term."""
    got = _fsm(threading=True, thread_pitch_mm=6.35)._safety_margin_display()
    assert got == pytest.approx(6.35 * 2 * 1.1)


def test_margin_keeps_the_leadscrew_floor_for_a_fine_thread():
    got = _fsm(threading=True, thread_pitch_mm=1.411)._safety_margin_display()
    assert got == pytest.approx(3.175 * 2 * 1.1)


def test_turning_ignores_the_feed_per_rev():
    """Turning has no phase correction; the feed per rev is not a move."""
    got = _fsm(threading=False, thread_pitch_mm=6.35)._safety_margin_display()
    assert got == pytest.approx(3.175 * 2 * 1.1)


# ── The take-up the firmware is told ───────────────────────────────────

@pytest.mark.parametrize("gear, expected", [("A", 484), ("B", 242), ("C", 968)])
def test_cut_pushes_the_take_up_in_the_engaged_gear(gear, expected):
    fsm = _fsm(gear=gear, backlash=484)
    # Retract mode: ready to cut means "at retract_z", so the margin (tested
    # above) does not decide whether this cut starts.
    fsm.controller.retract_enabled = True
    fsm.z_axis.scaledPosition = fsm.controller.retract_z
    fsm.enable()
    fsm.hal.reset_mock()
    fsm.cut()
    # Carried in the arming batch now, not written on its own.
    assert fsm.hal.arm.call_args.kwargs["backlash_steps"] == expected


def test_a_servo_without_a_gearbox_is_the_reference():
    fsm = _build_fsm(els_extra={"els_backlash_steps": 484})
    assert not hasattr(fsm.servo, "gearbox_position")
    assert fsm.engaged_gear() == ""
    assert fsm.engaged_backlash_steps() == 484


def test_a_stored_position_not_in_the_table_engages_the_reference():
    fsm = _fsm(gear="Q", backlash=484)
    assert fsm.engaged_gear() == "A"
    assert fsm.engaged_backlash_steps() == 484


def test_with_no_gearbox_a_stored_position_is_ignored():
    fsm = _fsm(gear="B", table=NO_GEARBOX, backlash=484)
    assert fsm.engaged_gear() == ""
    assert fsm.engaged_backlash_steps() == 484


# ── Changing the table re-teaches the firmware ──────────────────────────

class _TableServo(SimpleNamespace):
    """A servo stand-in whose gearbox_table text re-derives gear_table, as
    the real dispatcher's binding does."""

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name == "gearbox_table":
            super().__setattr__("gear_table", GearTable.parse(value))


def _fsm_with_table_servo(text, gear, legs=(100, 120, 110)):
    from unittest.mock import MagicMock
    fsm = _build_fsm()
    fsm.els.els_cal_measured_legs = list(legs)
    servo = _TableServo(ratioNum=1, ratioDen=1, leadScrewPitch=0.0,
                        leadScrewPitchIn=False, servoMode=0,
                        stop_feed=lambda: None, gearbox_position=gear)
    servo.gearbox_table = text
    fsm.servo = servo
    axis = MagicMock()
    fsm.board.axes = [axis]
    fsm.hal.reset_mock()
    return fsm, axis


def test_applying_a_table_persists_it_and_reteaches_the_engaged_factor():
    fsm, axis = _fsm_with_table_servo("A=1, B=2", "B")
    fsm.apply_gear_table(GearTable.parse("A=1, B=4"))
    assert fsm.servo.gearbox_table == "A=1, B=4"
    assert fsm.engaged_gear() == "B"
    axis.push_sync_ratio.assert_called_once()
    fsm.hal.set_cal_measured.assert_called_once_with([25, 30, 28])


def test_a_table_without_the_stored_position_engages_the_reference():
    fsm, axis = _fsm_with_table_servo("A=1, B=2", "B")
    fsm.apply_gear_table(GearTable.parse("Lo=1, Hi=3/2"))
    assert fsm.engaged_gear() == "Lo"
    fsm.hal.set_cal_measured.assert_called_once_with([100, 120, 110])


def test_clearing_the_table_reteaches_the_plain_leadscrew():
    fsm, axis = _fsm_with_table_servo("A=1, B=2", "B")
    fsm.apply_gear_table(NO_GEARBOX)
    assert fsm.servo.gearbox_table == ""
    assert fsm.engaged_gear() == ""
    fsm.hal.set_cal_measured.assert_called_once_with([100, 120, 110])
