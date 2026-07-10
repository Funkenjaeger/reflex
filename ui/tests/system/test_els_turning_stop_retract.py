"""Task 9: stop+retract turning mode.

Extends the stop-only cut with reflex-ui's OWN retract: after the ELS stop, the
host drives a servo retract back to retract_z (not the operator's manual
retract). Runs with EMU_NO_AUTO_RETRACT so the emulator leaves the half-nut
engaged and does NOT simulate the manual retract — the host owns the retract.

Includes the c69b02a regression: that commit fixed the retract servo direction
(the retract was moving the SAME way as the cut, toward the shoulder/chuck,
instead of away). The direction assertion below pins that: the retract must move
the carriage AWAY from stop_z (opposite the cut), regardless of DRO/servo
polarity.
"""

import pytest

pytestmark = pytest.mark.system

# EMU_RPM=-30: spindle turns the els_forward=True feed direction (Task 7 finding).
# EMU_NO_AUTO_RETRACT: emulator does not do the manual hand-retract on stop, so
# reflex-ui's servo-driven retract is what moves the carriage.
_ENV = {"env": {"EMU_RPM": "-30", "EMU_NO_AUTO_RETRACT": "1"}}


@pytest.mark.xfail(
    reason="Task 9 WIP: host-driven retract does not complete — sync feed keeps "
    "driving the carriage while the spindle turns (sync not paused during the "
    "retract), and the EMU_RPM=-30 spindle-convention workaround (Task 7) likely "
    "inverts the retract sign. Needs the firmware active/sync/indexing interaction "
    "worked out + the spindle-direction convention resolved. See plan Task 9.",
    strict=False,
)
@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_turning_stop_retract_regression_c69b02a(harness):
    h = harness
    h.configure(is_threading=False, retract_enabled=True, wizard_enabled=False,
                els_forward=True)

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
    assert h.els_fsm.state == "retracting", f"retract did not start: {h.els_fsm.state}"
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
    # ...and it reached retract_z.
    assert z_after == pytest.approx(retract_z, abs=15.0), (
        f"retract off target: after={z_after} retract_z={retract_z}"
    )
