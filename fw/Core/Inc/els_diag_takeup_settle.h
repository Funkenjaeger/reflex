/* Take-up settle trace, v2 -- diagnostic probe implementation.
 *
 * ONE PROBE PER HEADER, and every probe implements the SAME four entry points
 * (elsDiagInit / elsDiagArm / elsDiagCaptureStart / elsDiagCapturing /
 * elsDiagTick). els_diag.h
 * decides which header provides them, or supplies no-ops when no probe is
 * selected. See DIAG.md for the pattern and for what this probe measures.
 *
 * Included ONLY via els_diag.h, and only when this probe is the selected one.
 * Nothing here is compiled into a release build.
 *
 * WHAT THIS FILE DOES NOT OWN: where it is called from. The bodies live here,
 * but each call site in Ramps.c sits at one specific instant in the ISR tick and
 * cannot be moved -- this probe measures WHEN things happen within a tick, so a
 * relocated hook measures something else. Each function below records the
 * instant it must be called at, because that constraint is no longer visible
 * from the code around it now that the bodies have moved out.
 */
#ifndef ELS_DIAG_TAKEUP_SETTLE_H
#define ELS_DIAG_TAKEUP_SETTLE_H

/* Why the capture stopped. The distinction matters: a capture that ran out of
 * buckets did not finish measuring, and its last bucket is a floor rather than
 * a result. RELOCATED HERE from Ramps.h (2026-08-20): this probe (schema
 * takeup_settle) is their only consumer -- the other two probes
 * (els_diag_disengage_latch.h, els_diag_mode_watch.h) each independently
 * #define ELS_DIAG_LATCH_SEEN 1 as their own workaround rather than use these.
 * Pure relocation: values, meaning and the diagEndReason register they feed
 * are all unchanged. */
#define ELS_DIAG_END_PULSE  1   /* servo drove again -- settling is over */
#define ELS_DIAG_END_WINDOW 2   /* ran out of buckets while still quiet-or-moving */

#include <stdint.h>
#include "els_isr_rate.h"
#include <stdbool.h>

/* WHAT THIS PROBE EXISTS TO MEASURE -- the two numbers Ramps.c otherwise GUESSES:
 *
 *   1. How long the carriage actually keeps moving after the last step pulse.
 *      That is what ELS_SLIP_SETTLE_TICKS is supposed to be, and it had never
 *      been measured -- it cannot be derived from the emulator, whose lash model
 *      moves the carriage instantaneously with the pulse, so it has no settle
 *      behaviour to observe at all.
 *   2. Whether Z ever goes properly QUIET, or dithers indefinitely under spindle
 *      vibration. That sets the tolerance band for any future "has the carriage
 *      stopped" test, and a band chosen without it could make a healthy machine
 *      refuse every pass.
 *
 * The capture is a DECIMATED trace, not a raw one: each bucket is the SIGNED sum
 * of dZ over ELS_DIAG_BUCKET_TICKS. Signed matters -- dither around a fixed
 * position cancels while genuine drift accumulates, the same reason a quiescence
 * test must use net displacement rather than summed |dZ|. Summing in the ISR is
 * what makes it affordable: 50 buckets covers 50 ms, where a raw per-tick trace
 * of the same span would be 5000 samples and fit nowhere.
 *
 * v2, after the 2026-08-16 machine run. v1 started at commanded-take-up
 * completion and then ran unconditionally for its whole window -- so it kept
 * recording after the gate confirmed and the PASS STARTED, and most of every
 * trace was the carriage traversing under sync at a flat ~1.9 counts/ms. That is
 * not a settle tail; a settle tail decays. diagSettleTicks was worse than
 * useless: it reported the last nonzero dZ, which once the pass is running is
 * always "just before the window closed" (measured: 4954-4995 out of 5000).
 *
 * v2 ends the capture the moment the servo issues its next step pulse -- exactly
 * the boundary between settling and being driven again -- which makes every
 * field mean what its name says. Buckets dropped 100 ticks -> 10 so the
 * sub-millisecond tail is resolvable: v1 showed 3-6 whole buckets of ZERO at 1 ms
 * each, which bounds the settle below 3 ms but cannot see inside it.
 *
 * RESULT (2026-08-16), DOWNGRADED 2026-08-18: the 13 captures read all-zero,
 * which is consistent with "stops dead" but does not establish it. Audit of
 * the recorded data (els-settle-measurement-findings-2026-08-18.md) found the
 * claim unsupportable three ways: the 500-tick window could not observe past
 * HALF of ELS_SLIP_SETTLE_TICKS (1000), so no capture could distinguish a
 * good constant from a 10x-too-large one; the recorder of that era did not
 * export diagEndReason, so END_PULSE ("finished measuring") cannot be told
 * from END_WINDOW ("truncated") for any of the 13; and the v2 armed window
 * has never demonstrated a nonzero, so "still" and "not looking" are
 * indistinguishable in its own data (v1's nonzero traversal data exercises
 * the same dZ read path, which vouches for the plumbing but not the window).
 * ELS_SLIP_SETTLE_TICKS therefore remains UNMEASURED (fw/todo.md). This probe
 * is retained as the worked example for writing the next one. Schema ids live
 * in Ramps.h -- they are part of the register contract reflex-ui mirrors, not
 * a detail of this file. */

