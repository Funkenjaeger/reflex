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

from reflex.fsms.ui_controller import ElsUiController


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
    # Scale by 1000 so the display↔encoder round-trip preserves sub-mm decimals
    # (a real leadscrew has finer resolution than 1 count/mm).
    axis.position_to_encoder.side_effect = lambda mm: round(mm * 1000) + encoder_offset
    axis.scaled_from_encoder.side_effect = lambda enc: (enc - encoder_offset) / 1000.0
    inp = SimpleNamespace(
        inputIndex=2, ratioNum=1, ratioDen=1, encoderCurrent=0,
    )
    axis._primary_input.return_value = inp
    return axis


def _make_x_axis(scaled_position=0.0, encoder_offset=0):
    axis = MagicMock()
    axis.scaledPosition = scaled_position
    axis.position_to_encoder.side_effect = lambda mm: round(mm * 1000) + encoder_offset
    axis.scaled_from_encoder.side_effect = lambda enc: (enc - encoder_offset) / 1000.0
    axis._primary_input.return_value = SimpleNamespace(
        encoderCurrent=0, ratioNum=1, ratioDen=1, inputIndex=3,
    )
    return axis


def _make_collaborators(*, z_axis=None, x_axis=None, connected=False):
    board = MagicMock()
    board.connected = connected
    # ElsFsm._safety_margin_display reads leadScrewPitch / leadScrewPitchIn /
    # ratioNum / ratioDen off board.servo. leadScrewPitch=0.0 keeps the margin
    # at zero, matching these tests' assumption that "safe side of stop_z"
    # means strictly-before with no extra clearance band.
    board.servo = SimpleNamespace(
        ratioNum=1, ratioDen=1,
        leadScrewPitch=0.0, leadScrewPitchIn=False,
        servoMode=0,
        # ElsFsm.on_enter_disabled calls board.servo.stop_feed() as a safety stop.
        stop_feed=lambda: None,
    )
    # _safety_margin_display converts the margin to display units via
    # board.formats.factor.
    board.formats = SimpleNamespace(factor=1)
    els = MagicMock()
    els.get_z_axis.return_value = z_axis
    els.get_x_axis.return_value = x_axis
    els.get_spindle_axis.return_value = SimpleNamespace(
        syncRatioNum=1, syncRatioDen=1,
    )
    els.stop_direction_value.return_value = +1
    els.direction_sign.return_value = +1
    els.els_backlash_steps = 0
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
    ctrl.commit_standalone_stop_z(1.0)  # set a stop_z so the wizard can advance
    ctrl._ui_fsm.action()             # set_stop_z → set_retract_z
    assert ctrl._ui_fsm.state == "set_retract_z"
    ctrl.wizard_enabled = False
    _pump()
    assert ctrl._ui_fsm.state == "in_cycle.waiting_to_cut"


# ─── Action-button gating in non-wizard cycle states ───────────────────────

def test_stop_only_action_allowed_with_valid_stop_z(ctrl):
    """In stop-only mode, UI FSM auto-advances to in_cycle.waiting_to_cut at
    startup (no Start button). After engage, the action button is enabled
    when Z is on the safe side of stop_z."""
    # Z=0, stop_z=0 → Z at stop → blocked by _z_safe_for_cut.
    # Set stop_z so Z is on the safe side (cut_dir=+1, so Z < stop_z is safe).
    ctrl.commit_standalone_stop_z(10.0)
    _engage(ctrl)
    # Non-wizard mode auto-advances to in_cycle.waiting_to_cut at startup,
    # so no need to call start() here.
    assert ctrl._ui_fsm.state == "in_cycle.waiting_to_cut"
    # Z=0 < stop_z=10 → safe for cut (cut_dir=+1, cutting toward +Z)
    assert ctrl.action_allowed is True
    assert ctrl.instruction_text == "Ready to cut"


def test_stop_retract_action_blocked_until_retract_z_set(ctrl):
    _engage(ctrl)
    ctrl.retract_enabled = True
    _pump()
    # retract_z=0, stop_z=0 → retract_z > stop_z is False → retract_z_valid False
    assert ctrl.action_allowed is False
    assert "Start Z" in ctrl.instruction_text


