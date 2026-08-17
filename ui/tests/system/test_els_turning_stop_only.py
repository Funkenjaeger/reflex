"""Task 7: baseline turning cut, stop-only mode, default (all-normal) wiring.

Proves the whole real stack — emulator firmware + real FSMs/dispatchers over real
Modbus — can run one full engage -> feed -> auto-stop cycle and halt the carriage
at the operator's stop_z. Assertions are on RELATIVE Z travel (the DRO position
register carries a fixed startup offset; absolute counts are meaningless).
"""

from fractions import Fraction

import pytest

pytestmark = pytest.mark.system


# Real machine commissioning (elspi): a forward +30 rpm spindle (EMU_RPM=30) cuts
# correctly toward an els_forward=True stop only with the SERVO reversed
# (servo_reverse=true) — see the Task 9 retract test + morning-coffee #1. That's
# what commission_servo applies below (also raising maxSpeed to the real 10000).
# EMU_NO_AUTO_RETRACT: the carriage stays at the stop after the ELS fires (no
# simulated hand-retract), so the final read isn't racing the 400 ms retract.
@pytest.mark.parametrize(
    "emulator_process", [{"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}],
    indirect=True)
def test_turning_stop_only_reaches_stop_z(harness):
    h = harness
    h.configure(is_threading=False, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()
    h.set_feed(Fraction(254, 160))        # 16 TPI = 1.5875 mm/rev → ~0.79 mm/s at EMU_RPM=30

    # Stop target on the cutting side (els_forward=True => cut_dir=-1 => cut moves
    # in the -scaledPosition direction). With commissioned geometry the span is
    # real mm: it must clear the ~3.49 mm safety margin, but stay well inside the
    # 5 mm of physical -Z travel the reference machine has from its Z=0 start
    # (lathe.toml min_position_mm=-5) or the carriage pins on the travel limit
    # before reaching stop_z.
    z_start = h.z_scaled_position()
    margin = h.safety_margin()
    span = margin + 1.0                   # mm
    stop_z = z_start - span

    # Set stop_z BEFORE engaging so on_enter_stopped arms against it, then engage
    # (arms + holds), enable sync feed (held by active=1), then cut (releases).
    h.set_stop_z(stop_z)
    assert h.els_fsm.is_ready_to_cut(), (
        f"not ready to cut: z_start={z_start} stop_z={stop_z} margin={margin}"
    )
    h.engage()
    assert h.els_fsm.state == "stopped"
    h.enable_sync()
    h.cut()
    assert h.els_fsm.state == "cutting", f"cut did not start: {h.els_fsm.state}"

    # Feed is wall-clock paced (spindle sync); wait for the firmware auto-stop.
    reached = h.wait_until(lambda: h.els_fsm.state == "stopped", timeout_s=20)
    assert reached, (
        f"cut never stopped; state={h.els_fsm.state} "
        f"z_now={h.z_scaled_position()} stop_z={stop_z}"
    )

    z_final = h.z_scaled_position()
    # The carriage fed in the correct (-Z, toward-the-shoulder) direction...
    assert (z_final - z_start) < 0, (
        f"cut fed the WRONG way (away from the stop): start={z_start} final={z_final}"
    )
    # ...a meaningful distance toward the stop...
    assert abs(z_final - z_start) > span * 0.5, (
        f"carriage barely moved: start={z_start} final={z_final} span={span}"
    )
    # ...and halted in the stop_z vicinity. With commissioned geometry this is
    # real mm: at the ~0.79 mm/s selected feed the position-triggered stop
    # carries a small fraction of a millimeter past the target (stop-only mode
    # uses the loose firmware hysteresis). 0.25 mm bounds that while still being
    # a physically meaningful stop for a lathe carriage.
    assert z_final == pytest.approx(stop_z, abs=0.25), (
        f"stopped off target: final={z_final} stop_z={stop_z}"
    )
    # It must not have blown PAST the stop toward the chuck (els_forward=True
    # cuts in the -Z direction, so overshoot is more-negative than stop_z).
    assert z_final >= stop_z - 0.25, (
        f"overshot the stop: final={z_final} stop_z={stop_z}"
    )
