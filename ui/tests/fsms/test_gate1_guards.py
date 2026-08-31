"""The three Gate 1 items ratified 2026-08-30, and why each one is a defect.

They share one shape: a message or a guard that is confidently WRONG about the
machine, in a way nothing in the suite could previously have noticed.

  1. Three places told the operator to cycle the ELS stop -- for calibration,
     or to clear a stuck take-up -- without saying that the enable 0->1 edge
     clears referenceLatched and phaseOffsetSteps (Ramps.c:766). The remedy is
     not optional: Ramps.c:1110 says a timed-out take-up is released ONLY by
     the enable 1->0 escape hatch, so pressing Cut again cannot clear it.
  2. The calibration drift notice called ANY nonzero change "a large change
     worth investigating" -- one step, 1.984 um, below the Z scale's own 5 um
     resolution.
  3. The axis screen will build a two-input SUM transform for the ELS Z axis,
     and the firmware is told ONE scale index, so the DRO would show the sum
     while the machine tracked one contributor.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from reflex.dispatchers.axis_transform import AxisTransform
from reflex.fsms.ui_controller import ElsUiController
from reflex.utils.devices import (
    ELS_CAL_ERR_ENABLED,
    ELS_CAL_MESSAGES,
    ELS_TAKEUP_ERR_TIMEOUT,
    ELS_TAKEUP_ERR_UNCONFIRMED,
    ELS_TAKEUP_MESSAGES,
    ELS_TAKEUP_TIMEOUT_LATCHED,
    takeup_failure_text,
)
from tests.fsms.test_ui_controller import (_make_collaborators, _make_x_axis,
                                           _make_z_axis, _pump)


# ── Item 1: messages that name what their remedy costs ──────────────────

def test_the_timeout_message_names_the_cost_only_when_there_is_one():
    """Gated, not always shown -- otherwise it is item 2 all over again.

    An operator with nothing latched loses nothing by re-engaging, and telling
    them they will is the same cry-wolf defect this same commit fixes in the
    drift notice.
    """
    plain = takeup_failure_text(ELS_TAKEUP_ERR_TIMEOUT, reference_latched=False)
    warned = takeup_failure_text(ELS_TAKEUP_ERR_TIMEOUT, reference_latched=True)

    assert plain == ELS_TAKEUP_MESSAGES[ELS_TAKEUP_ERR_TIMEOUT]
    assert warned == ELS_TAKEUP_TIMEOUT_LATCHED
    assert warned != plain
    assert "reference" in warned.lower(), (
        "the whole point of the variant is naming what re-engaging costs")
    assert "reference" not in plain.lower(), (
        "the un-latched operator has no reference to lose; saying otherwise "
        "is the cry-wolf defect this commit is fixing elsewhere")


def test_reference_latched_does_not_leak_into_the_other_codes():
    """Only TIMEOUT forces the destructive remedy.

    UNCONFIRMED is cleared by closing the half-nut and pressing Cut again --
    ui_controller.start_cut says so in as many words -- so it must keep its
    own text no matter what is latched.
    """
    for latched in (False, True):
        assert takeup_failure_text(ELS_TAKEUP_ERR_UNCONFIRMED, 0,
                                   reference_latched=latched) == \
            ELS_TAKEUP_MESSAGES[ELS_TAKEUP_ERR_UNCONFIRMED]


def test_the_wrong_way_branch_still_wins_over_a_latched_reference():
    """A negative delta is a wiring fault, and it outranks everything."""
    from reflex.utils.devices import ELS_TAKEUP_WRONG_WAY
    assert takeup_failure_text(ELS_TAKEUP_ERR_UNCONFIRMED, -7,
                               reference_latched=True) == ELS_TAKEUP_WRONG_WAY


def test_the_calibration_refusal_states_what_re_engaging_costs():
    msg = ELS_CAL_MESSAGES[ELS_CAL_ERR_ENABLED]
    low = msg.lower()
    assert "disengage" in low, "it still has to say what to do"
    assert "reference" in low and "phase offset" in low, (
        "the refusal's own remedy clears both (Ramps.c:766) and the operator "
        "has no way to see that from the machine")


def test_the_help_page_states_it_at_both_sites():
    """The help page gave the same destructive remedy twice.

    Read as text rather than parsed: this is documentation, and the check that
    matters is that neither passage can be followed without meeting the cost.
    """
    import reflex
    path = os.path.join(os.path.dirname(reflex.__file__), "help",
                        "els_backlash_cal.md")
    text = open(path, encoding="utf-8").read()
    assert "Disengage first." not in text, (
        "the bare calibration remedy is back, with no cost named")
    assert "To clear a stuck take-up, disengage and re-engage" not in text, (
        "the bare take-up remedy is back, with no cost named")
    assert "costs you the thread reference" in text
    assert "pressing Cut again will not" in text, (
        "the page must say why the cheap remedy does not work, or the "
        "operator will try it and conclude the machine is broken")


# ── Item 2: the drift notice needs a threshold ──────────────────────────

def _drift_text(drift, threshold=12):
    """Call the popup's formatter without standing a Popup up.

    It is a pure function of two collaborators, and building the widget drags
    in kv the mock GL backend cannot texture.
    """
    from reflex.components.home.els_backlash_cal_popup import BacklashCalPopup
    stub = SimpleNamespace(
        _cal=SimpleNamespace(drift_steps=drift),
        app=SimpleNamespace(els=SimpleNamespace(
            els_cal_drift_notice_steps=threshold)))
    return BacklashCalPopup._drift_text(stub)


def test_no_drift_says_nothing():
    assert _drift_text(0) == ""


@pytest.mark.parametrize("drift", [1, -1, 5, 12, -12])
def test_a_small_drift_is_reported_but_not_escalated(drift):
    """THE DEFECT, stated as a test.

    One step is 1.984 um on elspi -- below the Z scale's own 5 um resolution --
    and it used to be announced as "a large change is worth investigating".
    ElsCalFsm.drift_steps' docstring says non-zero is normal.
    """
    text = _drift_text(drift)
    assert str(abs(drift)) in text, "the number is still always reported"
    assert "investigat" not in text.lower(), (
        f"{drift} steps is inside the {12}-step spread this machine is held "
        f"to within a single run, so it is not distinguishable from the "
        f"measurement's own noise -- demanding an investigation for it is "
        f"how an operator learns to skip the message")


@pytest.mark.parametrize("drift", [13, -13, 400])
def test_a_real_drift_still_escalates(drift):
    text = _drift_text(drift)
    assert str(abs(drift)) in text
    assert "investigat" in text.lower(), (
        "past the within-run spread the change is real and the operator "
        "should be told to go and look")


def test_the_direction_survives_the_threshold():
    assert "more" in _drift_text(400)
    assert "less" in _drift_text(-400)


def test_a_zero_threshold_never_escalates_rather_than_always():
    """An uncommissioned machine must not be nagged.

    0 is what an unset property reads as, and the fail-open choice here is the
    opposite of els_cal_motion_thresh_counts', which fails CLOSED -- because
    that one gates MOTION and this one gates a sentence.
    """
    assert "investigat" not in _drift_text(9999, threshold=0).lower()


# ── Item 3: the ELS refuses a Z axis it cannot actually track ───────────

def _ctrl(z_transform):
    board, els = _make_collaborators(
        z_axis=_make_z_axis(transform=z_transform), x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    _pump()
    c._els_fsm = MagicMock(name="els_fsm")
    c._els_fsm.state = "stopped"
    c._els_fsm.may_enable.return_value = True
    return c


def test_a_single_input_z_engages_normally():
    c = _ctrl(AxisTransform.identity(1))
    c.engaged = False
    c.toggle_engage()
    assert c._els_fsm.enable.called, (
        "the guard must not refuse the ordinary machine")


def test_a_summed_z_is_refused_at_engage():
    """THE DEFECT. The DRO would show scale[1] + scale[2] while the firmware
    tracked scale[1] alone -- ElsFsm.set_scale_index pushes _primary_input(),
    i.e. contributions[0]. Every stop position, every take-up confirmation and
    the thread datum would be measured against a number the operator is not
    reading.
    """
    c = _ctrl(AxisTransform.sum(1, 2))
    c.engaged = False
    c.toggle_engage()
    assert not c._els_fsm.enable.called, (
        "engaging here arms the ELS against a position the firmware cannot "
        "compute")
    assert "scale" in c.notice_text.lower(), (
        f"the operator has to be told which setting to change; got "
        f"{c.notice_text!r}")


def test_the_guard_asks_for_ONE_input_rather_than_naming_SUM():
    """Written as "is it single-input", so a transform type added later is
    refused by default instead of inheriting the ELS's trust."""
    c = _ctrl(AxisTransform.identity(1))
    three = AxisTransform(contributions=(1, 2, 3),
                          transform_type=AxisTransform.identity(1).transform_type)
    c._els.get_z_axis.return_value.transform = three
    assert not c._els_z_is_single_input()


def test_no_z_axis_is_left_to_its_own_branch():
    """The missing-axis case has its own message and must keep it."""
    c = _ctrl(AxisTransform.identity(1))
    c._els.get_z_axis.return_value = None
    assert c._els_z_is_single_input() is True
