"""Manual reference latch (interactive re-sync) over the real Modbus link.

Drives the REAL reflex-ui HAL/FSM stack against the REAL firmware emulator to
prove the latchCommand/latchSeq contract end to end, and — the point of the
feature — that once the operator latches a datum, EVERY subsequent pass cuts
on that datum's helix.

WHAT "SAME PHASE" MEANS HERE, AND HOW IT IS MEASURED WITHOUT AN ATOMIC
HOST-SIDE (SPINDLE, Z) SAMPLE
----------------------------------------------------------------------
Relative to the latched datum (S0, Z0), define

    residual = fold( (S - S0)·num/den − droSign·(Z − Z0)·tps/zcpp,  tps )

(fold = shortest signed value mod one thread pitch; droSign =
stopDirection·cuttingDir, mirroring els_phase.h). While a pass is CUTTING,
sync maintains this quantity up to the servo's steady-state FOLLOWING LAG —
observed ~100-plus steps at the emulator's dynamics, and NOT a defect: the
lag is the same every pass at the same speed, so the physical helix each pass
cuts is identical. The assertion that proves "every pass lands on the same
helix" is therefore residual EQUALITY ACROSS PASSES (tight spread), not
residual ≈ 0; a wrong-groove failure shows up as a ~half-pitch (~tps/2) jump
between passes.

Sampling: both scale positions arrive in one fastData bulk read (a single
Modbus transaction), coherent to ~one ISR tick — versus at-stop sampling,
which is unusable because the spindle keeps rotating after the trigger
freezes Z. Each sample is taken at the same fraction of the pass so the
speed-dependent lag term matches across passes.

THE ANCHORING CHAIN (why pass-equality also proves the DATUM anchors them)
--------------------------------------------------------------------------
  1. referenceLatched was set by the MANUAL latch, and latchedSpindle/Z read
     back as the operator's pair — asserted before the first cut.
  2. The stop trigger provably does not overwrite them — asserted after
     every pass.
  3. The resume-path correction provably ran (lastPhaseError/lastCorrection
     move off their zero boot values at the first resume) and the only
     reference it can measure from is the manual pair, per 1 and 2.

SELF-CHECKS THAT KEEP A GREEN RUN HONEST
----------------------------------------
  - The disabled-latch probe at the top asserts the command is consumed with
    NO latchSeq ack — the same absent-ack refusal the host controller's
    timeout logic depends on, proven over the real link.
  - Each sample is validated against a deliberately half-pitch-shifted datum,
    which must move the residual by exactly ~tps/2 (mod tps): proof the
    arithmetic can see an off-helix state, so a tight cross-pass spread is a
    measurement, not a tautology.
  - Cut-state guards before AND after each sample reject a sample that raced
    the stop trigger (sync gated ⇒ the invariant no longer holds).

EMULATOR GREEN IS NOT A HARDWARE RESULT: no servo dynamics beyond the model,
no Modbus timing stress, no metal. The elspi verification pass is Evan's.
"""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.system

# Host drives the retract; the emulator must not simulate a hand-retract.
_PARAM = {"env": {"EMU_NO_AUTO_RETRACT": "1"}}


def _fold(value: float, pitch: float) -> float:
    """Shortest signed residue of value modulo pitch — mirrors the fold in
    els_phase.h / elsComputePhaseCorrection."""
    r = value % pitch
    if r > pitch / 2:
        r -= pitch
    return r


class _Datum:
    """Phase-residual calculator anchored to a latched (spindle, Z) pair."""

    def __init__(self, h, s0: int, z0: int):
        self.s0 = s0
        self.z0 = z0
        self.num = int(h.board.device['scales'][0]['syncRatioNum'])
        self.den = int(h.board.device['scales'][0]['syncRatioDen'])
        self.tps = float(h.register("elsStop", "threadPitchSteps"))
        self.zcpp = float(h.register("elsStop", "zCountsPerPitch"))
        stop_dir = int(h.register("elsStop", "stopDirection"))
        cutting_dir = 1 if self.num > 0 else -1
        if self.tps * self.zcpp < 0:
            cutting_dir = -cutting_dir
        self.dro_sign = stop_dir * cutting_dir

    def residual(self, s: int, z: int) -> float:
        ideal = (s - self.s0) * self.num / self.den
        actual = (z - self.z0) * self.tps / self.zcpp
        return _fold(ideal - self.dro_sign * actual, self.tps)


