"""Task 9: stop+retract turning mode.

Extends the stop-only cut with reflex-ui's OWN retract: after the ELS stop, the
host drives a servo retract back to retract_z (not the operator's manual
retract). Runs with EMU_NO_AUTO_RETRACT so the emulator leaves the half-nut
engaged and does NOT simulate the manual retract — the host owns the retract.

Commissioning note (this is what makes Task 9 pass — see below):
  This test uses the REAL machine's commissioning (elspi): a forward +30 spindle
  with the SERVO reversed (servo_reverse=true, servoDir=-1) and els_forward=true.
  (The whole system suite now uses this real commissioning; an earlier EMU_RPM=-30
  spindle band-aid was retired once this diagnosis landed.)

  Why the retract specifically needs it: the cut is spindle-SYNC-mediated, so
  reversing the spindle (the old EMU_RPM=-30 band-aid) was an equivalent way to
  get the cut feeding toward the stop. But the retract is a DIRECT servo indexing
  move (elsStop → servo.stepsToGo), whose direction depends on servoDir, not the
  spindle sign. The spindle band-aid never corrected it, so under all-default
  toggles the retract drove the carriage the WRONG way (toward the shoulder) and
  — via the retract self-loop recomputing an ever-larger delta — ran away.
  Commissioning servo_reverse=true (as the real lathe does) fixes both cut and
  retract. maxSpeed is also raised to the real 10000 (vs the hermetic 1000
  default) so no cut-time step backlog flushes after
  the stop; see SystemHarness.commission_servo.

Includes the c69b02a regression: that commit fixed the retract servo direction
(the retract was moving the SAME way as the cut, toward the shoulder/chuck,
instead of away). The direction assertion below pins that: the retract must move
the carriage AWAY from stop_z (opposite the cut).
"""

import pytest

pytestmark = pytest.mark.system

# EMU_RPM=30: real forward spindle (the servo, not the spindle, is reversed at
# commissioning — see the module docstring). EMU_NO_AUTO_RETRACT: the emulator
# does not do the manual hand-retract on stop, so reflex-ui's servo-driven
# retract is what moves the carriage.
_ENV = {"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}


@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_turning_stop_retract_regression_c69b02a(harness):
    h = harness
    h.configure(is_threading=False, retract_enabled=True, wizard_enabled=False,
                els_forward=True)
    # Real machine commissioning: servo reversed + realistic servo speed. This
    # is what makes the direct-servo retract go the right way and complete.
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)

    z_start = h.z_scaled_position()
    margin = h.safety_margin()
    span = max(margin * 2, 0.0) + 30.0
    stop_z = z_start - span          # cut moves -Z toward the shoulder
    retract_z = z_start              # retract back to the cut-start position (+Z)

    h.set_stop_z(stop_z)
    h.set_retract_z(retract_z)

    # Cut -> ELS stop. In stop+retract mode the UI FSM parks in waiting_to_retract.
    h.engage()
    h.enable_sync()
    h.cut()
    reached = h.wait_until(
        lambda: h.ui_fsm.state == "in_cycle.waiting_to_retract", timeout_s=20)
    assert reached, (
        f"did not reach waiting_to_retract: ui={h.ui_fsm.state} els={h.els_fsm.state}"
    )
    z_at_stop = h.z_scaled_position()
    assert (z_at_stop - z_start) < 0, (
        f"cut fed the wrong way: start={z_start} at_stop={z_at_stop}"
    )
    assert abs(z_at_stop - z_start) > span * 0.5, (
        f"cut barely moved: start={z_start} at_stop={z_at_stop}"
    )

    # Retract (operator presses Retract). Host drives the servo retract to retract_z.
    h.trigger_retract()
    done = h.wait_until(lambda: h.els_fsm.state == "stopped", timeout_s=20)
    assert done, (
        f"retract never completed: els={h.els_fsm.state} z={h.z_scaled_position()}"
    )
    z_after = h.z_scaled_position()

    # c69b02a: the retract must move AWAY from the shoulder (opposite the cut) —
    # +Z here, back toward retract_z. The bug moved it the SAME way as the cut.
    assert (z_after - z_at_stop) > 0, (
        f"retract moved the WRONG way (toward the shoulder — the c69b02a bug): "
        f"at_stop={z_at_stop} after={z_after}"
    )
    # ...and it reached retract_z (reaching or passing it in the retract
    # direction). Servo momentum at the commissioned speed carries it a little
    # past retract_z (well under 1 mm at 400 counts/mm); the cap only has to
    # discriminate that modest overshoot from a runaway (thousands of counts).
    assert z_after >= retract_z - 5.0, (
        f"retract stopped short of retract_z: after={z_after} retract_z={retract_z}"
    )
    assert z_after <= retract_z + max(span * 8, 500.0), (
        f"retract overshot far past retract_z (runaway?): "
        f"after={z_after} retract_z={retract_z} span={span}"
    )