def test_switching_to_stop_only_rescues_a_cycle_parked_in_waiting_to_retract(ctrl):
    """Real-machine regression (2026-08-01).

    The operator was in stop+retract, the cycle sat in waiting_to_retract, and
    they switched to stop-only. Nothing moved the cycle back: stop-only's
    cut_done routes to waiting_to_cut but that never runs, and
    _poll_carriage_retracted (the only other way out) returns early when retract
    is disabled. The bar was left showing a permanently-disabled "Retract"
    reading "Enter Start Z to retract" — which is what "the action button never
    enables in stop-only mode" actually was. There is no operator action that
    recovers it, so this must be repaired on the mode switch itself.
    """
    _engage(ctrl)
    ctrl.retract_enabled = True
    _pump()
    ctrl._ui_fsm.fsm.set_state("in_cycle.waiting_to_retract")
    _pump()

    ctrl.retract_enabled = False
    _pump()

    assert ctrl._ui_fsm.state == "in_cycle.waiting_to_cut"
    assert ctrl.action_button_text == "Cut"


def test_stop_retract_action_allows_after_setting_retract_z(ctrl):
    _engage(ctrl)
    ctrl.retract_enabled = True
    ctrl.commit_standalone_stop_z(5.0)
    ctrl.commit_standalone_retract_z(10.0)
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
    """In stop-only mode, the UI FSM auto-advances to in_cycle.waiting_to_cut
    at startup. Before engage, action is blocked by 'not engaged'. After
    engage, action is enabled when Z is on the safe side of stop_z."""
    assert ctrl.action_allowed is False  # not engaged
    ctrl.commit_standalone_stop_z(10.0)  # Z=0 < stop_z=10 → safe for cut
    _engage(ctrl)
    assert ctrl.engaged is True
    assert ctrl._ui_fsm.state == "in_cycle.waiting_to_cut"
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
    ctrl.commit_standalone_stop_z(10.0)  # Z=0 < stop_z=10 → safe for cut
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
    # _validate_retract_z is direction-agnostic: with zero safety margin
    # (leadScrewPitch=0 in the fixture) it requires only that retract_z be a
    # non-zero distance from stop_z — retracting in the negative direction is
    # valid (mirrors ElsFsm.is_retracted's negative-span handling).
    c.commit_standalone_stop_z(10.0)
    c.commit_standalone_retract_z(10.0)  # equal to stop_z → zero span → invalid
    assert c.retract_z_valid is False
    assert c.retract_z_error
    c.commit_standalone_retract_z(5.0)  # below stop_z, but non-zero span → valid
    assert c.retract_z_valid is True
    assert c.retract_z_error == ""
    c.commit_standalone_retract_z(20.0)  # above stop_z → also valid
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
        c.commit_standalone_stop_z(v)
        c.start_dia = v
        assert c.stop_z_valid is True
        assert c.start_dia_valid is True


# ─── commit_standalone_* ───────────────────────────────────────────────────

def test_commit_standalone_stop_z_anchors_to_encoder():
    """Entering Stop Z freezes it to the physical encoder (converts once) and
    sets the derived display value + validity — but the action button remains
    the sole initiator of motion (nothing is fed here)."""
    z = _make_z_axis(encoder_offset=0)
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(),
                                     connected=True)
    c = ElsUiController(els=els, board=board)
    z.position_to_encoder.reset_mock()
    c.commit_standalone_stop_z(42.0)
    assert c.stop_z == 42.0
    assert c.stop_z_valid is True
    assert c.stop_z_encoder is not None          # anchored to a physical encoder
    z.position_to_encoder.assert_called_once_with(42.0)


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


# ─── Regression: engage gating when no Z axis is assigned ──────────────────

