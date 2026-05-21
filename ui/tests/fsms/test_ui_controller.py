"""Tests for ElsUiController — the Kivy-facing layer that owns both
FSMs, exposes properties to kv, and routes operator intents.

The controller composes the domain FSM, UI FSM, and HAL, so these
tests intentionally drive the real ElsFsm + ElsUiFsm together with
MagicMock'd hardware. Where a unit-level test exists in test_ui_fsm
or test_els_fsm, this file covers the controller's *coordination*
responsibility: action gating, auto-advance on mode toggle, validation
bindings, and operator-intent methods.
"""
import os
# Mock window / GL before kivy.clock is imported anywhere.
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kivy.clock import Clock

from rcp.fsms.ui_controller import ElsUiController


def _pump(n=3):
    """Drain any Clock.schedule_once callbacks pending after a state
    change. Kivy normally pumps these on each frame; in tests we tick
    manually so derived properties (action_button_text, instruction_text)
    are observable synchronously."""
    for _ in range(n):
        Clock.tick()


def _engage(ctrl):
    """Toggle the domain FSM into 'stopped' and pump until `engaged` is
    mirrored back into the controller. Most tests of cycle-state gating
    need this — the bar refuses to enable Start/Stop or action until the
    operator has hit Engage, since the underlying FSM triggers have no
    valid source from 'disabled'."""
    ctrl.toggle_engage()
    _pump()


def _make_z_axis(scaled_position=0.0, encoder_offset=0):
    axis = MagicMock()
    axis.scaledPosition = scaled_position
    axis.position_to_encoder.side_effect = lambda mm: int(mm) + encoder_offset
    inp = SimpleNamespace(
        inputIndex=2, ratioNum=1, ratioDen=1, encoderCurrent=0,
    )
    axis._primary_input.return_value = inp
    return axis


def _make_x_axis(scaled_position=0.0):
    axis = MagicMock()
    axis.scaledPosition = scaled_position
    axis._primary_input.return_value = SimpleNamespace(
        encoderCurrent=0, ratioNum=1, ratioDen=1, inputIndex=3,
    )
    return axis


def _make_collaborators(*, z_axis=None, x_axis=None, connected=False):
    board = MagicMock()
    board.connected = connected
    board.servo = SimpleNamespace(ratioNum=1, ratioDen=1)
    els = MagicMock()
    els.get_z_axis.return_value = z_axis
    els.get_x_axis.return_value = x_axis
    els.get_spindle_axis.return_value = SimpleNamespace(
        syncRatioNum=1, syncRatioDen=1,
    )
    els.stop_direction_value.return_value = +1
    els.scale_to_step_sign.return_value = -1
    els.els_backlash_steps = 0
    els.cut_polarity_inverted = False
    els.stop_polarity_inverted = False
    els.z_scale_step_inverted = False
    return board, els


@pytest.fixture
def ctrl():
    """Default rig: non-wizard mode, Z and X axes set, board disconnected."""
    board, els = _make_collaborators(
        z_axis=_make_z_axis(), x_axis=_make_x_axis(),
    )
    c = ElsUiController(els=els, board=board)
    _pump()
    return c


# ─── Initial state: non-wizard auto-advances to in_cycle.waiting_to_cut ────

def test_initial_state_is_waiting_to_cut_in_non_wizard_mode(ctrl):
    assert ctrl._ui_fsm.state == "in_cycle.waiting_to_cut"


def test_initial_action_button_text_reflects_state(ctrl):
    # Non-wizard, board disconnected → start_stop_enabled is False but
    # the action button still reads "Cut" from the UI policy.
    assert ctrl.action_button_text == "Cut"


# ─── Auto-advance on mode toggle ───────────────────────────────────────────

def test_toggling_wizard_on_returns_to_idle(ctrl):
    assert ctrl._ui_fsm.state.startswith("in_cycle")
    ctrl.wizard_enabled = True
    _pump()
    assert ctrl._ui_fsm.state == "idle"


def test_toggling_wizard_off_returns_to_in_cycle(ctrl):
    ctrl.wizard_enabled = True
    _pump()
    ctrl.wizard_enabled = False
    _pump()
    assert ctrl._ui_fsm.state == "in_cycle.waiting_to_cut"


def test_toggling_wizard_off_mid_wizard_cancels_and_enters_in_cycle(ctrl):
    ctrl.wizard_enabled = True
    _pump()
    ctrl._ui_fsm.start()              # idle → set_stop_z
    ctrl._ui_fsm.action()             # set_stop_z → set_retract_z
    assert ctrl._ui_fsm.state == "set_retract_z"
    ctrl.wizard_enabled = False
    _pump()
    assert ctrl._ui_fsm.state == "in_cycle.waiting_to_cut"


