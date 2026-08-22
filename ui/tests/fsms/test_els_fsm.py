"""Tests for ElsFsm (the domain FSM coordinating the ELS stop block).

The domain FSM is a thin wrapper around state callbacks that issue HAL
writes. We exercise it with a MagicMock HAL and assert on HAL method
calls — the right writes happen in the right order, and mode-flag
inputs steer the conditional behavior.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from reflex.fsms.els_fsm import ElsFsm


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


def _make_els(*, z_axis=None, x_axis=None, spindle=None, els_backlash_steps=0):
    els = MagicMock()
    els.get_z_axis.return_value = z_axis
    els.get_x_axis.return_value = x_axis
    els.get_spindle_axis.return_value = spindle or _make_spindle()
    # Mirror reflex.dispatchers.els.ElsDispatcher exactly: stop_direction_value
    # is -1 for forward, +1 for reverse; direction_sign is the inverse. These
    # MUST be side_effect (not a fixed return_value) so cut_dir tracks whichever
    # els_forward each test's controller actually carries — a pinned return
    # value here is what let the mock drift out of sync with production and
    # go undetected (it silently ignored the els_forward argument entirely).
    els.stop_direction_value.side_effect = lambda els_forward: -1 if els_forward else 1
    els.direction_sign.side_effect = lambda els_forward: 1 if els_forward else -1
    els.els_backlash_steps = els_backlash_steps
    return els


def _make_servo(ratio_num=1, ratio_den=1, lead_screw_pitch=0.0,
                lead_screw_pitch_in=False):
    """Mock ServoDispatcher surface used by ElsFsm.

    _scale_counts_to_steps / push_thread_geometry read ratioNum/ratioDen;
    _safety_margin_display additionally reads leadScrewPitch /
    leadScrewPitchIn. Default leadScrewPitch=0.0 keeps the safety margin at
    zero so existing geometry/step assertions stay exact — tests that care
    about the margin set it explicitly.
    """
    return SimpleNamespace(
        ratioNum=ratio_num, ratioDen=ratio_den,
        leadScrewPitch=lead_screw_pitch,
        leadScrewPitchIn=lead_screw_pitch_in,
        servoMode=0,
        # ElsFsm.on_enter_disabled calls board.servo.stop_feed() as a safety stop.
        stop_feed=lambda: None,
    )


def _make_board(servo=None):
    board = MagicMock()
    board.connected = True
    board.servo = servo if servo is not None else _make_servo()
    # _safety_margin_display reads board.formats.factor (display-unit scale).
    board.formats = SimpleNamespace(factor=1)
    return board


def _make_controller(*, stop_z=10.0, retract_z=20.0,
                     wizard_enabled=False, retract_enabled=False,
                     els_forward=True, is_threading=False):
    return SimpleNamespace(
        stop_z=stop_z, retract_z=retract_z,
        # Frozen encoder counts the FSM now writes to firmware. The mock z_axis's
        # position_to_encoder is identity (int(mm)), so mirror that here.
        stop_z_encoder=int(stop_z), retract_z_encoder=int(retract_z),
        wizard_enabled=wizard_enabled,
        retract_enabled=retract_enabled,
        els_forward=els_forward,
        # on_enter_cutting branches on is_threading to decide whether to push
        # thread geometry vs. clear it; default to feed (non-threading) mode.
        is_threading=is_threading,
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
    # z on the safe side (above stop_z=10) for the default els_forward=True
    # so arm_idle_stop succeeds and set_enable(True) is actually issued —
    # the default z=0.0 from _build_fsm is past the stop for forward.
    z, _ = _make_z_axis(scaled_position=15.0)
    hal = MagicMock()
    controller = _make_controller(wizard_enabled=False, retract_enabled=False)
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    fsm.enable()
    hal.set_hysteresis_loose.assert_called_once()
    hal.set_hysteresis_tight.assert_not_called()
    hal.set_enable.assert_called_with(True)


@pytest.mark.parametrize("els_forward, safe_z", [(True, 15.0), (False, 5.0)])
def test_on_enter_stopped_arms_in_stopped_state(els_forward, safe_z):
    """When engaging with Z on the safe side, ELS should arm in STOPPED
    state (active=1 before enable=1) so sync motion is paused until Cut.

    Real convention: cut_dir = stop_direction_value(els_forward) is -1 when
    forward, so the safe side (diff <= 0) is z_pos ABOVE stop_z=10 for
    forward and BELOW it for reverse — the two directions are mirror images
    of each other around stop_z.
    """
    hal = MagicMock()
    z, _ = _make_z_axis(scaled_position=safe_z)
    controller = _make_controller(els_forward=els_forward)
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    fsm.enable()
    # active should be set True (stopped state), not False
    hal.set_active.assert_called_with(True)
    hal.set_enable.assert_called_with(True)


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


@pytest.mark.parametrize("els_forward, expected_sign", [(True, -1), (False, +1)])
def test_on_enter_stopped_writes_stop_direction(els_forward, expected_sign):
    """stop_direction_value mirrors production: -1 forward, +1 reverse. Both
    signs are exercised so a mock pinned to one value (as it used to be)
    cannot silently pass."""
    hal = MagicMock()
    controller = _make_controller(els_forward=els_forward)
    fsm = _build_fsm(hal=hal, controller=controller)
    fsm.enable()
    hal.set_stop_direction.assert_called_once_with(expected_sign)


@pytest.mark.parametrize("els_forward, safe_z", [(True, 50.0), (False, 30.0)])
def test_on_enter_stopped_writes_stop_position_and_arms_on_engage(els_forward, safe_z):
    """When entering stopped from disabled (enable trigger), ELS should be
    armed with a fresh stopPosition from controller.stop_z, and active=1
    so it starts in STOPPED state. safe_z is on the true safe side of
    stop_z=42 for each direction (above for forward, below for reverse)."""
    z, _ = _make_z_axis(scaled_position=safe_z)
    hal = MagicMock()
    controller = _make_controller(stop_z=42.0, els_forward=els_forward)  # frozen stop_z_encoder = 42
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    fsm.enable()  # disabled → stopped (fires on_enter_stopped with _engaging=True)
    # stopPosition is written from the operator's FROZEN stop encoder.
    hal.set_stop_position.assert_called_with(controller.stop_z_encoder)
    hal.set_enable.assert_called_with(True)
    # active should be set True (stopped state), NOT cleared to False
    hal.set_active.assert_called_once_with(True)


def test_on_enter_stopped_does_not_arm_when_returning_from_cut():
    """When entering stopped from cutting (stop_active trigger), ELS should
    NOT be re-armed — it's already armed from before."""
    z, _ = _make_z_axis()
    hal = MagicMock()
    controller = _make_controller(retract_enabled=True)
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    # Go through: disabled → stopped (enable) → cutting → stopped (stop_active)
    z.scaledPosition = controller.retract_z  # satisfy is_ready_to_cut
    fsm.enable()
    hal.reset_mock()
    fsm.cut()
    hal.reset_mock()
    fsm.stop_active()  # cutting → stopped (_engaging=False)
    # set_stop_position should NOT be called (no re-arm on return from cut)
    hal.set_stop_position.assert_not_called()
    # But direction and hysteresis should still be updated
    hal.set_stop_direction.assert_called_once()
    hal.set_hysteresis_tight.assert_called_once()


