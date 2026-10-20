"""protocolVersion 13 against the firmware emulator: DISENGAGE through the
command channel, and the calMeasured split.

The emulator runs the firmware's own elsCommandIntake / elsCommandFinish on
its ~100 ms task tick (fw/emulator/src/main.cpp), so these drive the UI's
command path into the real consumption code, over the real Modbus link:

- an engaged job with sync armed and the spindle stopped (the one sync-on
  case disengage_refusal() allows) is disengaged by ONE command: the
  firmware clears sync, the feed and the stop, acks on commandSeq with OK,
  publishes OFF, and the UI's mirrors follow without writing;
- a failed calibration leaves the take-up gate's basis (calBasisSteps) as
  reconcile taught it, while calMeasured carries the failed result;
- a passed calibration installs its mean, and Discard puts the accepted
  basis back.

The emulator table test (fw/emulator/test/els_command_test.cpp) covers every
machine mode; this is the end-to-end path the operator's button takes.
"""
import pytest

from tests.system.test_els_safety import _commission, _max_travel

pytestmark = pytest.mark.system

MMODE_OFF = 0


def _settle(h, ticks=30):
    for _ in range(ticks):
        h.pump()


def _command_seq(h):
    return int(h.register('elsStop', 'commandSeq'))


@pytest.mark.parametrize(
    "emulator_process", [{"env": {"EMU_RPM": "0"}}], indirect=True)
def test_disengage_with_sync_armed_is_one_command_the_firmware_acks(harness):
    h = harness
    _commission(h, els_forward=True, retract_enabled=False)
    z0 = h.z_scaled_position()
    h.set_stop_z(z0 - 50.0)
    h.engage()
    h.enable_sync()
    _settle(h)
    assert h.els_fsm.state == "stopped"
    assert int(h.register('fastData', 'servoMode')) == 1, "precondition: feed on"
    assert any(int(s['syncEnable']) for s in h.board.device['scales']), \
        "precondition: sync armed"
    seq0 = _command_seq(h)

    assert h.controller.disengage() is None, "allowed: the spindle is stopped"
    assert h.els_fsm.state == "disabled", "disabled at the write"
    assert h.wait_until(lambda: not h.els_fsm.disengage_unacked, timeout_s=5), \
        "the firmware never acknowledged DISENGAGE"

    assert _command_seq(h) == seq0 + 1, "exactly one ack"
    assert int(h.register('elsStop', 'commandResult')) == 0, "OK"
    assert int(h.register('elsStop', 'command')) == 0, "consumed"
    assert int(h.register('elsStop', 'enable')) == 0
    assert int(h.register('elsStop', 'active')) == 0
    assert int(h.register('fastData', 'servoMode')) == 0
    assert not any(int(s['syncEnable']) for s in h.board.device['scales'])
    assert int(h.register('elsStop', 'machineMode')) == MMODE_OFF
    # The UI's mirrors followed the firmware without writing anything.
    assert h.board.servo.servoMode == 0
    assert not any(a.syncEnable for a in h.board.axes)
    settle = _max_travel(h, 1.0)
    assert settle < 0.05, f"carriage moved after the disengage: {settle:.3f} mm"


@pytest.mark.parametrize(
    "emulator_process", [{"env": {"EMU_RPM": "0"}}], indirect=True)
def test_a_failed_calibration_leaves_the_firmware_basis_intact(harness):
    from reflex.fsms.els_cal import BacklashCalibration, CalState

    h = harness
    _settle(h)
    h.els.els_cal_measured_legs = [365, 373, 366]
    h.controller._on_connected_changed(h.board, True)       # reconcile
    _settle(h)
    assert int(h.register('elsStop', 'calBasisSteps')) == 368

    h.configure(is_threading=False, retract_enabled=False,
                wizard_enabled=False, els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.enable_sync()
    # A ceiling far under the serve-mode lash (~151 steps, and the emulator
    # emits about a tenth of a drained leg; see test_els_virgin_boot): every
    # leg drains without Z moving, NO_MOTION.
    h.els.els_cal_ceiling_steps = 50
    cal = BacklashCalibration(h.hal, h.els)
    assert cal.start() is True, cal.message
    assert h.wait_until(lambda: cal.poll() != CalState.RUNNING, timeout_s=60)
    assert cal.state == CalState.REFUSED, (cal.state, cal.result_code, cal.message)

    assert int(h.register('elsStop', 'calBasisSteps')) == 368, (
        "a failed run moved the take-up gate's basis")
    assert any(int(v) <= 0 for v in h.register('elsStop', 'calMeasured')), (
        "calMeasured should carry the failed run's incomplete result")


@pytest.mark.parametrize(
    "emulator_process", [{"env": {"EMU_RPM": "0"}}], indirect=True)
def test_a_passed_calibration_installs_its_mean_and_discard_restores(harness):
    from reflex.fsms.els_cal import BacklashCalibration, CalState

    h = harness
    _settle(h)
    h.els.els_cal_measured_legs = [365, 373, 366]
    h.controller._on_connected_changed(h.board, True)
    _settle(h)

    h.configure(is_threading=False, retract_enabled=False,
                wizard_enabled=False, els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.enable_sync()
    h.els.els_cal_ceiling_steps = 4000      # see test_els_virgin_boot
    cal = BacklashCalibration(h.hal, h.els)
    assert cal.start() is True, cal.message
    assert h.wait_until(lambda: cal.poll() != CalState.RUNNING, timeout_s=60)
    assert cal.state == CalState.PASSED, (cal.state, cal.result_code, cal.message)

    mean = sum(int(v) for v in cal.measured) // 3
    assert int(h.register('elsStop', 'calBasisSteps')) == mean, (
        "the firmware did not install the passed run's mean")

    cal.cancel()                              # the wizard's Discard
    _settle(h)
    assert int(h.register('elsStop', 'calBasisSteps')) == 368, (
        "Discard did not put the accepted basis back")