/* Bucket width in ISR ticks. ~0.39 ms at the measured 103 kHz ISR rate.
 * PROBE-SPECIFIC: it belongs to this probe's trace geometry, not to the
 * scratchpad, which is why it moved here from Ramps.c with the rest of the
 * probe. The firmware PUBLISHES it in diagBucketTicks so no reader has to know
 * it -- see the note in els_diag.py about not baking rates into the host.
 *
 * 40, not the historical 10: 10 gave a 50-bucket window of 500 ticks, HALF of
 * the ELS_SLIP_SETTLE_TICKS = 1000 gate this probe exists to validate -- a
 * capture that runs out of window below the constant cannot justify keeping
 * it, let alone lowering it (2026-08-18 finding). 40 x 50 = 2000 ticks covers
 * the constant with 2x margin. No schema bump: bucket width is self-describing
 * (published per capture), unlike the v1->v2 gating change which altered what
 * the numbers MEANT.
 *
 * 40 x 50 = 2000 ticks ~= 19.4 ms at the measured ~103 kHz, which covered
 * ELS_SLIP_SETTLE_TICKS with 2x margin when that constant was 1000, and with
 * ~2.9x since it was commissioned down to 700 on 2026-08-27 using exactly these
 * captures -- the margin only grew, so the geometry still stands. Under v3 the
 * gate is held open
 * for exactly this span (see ELS_DIAG_SETTLE_HOLD_TICKS below), so the window
 * is the measurement rather than a race against the next commanded move.
 *
 * The capture still sits inside ELS_TAKEUP_CONFIRM_WINDOW_TICKS (Ramps.c) =
 * 25000 ~= 242.7 ms and is ~12.5x shorter than it, so a disturbance timed for
 * the confirm gate is still invisible here -- the "+5000 ticks" nudge case in
 * els_takeup_confirm_test.cpp shows exactly that. Do not read a zero from this
 * probe as a statement about the confirm window. See DIAG.md. */
/* 400 us per bucket. Expressed as a duration so the bucket keeps its
 * wall-clock width across a tick-rate change -- the 18 commissioning
 * captures were taken at 400 us and a silently doubled bucket would make
 * new captures incomparable with them. diagBucketTicks is published to the
 * host precisely so it never has to assume this. */
#define ELS_DIAG_BUCKET_TICKS ELS_US_TO_TICKS(400)