@pytest.mark.parametrize("els_forward, past_stop_z", [(True, 5.0), (False, 15.0)])
def test_on_enter_stopped_arms_even_when_z_past_stop(els_forward, past_stop_z):
    """Engaging with Z past the stop ARMS, exactly like engaging clear of it.

    Inverted 2026-08-17: a refusal lived here, justified by a heritage
    comment claiming arming past the stop would fire ELS immediately and
    bank a takeup — false for the active-before-enable write order
    (els_arm_past_stop_test in reflex-fw pins the firmware side). The
    refusal was itself the defect: it silently left the engaged machine
    with no hold and sync armed, and produced round 2's misleading "no
    stop set" feed dialog with the carriage parked at the shoulder.

    "Past stop" flips sides with direction: below stop_z=10 for forward
    (cut_dir=-1), above stop_z=10 for reverse (cut_dir=+1) — both must arm.
    """
    z, _ = _make_z_axis(scaled_position=past_stop_z)
    hal = MagicMock()
    controller = _make_controller(stop_z=10.0, els_forward=els_forward)
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    fsm.enable()
    hal.set_stop_position.assert_called_once()
    hal.set_active.assert_called_once_with(True)
    hal.set_enable.assert_called_once_with(True)


def test_on_enter_stopped_does_not_arm_when_no_stop_committed():
    """Engaging before any stop is set must leave ELS DISARMED.

    push_stop_to_firmware() is a no-op with nothing committed, so writing
    `enable` here would arm ELS against whatever stopPosition the firmware still
    holds from a previous session. It also made the controller's
    feed_without_armed_stop() report "armed", silently skipping the no-stop feed
    confirmation — observed on the real machine, which armed at engage while the
    bar still showed a stop of "--"."""
    hal = MagicMock()
    controller = _make_controller()
    controller.stop_z_encoder = None
    fsm = _build_fsm(hal=hal, controller=controller)
    fsm.enable()
    hal.set_stop_position.assert_not_called()
    hal.set_enable.assert_not_called()
    hal.set_active.assert_not_called()
    # Direction / hysteresis are still applied — those aren't the armed stop.
    hal.set_stop_direction.assert_called_once()