def test_toggle_engage_without_z_axis_does_not_raise_and_stays_disengaged(caplog):
    """Regression for the on_enter_stopped crash.

    With no ELS Z axis mapped, els.get_z_axis() returns None. toggle_engage()
    must refuse to engage (logging a warning) rather than driving the domain
    FSM into 'stopped', whose on_enter_stopped would dereference the missing
    axis and crash with "'NoneType' object has no attribute 'scaledPosition'".
    """
    board, els = _make_collaborators(z_axis=None, x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    _pump()
    assert c.engaged is False
    assert c._els_fsm.state == "disabled"

    import logging
    with caplog.at_level(logging.WARNING):
        c.toggle_engage()      # must NOT raise
    _pump()

    # Engage was refused: FSM stayed disabled, engaged stayed False.
    assert c._els_fsm.state == "disabled"
    assert c.engaged is False
    assert any("no Z axis" in rec.getMessage() for rec in caplog.records)


def test_toggle_engage_with_z_axis_drives_fsm_out_of_disabled():
    """The companion to the no-axis case: when a valid Z axis IS present,
    toggle_engage() still engages the domain FSM (out of 'disabled')."""
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    _pump()
    assert c._els_fsm.state == "disabled"
    c.toggle_engage()
    _pump()
    assert c._els_fsm.state != "disabled"
    assert c._els_fsm.state == "stopped"
    assert c.engaged is True


def test_toggle_engage_engages_after_z_axis_assigned_late():
    """get_z_axis() is resolved live on every toggle, so an axis mapped AFTER
    the controller is built still lets the operator engage (no stale None)."""
    board, els = _make_collaborators(z_axis=None, x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    _pump()
    c.toggle_engage()          # refused: no axis yet
    _pump()
    assert c._els_fsm.state == "disabled"
    # Operator maps the ELS Z axis in setup after construction.
    els.get_z_axis.return_value = _make_z_axis()
    c.toggle_engage()
    _pump()
    assert c._els_fsm.state == "stopped"
    assert c.engaged is True


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


def test_on_action_button_clicked_without_x_axis_does_not_raise():
    """Operator mapped Z (so the wizard runs) but left X unmapped. Reaching the
    diameter step and pressing the action button must NOT dereference a None
    axis (mirrors the engage-time Z guard); it should warn and stay put."""
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=None)
    c = ElsUiController(els=els, board=board)
    c.wizard_enabled = True
    _pump()
    _engage(c)
    c._ui_fsm.fsm.set_state("set_start_dia")
    c._apply_policy()
    c.on_action_button_clicked()       # X axis is None → must not raise
    assert c._ui_fsm.state == "set_start_dia"   # did not advance


def test_on_action_button_clicked_in_waiting_to_cut_enters_cutting(ctrl):
    """Non-wizard cycle: action button drives waiting_to_cut → cutting."""
    z = ctrl._els.get_z_axis()
    z.scaledPosition = 0.0
    ctrl.commit_standalone_stop_z(10.0)  # Z=0 < stop_z=10 → safe for cut
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
    c.commit_standalone_stop_z(4.0)  # keypad-style entry; bypasses live capture
    c.try_advance_wizard()
    assert c._ui_fsm.state == "set_retract_z"


def test_try_advance_wizard_noop_in_non_wizard_cycle_state(ctrl):
    """In non-wizard mode the UI FSM lives in in_cycle.waiting_to_cut.
    try_advance_wizard must be a no-op there — otherwise a long-press
    capture would auto-fire the action transition and start a cut,
    bypassing the requirement that the action button is the only
    motion initiator."""
    ctrl.commit_standalone_stop_z(10.0)  # Z=0 < stop_z=10 → safe for cut
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
    c.commit_standalone_retract_z(5.0)
    c.commit_standalone_stop_z(1.0)
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
    c.commit_standalone_retract_z(5.0)
    c.commit_standalone_stop_z(1.0)
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
    c.commit_standalone_retract_z(5.0); c.commit_standalone_stop_z(1.0)
    _pump()
    c._ui_fsm.fsm.set_state("in_cycle.waiting_to_retract")
    c._apply_policy()
    assert c.depth_reached is True
    c._ui_fsm.cancel()  # → idle (fires after_state_change → _apply_policy via Clock)
    _pump()
    assert c.depth_reached is False


def test_poll_carriage_retracted_noop_in_stop_only_mode():
    """_poll_carriage_retracted must not fire carriage_unretracted in
    stop-only mode — there is no retract threshold to cross, and
    is_retracted() would return False (span=0, z!=0), incorrectly
    transitioning the UI FSM to waiting_to_retract."""
    z = _make_z_axis(scaled_position=12.7)
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    _pump()
    # Non-wizard mode auto-advances to in_cycle.waiting_to_cut at startup.
    assert c._ui_fsm.state == "in_cycle.waiting_to_cut"
    assert c.retract_enabled is False
    # Simulate a board update tick
    c._poll_carriage_retracted()
    # Should still be in waiting_to_cut, not transitioned to waiting_to_retract
    assert c._ui_fsm.state == "in_cycle.waiting_to_cut"


def test_action_button_disabled_when_z_past_stop_in_stop_only_mode():
    """In stop-only mode, the action button should be disabled when Z
    is past stop_z in the cutting direction, and re-enable when Z
    moves to the safe side."""
    z = _make_z_axis(scaled_position=12.7)
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(), connected=True)
    c = ElsUiController(els=els, board=board)
    # Commit a stop_z (=0.0) so stop_z_valid is True and the button gate is
    # exercised on Z position alone, not the "not set" guard (audit H1).
    c.commit_standalone_stop_z(0.0)
    _pump()
    # Non-wizard mode auto-advances to in_cycle.waiting_to_cut at startup.
    assert c._ui_fsm.state == "in_cycle.waiting_to_cut"
    # Engage domain FSM so action button is not blocked by "not engaged"
    c.toggle_engage()
    _pump()
    # Z=12.7, stop_z=0.0, cut_dir=+1 → Z past stop → button disabled
    assert c._ui_fsm.state == "in_cycle.waiting_to_cut"
    assert c.action_allowed is False
    # Move Z to safe side
    z.scaledPosition = -1.0
    c._poll_apply_policy()
    assert c.action_allowed is True


