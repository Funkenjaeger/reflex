"""After a calibration the servo goes back to the mode it was in, and STAYS.

The run is started from servo off. BacklashCalibration.start() writes mode 1,
and through the servoMode binding (dispatchers/els.py) that arms spindle
sync. At the end the restore writes mode 0. servoEnableTask (firmware, 100 ms)
puts servoMode back to 1 whenever any sync is armed and elsStop.active is
clear, so a mode-0 write that lands while sync is still armed can be undone,
and nothing undoes that: the UI's divergence watchdog fired after a passed
run on the 2026-10-18 bench and stayed until the next disengage.

Left to the binding, sync is cleared only AFTER the mode write, a window of a
few Modbus writes. In this emulator that lost about 1 run in 10, before and
after protocolVersion 13 alike. This test makes the window deterministic: it
holds the restore for 300 ms (three task periods) right after the servoMode
write, so a restore that has not cleared sync first fails every time.
"""

import time

import pytest

pytestmark = pytest.mark.system

#: Longer than servoEnableTask's 100 ms period, several times over.
HOLD_AFTER_MODE_WRITE_S = 0.3


@pytest.mark.parametrize(
    "emulator_process", [{"env": {"EMU_RPM": "0"}}], indirect=True)
def test_servo_off_after_a_passed_calibration_stays_off(harness, monkeypatch):
    from reflex.dispatchers import servo as servo_mod
    from reflex.fsms.els_cal import BacklashCalibration, CalState

    h = harness
    for _ in range(30):
        h.pump()
    h.configure(is_threading=False, retract_enabled=False,
                wizard_enabled=False, els_forward=True)
    h.commission_geometry()
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    for _ in range(10):
        h.pump()
    assert int(h.register('fastData', 'servoMode')) == 0, (
        "the scenario starts from servo off")

    real_write = servo_mod.write_confirmed

    def held_write(block, field, value):
        ok = real_write(block, field, value)
        if field == 'servoMode' and int(value) == 0:
            time.sleep(HOLD_AFTER_MODE_WRITE_S)
        return ok

    monkeypatch.setattr(servo_mod, "write_confirmed", held_write)

    # See test_els_virgin_boot.py: the emulator's 10 kHz ISR needs the wider
    # ceiling for a leg to clear the serve-mode lash.
    h.els.els_cal_ceiling_steps = 4000
    cal = BacklashCalibration(h.hal, h.els)
    assert cal.start() is True, cal.message
    assert h.wait_until(lambda: cal.poll() != CalState.RUNNING, timeout_s=60)
    assert cal.state == CalState.PASSED, cal.message

    seen = []
    for _ in range(20):
        h.pump()
        seen.append(int(h.board.fast_data_values['servoMode']))
    cal.cancel()            # Discard, as on the bench
    for _ in range(10):
        h.pump()
        seen.append(int(h.board.fast_data_values['servoMode']))

    assert set(seen) == {0}, (
        f"the firmware put servoMode back to 1 after the restore wrote 0 "
        f"(polled {seen}): sync was still armed when the mode went to 0")
    assert h.board.servo._divergence_polls == 0
    assert all(int(s['syncEnable']) == 0 for s in h.board.device['scales'])
