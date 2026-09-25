"""The one set of disengage rules, and the firmware's word on the result.

2026-09-25 08:32, on the lathe: an in-app update failed in 11 s because the
controller refuses to reboot into its bootloader while an ELS job is engaged
(Ramps.c elsBootCommandTick), and the operator had left one engaged-idle
overnight. The Update screen now offers to disengage -- through the SAME rules
the ADV bar's Disengage button obeys, which is what this file pins:

  * disengage_refusal() is the rule set, and toggle_engage goes through
    disengage(), which asks it -- so the button and the Update screen cannot
    disagree about when disengaging is allowed;
  * firmware_els_enable() is the CONTROLLER's report of elsStop.enable, and a
    fabricated read (no snapshot) is None, never "released";
  * after a UI-side disengage, reconnecting does not re-arm the job (the
    journal after the 08:32 failure showed a re-assert of engaged-idle).

Fixture idiom is test_ui_controller.py's: the real controller over MagicMock'd
hardware, driven by hand.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

from unittest.mock import MagicMock, patch

import pytest

from reflex.fsms.ui_controller import (DISENGAGE_REFUSED_CYCLE,
                                       DISENGAGE_REFUSED_SYNC, ElsUiController)
from reflex.utils.notices import NOTICE_INFO, NOTICE_WARNING
from tests.fsms.test_ui_controller import (_engage, _make_collaborators,
                                           _make_x_axis, _make_z_axis, _pump)


@pytest.fixture
def ctrl():
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    _pump()
    return c


def engaged_idle(c, *, sync=False, spindle=False):
    """Engaged, not in a cycle -- the 08:32 state is sync off, spindle stopped."""
    _engage(c)
    assert c.engaged and c._els_fsm.state == "stopped", "precondition: engaged-idle"
    c._board.servo.servoMode = 1 if sync else 0
    c._els.spindle_is_running = spindle
    return c


def in_a_cycle(c):
    """A retract running: the domain FSM would allow disable() from here, so
    only the in_cycle rule (the kv's `is_running`) stands in the way."""
    engaged_idle(c)
    c._ui_fsm.fsm.set_state("in_cycle.retracting")
    c._apply_policy()
    assert c.in_cycle and c._els_fsm.may_disable(), "precondition: only in_cycle refuses"
    return c


# ─── the rules ────────────────────────────────────────────────────────────────

def test_nothing_to_refuse_when_not_engaged(ctrl):
    assert ctrl.engaged is False
    assert ctrl.disengage_refusal() is None


def test_engaged_idle_with_sync_off_may_disengage(ctrl):
    """The 08:32 case: engaged-idle, spindle stopped, sync off."""
    engaged_idle(ctrl)
    assert ctrl.disengage_refusal() is None


def test_sync_armed_with_the_spindle_stopped_may_disengage(ctrl):
    """Operator decision 2026-08-17: with the spindle stopped nothing can move,
    so the refusal would be pure friction."""
    engaged_idle(ctrl, sync=True, spindle=False)
    assert ctrl.disengage_refusal() is None


def test_sync_armed_with_the_spindle_turning_names_sync_enable(ctrl):
    engaged_idle(ctrl, sync=True, spindle=True)
    assert ctrl.disengage_refusal() == DISENGAGE_REFUSED_SYNC
    assert "Sync Enable" in DISENGAGE_REFUSED_SYNC


def test_a_running_cycle_names_the_cycle(ctrl):
    in_a_cycle(ctrl)
    assert ctrl.disengage_refusal() == DISENGAGE_REFUSED_CYCLE
    assert "Stop the cycle" in DISENGAGE_REFUSED_CYCLE


# ─── disengage() obeys them, and toggle_engage goes through it ───────────────

def test_disengage_disables_the_domain_fsm(ctrl):
    engaged_idle(ctrl)
    assert ctrl.disengage() is None
    assert ctrl._els_fsm.state == "disabled"
    _pump()
    assert ctrl.engaged is False


def test_a_refused_disengage_changes_nothing_and_says_why(ctrl):
    engaged_idle(ctrl, sync=True, spindle=True)
    assert ctrl.disengage() == DISENGAGE_REFUSED_SYNC
    assert ctrl._els_fsm.state == "stopped"
    assert ctrl.notice_severity == NOTICE_WARNING
    assert ctrl.notice_text == DISENGAGE_REFUSED_SYNC


def test_a_running_cycle_is_not_disengaged(ctrl):
    in_a_cycle(ctrl)
    assert ctrl.disengage() == DISENGAGE_REFUSED_CYCLE
    assert ctrl._els_fsm.state == "stopped"
    assert ctrl.notice_severity == NOTICE_WARNING


def test_a_stale_disengage_tap_is_info(ctrl):
    """The double-tap race guard keeps its severity and its words."""
    engaged_idle(ctrl)
    ctrl._els_fsm = MagicMock()
    ctrl._els_fsm.may_disable.return_value = False
    ctrl._els_fsm.state = "disabled"
    assert ctrl.disengage() == "Disengage ignored — ELS is disabled"
    ctrl._els_fsm.disable.assert_not_called()
    assert ctrl.notice_severity == NOTICE_INFO


def test_the_button_goes_through_the_same_path(ctrl):
    """toggle_engage on an engaged controller IS disengage(): one rule set."""
    engaged_idle(ctrl)
    with patch.object(ctrl, "disengage", wraps=ctrl.disengage) as spy:
        ctrl.toggle_engage()
    spy.assert_called_once_with()
    assert ctrl._els_fsm.state == "disabled"


def test_the_button_refuses_a_running_cycle_too(ctrl):
    """New with the shared rules: the kv greyed the button on is_running, but
    toggle_engage itself allowed disable() from 'retracting'."""
    in_a_cycle(ctrl)
    ctrl.toggle_engage()
    assert ctrl._els_fsm.state == "stopped"


# ─── the controller's word ──────────────────────────────────────────────────

@pytest.mark.parametrize("snapshot, expected", [
    ({"enable": 1}, True),
    ({"enable": 0}, False),
    ({}, None),        # no snapshot this tick: a FABRICATED 0, never "released"
])
def test_firmware_els_enable_reads_the_snapshot_honestly(ctrl, snapshot, expected):
    ctrl._board.els_stop_values = snapshot
    assert ctrl.firmware_els_enable() is expected


# ─── reconnect after a UI-side disengage does not re-arm ─────────────────────

def test_reconnect_after_a_ui_disengage_clears_rather_than_re_arms(ctrl):
    """After the 08:32 failure the journal showed `re-asserted engaged-idle
    (armed=True)` on reconnect -- the FSM was still 'stopped'. After a
    disengage it is 'disabled', and reconcile takes the clear branch."""
    engaged_idle(ctrl)
    assert ctrl.disengage() is None
    hal = ctrl._els_fsm.hal = MagicMock(wraps=ctrl._els_fsm.hal)
    with patch.object(ctrl._els_fsm, "arm_idle_stop") as arm:
        ctrl._els_fsm.reconcile_firmware_on_connect()
    arm.assert_not_called()
    hal.set_enable.assert_called_with(False)
