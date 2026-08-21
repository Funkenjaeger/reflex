"""Probe test for the emulator's stdin X-axis command channel.

Exercises the new runtime command channel end-to-end through the real
reflex-ui stack: SystemHarness.emu_cmd() (harness.py) writes lines to the
emulator subprocess's stdin (now piped by the emulator_process fixture,
conftest.py), which reflex-fw's stdinCommandThreadFunc (emulator/src/main.cpp,
serve mode only) parses and applies to LathePhysics::moveCrossSlideTo /
jogCrossSlide (emulator/src/physics.cpp). No new Modbus register is involved;
this only confirms the channel drives the SAME cross-slide physics the
firmware's scales[2] (X) register already reports.

Boot state (reflex-fw emulator/config/lathe.toml [cross_slide]):
  initial_position_mm = 50.0, encoder_counts_per_mm = 400, scale_dir = 1
  -> scales[2].position starts at +20000 counts (harness.X_SCALE_INDEX).
"""

import pytest

pytestmark = pytest.mark.system

COUNTS_PER_MM = 400
X_INITIAL_COUNTS = 20000  # 50.0 mm * 400 counts/mm, see module docstring
COUNTS_TOL = 80  # +/- 0.2 mm, per the task's tolerance


def test_x_command_channel_ramps_and_survives_malformed_input(harness):
    """Full probe flow: boot readback -> ramped move -> malformed line ->
    move still works."""

    def x_counts() -> int:
        return harness.x_position_counts()

    # 1. Boot X counts read back correctly.
    start = x_counts()
    assert start == pytest.approx(X_INITIAL_COUNTS, abs=COUNTS_TOL), (
        f"unexpected boot X position: {start} counts (want ~{X_INITIAL_COUNTS})"
    )

    # 2. "x move 30" (target 30 mm < start 50 mm): expect X counts to
    #    DECREASE by ~(50-30)*400 = 8000 counts, landing near 12000.
    harness.emu_cmd("x move 30")
    target1 = 30 * COUNTS_PER_MM  # 12000

    samples = []

    def near_target1() -> bool:
        c = x_counts()
        samples.append(c)
        return abs(c - target1) <= COUNTS_TOL

    reached = harness.wait_until(near_target1, timeout_s=15.0)
    assert reached, (
        f"X did not settle near {target1} counts within timeout; "
        f"last samples: {samples[-5:]}"
    )

    final1 = x_counts()
    assert final1 == pytest.approx(target1, abs=COUNTS_TOL), (
        f"X settled at {final1} counts, want ~{target1} (+/-{COUNTS_TOL})"
    )

    # 3. Motion must be RAMPED (velocity/acceleration profile), not
    #    teleported: at least 3 strictly-intermediate samples were observed
    #    between the start and final counts.
    intermediate = {c for c in samples if min(start, target1) < c < max(start, target1)}
    assert len(intermediate) >= 3, (
        f"expected a ramped move (>=3 distinct intermediate X samples between "
        f"{start} and {target1}); got {sorted(intermediate)} from samples "
        f"{samples}"
    )

    # 4. A malformed line must not wedge the channel: send garbage, then a
    #    valid move, and confirm it still lands (final ~14000 counts).
    harness.emu_cmd("bogus nonsense")
    harness.emu_cmd("x move 35")
    target2 = 35 * COUNTS_PER_MM  # 14000

    reached2 = harness.wait_until(
        lambda: abs(x_counts() - target2) <= COUNTS_TOL, timeout_s=15.0
    )
    final2 = x_counts()
    assert reached2, (
        f"channel appears wedged after a malformed line; final X={final2} "
        f"counts, want ~{target2} (+/-{COUNTS_TOL})"
    )