def test_stop_z_invalid_until_set_blocks_cut():
    """Audit H1: a never-set stop_z is invalid, so a cut is blocked until the
    operator commits one — and re-mapping the ELS Z axis invalidates it again."""
    z = _make_z_axis(scaled_position=-5.0)
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(), connected=True)
    c = ElsUiController(els=els, board=board)
    _pump()
    # Never set → invalid → cut gated even though engaged with Z on the safe side.
    assert c.stop_z_valid is False
    c.toggle_engage()
    _pump()
    assert c._ui_fsm.state == "in_cycle.waiting_to_cut"
    assert c.action_allowed is False, "cut allowed with no stop_z set"
    # Operator sets a stop_z → valid → the stop_z gate no longer blocks.
    c.commit_standalone_stop_z(-10.0)
    _pump()
    assert c.stop_z_valid is True
    # A change to the stored value's frame of reference (Z-axis remap) invalidates
    # it again so a stale target can't be silently reused.
    c.invalidate_stop_z()
    _pump()
    assert c.stop_z_valid is False


def test_wizard_set_stop_z_commits_even_when_value_unchanged():
    """Review finding 4: capturing the live Z at the wizard 'Set' step must commit
    stop_z even when it equals the current value (e.g. zero-at-the-shoulder → Set
    with Z reading 0.0), so the wizard can advance instead of dead-ending."""
    z = _make_z_axis(scaled_position=0.0)  # capture equals the 0.0 default
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(), connected=True)
    c = ElsUiController(els=els, board=board)
    c.wizard_enabled = True
    _pump()
    c._ui_fsm.start()                       # idle → set_stop_z
    c.toggle_engage()                       # engage so the Set button isn't gated
    _pump()
    assert c._ui_fsm.state == "set_stop_z"
    assert c.stop_z_valid is False
    c.on_action_button_clicked()            # Set: capture Z=0.0 (== default)
    assert c.stop_z_valid is True
    assert c._ui_fsm.state == "set_retract_z", "wizard did not advance past Set"


def test_reframe_warns_on_offset_change_not_units():
    """A committed target whose displayed value changes because of a DRO re-zero
    / offset change (factor unchanged) fires the notify per the setting; the
    physical encoder is untouched."""
    z = _make_z_axis()
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(), connected=True)
    els.stop_z_reframe_notify = "warn"
    c = ElsUiController(els=els, board=board)
    c.commit_standalone_stop_z(50.0)
    enc = c.stop_z_encoder
    assert c.stop_z == 50.0
    assert c.targets_reframed_warn is False
    # Offset change: the SAME encoder now renders 10 higher (a re-zero).
    z.scaled_from_encoder.side_effect = lambda e: e / 1000.0 + 10.0
    c._poll_reframe_targets()
    assert c.stop_z == 60.0                 # display re-referenced
    assert c.stop_z_encoder == enc          # physical target unchanged
    assert c.targets_reframed_warn is True  # warned


def test_reframe_preserves_retract_margin_validity():
    """Regression: a DRO re-zero must NOT flip retract_z_valid. The reframe poll
    re-validates only AFTER both Z mirrors are in the new frame — validating in a
    mixed frame (stop_z reframed, retract_z not) would compute a garbage span and
    silently enable a too-close retract (disabling the safety margin)."""
    z = _make_z_axis()
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(), connected=True)
    board.servo.leadScrewPitch = 2.0        # margin ≈ 2.0 * 1.1 = 2.2 mm
    board.servo.leadScrewPitchIn = False
    c = ElsUiController(els=els, board=board)
    c.retract_enabled = True
    c.commit_standalone_stop_z(0.0)
    c.commit_standalone_retract_z(1.0)      # span 1.0 < 2.2 → too close → invalid
    assert c.retract_z_valid is False
    # Re-zero by +10 (both encoders shift equally; physical span is unchanged).
    z.scaled_from_encoder.side_effect = lambda e: e / 1000.0 + 10.0
    c._poll_reframe_targets()
    assert c.retract_z_valid is False, "re-zero wrongly enabled a too-close retract"