@pytest.mark.parametrize("els_forward, safe_z", [(True, 50.0), (False, 30.0)])
def test_arm_idle_stop_arms_once_the_operator_sets_a_stop_while_engaged(els_forward, safe_z):
    """Engaging before setting a stop is the normal order of operations, so the
    arm has to be retried when the stop is committed (the controller calls this
    from _propagate_stop_z_to_firmware) — otherwise the operator sits engaged
    with no protection until they press Cut.

    safe_z is on the true safe side of the stop committed below (stop_z=42):
    above for forward (cut_dir=-1), below for reverse (cut_dir=+1). It dates
    from when arm_idle_stop refused past-the-stop arming (a mis-pinned
    direction mock once made a safe position look past the stop and masked
    its own inversion); the positional refusal was deleted 2026-08-17, so
    safe_z no longer affects the outcome — kept for the direction coverage.
    """
    z, _ = _make_z_axis(scaled_position=safe_z)
    hal = MagicMock()
    controller = _make_controller(els_forward=els_forward)
    controller.stop_z_encoder = None
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    fsm.enable()
    hal.reset_mock()

    controller.stop_z = 42.0
    controller.stop_z_encoder = 42
    assert fsm.arm_idle_stop() is True
    hal.set_stop_position.assert_called_with(42)
    hal.set_active.assert_called_once_with(True)
    hal.set_enable.assert_called_once_with(True)


# ─── set_stop_z: HAL writes stopPosition + scaleIndex ──────────────────────

def test_set_stop_z_writes_frozen_encoder_to_hal():
    z, z_inp = _make_z_axis()
    hal = MagicMock()
    controller = _make_controller(stop_z=42.0)  # frozen stop_z_encoder = 42
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    fsm.push_stop_to_firmware()  # writes the operator's frozen encoder
    hal.set_stop_position.assert_called_once_with(controller.stop_z_encoder)
    hal.set_scale_index.assert_called_with(z_inp.inputIndex)


# ─── cutting entry: thread geometry, backlash, set_active(False) ──────────

def test_on_enter_cutting_arms_and_writes_thread_geometry():
    z, z_inp = _make_z_axis()
    hal = MagicMock()
    # is_threading=True so on_enter_cutting takes the thread-geometry branch
    # (pushes pitch/counts-per-pitch and the real backlash_steps); the feed
    # branch would instead zero them.
    controller = _make_controller(retract_enabled=True, is_threading=True)
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


