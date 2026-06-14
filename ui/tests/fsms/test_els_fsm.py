"""Tests for ElsFsm (the domain FSM coordinating the ELS stop block).

The domain FSM is a thin wrapper around state callbacks that issue HAL
writes. We exercise it with a MagicMock HAL and assert on HAL method
calls — the right writes happen in the right order, and mode-flag
inputs steer the conditional behavior.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rcp.fsms.els_fsm import ElsFsm


# ─── fixtures: mock collaborators with just enough surface for the FSM ─────

def _set_carriage(z, inp, mm):
    """Move the simulated carriage: update both the axis's display value
    AND the encoder counter (the FSM reads encoderCurrent for step_delta
    computation, not scaledPosition)."""
    z.scaledPosition = mm
    inp.encoderCurrent = int(mm)


def _make_z_axis(scaled_position=0.0, encoder_offset=0):
    """Minimal Z-axis mock. position_to_encoder maps mm → encoder counts
    one-to-one (offset configurable). _primary_input is needed for the
    cut path's scaleIndex write."""
    inp = SimpleNamespace(
        inputIndex=2,
        ratioNum=1, ratioDen=1,
        encoderCurrent=0,
    )
    axis = MagicMock()
    axis.scaledPosition = scaled_position
    axis.position_to_encoder.side_effect = lambda mm: int(mm) + encoder_offset
    axis._primary_input.return_value = inp
    return axis, inp


def _make_x_axis(encoder_current=0):
    inp = SimpleNamespace(encoderCurrent=encoder_current)
    axis = MagicMock()
    axis._primary_input.return_value = inp
    return axis, inp


def _make_spindle():
    return SimpleNamespace(syncRatioNum=1, syncRatioDen=1)


def _make_els(*, z_axis=None, x_axis=None, spindle=None,
                forward_stop=+1, els_backlash_steps=0,
                direction_sign=+1):
    els = MagicMock()
    els.get_z_axis.return_value = z_axis
    els.get_x_axis.return_value = x_axis
    els.get_spindle_axis.return_value = spindle or _make_spindle()
    els.stop_direction_value.return_value = forward_stop
    els.direction_sign.return_value = direction_sign
    els.els_backlash_steps = els_backlash_steps
    return els


def _make_board():
    board = MagicMock()
    board.connected = True
    board.servo = SimpleNamespace(ratioNum=1, ratioDen=1)
    return board


def _make_controller(*, stop_z=10.0, retract_z=20.0,
                     wizard_enabled=False, retract_enabled=False,
                     els_forward=True):
    return SimpleNamespace(
        stop_z=stop_z, retract_z=retract_z,
        wizard_enabled=wizard_enabled,
        retract_enabled=retract_enabled,
        els_forward=els_forward,
    )


@pytest.fixture
def domain():
    """Default rig: stop-only mode, default polarities, Z and X axes set."""
    z, _ = _make_z_axis()
    x, _ = _make_x_axis()
    return _build_fsm(z=z, x=x)


def _build_fsm(*, z=None, x=None, controller=None, hal=None,
               els_extra=None):
    if z is None:
        z, _ = _make_z_axis()
    if x is None:
        x, _ = _make_x_axis()
    if controller is None:
        controller = _make_controller()
    if hal is None:
        hal = MagicMock()
    els = _make_els(z_axis=z, x_axis=x, **(els_extra or {}))
    board = _make_board()
    fsm = ElsFsm(els=els, board=board, hal=hal, controller=controller)
    return fsm


# ─── enable / disable: stopped ↔ disabled ──────────────────────────────────

def test_initial_state_is_disabled(domain):
    assert domain.state == "disabled"


def test_enable_transitions_to_stopped(domain):
    domain.enable()
    assert domain.state == "stopped"


def test_disable_transitions_back_to_disabled(domain):
    domain.enable()
    domain.disable()
    assert domain.state == "disabled"


def test_on_enter_disabled_writes_set_enable_false():
    hal = MagicMock()
    fsm = _build_fsm(hal=hal)
    fsm.enable()    # disabled → stopped
    fsm.disable()   # stopped → disabled (fires on_enter_disabled)
    # The last set_enable(False) call should come from on_enter_disabled.
    hal.set_enable.assert_called_with(False)


# ─── stopped re-arm: hysteresis driven by mode flags ───────────────────────