def _coherent_sample(h):
    """One fastData bulk refresh → (spindle, Z) raw counts from the same
    Modbus transaction."""
    h.pump()
    vals = h.board.fast_data_values['scaleCurrent']
    return int(vals[0]), int(vals[1])


@pytest.mark.parametrize("emulator_process", [_PARAM], indirect=True)
def test_manual_latch_anchors_every_pass(harness):
    h = harness
    hal = h.els_fsm.hal

    h.configure(is_threading=True, retract_enabled=True, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()
    # THREAD_IN "16" (16 TPI) at the reference geometry → exactly the pinned
    # 400 steps/pitch, 635 counts/pitch (test_els_real_config.py).
    from fractions import Fraction
    h.set_feed(Fraction(254, 160))
    h.els.els_backlash_steps = 0     # inline correction branch; the take-up
    h.pump()                         # gate is covered by its own suites

    # ── Refusal probe: a latch with NO job armed is consumed without an ack ──
    seq_base = hal.read_latch_seq()
    hal.request_latch()
    h.wait_until(lambda: int(h.register("elsStop", "latchCommand")) == 0,
                 timeout_s=2)
    assert int(h.register("elsStop", "latchCommand")) == 0, \
        "latchCommand left pending with enable == 0"
    assert hal.read_latch_seq() == seq_base, \
        "latchSeq acked a latch the firmware should have refused (enable == 0)"
    assert not hal.read_reference_latched()

    # ── Arm a job and latch the operator's datum ─────────────────────────────
    z_start = h.z_scaled_position()
    margin = h.safety_margin()
    assert margin > 0
    span = margin + 1.0
    h.set_stop_z(z_start - span)     # els_forward=True cuts -Z toward the stop
    # Retract target sits 0.25 mm short of the start so the returning carriage
    # always reaches/overshoots it: with els_backlash_steps=0 the retract gets
    # no lash compensation, and the physics model's 0.02 mm lash would
    # otherwise leave Z just shy of the target — failing is_retracted() and
    # refusing every pass after the first.
    h.set_retract_z(z_start - 0.25)

    h.engage()                       # enable=1, active=1 → sync gated, Z frozen
    h.enable_sync()

    z_datum = h.carriage_position_counts()
    hal.request_latch()
    latched = h.wait_until(lambda: hal.read_latch_seq() == seq_base + 1,
                           timeout_s=2)
    assert latched, "manual latch never acked with an armed job"
    assert hal.read_reference_latched()
    s_datum = hal.read_latched_spindle()
    # Z is frozen while stopped (sync gated), so the firmware's atomic capture
    # must agree with the host's read exactly. The spindle is rotating, so its
    # latched value can only be sanity-bounded, not equality-checked.
    assert hal.read_latched_z() == z_datum, (
        f"latchedZ {hal.read_latched_z()} != host-read Z {z_datum} — "
        "host and firmware disagree on a frozen axis"
    )

    # ── Three passes, each phase-checked mid-cut against the datum ──────────
    # The thread geometry registers are pushed by the FSM when a cut starts
    # (on_enter_cutting), so the residual calculator is built after the first
    # cut is underway — the latched datum itself was captured above.
    datum = None
    residuals = []
    for pass_no in (1, 2, 3):
        h.cut()
        assert h.ui_fsm.state == "in_cycle.cutting", (
            f"pass {pass_no}: cut refused (ui={h.ui_fsm.state})"
        )
        if datum is None:
            datum = _Datum(h, s_datum, z_datum)
            assert datum.tps == pytest.approx(400.0, abs=1e-3)
            assert datum.zcpp == pytest.approx(635.0, abs=1e-3)

        # Wait for the carriage to reach the SAME fraction of the pass each
        # time, so the speed-dependent following-lag term in the residual is
        # comparable across passes. Progress is measured in raw counts toward
        # the stop (reference geometry: 400 counts/mm).
        z_cut_start = h.carriage_position_counts()
        span_counts = int(span * 400)
        moved = h.wait_until(
            lambda: abs(h.carriage_position_counts() - z_cut_start)
                    > span_counts * 0.5,
            timeout_s=20)
        assert moved, f"pass {pass_no}: carriage never got underway"

        # Guarded coherent sample: reject it if the stop triggered around it.
        assert int(h.register("elsStop", "active")) == 0, (
            f"pass {pass_no}: stop already triggered before the sample — "
            "widen the sampling window"
        )
        s_mid, z_mid = _coherent_sample(h)
        assert int(h.register("elsStop", "active")) == 0, (
            f"pass {pass_no}: stop triggered mid-sample — sample invalid"
        )

        res = datum.residual(s_mid, z_mid)
        residuals.append(res)
        print(f"[thread_resync] pass {pass_no}: s_mid={s_mid} z_mid={z_mid} "
              f"dS={s_mid - datum.s0} dZ={z_mid - datum.z0} "
              f"ideal={(s_mid - datum.s0) * datum.num / datum.den:+.1f} "
              f"actual={(z_mid - datum.z0) * datum.tps / datum.zcpp:+.1f} "
              f"droSign={datum.dro_sign} residual={res:+.1f} "
              f"fw(lastIdeal={float(h.register('elsStop', 'lastIdealAdvance')):+.1f} "
              f"lastActual={float(h.register('elsStop', 'lastActualAdvance')):+.1f} "
              f"lastErr={float(h.register('elsStop', 'lastPhaseError')):+.1f} "
              f"lastCorr={float(h.register('elsStop', 'lastCorrection')):+.1f})")

        # Arithmetic self-check: shifting the datum half a pitch must move
        # this sample's residual by exactly ~tps/2 (mod tps) — proof the math
        # can see an off-helix state. (A tps/2 shift in ideal-advance space is
        # den·tps/(2·num) spindle counts.)
        shifted = _Datum(h, s_datum + int(round(datum.den * datum.tps
                                                / (2 * datum.num))), z_datum)
        control_delta = abs(_fold(shifted.residual(s_mid, z_mid) - res, datum.tps))
        assert abs(control_delta - datum.tps / 2) < datum.tps / 16, (
            f"half-pitch control failed (moved {control_delta:.1f}, expected "
            f"~{datum.tps / 2:.0f}) — the residual math could not detect a "
            "deliberately shifted datum, so the cross-pass spread below proves nothing"
        )

        if pass_no == 1:
            # Anchoring chain link 3: the resume-path correction ran, and the
            # only reference it can have measured from is the manual pair.
            assert float(h.register("elsStop", "lastPhaseError")) != 0.0, (
                "first resume never computed a phase correction — the manual "
                "latch was not consumed by the resume path"
            )

        # The datum must survive the pass's stop trigger untouched.
        stopped = h.wait_until(
            lambda: h.ui_fsm.state == "in_cycle.waiting_to_retract",
            timeout_s=20)
        assert stopped, f"pass {pass_no}: never reached the ELS stop"
        assert hal.read_latched_spindle() == s_datum, (
            f"pass {pass_no}: stop trigger overwrote latchedSpindle — "
            "auto-latch suppression failed"
        )
        assert hal.read_latched_z() == z_datum, (
            f"pass {pass_no}: stop trigger overwrote latchedZ"
        )

        h.trigger_retract()
        # Wait for BOTH FSMs: the domain FSM lands in 'stopped' before the UI
        # FSM's retract_done event cascade delivers, and a cut pressed in that
        # window is silently ignored (no `action` transition from
        # in_cycle.retracting).
        done = h.wait_until(
            lambda: (h.els_fsm.state == "stopped"
                     and h.ui_fsm.state == "in_cycle.waiting_to_cut"),
            timeout_s=20)
        assert done, (
            f"pass {pass_no}: retract never completed "
            f"(els={h.els_fsm.state} ui={h.ui_fsm.state})"
        )

    # ── The claim under test: every pass on the SAME helix ──────────────────
    # Fold pairwise differences so a spread straddling the ±tps/2 seam cannot
    # alias; a wrong-groove pass would show as a ~tps/2 outlier.
    spread = max(abs(_fold(a - b, datum.tps))
                 for a in residuals for b in residuals)
    print(f"[thread_resync] mid-cut residuals vs manual datum (steps, "
          f"pitch={datum.tps:.0f}): "
          + ", ".join(f"{r:+.1f}" for r in residuals)
          + f"  spread={spread:.1f}")
    assert spread < datum.tps / 16, (
        f"pass residuals {['%+.1f' % r for r in residuals]} spread "
        f"{spread:.1f} steps (limit {datum.tps / 16:.0f}) — passes are NOT "
        "landing on one common helix"
    )