/* THE HOLD, AND WHY v2 HAD TO BE RETIRED TO GET IT.
 *
 * v2 measured nothing on a CONFIRMED take-up, and the reason is structural
 * rather than a tuning miss. Observed by running the real ISR (2026-08-22):
 * the capture starts one tick after the take-up's last pulse -- t=0 was
 * always right -- and then the gate's dwell expires ELS_SETTLE_TICKS (50)
 * ticks later, the gate confirms, applyPhaseCorrection() queues its jog, and
 * that jog's first pulse ends the capture at ~51 ticks with END_PULSE. Every
 * capture taken on the real lathe on 2026-08-21 reads 59-69 ticks for exactly
 * this reason. The 2000-tick window could only ever fill on a REFUSED take-up
 * -- where nothing is coupled and there is therefore nothing to measure.
 *
 * So this probe does two things v2 did not. It HOLDS the gate's dwell open
 * until the capture publishes (elsDiagExtraDwell), so the post-confirmation jog
 * can no longer cut the measurement short; and it treats a pulse arriving while
 * takeupPending is still set as a RESTART rather than an end (elsDiagTick), so
 * t=0 is the last pulse of the take-up rather than the crossing that precedes
 * it. Both are needed: with only the hold, the ramp's own residual steps ended
 * the capture at 134 ticks. The machine is standing still at the shoulder with
 * the tool clear, about to begin a pass; the cost is ~19.4 ms of extra
 * stillness before the cut starts, in a diagnostic build only, bounded by
 * ELS_DIAG_SETTLE_HOLD_CEILING_TICKS.
 *
 * WHAT THIS DELIBERATELY CHANGES, AND IT MUST NOT BE READ PAST. The gate's
 * verdict is computed LATER than in a release build, so more of the settle
 * tail falls inside elsSlipConfirmed()'s attribution horizon
 * (ELS_SLIP_SETTLE_TICKS, 1000 when these captures were taken, 700 since) and
 * is counted as evidence. A diagnostic
 * build is therefore MORE PERMISSIVE than release on a marginal take-up --
 * partial engagement is the case that can differ. An open half-nut still
 * refuses either way (no motion is attributable at any dwell). NEVER read "the
 * diagnostic build confirmed" as "release would have confirmed"; the emulator
 * pins both the refusal and the partial-engagement outcomes under the hold.
 *
 * That divergence is itself the finding this probe exists to settle: the gate
 * releases the cut 50 ticks after the last pulse while the attribution horizon
 * in the same path claims 1000, a 20x disagreement that no measurement has
 * ever adjudicated. */
#define ELS_DIAG_SETTLE_WINDOW_TICKS \
  ((int32_t)ELS_DIAG_TRACE_BUCKETS * (int32_t)ELS_DIAG_BUCKET_TICKS)

/* CEILING, not the hold itself. The hold lasts until the capture PUBLISHES
 * (below), which is the honest condition -- the gate should wait for the
 * measurement, not for a guess at how long the measurement takes. But a
 * capture that never publishes would hold takeupPending set forever, gating
 * sync with no way out but the enable escape hatch, so the wait is bounded:
 * once elsStopSettleCount passes this the dwell expires whatever the probe is
 * doing. 4x the window leaves room for the ramp tail to restart the capture a
 * few times (see elsDiagTick) and still complete one clean run. ~78 ms. */
#define ELS_DIAG_SETTLE_HOLD_CEILING_TICKS (ELS_DIAG_SETTLE_WINDOW_TICKS * 4)

/* Hold the take-up gate's dwell open while a capture is armed or running, and
 * release it the instant one publishes (state 0). Ramps.c adds this to the
 * dwell AND to the confirm-window abort threshold, so the window that follows
 * the dwell keeps its full length either way. */
static inline int32_t elsDiagExtraDwell(const elsDiagCtx_t *ctx) {
  return (ctx->state != 0) ? ELS_DIAG_SETTLE_HOLD_CEILING_TICKS : 0;
}

/* Startup. Publishes which probe is compiled in and its trace geometry, then
 * clears the block.
 *
 * diagSchema comes straight from the selection macro rather than a literal: the
 * register's whole job is to say which probe a reader is looking at, so a
 * hardcoded schema here could disagree with the probe actually compiled in --
 * the one lie this field must never be able to tell.
 *
 * The clears are explicit rather than left to BSS. A host that connects before
 * the first take-up must see "no capture yet", not whatever was in RAM -- and on
 * this part BSS happens to be zero, which would make the invariant look held
 * while nothing actually enforced it. */