# ─── Action-button gating in non-wizard cycle states ───────────────────────

def test_stop_only_action_allowed_with_valid_stop_z(ctrl):
    _engage(ctrl)
    # Default stop_z=0; validator considers it valid (no criteria).
    assert ctrl.action_allowed is True
    assert ctrl.instruction_text == "Ready to cut"


def test_stop_retract_action_blocked_until_retract_z_set(ctrl):
    _engage(ctrl)
    ctrl.retract_enabled = True
    _pump()
    # retract_z=0, stop_z=0 → retract_z > stop_z is False → retract_z_valid False
    assert ctrl.action_allowed is False
    assert "Start Z" in ctrl.instruction_text


def test_stop_retract_action_allows_after_setting_retract_z(ctrl):
    _engage(ctrl)
    ctrl.retract_enabled = True
    ctrl.stop_z = 5.0
    ctrl.retract_z = 10.0
    _pump()
    assert ctrl.retract_z_valid is True
    assert ctrl.action_allowed is True
    assert ctrl.instruction_text == "Ready to cut"


# ─── Disengaged: Start/Stop and action are gated off entirely ──────────────

def test_action_blocked_when_not_engaged(ctrl):
    """Without Engage, the underlying FSM triggers (cut, retract) have no
    valid source — pressing the action button used to raise MachineError.
    Now the button is disabled until the operator engages."""
    assert ctrl.engaged is False
    assert ctrl.action_allowed is False
    assert "Engage" in ctrl.instruction_text


def test_engaging_enables_action(ctrl):
    assert ctrl.action_allowed is False
    _engage(ctrl)
    assert ctrl.engaged is True
    assert ctrl.action_allowed is True


def test_start_stop_disabled_when_not_engaged_in_wizard_mode(ctrl):
    """The wizard's Start button must also be gated on engagement — without
    it, pressing Start would walk the wizard through set_* and confirm but
    crash when the cycle finally fires cut() against a disabled FSM."""
    ctrl.wizard_enabled = True
    _pump()
    # idle policy says can_stop=True, but engaged=False blocks it.
    assert ctrl.engaged is False
    assert ctrl.start_stop_enabled is False


def test_disengaging_mid_cycle_disables_action(ctrl):
    """If the operator disengages mid-cycle, the action button must
    immediately disable so they can't press it and crash the FSM."""
    _engage(ctrl)
    assert ctrl.action_allowed is True
    ctrl.toggle_engage()   # back to disabled
    _pump()
    assert ctrl.engaged is False
    assert ctrl.action_allowed is False


# ─── Validators ────────────────────────────────────────────────────────────

def test_retract_z_validator():
    board, els = _make_collaborators(
        z_axis=_make_z_axis(), x_axis=_make_x_axis(),
    )
    c = ElsUiController(els=els, board=board)
    # retract_z > stop_z required
    c.stop_z = 10.0
    c.retract_z = 5.0
    assert c.retract_z_valid is False
    assert c.retract_z_error
    c.retract_z = 20.0
    assert c.retract_z_valid is True
    assert c.retract_z_error == ""


def test_stop_dia_validator():
    board, els = _make_collaborators(
        z_axis=_make_z_axis(), x_axis=_make_x_axis(),
    )
    c = ElsUiController(els=els, board=board)
    c.start_dia = 10.0
    c.stop_dia = 15.0       # invalid: stop_dia must be < start_dia
    assert c.stop_dia_valid is False
    c.stop_dia = 5.0
    assert c.stop_dia_valid is True


def test_stop_z_and_start_dia_validators_always_pass():
    """No range criteria — they only require a numeric assignment."""
    board, els = _make_collaborators(
        z_axis=_make_z_axis(), x_axis=_make_x_axis(),
    )
    c = ElsUiController(els=els, board=board)
    for v in (-100.0, 0.0, 42.0):
        c.stop_z = v
        c.start_dia = v
        assert c.stop_z_valid is True
        assert c.start_dia_valid is True


# ─── commit_standalone_* ───────────────────────────────────────────────────

def test_commit_standalone_stop_z_only_sets_property():
    """The action button is the sole initiator of motion; entering Stop Z
    must not push anything to the HAL or trigger an encoder lookup."""
    z = _make_z_axis(encoder_offset=0)
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(),
                                     connected=True)
    c = ElsUiController(els=els, board=board)
    z.position_to_encoder.reset_mock()
    c.commit_standalone_stop_z(42.0)
    assert c.stop_z == 42.0
    z.position_to_encoder.assert_not_called()