def test_on_enter_cutting_in_turning_keeps_backlash_and_signed_polarity():
    """Turning (2026-08-21): the PITCH is cleared so the firmware never
    phase-corrects a turning pass, but the pre-cut take-up now runs on every
    pass, so backlashSteps is written unchanged and zCountsPerPitch carries the
    Z polarity SIGN the firmware derives the take-up direction from. Until
    2026-08-21 all three were zeroed here and every turning pass ran ungated.
    """
    z, z_inp = _make_z_axis()
    z_inp.ratioNum = -1          # reversed Z scale wiring: mm-per-count < 0
    hal = MagicMock()
    controller = _make_controller(retract_enabled=True, is_threading=False)
    fsm = _build_fsm(
        z=z, hal=hal, controller=controller,
        els_extra={"els_backlash_steps": 42},
    )
    z.scaledPosition = controller.retract_z
    fsm.enable()
    hal.reset_mock()
    fsm.cut()
    assert fsm.state == "cutting"
    hal.set_thread_pitch_steps.assert_called_once_with(0.0)
    (zcpp,), _ = hal.set_z_counts_per_pitch.call_args
    assert zcpp < 0, "the Z polarity sign must reach the firmware in turning"
    hal.set_backlash_steps.assert_called_once_with(42)
    hal.set_active.assert_called_once_with(False)


# stop_direction_value(els_forward) is -1 for forward, +1 for reverse
# (reflex/dispatchers/els.py), so cut_dir = -1 when els_forward=True. With
# stop_z=10 and zero safety margin, diff = (z_pos - stop_z) * cut_dir is
# negative (safe) when z_pos > stop_z for forward, and z_pos < stop_z for
# reverse — the two directions mirror around stop_z.
@pytest.mark.parametrize("els_forward, unsafe_z, safe_z", [
    (True, 8.0, 12.7),
    (False, 12.7, 8.0),
])
def test_cut_allowed_in_stop_only_mode_when_z_safe_of_stop(els_forward, unsafe_z, safe_z):
    """In stop-only mode, cut is allowed when Z is on the safe side
    of stop_z (not past it in the cutting direction)."""
    z, _ = _make_z_axis(scaled_position=unsafe_z)
    controller = _make_controller(stop_z=10.0, retract_enabled=False, els_forward=els_forward)
    fsm = _build_fsm(z=z, controller=controller)
    assert not fsm.is_ready_to_cut()
    z.scaledPosition = safe_z
    assert fsm.is_ready_to_cut()


@pytest.mark.parametrize("els_forward, past_stop_z", [(True, 5.0), (False, 15.0)])
def test_cut_blocked_in_stop_only_mode_when_z_past_stop(els_forward, past_stop_z):
    """In stop-only mode, cut is blocked when Z has already overshot stop_z."""
    z, _ = _make_z_axis(scaled_position=past_stop_z)
    controller = _make_controller(stop_z=10.0, retract_enabled=False, els_forward=els_forward)
    fsm = _build_fsm(z=z, controller=controller)
    assert not fsm.is_ready_to_cut()


@pytest.mark.parametrize("els_forward, safe_z", [(True, 10.1), (False, 9.9)])
def test_cut_blocked_in_stop_only_mode_when_z_at_stop(els_forward, safe_z):
    """In stop-only mode, cut is blocked when Z is exactly at stop_z.
    The cut should only be allowed when Z is strictly before the stop
    position in the cutting direction. diff=0 at the stop regardless of
    cut_dir's sign, so this half is direction-invariant; only the
    "move to the safe side" half differs by direction."""
    z, _ = _make_z_axis(scaled_position=10.0)
    controller = _make_controller(stop_z=10.0, retract_enabled=False, els_forward=els_forward)
    fsm = _build_fsm(z=z, controller=controller)
    assert not fsm.is_ready_to_cut()
    z.scaledPosition = safe_z
    assert fsm.is_ready_to_cut()


# ─── retracting entry: encoder delta + step delta computed and pushed ──────