def test_on_enter_stopped_arms_loose_hysteresis_in_stop_only_mode():
    hal = MagicMock()
    controller = _make_controller(wizard_enabled=False, retract_enabled=False)
    fsm = _build_fsm(hal=hal, controller=controller)
    fsm.enable()
    hal.set_hysteresis_loose.assert_called_once()
    hal.set_hysteresis_tight.assert_not_called()
    hal.set_enable.assert_called_with(True)


def test_on_enter_stopped_clears_active_flag():
    """Arming the stop must clear any stale active flag from a previous
    cycle so the UI doesn't show 'Stopped' immediately after engage."""
    hal = MagicMock()
    fsm = _build_fsm(hal=hal)
    fsm.enable()
    hal.set_active.assert_called_with(False)


def test_on_enter_stopped_arms_tight_hysteresis_when_retract_enabled():
    hal = MagicMock()
    controller = _make_controller(retract_enabled=True)
    fsm = _build_fsm(hal=hal, controller=controller)
    fsm.enable()
    hal.set_hysteresis_tight.assert_called_once()
    hal.set_hysteresis_loose.assert_not_called()


def test_on_enter_stopped_arms_tight_hysteresis_when_wizard_enabled():
    hal = MagicMock()
    controller = _make_controller(wizard_enabled=True)
    fsm = _build_fsm(hal=hal, controller=controller)
    fsm.enable()
    hal.set_hysteresis_tight.assert_called_once()


def test_on_enter_stopped_writes_stop_direction():
    hal = MagicMock()
    controller = _make_controller(els_forward=False)
    fsm = _build_fsm(hal=hal, controller=controller)
    fsm.enable()
    # stop_direction_value returns +1 in our mock regardless of forward;
    # what matters is that the HAL was told to write it.
    hal.set_stop_direction.assert_called_once_with(+1)


# ─── set_stop_z: HAL writes stopPosition + scaleIndex ──────────────────────

def test_set_stop_z_writes_encoder_position_to_hal():
    z, z_inp = _make_z_axis(encoder_offset=100)
    hal = MagicMock()
    fsm = _build_fsm(z=z, hal=hal)
    fsm.set_stop_z(42.0)
    # position_to_encoder(42.0) → 42 + 100 = 142
    hal.set_stop_position.assert_called_once_with(142)
    hal.set_scale_index.assert_called_with(z_inp.inputIndex)


# ─── cutting entry: thread geometry, backlash, set_active(False) ──────────

def test_on_enter_cutting_arms_and_writes_thread_geometry():
    z, z_inp = _make_z_axis()
    hal = MagicMock()
    controller = _make_controller(retract_enabled=True)
    fsm = _build_fsm(
        z=z, hal=hal, controller=controller,
        els_extra={"els_backlash_steps": 42},
    )
    # Place the carriage at retract_z so is_ready_to_cut → is_retracted → True.
    z.scaledPosition = controller.retract_z
    fsm.enable()       # → stopped (on_enter_stopped fires)
    hal.reset_mock()   # focus on cutting writes only
    fsm.cut()
    assert fsm.state == "cutting"
    hal.set_scale_index.assert_called_with(z_inp.inputIndex)
    hal.set_backlash_steps.assert_called_once_with(42)
    hal.set_active.assert_called_once_with(False)
    # Thread geometry comes from spindle/servo ratios; just verify writes happened.
    assert hal.set_thread_pitch_steps.called
    assert hal.set_z_counts_per_pitch.called


def test_cut_allowed_in_stop_only_mode_when_z_safe_of_stop():
    """In stop-only mode, cut is allowed when Z is on the safe side
    of stop_z (not past it in the cutting direction)."""
    z, _ = _make_z_axis(scaled_position=12.7)
    controller = _make_controller(stop_z=10.0, retract_enabled=False, els_forward=True)
    fsm = _build_fsm(z=z, controller=controller)
    # els_forward=True → cut_dir=+1, stop_z=10, z=12.7 → (12.7-10)*1 = 2.7 > 0 → NOT safe
    assert not fsm.is_ready_to_cut()
    # Move Z to safe side (below stop_z when cutting direction is +1)
    z.scaledPosition = 8.0
    assert fsm.is_ready_to_cut()


def test_cut_blocked_in_stop_only_mode_when_z_past_stop():
    """In stop-only mode, cut is blocked when Z has already overshot stop_z."""
    z, _ = _make_z_axis(scaled_position=15.0)
    controller = _make_controller(stop_z=10.0, retract_enabled=False, els_forward=True)
    fsm = _build_fsm(z=z, controller=controller)
    # cut_dir=+1, z=15, stop_z=10 → already past stop
    assert not fsm.is_ready_to_cut()