def test_commit_standalone_retract_z_only_sets_property(ctrl):
    # retract_z is consumed at retract time by ElsFsm.on_enter_retracting,
    # so the commit method should not call the HAL.
    ctrl.commit_standalone_retract_z(7.5)
    assert ctrl.retract_z == 7.5


# ─── Operator intents: engage / start-stop ─────────────────────────────────

def test_toggle_engage_drives_domain_fsm(ctrl):
    # `engaged` mirrors the domain FSM via a bus subscription marshaled
    # through Clock.schedule_once — pump between toggles so toggle_engage
    # sees the updated property and routes to disable() rather than enable().
    assert ctrl._els_fsm.state == "disabled"
    ctrl.toggle_engage()
    _pump()
    assert ctrl._els_fsm.state == "stopped"
    assert ctrl.engaged is True
    ctrl.toggle_engage()
    _pump()
    assert ctrl._els_fsm.state == "disabled"
    assert ctrl.engaged is False


def test_start_stop_button_in_wizard_idle_starts_wizard(ctrl):
    ctrl.wizard_enabled = True
    _pump()
    assert ctrl._ui_fsm.state == "idle"
    ctrl.on_start_stop_button_clicked()
    assert ctrl._ui_fsm.state == "set_stop_z"


def test_start_stop_button_in_wizard_state_cancels(ctrl):
    ctrl.wizard_enabled = True
    _pump()
    ctrl.on_start_stop_button_clicked()
    assert ctrl._ui_fsm.state == "set_stop_z"
    ctrl.on_start_stop_button_clicked()
    assert ctrl._ui_fsm.state == "idle"


# ─── on_action_button_clicked: captures live axis position in wizard ─────

def test_on_action_button_clicked_captures_stop_z_in_wizard():
    z = _make_z_axis(scaled_position=12.34)
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    c.wizard_enabled = True
    _pump()
    _engage(c)
    c._ui_fsm.start()                  # idle → set_stop_z
    c.on_action_button_clicked()       # captures live z → advances FSM
    assert c.stop_z == 12.34
    assert c._ui_fsm.state == "set_retract_z"


def test_on_action_button_clicked_captures_diameters_in_wizard():
    x = _make_x_axis(scaled_position=22.0)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=x)
    c = ElsUiController(els=els, board=board)
    c.wizard_enabled = True
    _pump()
    _engage(c)
    c._ui_fsm.fsm.set_state("set_start_dia")
    c._apply_policy()   # set_state bypasses broadcast; refresh action_allowed
    c.on_action_button_clicked()
    assert c.start_dia == 22.0
    assert c._ui_fsm.state == "set_stop_dia"
    x.scaledPosition = 5.0
    c.on_action_button_clicked()
    assert c.stop_dia == 5.0
    assert c._ui_fsm.state == "confirm"


def test_on_action_button_clicked_in_waiting_to_cut_enters_cutting(ctrl):
    """Non-wizard cycle: action button drives waiting_to_cut → cutting."""
    z = ctrl._els.get_z_axis()
    z.scaledPosition = 0.0   # at retract_z (default 0) → is_retracted True
    ctrl.toggle_engage()
    _pump()
    assert ctrl._ui_fsm.state == "in_cycle.waiting_to_cut"
    assert ctrl.action_allowed
    ctrl.on_action_button_clicked()
    assert ctrl._ui_fsm.state == "in_cycle.cutting"


# ─── X-position predicates: is_inner flips the comparison ──────────────────

def test_x_clear_of_start_dia_od_work_requires_x_greater_than_start_dia():
    x = _make_x_axis(scaled_position=15.0)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=x)
    c = ElsUiController(els=els, board=board)
    c.is_inner = False
    c.start_dia = 10.0
    assert c._x_clear_of_start_dia() is True   # 15 > 10
    x.scaledPosition = 5.0
    assert c._x_clear_of_start_dia() is False  # 5 > 10 False


def test_x_clear_of_start_dia_id_work_requires_x_less_than_start_dia():
    x = _make_x_axis(scaled_position=5.0)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=x)
    c = ElsUiController(els=els, board=board)
    c.is_inner = True
    c.start_dia = 10.0
    assert c._x_clear_of_start_dia() is True   # 5 < 10
    x.scaledPosition = 15.0
    assert c._x_clear_of_start_dia() is False  # 15 < 10 False