def test_reframe_units_change_is_silent():
    """A units switch (factor changes) just re-renders — it never notifies (it's
    on the operator to know a literal value doesn't carry across units)."""
    z = _make_z_axis()
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(), connected=True)
    els.stop_z_reframe_notify = "warn"
    c = ElsUiController(els=els, board=board)
    c.commit_standalone_stop_z(50.0)
    board.formats.factor = 2.0              # units changed
    z.scaled_from_encoder.side_effect = lambda e: e / 500.0
    c._poll_reframe_targets()
    assert c.targets_reframed_warn is False  # units → silent


def test_reframe_notice_clears_on_reset_of_value():
    """Re-setting a target addresses the re-reference — the warn flag clears."""
    z = _make_z_axis()
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(), connected=True)
    els.stop_z_reframe_notify = "warn"
    c = ElsUiController(els=els, board=board)
    c.commit_standalone_stop_z(50.0)
    z.scaled_from_encoder.side_effect = lambda e: e / 1000.0 + 10.0
    c._poll_reframe_targets()
    assert c.targets_reframed_warn is True
    c.commit_standalone_stop_z(60.0)          # operator re-sets
    assert c.targets_reframed_warn is False


def test_reset_reframed_targets_invalidates_and_clears():
    """The confirm bar's 'Reset both' invalidates the re-referenced targets
    (→ must re-set) and clears the notice."""
    z = _make_z_axis()
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(), connected=True)
    els.stop_z_reframe_notify = "confirm"
    c = ElsUiController(els=els, board=board)
    c.commit_standalone_stop_z(50.0)
    assert c.stop_z_valid is True
    z.scaled_from_encoder.side_effect = lambda e: e / 1000.0 + 10.0
    c._poll_reframe_targets()
    assert c.reframe_confirm_pending is True
    c.reset_reframed_targets()
    assert c.stop_z_valid is False            # invalidated → "--", must re-set
    assert c.reframe_confirm_pending is False


def test_confirm_feed_enable_is_noop_when_already_on():
    """Review finding 6: if the feed turned on between the confirm dialog opening
    and the operator confirming, request_feed_enable(confirmed=True) must NOT
    toggle it back off."""
    board, els = _make_collaborators(connected=True)
    c = ElsUiController(els=els, board=board)
    board.servo.servoMode = 1               # feed enabled meanwhile
    assert c.request_feed_enable(confirmed=True) is True
    assert board.servo.servoMode == 1       # still on — not toggled off


def test_action_button_disabled_when_z_at_stop_in_stop_only_mode():
    """In stop-only mode, the action button should be disabled when Z
    is exactly at stop_z — the cut should only start when Z is strictly
    before the stop position in the cutting direction."""
    z = _make_z_axis(scaled_position=10.0)
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis(), connected=True)
    c = ElsUiController(els=els, board=board)
    c.commit_standalone_stop_z(10.0)
    _pump()
    c.toggle_engage()
    _pump()
    # Non-wizard mode auto-advances to in_cycle.waiting_to_cut at startup.
    assert c._ui_fsm.state == "in_cycle.waiting_to_cut"
    # Z=10.0, stop_z=10.0 → exactly at stop → button disabled
    assert c.action_allowed is False
    # Move Z to safe side (below stop_z when cut_dir=+1)
    z.scaledPosition = 9.9
    c._poll_apply_policy()
    assert c.action_allowed is True


def test_fsm_cut_blocked_when_z_at_stop_in_stop_only_mode():
    """The domain FSM's is_ready_to_cut should return False when Z
    is exactly at stop_z, preventing the cut transition."""
    z = _make_z_axis(scaled_position=10.0)
    board, els = _make_collaborators(z_axis=z, x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    c.commit_standalone_stop_z(10.0)
    _pump()
    # Z=10.0, stop_z=10.0 → exactly at stop → not ready to cut
    assert not c._els_fsm.is_ready_to_cut()
    # Move Z to safe side
    z.scaledPosition = 9.9
    assert c._els_fsm.is_ready_to_cut()
