"""The gearbox convention (reflex/fsms/els_gear.py) and what it does to the
cut-start margin and the backlash take-up."""
from fractions import Fraction
from types import SimpleNamespace

import pytest

from reflex.fsms.els_gear import (
    FASTEST_FACTOR,
    GEAR_FACTORS,
    REFERENCE_GEAR,
    gear_factor,
    steps_in_gear,
    worst_case_travel_factor,
)
from tests.fsms.test_els_fsm import _build_fsm, _make_controller, _make_servo


# ── The module ──────────────────────────────────────────────────────────

def test_the_three_positions_and_the_reference():
    assert GEAR_FACTORS == {"A": 1, "B": 2, "C": Fraction(1, 2)}
    assert REFERENCE_GEAR == "A"
    assert FASTEST_FACTOR == 2


def test_unknown_gear_reads_as_the_reference():
    assert gear_factor("Z") == 1
    assert gear_factor("") == 1


@pytest.mark.parametrize("gear, expected", [("A", 484), ("B", 242), ("C", 968)])
def test_backlash_steps_follow_the_lever(gear, expected):
    """The play is on the leadscrew side: fast B needs half the steps, slow C
    twice as many. On 2026-10-03 the A-sized take-up was refused in C."""
    assert steps_in_gear(484, gear) == expected


def test_a_one_step_take_up_never_rounds_to_zero():
    assert steps_in_gear(1, "B") == 1
    assert steps_in_gear(-1, "B") == -1
    assert steps_in_gear(0, "B") == 0


@pytest.mark.parametrize("gear, expected", [("A", 2), ("B", 1), ("C", 4)])
def test_worst_case_is_the_fastest_position(gear, expected):
    assert worst_case_travel_factor(gear) == expected


# ── The cut-start margin ────────────────────────────────────────────────

def _fsm(*, gear="A", backlash=0, threading=False, thread_pitch_mm=None):
    """An ElsFsm on an 8 TPI / 1600-step leadscrew in mm display units."""
    controller = _make_controller(is_threading=threading)
    fsm = _build_fsm(controller=controller,
                     els_extra={"els_backlash_steps": backlash})
    lead_mm = Fraction(254, 80)                      # 0.125 in
    ratio = lead_mm / 1600 * gear_factor(gear)       # as configure_lead_screw_ratio
    servo = _make_servo(ratio_num=ratio.numerator, ratio_den=ratio.denominator,
                        lead_screw_pitch=0.125, lead_screw_pitch_in=True)
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


def test_margin_includes_the_take_up_in_the_engaged_gear():
    """484 reference steps are 0.9604 mm of carriage in any gear: fewer steps
    in B, each moving further."""
    backlash_mm = 484 * 3.175 / 1600
    for gear, travel in (("A", 2), ("B", 1), ("C", 4)):
        lead = 3.175 * float(gear_factor(gear))
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
    assert fsm.engaged_gear() == "A"
    assert fsm.engaged_backlash_steps() == 484