def test_on_enter_retracting_pushes_steps_to_go():
    """retract_z=20, stop_z=10, encoder offset 0:
       encoder_target = position_to_encoder(20) = 20
       encoder_current = 0 (z._primary_input().encoderCurrent default)
       enc_delta = 0 - 20 = -20  (inverted: DRO and servo have opposite polarity)
       step_delta = scale_counts_to_steps(-20)
         Fraction(-20) * 1/1 / 1/1 = -20; sign -1; magnitude 20 → -20"""
    z, _ = _make_z_axis()
    hal = MagicMock()
    controller = _make_controller(stop_z=10.0, retract_z=20.0)
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    fsm.enable()
    hal.reset_mock()
    fsm.retract()   # is_ready_to_retract: check_x_retract defaults False → allowed
    assert fsm.state == "retracting"
    hal.set_steps_to_go.assert_called_once_with(-20)


def test_retract_allowed_when_x_encoder_is_negative():
    """The X gate is opt-in via check_x_retract, which is off.

    Regression: Python's conditional-expression precedence made the original
    one-liner parse as `(not check_x_retract or ...) if inside else (x_pos >=
    safe_x)`, so with `inside` False the opt-in flag was ignored and the gate
    reduced to a raw-encoder-count `x_pos >= 0`. Every retract was refused on a
    machine whose cross-slide encoder sat below its power-on zero, which parked
    the bar in "Retracting…" forever. The other retract tests all used the
    default x encoder of 0, which passes `>= 0` — they could not catch this.
    """
    x, _ = _make_x_axis(encoder_current=-4200)
    fsm = _build_fsm(x=x)
    fsm.enable()
    assert fsm.is_ready_to_retract() is True
    fsm.retract()
    assert fsm.state == "retracting"


def test_retract_refused_when_no_retract_target_committed():
    """With no committed Start Z there is nothing to move to. The FSM must stay
    in 'stopped': entering 'retracting' and then refusing to move binds no move
    poller, and retract_done is only ever published by that poller — so the
    state would be terminal."""
    hal = MagicMock()
    controller = _make_controller()
    controller.retract_z_encoder = None
    fsm = _build_fsm(hal=hal, controller=controller)
    fsm.enable()
    hal.reset_mock()
    fsm.retract()
    assert fsm.state == "stopped"
    hal.set_steps_to_go.assert_not_called()


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
    # enc_delta = 10 - 20 = -10 → raw step_delta = -10 (retract direction);
    # backlash of 5 applied in same sign → -15.
    hal.set_steps_to_go.assert_called_once_with(-15)


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
    # Self-loop landed back in retracting. enc_delta = 18 - 20 = -2 →
    # step_delta = -2 (retract direction, no backlash compensation).
    hal.set_steps_to_go.assert_called_once_with(-2)


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
    # enc_delta = 10 - 20 = -10 → raw step_delta = -10; backlash applied again on
    # this fresh first-retract-of-cycle → -15.
    hal.set_steps_to_go.assert_called_once_with(-15)


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
    hal.set_steps_to_go.assert_called_once_with(-20)


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


# ─── Regression: z_axis / x_axis resolve live (not cached at construction) ──

def test_z_axis_reflects_later_axis_assignment():
    """Axis mapping can change after the FSM is built (config load, setup
    edits) and defaults to unassigned until the operator maps it. ElsFsm.z_axis
    must dereference els.get_z_axis() on every access, not cache it at __init__
    — otherwise a None captured at construction would persist and crash
    on_enter_stopped."""
    hal = MagicMock()
    els = _make_els(z_axis=None, x_axis=None)
    board = _make_board()
    controller = _make_controller()
    fsm = ElsFsm(els=els, board=board, hal=hal, controller=controller)

    # Built with no axis → property reflects None.
    assert fsm.z_axis is None

    # Operator maps the ELS Z axis after construction.
    z, _ = _make_z_axis(scaled_position=3.0)
    els.get_z_axis.return_value = z
    assert fsm.z_axis is z
    assert fsm.z_axis.scaledPosition == 3.0

    # Re-mapping again is also reflected (proves no one-shot caching).
    z2, _ = _make_z_axis(scaled_position=9.0)
    els.get_z_axis.return_value = z2
    assert fsm.z_axis is z2