def test_x_clear_of_start_dia_missing_x_axis_assumes_clear():
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=None)
    c = ElsUiController(els=els, board=board)
    assert c._x_clear_of_start_dia() is True


def test_x_reached_stop_dia_od_vs_id():
    x = _make_x_axis(scaled_position=4.0)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=x)
    c = ElsUiController(els=els, board=board)
    # OD work: stop_dia is the smaller, terminal diameter (tool moving in)
    c.is_inner = False
    c.stop_dia = 5.0
    assert c._x_reached_stop_dia() is True   # 4 <= 5
    x.scaledPosition = 6.0
    assert c._x_reached_stop_dia() is False  # 6 <= 5 False
    # ID work: stop_dia is the larger, terminal diameter (tool moving out)
    c.is_inner = True
    c.stop_dia = 5.0
    x.scaledPosition = 6.0
    assert c._x_reached_stop_dia() is True   # 6 >= 5
    x.scaledPosition = 4.0
    assert c._x_reached_stop_dia() is False


# ─── try_advance_wizard: respects action_allowed and state ─────────────────

def test_try_advance_wizard_advances_when_in_matching_state():
    z = _make_z_axis(scaled_position=0.0)
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    c.wizard_enabled = True
    _pump()
    _engage(c)
    c._ui_fsm.start()
    c.stop_z = 4.0  # keypad-style entry; bypasses live capture
    c.try_advance_wizard()
    assert c._ui_fsm.state == "set_retract_z"


def test_try_advance_wizard_noop_in_non_wizard_cycle_state(ctrl):
    """In non-wizard mode the UI FSM lives in in_cycle.waiting_to_cut.
    try_advance_wizard must be a no-op there — otherwise a long-press
    capture would auto-fire the action transition and start a cut,
    bypassing the requirement that the action button is the only
    motion initiator."""
    _engage(ctrl)
    assert ctrl._ui_fsm.state == "in_cycle.waiting_to_cut"
    assert ctrl.action_allowed is True
    ctrl.try_advance_wizard()
    # Must still be in waiting_to_cut — no auto-cut.
    assert ctrl._ui_fsm.state == "in_cycle.waiting_to_cut"


def test_try_advance_wizard_noop_when_action_not_allowed():
    """Threading + X-still-at-depth in waiting_to_retract blocks the action."""
    x = _make_x_axis(scaled_position=4.0)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=x)
    c = ElsUiController(els=els, board=board)
    c.wizard_enabled = True
    c.is_threading = True
    c.is_inner = False
    c.start_dia = 10.0
    c.stop_dia = 5.0
    c.retract_z = 5.0
    c.stop_z = 1.0
    _pump()
    # set_state bypasses the FSM's after_state_change broadcast, so call
    # _apply_policy() explicitly to recompute action_allowed for the new state.
    c._ui_fsm.fsm.set_state("in_cycle.waiting_to_retract")
    c._apply_policy()
    assert c.action_allowed is False
    c.try_advance_wizard()
    assert c._ui_fsm.state == "in_cycle.waiting_to_retract"


# ─── depth_reached latch (informational) ───────────────────────────────────

def test_depth_reached_latches_in_waiting_to_retract_when_x_reaches_stop():
    """The latch turns on the first time the cut visits stop_dia and stays
    on through the rest of the cycle until the operator returns to idle."""
    x = _make_x_axis(scaled_position=5.0)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=x)
    c = ElsUiController(els=els, board=board)
    c.wizard_enabled = True
    c.is_inner = False
    c.start_dia = 10.0
    c.stop_dia = 5.0
    c.retract_z = 5.0
    c.stop_z = 1.0
    _pump()
    assert not c.depth_reached
    c._ui_fsm.fsm.set_state("in_cycle.waiting_to_retract")
    c._apply_policy()
    assert c.depth_reached is True
    # The latch survives wandering back to waiting_to_cut.
    c._ui_fsm.fsm.set_state("in_cycle.waiting_to_cut")
    c._apply_policy()
    assert c.depth_reached is True


def test_depth_reached_clears_when_returning_to_idle():
    x = _make_x_axis(scaled_position=5.0)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=x)
    c = ElsUiController(els=els, board=board)
    c.wizard_enabled = True
    c.start_dia = 10.0; c.stop_dia = 5.0
    c.retract_z = 5.0; c.stop_z = 1.0
    _pump()
    c._ui_fsm.fsm.set_state("in_cycle.waiting_to_retract")
    c._apply_policy()
    assert c.depth_reached is True
    c._ui_fsm.cancel()  # → idle (fires after_state_change → _apply_policy via Clock)
    _pump()
    assert c.depth_reached is False
