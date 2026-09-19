"""The stop-overshoot correction end to end: UI -> Modbus -> emulator firmware.

protocolVersion 11. With the correction ON, the controller writes
elsStop.stopOffset live from the Z rate and the firmware fires the stop that
many counts EARLY, recording the clamped value it used in stopTriggerOffset.
What only the full stack can show:

  * the value the UI wrote is the value the ISR applied (stopTriggerOffset);
  * the stop fired on the APPROACH side of stopPosition by no more than that --
    early, never late -- while stopPosition itself was never rewritten;
  * the write limiter held the traffic: at most one stopOffset write per 250 ms
    of wall clock across the whole pass.
"""

import time
from fractions import Fraction

import pytest

pytestmark = pytest.mark.system


@pytest.mark.parametrize(
    "emulator_process", [{"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}],
    indirect=True)
def test_correction_on_fires_early_by_the_offset_it_wrote(harness):
    h = harness
    h.configure(is_threading=False, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()
    h.set_feed(Fraction(254, 160))        # ~0.79 mm/s at EMU_RPM=30

    h.els.els_overshoot_correction = True
    h.els.els_overshoot_margin_counts = 1

    # Count every stopOffset write the controller makes, at the HAL.
    hal = h.controller.hal
    writes = []
    original = hal.set_stop_offset

    def counting(counts):
        writes.append((time.monotonic(), int(counts)))
        original(counts)

    hal.set_stop_offset = counting

    z_start = h.z_scaled_position()
    stop_z = z_start - (h.safety_margin() + 1.0)
    h.set_stop_z(stop_z)
    stop_enc = h.controller.stop_z_encoder
    h.engage()
    assert h.els_fsm.state == "stopped"
    h.enable_sync()
    t0 = time.monotonic()
    h.cut()
    assert h.els_fsm.state == "cutting", f"cut did not start: {h.els_fsm.state}"

    reached = h.wait_until(lambda: h.els_fsm.state == "stopped", timeout_s=20)
    t1 = time.monotonic()
    assert reached, f"cut never stopped; state={h.els_fsm.state}"

    written = h.controller.stop_offset_corrector.offset_in_effect
    trig_off = int(h.register('elsStop', 'stopTriggerOffset'))
    trig_z = int(h.register('elsStop', 'stopTriggerZ'))
    stop_pos = int(h.register('elsStop', 'stopPosition'))
    direction = int(h.register('elsStop', 'stopDirection'))

    assert stop_pos == stop_enc, "stopPosition is the committed target, unmodified"
    assert written is not None and written > 0, f"nothing written: {writes}"
    # The value the ISR applied is one the UI wrote. Normally the LAST one --
    # the corrector holds once the snapshot shows the stop latched -- but a
    # write due in the same tick the trigger lands (after that tick's snapshot
    # read) can follow it, so the one before is accepted too.
    recent = [v for _, v in writes[-2:]]
    assert trig_off in recent, (
        f"firmware applied {trig_off}; UI's last writes were {recent}")
    # EARLY by at most the offset: on the approach side of stopPosition.
    early = (stop_pos - trig_z) * (1 if direction >= 0 else -1)
    assert 0 < early <= trig_off, (
        f"trigger at {trig_z}, stopPosition {stop_pos}, dir {direction}, "
        f"offset {trig_off}: early by {early}")

    in_pass = [w for w in writes if t0 <= w[0] <= t1]
    budget = 1 + int((t1 - t0) / 0.25) + 1
    assert len(in_pass) <= budget, (
        f"{len(in_pass)} stopOffset writes in {t1 - t0:.1f} s exceeds the "
        f"limiter's budget of {budget}: {in_pass}")
