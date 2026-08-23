"""The thread-phase offset READOUT: what the operator is told, and when.

The firmware owns the running total (elsStop.phaseOffsetSteps); the controller
republishes it as the strip in the advanced ELS bar. What is worth pinning here
is not the arithmetic -- tests/fsms/test_els_phase_offset.py owns that -- but
the three ways a status readout can lie:

  1. SHOWING A TORN VALUE. phaseOffsetSteps is 32-bit and the Modbus task
     copies the block one 16-bit register at a time, so a read can be half-old
     and half-new (see test_ui_controller_takeup_outcome.py, where exactly that
     was observed on elspi). The poller renders a total only once it has seen
     the same one twice.
  2. SHOWING NOTHING WHEN THERE IS SOMETHING. An offset that cannot be
     converted -- no servo ratio, no spindle pitch -- must still announce
     itself. Rendering "+0.000 mm" or an empty strip would tell the operator
     the machine is in phase when it is not, and the next thread is scrap.
  3. GOING STALE. The step count does not move when the operator switches
     mm<->in, but the number on screen has to.

GEOMETRY, once: servo 1/1000 mm per step (1000 steps/mm), spindle 3/2 mm per
rev, so one pitch is 1500 steps. The offset used throughout is 500 steps /
0.500 mm -- an exact third of a pitch, chosen so the strip's named-fraction
branch is the one under test. A real groove-widening total is a few hundredths
of a millimeter and renders as a decimal. The same rig as
test_els_phase_offset.py, deliberately.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from reflex.fsms.ui_controller import ElsUiController, phase_offset_fraction_text
from tests.fsms.test_ui_controller import (_make_collaborators, _make_x_axis,
                                           _make_z_axis, _pump)

PITCH_STEPS = 1500
THIRD = 500          # an exact 1/3 of a pitch: the named-fraction branch


@pytest.fixture
def ctrl():
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=_make_x_axis())
    # A leadscrew fine enough that a step is not a visible distance, and a real
    # display format -- the readout is built out of both.
    board.servo.ratioNum, board.servo.ratioDen = 1, 1000
    board.formats = SimpleNamespace(factor=Fraction(1),
                                    position_format="{:+0.3f}",
                                    current_format="MM")
    els.get_spindle_axis.return_value = SimpleNamespace(syncRatioNum=3,
                                                        syncRatioDen=2)
    c = ElsUiController(els=els, board=board)
    _pump()
    # The FSM holds the HAL the readout reads through, so it is the one to
    # stand in for -- replacing only the controller's `_hal` would leave the
    # readout talking to the real (disconnected) one and reading zero forever.
    c._els_fsm.hal = MagicMock(name="hal")
    return c


def _polls(ctrl, *step_totals):
    for steps in step_totals:
        ctrl._els_fsm.hal.read_phase_offset_steps.return_value = steps
        ctrl._poll_phase_offset()


# ─── the readout itself ───────────────────────────────────────────────────

def test_a_settled_offset_names_the_distance_and_the_fraction(ctrl):
    _polls(ctrl, THIRD, THIRD)
    assert ctrl.phase_offset_active is True
    # BOTH numbers, distance first: it is how far the groove has been widened
    # and is checkable against a dial. The fraction is the aliasing bound --
    # how much of the one pitch an offset may accumulate has been spent.
    assert "+0.500 mm" in ctrl.phase_offset_text
    assert "1/3 of a pitch" in ctrl.phase_offset_text


def test_no_offset_shows_nothing(ctrl):
    _polls(ctrl, 0, 0)
    assert ctrl.phase_offset_active is False
    assert ctrl.phase_offset_text == ""


def test_clearing_the_offset_takes_the_strip_down(ctrl):
    _polls(ctrl, THIRD, THIRD)
    _polls(ctrl, 0, 0)
    assert ctrl.phase_offset_active is False
    assert ctrl.phase_offset_text == ""


# ─── torn 32-bit reads ────────────────────────────────────────────────────

def test_a_value_seen_once_is_not_rendered(ctrl):
    _polls(ctrl, THIRD)
    assert ctrl.phase_offset_active is False
    assert ctrl.phase_offset_text == ""


def test_a_torn_read_never_reaches_the_screen(ctrl):
    """Half-old/half-new: the high word of 500 with the low word of nothing.

    One poll of garbage, then the consistent value twice. The garbage must
    never have been on screen -- a wrong distance on this strip is a cut in the
    wrong place, and it would be gone again before it could be questioned.
    """
    torn = 0x01F4_0000        # a plausibly-torn composition of 500
    _polls(ctrl, torn)
    # Asserted HERE, not just on the settled text at the end: with the guard
    # removed the strip flashes the torn value and then corrects itself, so an
    # end-state-only assertion passes against the very bug it is named for.
    assert ctrl.phase_offset_active is False
    assert ctrl.phase_offset_text == ""

    _polls(ctrl, THIRD, THIRD)
    assert "+0.500 mm" in ctrl.phase_offset_text
    assert "1/3 of a pitch" in ctrl.phase_offset_text


# ─── the readout must not go stale or go quiet ────────────────────────────

def test_a_units_switch_re_renders_without_the_total_moving(ctrl):
    _polls(ctrl, THIRD, THIRD)
    metric = ctrl.phase_offset_text

    ctrl._board.formats.current_format = "IN"
    ctrl._board.formats.position_format = "{:+0.4f}"
    ctrl._board.formats.factor = Fraction(10, 254)
    _polls(ctrl, THIRD)          # same total; already seen twice

    assert ctrl.phase_offset_text != metric
    assert "+0.0197 in" in ctrl.phase_offset_text
    # The fraction is a ratio of steps to steps, so it is unit-free and must
    # NOT have changed with the display.
    assert "1/3 of a pitch" in ctrl.phase_offset_text


def test_an_unconvertible_offset_still_announces_itself(ctrl):
    """No servo ratio: nothing here can turn steps into a distance.

    THE STRIP MUST STILL SHOW. This is why visibility hangs off a flag rather
    than off the text being non-empty the way takeup_warning does -- a silent
    strip here reads as "no phase offset", which is the one thing it exists to
    contradict.
    """
    ctrl._board.servo.ratioNum = 0
    _polls(ctrl, THIRD, THIRD)

    assert ctrl.phase_offset_active is True
    assert "+0.000" not in ctrl.phase_offset_text
    assert "500 leadscrew steps" in ctrl.phase_offset_text


# ─── naming a fraction is a claim ─────────────────────────────────────────

@pytest.mark.parametrize("fraction, expected", [
    (1 / 3, "1/3"),                 # exact
    (500 / 1499, "1/3"),            # a pitch's worth of step rounding: still 1/3
    (0.5, "1/2"),
    (2 / 3, "2/3"),
    (0.275, "0.275"),               # nothing clean to say -- say the number
    (0.3300, "0.330"),              # 1% off a third: NOT a third
    (0.0, "0.000"),
    (1.0, "1.000"),                 # a whole pitch is not "1/1" of one
    (1.25, "1.250"),                # past a pitch: the pitch moved, be literal
])
def test_a_fraction_is_named_only_when_it_really_is_one(fraction, expected):
    assert phase_offset_fraction_text(fraction) == expected


def test_a_failed_read_does_not_clear_the_offset_readout(ctrl):
    """A checksum failure returns 0, and 0 here means "no phase shift is being
    cut". Clearing the strip on a bad frame would hide a live offset -- the one
    thing this readout exists to stop the operator forgetting."""
    cm = ctrl._board.connection_manager

    # Establish a live offset on screen (seen twice, per the torn-read guard).
    _polls(ctrl, 200, 200)
    assert ctrl.phase_offset_active, "fixture precondition: an offset is showing"
    text_before = ctrl.phase_offset_text

    def _zero_and_fail(*_a, **_k):
        cm.fail_read()
        return 0

    ctrl._els_fsm.hal.read_phase_offset_steps.side_effect = _zero_and_fail
    ctrl._poll_phase_offset()
    ctrl._poll_phase_offset()

    assert ctrl.phase_offset_active, "a failed read cleared a live offset readout"
    assert ctrl.phase_offset_text == text_before