def test_cut_blocked_in_stop_only_mode_when_z_at_stop():
    """In stop-only mode, cut is blocked when Z is exactly at stop_z.
    The cut should only be allowed when Z is strictly before the stop
    position in the cutting direction."""
    z, _ = _make_z_axis(scaled_position=10.0)
    controller = _make_controller(stop_z=10.0, retract_enabled=False, els_forward=True)
    fsm = _build_fsm(z=z, controller=controller)
    # cut_dir=+1, z=10, stop_z=10 → exactly at stop → NOT ready
    assert not fsm.is_ready_to_cut()
    # Move Z to safe side
    z.scaledPosition = 9.9
    assert fsm.is_ready_to_cut()


# ─── retracting entry: encoder delta + step delta computed and pushed ──────

def test_on_enter_retracting_pushes_steps_to_go():
    """retract_z=20, stop_z=10, encoder offset 0:
       encoder_target = position_to_encoder(20) = 20
       encoder_current = 0 (z._primary_input().encoderCurrent default)
       enc_delta = 20 - 0 = 20
       step_delta = scale_counts_to_steps(20)
         Fraction(20) * 1/1 / 1/1 = 20; sign +1; magnitude 20 → +20"""
    z, _ = _make_z_axis()
    hal = MagicMock()
    controller = _make_controller(stop_z=10.0, retract_z=20.0)
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    fsm.enable()
    hal.reset_mock()
    fsm.retract()   # is_ready_to_retract: check_x_retract defaults False → allowed
    assert fsm.state == "retracting"
    hal.set_steps_to_go.assert_called_once_with(20)


# ─── retract backlash compensation ─────────────────────────────────────────

def test_first_retract_after_cut_adds_backlash_to_step_delta():
    """On the first retract after a cut, the nut is at the cut-side wall.
    The host must add backlash_steps in the retract direction so the
    carriage actually reaches retract_z instead of stopping short by the
    play-window distance."""
    z, inp = _make_z_axis()
    hal = MagicMock()
    controller = _make_controller(stop_z=10.0, retract_z=20.0,
                                  retract_enabled=True)
    fsm = _build_fsm(z=z, hal=hal, controller=controller,
                     els_extra={"els_backlash_steps": 5})
    # Walk through cut → stopped → retract so the cut entry clears the
    # flag and we test the first retract after.
    _set_carriage(z, inp, 20.0)
    fsm.enable()
    fsm.cut()
    fsm.stop_active()
    _set_carriage(z, inp, 10.0)   # carriage parked at stop_z after cut
    hal.reset_mock()
    fsm.retract()
    # enc_delta = 20 - 10 = 10 → raw step_delta = +10 (sign × ceil);
    # backlash of 5 applied in the same sign → +15.
    hal.set_steps_to_go.assert_called_once_with(15)


def test_self_loop_retract_does_not_add_backlash_twice():
    """After the first retract has consumed the play window, the nut sits
    at the retract-side wall. A retracting → retracting self-loop (which
    fires when the first retract lands short of retract_z) is in the
    same direction, so it must NOT add backlash again."""
    z, inp = _make_z_axis()
    hal = MagicMock()
    controller = _make_controller(stop_z=10.0, retract_z=20.0,
                                  retract_enabled=True)
    fsm = _build_fsm(z=z, hal=hal, controller=controller,
                     els_extra={"els_backlash_steps": 5})
    _set_carriage(z, inp, 20.0)   # at retract_z initially
    fsm.enable()
    fsm.cut()
    fsm.stop_active()
    _set_carriage(z, inp, 10.0)   # cut parked carriage at stop_z
    fsm.retract()                 # 1st: adds backlash
    # Simulate the carriage landing 2 mm short of retract_z, then a self-
    # loop retract (retract_done with is_retracted=False → same state).
    _set_carriage(z, inp, 18.0)
    hal.reset_mock()
    fsm.retract_done()
    # Self-loop landed back in retracting. enc_delta = 20 - 18 = 2 →
    # step_delta = +2 (no backlash compensation).
    hal.set_steps_to_go.assert_called_once_with(2)


