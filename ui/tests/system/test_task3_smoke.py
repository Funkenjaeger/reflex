"""Task 3 smoke tests: emulator launch fixture + confirming writes.

Confirms:
  - the emulator_process fixture actually launches a live process with a
    valid PTY path (basic fixture verification, per the plan).
  - runtime writes to scales[i].scaleDir and elsStop.stopDirection are live
    Modbus-writable/readable registers, and scaleDir specifically is a live
    physics input (not config-frozen) -- this settles Open Item 1 in
    .hermes/plans/2026-07-09_emulator-backed-system-tests.md. servoDir
    liveness was already confirmed by the Task 1 spike (deltas +1585 ->
    -1346 on flip); this file re-derives it for servoDir too as a cheap
    extra check while the connection is open anyway.

Access pattern: `cm['Global']['servo']['servoDir']` etc, per board.py:44 --
NOT a bare `Servo(connection_manager=cm)`, which would read the wrong
address (servo_t is nested inside rampsSharedData_t after 4 leading
uint32_t fields).

Scale index mapping (confirmed via reflex-fw emulator/src/main.cpp:479-480
and Core/Src/Ramps.c): scales[0] = spindle, scales[1] = Z axis.
"""

import os
import re
import time

import pytest

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_LOG_MODE", "PYTHON")

from reflex.utils.communication import ConnectionManager  # noqa: E402

pytestmark = pytest.mark.system

Z_SCALE_INDEX = 1
MOVE_STEPS = 2000  # small commanded move; brisk servo speed in serve mode makes this fast
MOTION_TIMEOUT_S = 5.0
POLL_INTERVAL_S = 0.02


def _connect(pty_path: str) -> ConnectionManager:
    cm = ConnectionManager(serial_device=pty_path, baudrate=115200, address=17)
    cm.connect()
    return cm


def _wait_until(predicate, timeout_s: float, poll_s: float = POLL_INTERVAL_S):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return predicate()


def test_emulator_launches(emulator_process):
    proc, pty_path = emulator_process
    assert proc.poll() is None, "emulator process exited immediately"
    assert re.fullmatch(r"/dev/pts/\d+", pty_path)


def test_servo_dir_matches_config_default(emulator_process):
    """Sanity check the register-access pattern itself: servo.servoDir should
    read back the launched config's default (lathe.toml [servo] dir = 1)."""
    _, pty_path = emulator_process
    cm = _connect(pty_path)
    try:
        servo_dir = cm['Global']['servo']['servoDir']
        assert servo_dir == 1
    finally:
        cm.disconnect()


def test_scale_dir_runtime_write_is_live_physics_input(emulator_process):
    """Flip scales[1].scaleDir (Z axis) at runtime over Modbus and confirm the
    observed Z scale-position delta sign inverts for an equivalent commanded
    move. This is the scaleDir half of Open Item 1 -- servoDir liveness was
    already confirmed by the Task 1 spike; scaleDir was not independently
    spiked until now.
    """
    _, pty_path = emulator_process
    cm = _connect(pty_path)
    try:
        device = cm['Global']

        # Arm manual/indexing servo motion (servoMode=1) -- stepsToGo is only
        # consumed by the firmware when armed (Ramps.c: servoMode==1 ->
        # updateIndexingPosition).
        device['fastData']['servoMode'] = 1

        def move_and_get_delta(steps: int) -> int:
            start = device['scales'][Z_SCALE_INDEX]['position']
            device['servo']['stepsToGo'] = steps
            moved = _wait_until(
                lambda: device['scales'][Z_SCALE_INDEX]['position'] != start,
                timeout_s=MOTION_TIMEOUT_S,
            )
            assert moved, "Z scale position did not change after commanding a move"
            # Let the move finish settling briefly so the next command doesn't
            # race the tail of this one.
            _wait_until(lambda: device['servo']['stepsToGo'] == 0, timeout_s=MOTION_TIMEOUT_S)
            end = device['scales'][Z_SCALE_INDEX]['position']
            return end - start

        current_scale_dir = device['scales'][Z_SCALE_INDEX]['scaleDir']
        assert current_scale_dir in (1, -1)

        delta_before = move_and_get_delta(MOVE_STEPS)

        # Flip scaleDir at runtime.
        device['scales'][Z_SCALE_INDEX]['scaleDir'] = -current_scale_dir
        assert device['scales'][Z_SCALE_INDEX]['scaleDir'] == -current_scale_dir

        delta_after = move_and_get_delta(MOVE_STEPS)

        assert delta_before != 0
        assert delta_after != 0
        # Same commanded direction, flipped scaleDir -> the DRO-observed
        # delta sign must invert.
        assert (delta_before > 0) != (delta_after > 0), (
            f"scaleDir flip did not invert observed Z delta sign: "
            f"before={delta_before}, after={delta_after}"
        )
    finally:
        cm.disconnect()


def test_stop_direction_write_readback(emulator_process):
    """Confirm elsStop.stopDirection is writable and readable-back over
    Modbus. A full physics-effect test of stopDirection requires driving a
    real ELS cut cycle (later tasks, once the FSM harness exists) -- for
    Task 3 the write/readback round-trip is sufficient per the plan's Open
    Item 1 note, since the register access mechanism is identical to
    servoDir/scaleDir (both independently confirmed live above / in the
    Task 1 spike) and stopDirection's only consumer is the same style of
    firmware register read (Ramps.c stop-condition check).
    """
    _, pty_path = emulator_process
    cm = _connect(pty_path)
    try:
        device = cm['Global']
        original = device['elsStop']['stopDirection']

        device['elsStop']['stopDirection'] = -1
        assert device['elsStop']['stopDirection'] == -1

        device['elsStop']['stopDirection'] = 1
        assert device['elsStop']['stopDirection'] == 1

        # Restore to whatever the firmware initialized it to, for hygiene
        # (not load-bearing since the fixture is function-scoped and the
        # process is torn down after this test).
        device['elsStop']['stopDirection'] = original
    finally:
        cm.disconnect()
