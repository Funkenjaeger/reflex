"""Task 8: the safety-critical valid-configuration reversing matrix.

For every one of the 16 physical-wiring permutations, with the operator's UI
reverse toggles set to CANCEL that wiring (the "valid configuration"), a
stop-only turning cut must behave exactly like the all-normal baseline: feed
toward stop_z and halt there. If the toggles correctly neutralize the wiring,
every permutation is canonical; a permutation that feeds the wrong way or fails
to stop is a real compensation bug this suite exists to catch.

Turning exercises the spindle (feed source), Z (feed/DRO), and servo (motor)
wiring axes. The X/cross-slide axis does not move during a stop-only turning cut,
so `x_reverse` is applied but not exercised here — the X invariant is covered by
the retract cycle in Task 9 (test_els_turning_stop_retract).

Assertions are on RELATIVE Z travel (the DRO register carries a fixed startup
offset). Stop tolerance is generous — sub-unit precision / no-overshoot is
Task 8b's job; this matrix pins the CAUSAL relationship (right direction, stops
at the target) across every wiring.
"""

import pytest

pytestmark = pytest.mark.system


def _run_stop_only_cut(h):
    """Drive one stop-only turning cut on an already-configured harness and
    return (z_start, stop_z, z_final, span). Raises on any FSM-sequence failure."""
    z_start = h.z_scaled_position()
    margin = h.safety_margin()
    span = max(margin * 2, 0.0) + 30.0
    stop_z = z_start - span

    h.set_stop_z(stop_z)
    assert h.els_fsm.is_ready_to_cut(), (
        f"not ready to cut: z_start={z_start} stop_z={stop_z} margin={margin}"
    )
    h.engage()
    assert h.els_fsm.state == "stopped"
    h.enable_sync()
    h.cut()
    assert h.els_fsm.state == "cutting", f"cut did not start: {h.els_fsm.state}"

    # Capture the FURTHEST excursion during the cut (== the stop point). In serve
    # mode the emulator auto-retracts the carriage to home on each ELS stop, so we
    # must NOT wait/settle after the stop (that reads the post-retract position);
    # the deepest point reached is where the ELS halted the feed.
    deepest = z_start

    def _poll():
        nonlocal deepest
        z = h.z_scaled_position()
        deepest = min(deepest, z)          # cut moves -Z; deepest = most negative
        return h.els_fsm.state == "stopped"

    reached = h.wait_until(_poll, timeout_s=20)
    assert reached, (
        f"cut never stopped; state={h.els_fsm.state} "
        f"z_now={h.z_scaled_position()} stop_z={stop_z}"
    )
    return z_start, stop_z, deepest, span


def test_valid_wiring_reaches_stop_z(wiring_cut_harness):
    h, perm = wiring_cut_harness
    z_start, stop_z, z_final, span = _run_stop_only_cut(h)

    # Fed a meaningful distance toward the stop (in the correct direction)...
    assert abs(z_final - z_start) > span * 0.5, (
        f"[{perm.id}] carriage barely moved: start={z_start} final={z_final} span={span}"
    )
    # ...toward stop_z (never the wrong way — that's the safety-critical bit):
    assert (z_final - z_start) < 0, (
        f"[{perm.id}] fed the WRONG direction (away from stop): "
        f"start={z_start} final={z_final} stop_z={stop_z} toggles={perm.toggles}"
    )
    # ...and halted in the stop_z vicinity, not overshooting past it.
    assert z_final == pytest.approx(stop_z, abs=15.0), (
        f"[{perm.id}] stopped off target: final={z_final} stop_z={stop_z}"
    )
    assert z_final >= stop_z - 20.0, (
        f"[{perm.id}] overshot the stop toward the chuck: final={z_final} stop_z={stop_z}"
    )