def test_retract_backlash_resets_on_next_cut():
    """A new cut starts a fresh cycle: the next retract must again pre-
    consume the play window."""
    z, inp = _make_z_axis()
    hal = MagicMock()
    controller = _make_controller(stop_z=10.0, retract_z=20.0,
                                  retract_enabled=True)
    fsm = _build_fsm(z=z, hal=hal, controller=controller,
                     els_extra={"els_backlash_steps": 5})
    _set_carriage(z, inp, 20.0)
    fsm.enable()
    fsm.cut()
    fsm.stop_active()
    _set_carriage(z, inp, 10.0)
    fsm.retract()                 # 1st retract: backlash added
    # Land at retract_z so retract_done transitions retracting → stopped.
    _set_carriage(z, inp, 20.0)
    fsm.retract_done()
    assert fsm.state == "stopped"
    # Next cycle: cut → stop → retract again
    fsm.cut()                     # on_enter_cutting resets the flag
    fsm.stop_active()
    _set_carriage(z, inp, 10.0)
    hal.reset_mock()
    fsm.retract()
    # enc_delta = 10 → raw step_delta = +10; backlash applied again on
    # this fresh first-retract-of-cycle → +15.
    hal.set_steps_to_go.assert_called_once_with(15)


def test_retract_with_zero_backlash_setting_is_unchanged():
    """When the user hasn't configured backlash, the host commands the
    raw step_delta — no compensation."""
    z, _ = _make_z_axis()
    hal = MagicMock()
    controller = _make_controller(stop_z=10.0, retract_z=20.0)
    fsm = _build_fsm(z=z, hal=hal, controller=controller,
                     els_extra={"els_backlash_steps": 0})
    fsm.enable()
    hal.reset_mock()
    fsm.retract()
    hal.set_steps_to_go.assert_called_once_with(20)


# ─── is_retracted: position predicate semantics ────────────────────────────

def test_is_retracted_positive_span_threshold():
    z, _ = _make_z_axis()
    controller = _make_controller(stop_z=10.0, retract_z=20.0)
    fsm = _build_fsm(z=z, controller=controller)
    # z.scaledPosition < retract_z → not retracted yet
    z.scaledPosition = 15.0
    assert not fsm.is_retracted()
    # at or past retract_z → retracted
    z.scaledPosition = 20.0
    assert fsm.is_retracted()
    z.scaledPosition = 25.0
    assert fsm.is_retracted()


def test_is_retracted_negative_span():
    """retract_z < stop_z (retract direction is negative)."""
    z, _ = _make_z_axis()
    controller = _make_controller(stop_z=20.0, retract_z=10.0)
    fsm = _build_fsm(z=z, controller=controller)
    z.scaledPosition = 15.0
    assert not fsm.is_retracted()
    z.scaledPosition = 10.0
    assert fsm.is_retracted()
    z.scaledPosition = 5.0
    assert fsm.is_retracted()


def test_is_retracted_zero_span_is_only_true_at_target():
    z, _ = _make_z_axis()
    controller = _make_controller(stop_z=10.0, retract_z=10.0)
    fsm = _build_fsm(z=z, controller=controller)
    z.scaledPosition = 10.0
    assert fsm.is_retracted()
    z.scaledPosition = 10.1
    assert not fsm.is_retracted()


# ─── fault path: any state → alarm ──────────────────────────────────────────

@pytest.mark.parametrize("starting_state",
                         ["disabled", "stopped", "cutting", "retracting"])
def test_fault_from_any_state_transitions_to_alarm(starting_state):
    fsm = _build_fsm()
    fsm.fsm.set_state(starting_state)
    fsm.fault()
    assert fsm.state == "alarm"


# ─── _scale_counts_to_steps: rounding & sign semantics ─────────────────────

def test_scale_counts_to_steps_zero_is_zero():
    fsm = _build_fsm()
    assert fsm._scale_counts_to_steps(0) == 0


def test_scale_counts_to_steps_rounds_magnitude_away_from_zero():
    """If 1 scale count converts to < 1 servo step, we still command 1
    step. Truncation toward zero would leave the retract short."""
    # Make scale resolution 10× finer than servo: scale=mm/10, servo=mm/1.
    # 1 scale count = 0.1 mm = 0.1 step → magnitude ceil = 1.
    z = MagicMock()
    z.scaledPosition = 0.0
    inp = SimpleNamespace(inputIndex=2, ratioNum=1, ratioDen=10, encoderCurrent=0)
    z._primary_input.return_value = inp
    z.position_to_encoder.side_effect = lambda mm: int(mm)
    fsm = _build_fsm(z=z)
    assert fsm._scale_counts_to_steps(1) == 1
    assert fsm._scale_counts_to_steps(-1) == -1