static inline void elsDiagInit(elsDiagCtx_t *ctx, elsStop_t *stop) {
  stop->diagSchema       = ELS_DIAG_PROBE;
  stop->diagBucketTicks  = ELS_DIAG_BUCKET_TICKS;
  stop->diagBucketCount  = ELS_DIAG_TRACE_BUCKETS;
  stop->diagCaptureTicks = 0;
  stop->diagEndReason    = 0;
  stop->diagSettleTicks  = 0;
  stop->diagNetCounts    = 0;
  /* diagSeq too. It is the host's edge-detect source, so a garbage value at
   * boot reads as 'a capture just completed' against a block that holds
   * nothing. Left implicit until 2026-08-16, when it happened to be zero
   * because BSS is zero on this part -- the invariant held without anything
   * enforcing it, which is the same trap the clears below already call out. */
  stop->diagSeq          = 0;
  ctx->state             = 0;
  ctx->captureTick       = 0;
}

/* CALL AT TAKE-UP INITIATION, before any pulses are issued.
 *
 * Arms the trace and clears it HERE rather than when the capture starts. That
 * tick already does a pile of one-off work, so a 50-entry clear costs nothing
 * measurable, whereas clearing at capture start would put it on the tick where
 * the take-up completes -- the one tick whose timing this probe exists to
 * measure. DO NOT MOVE THE CALL SITE.
 *
 * The outcome fields are cleared too, so a reader that catches the block
 * mid-flight can never see the PREVIOUS capture's verdict beside this one's
 * trace. */
static inline void elsDiagRearm(elsDiagCtx_t *ctx, elsStop_t *stop) {
  ctx->state             = 1;
  ctx->captureTick       = 0;
  stop->diagSettleTicks  = 0;
  stop->diagNetCounts    = 0;
  stop->diagCaptureTicks = 0;
  stop->diagEndReason    = 0;
  for (int b = 0; b < ELS_DIAG_TRACE_BUCKETS; b++) {
    stop->diagTrace[b] = 0;
  }
}

static inline void elsDiagArm(elsDiagCtx_t *ctx, elsStop_t *stop) {
  elsDiagRearm(ctx, stop);
}

/* CALL ON THE FIRST TICK AT WHICH THE COMMANDED MOTION IS COMPLETE -- the last
 * step pulse, and therefore t=0 for the settle question. This fires BEFORE the
 * ELS_SETTLE_TICKS dwell and before the gate's first evaluation, so the trace
 * covers the whole of both. */
static inline void elsDiagCaptureStart(elsDiagCtx_t *ctx) {
  if (ctx->state == 1) {
    ctx->state       = 2;
    ctx->captureTick = 0;
  }
}

/* Is a capture in flight? Must stay TRIVIAL -- one field compare, no memory
 * beyond the context.
 *
 * It exists so the ISR can skip computing elsDiagTick's arguments on ticks that
 * would discard them. dZ costs two array indexings and a multiply, dServo a
 * subtract; as function arguments they are evaluated before the callee can
 * early-return, so without this guard every tick pays for them whether or not a
 * capture is running. That cost landed in the ISR this probe exists to
 * TIME, which makes it self-defeating rather than merely wasteful -- measured at
 * +128 bytes of ISR when this guard was missing. */
static inline bool elsDiagCapturing(const elsDiagCtx_t *ctx) {
  return ctx->state == 2;
}

/* CALL ONCE PER ISR TICK WHILE elsDiagCapturing(), at the point where dZ is
 * THIS tick's delta.
 *
 * IT ENDS ON THE SERVO'S NEXT PULSE, and that is the whole correction over v1.
 * v1 was deliberately not gated on takeupPending, on the reasoning that the
 * interesting part is what Z does AFTER the gate decides. True, but it ran on
 * unconditionally afterwards -- and what comes after the decision is the PASS,
 * so most of every v1 trace was the carriage traversing under sync at a flat
 * ~1.9 counts/ms. Flat is the tell: a settle tail decays.
 *
 * The next commanded pulse is the exact boundary between "still settling from
 * the take-up" and "being driven again", and it does not depend on which
 * firmware state machine happens to clear which flag first. dServo is checked
 * BEFORE accumulating, so the driving tick itself is never counted as settle. */