def test_x_axis_reflects_later_axis_assignment():
    """Companion to z_axis: the X-axis property is also resolved live."""
    hal = MagicMock()
    els = _make_els(z_axis=None, x_axis=None)
    fsm = ElsFsm(els=els, board=_make_board(), hal=hal,
                 controller=_make_controller())
    assert fsm.x_axis is None
    x, _ = _make_x_axis()
    els.get_x_axis.return_value = x
    assert fsm.x_axis is x


# ─── connect-time reconciliation of firmware-retained elsStop state ────────
# Firmware retains elsStop.{enable, active, stopPosition} across app restarts
# (observed on the real machine 2026-08-01). reconcile_firmware_on_connect
# drives the retained block to match THIS session's FSM state; without it a
# fresh session inherits the previous session's armed stop and the feed guard
# (which trusts the firmware enable bit) silently skips its confirmation.

def test_reconcile_clears_retained_arm_when_disabled():
    hal = MagicMock()
    fsm = _build_fsm(hal=hal)
    assert fsm.state == "disabled"
    fsm.reconcile_firmware_on_connect()
    hal.set_enable.assert_called_once_with(False)
    hal.set_active.assert_called_once_with(False)
    # Clearing must not write a stop position — there is nothing to arm.
    hal.set_stop_position.assert_not_called()


def test_reconcile_clears_retained_arm_in_alarm():
    hal = MagicMock()
    fsm = _build_fsm(hal=hal)
    fsm.enable()
    fsm.fault()
    assert fsm.state == "alarm"
    hal.reset_mock()
    fsm.reconcile_firmware_on_connect()
    hal.set_enable.assert_called_once_with(False)
    hal.set_active.assert_called_once_with(False)


def test_reconcile_rearms_engaged_idle_with_committed_stop():
    """Engaged-idle with a committed stop and Z on the safe side: a reconnect
    (possibly a firmware reboot that lost our arm) must re-push and re-arm."""
    hal = MagicMock()
    z, _ = _make_z_axis(scaled_position=50.0)  # forward: safe side is above stop
    controller = _make_controller(stop_z=42.0, els_forward=True)
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    fsm.enable()
    hal.reset_mock()
    fsm.reconcile_firmware_on_connect()
    hal.set_stop_position.assert_called_with(controller.stop_z_encoder)
    hal.set_active.assert_called_once_with(True)
    hal.set_enable.assert_called_once_with(True)
    hal.set_stop_direction.assert_called()


def test_reconcile_clears_when_engaged_but_no_committed_stop():
    """Engaged-idle but nothing committed THIS session: the retained arm must
    be cleared, not trusted — this is the exact stale-shoulder scenario."""
    hal = MagicMock()
    controller = _make_controller()
    controller.stop_z_encoder = None
    fsm = _build_fsm(hal=hal, controller=controller)
    fsm.enable()
    hal.reset_mock()
    fsm.reconcile_firmware_on_connect()
    hal.set_enable.assert_called_once_with(False)
    hal.set_active.assert_called_once_with(False)
    hal.set_stop_position.assert_not_called()


def test_reconcile_hands_off_mid_cut():
    """Reconnect while cutting: motion may be live and the firmware's armed
    stop is actively protecting it — reconcile must write NOTHING."""
    hal = MagicMock()
    z, _ = _make_z_axis(scaled_position=50.0)
    controller = _make_controller(stop_z=42.0, els_forward=True)
    fsm = _build_fsm(z=z, hal=hal, controller=controller)
    fsm.enable()
    fsm.cut()
    assert fsm.state == "cutting"
    hal.reset_mock()
    fsm.reconcile_firmware_on_connect()
    hal.set_enable.assert_not_called()
    hal.set_active.assert_not_called()
    hal.set_stop_position.assert_not_called()
    hal.set_stop_direction.assert_not_called()