static inline void elsDiagTick(elsDiagCtx_t *ctx, elsStop_t *stop,
                               int32_t dZ, int32_t dServo) {
  if (ctx->state != 2) {
    return;
  }

  if (dServo != 0) {
    if (stop->takeupPending != 0u) {
      /* THE COMMANDED TAKE-UP IS NOT FINISHED, so this pulse is not the end of
       * the measurement -- it is the new start of it.
       *
       * elsDiagCaptureStart() fires on takeupReached, which is a CROSSING test
       * on servo.currentSteps. The decel ramp overshoots the target and keeps
       * emitting the residual, so the crossing is reached with steps still to
       * go: observed 2026-08-22 on the real ISR, 4 residual steps with the last
       * pair 134 ticks apart, because the gaps stretch as the ramp decelerates.
       * A capture started at the crossing is therefore timing the ramp's tail,
       * not the carriage's settle. v2 never revealed this -- the
       * post-confirmation jog ended every capture at ~51 ticks first.
       *
       * Re-arming here makes t=0 the LAST pulse by construction, with no need
       * to know in advance which pulse that is: each residual pulse throws the
       * partial capture away and the one that survives is the one nothing
       * interrupted. The clear costs 50 int16 writes on a pulse tick, and only
       * on pulses AFTER the crossing -- single digits per take-up, not the
       * hundreds the take-up itself emits.
       *
       * Gated on takeupPending, so the moment the gate releases the machine a
       * pulse ENDS the capture as it always did: the pass starting is a real
       * end, the ramp finishing is not. */
      elsDiagRearm(ctx, stop);
      return;
    }
    stop->diagCaptureTicks = (uint16_t)ctx->captureTick;
    stop->diagEndReason    = ELS_DIAG_END_PULSE;
    ctx->state = 0;
    stop->diagSeq++;   /* publish LAST: the seq bump is the ack that the block is complete and readable */
  } else {
    uint32_t bucket = ctx->captureTick / ELS_DIAG_BUCKET_TICKS;
    if (bucket < (uint32_t)ELS_DIAG_TRACE_BUCKETS) {
      /* int16 per bucket. Per-tick dZ is bounded by the int16 cast in the
       * scale-refresh loop and a settle tail is single-digit counts, so a
       * 10-tick bucket has orders of magnitude of headroom. A probe is not
       * worth saturation logic in a 100 kHz ISR. */
      stop->diagTrace[bucket] = (int16_t)(stop->diagTrace[bucket] + dZ);
    }

    stop->diagNetCounts += dZ;
    if (dZ != 0) {
      stop->diagSettleTicks = (int32_t)ctx->captureTick;
    }

    ctx->captureTick++;
    if (ctx->captureTick >= (uint32_t)ELS_DIAG_TRACE_BUCKETS * ELS_DIAG_BUCKET_TICKS) {
      /* Ran out of buckets before the servo moved again.
       *
       * UNDER v3 THIS IS THE SUCCESS CASE, which inverts v2's reading of the
       * same code and is why the schema id had to change. The gate is held for
       * exactly this window, so a healthy take-up reaches the end of it with
       * the servo still quiet: the trace covers the whole settle horizon and
       * settle_ticks is the measurement. Under v2, END_WINDOW meant "the servo
       * stayed quiet longer than I could watch" -- a floor, not a result.
       *
       * A capture that still ends END_PULSE under v3 means something drove the
       * servo during the hold, which the hold exists to prevent: read that as
       * the trace being cut short, and look for what moved. */
      stop->diagCaptureTicks = (uint16_t)ctx->captureTick;
      stop->diagEndReason    = ELS_DIAG_END_WINDOW;
      ctx->state = 0;
      stop->diagSeq++;
    }
  }
}

/* This probe does not watch servoEnableTask, so it never intervenes. Present
 * because the entry-point contract is fixed: Ramps.c calls the same set
 * whichever probe is selected. */
static inline bool elsDiagServoGate(elsDiagCtx_t *ctx, elsStop_t *stop,
                                    uint16_t servoModeNow) {
  (void)ctx; (void)stop; (void)servoModeNow;
  return false;
}

/* Task-tick hook (mode publication) — not this probe's concern. */
static inline void elsDiagTaskTick(elsDiagCtx_t *ctx, rampsSharedData_t *shared,
                                   uint16_t calRunning) {
  (void)ctx; (void)shared; (void)calRunning;
}

#endif /* ELS_DIAG_TAKEUP_SETTLE_H */
