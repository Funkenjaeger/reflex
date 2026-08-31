/*
 * STOP-OVERSHOOT PROBE -- schema 7. What happens to the carriage AFTER the ELS
 * stop fires, and whether the firmware caused any of it.
 *
 * ---- THE OBSERVATION THIS EXISTS TO EXPLAIN --------------------------------
 * 2026-08-28, elspi, air passes: the carriage reliably ends up ~0.0022"
 * (~56 um, ~28 leadscrew steps, ~11 Z counts) past the programmed stop.
 * Evan's two findings, and they are what rule out the easy answers:
 *
 *   1. It is REPEATABLE pass after pass, not scattered.
 *   2. Pushing the carriage back by hand does not recover it -- not even a
 *      tenth (0.0001"). It is rigid where it stopped.
 *
 * (2) killed the model that had been assumed until then. If this were servo
 * overshoot-and-recover, the carriage would be sitting forward of the leadscrew
 * inside the 370-step lash and would push back freely. It does not move, so
 * whatever put it there is holding it there.
 *
 * ---- WHY EXISTING DATA COULD NOT ANSWER IT ---------------------------------
 * servoBacklog and stepsToGo are already published and already recorded in
 * phase_live.jsonl -- and that recorder samples at 0.97 Hz. The stop trigger
 * and anything draining after it happen in MILLISECONDS. Three orders of
 * magnitude too slow: its zeros describe the steady state and say nothing about
 * the trigger instant. That is the whole reason this measurement has to live in
 * the ISR.
 *
 * ---- WHAT IT MEASURES, AND WHAT IT CANNOT ----------------------------------
 * From the tick the stop latches (elsStop.active 0 -> 1) it accumulates:
 *
 *   diagNetCounts   signed Z counts the carriage travelled AFTER the trigger.
 *                   This is the overshoot, per pass, in the units the stop
 *                   itself is judged in. ~11 counts is the number to explain.
 *   diagReserved    servo steps the FIRMWARE EMITTED after the trigger, as an
 *     [0..1]        int32. This is the discriminator.
 *   diagSettleTicks last tick that saw nonzero dZ -- when the carriage stopped.
 *   diagTrace[]     per-bucket signed dZ, so the SHAPE is visible: a smooth
 *                   decay reads differently from a step.
 *
 * READ IT LIKE THIS:
 *
 *   steps emitted > 0  ->  the FIRMWARE commanded the overshoot. Its cause is
 *                          in this repo and is fixable here.
 *   steps emitted == 0 ->  the firmware sent nothing and the carriage moved
 *                          anyway. The cause is DOWNSTREAM of the pulse train.
 *
 * AND THE SECOND CASE DOES NOT SAY "MECHANICAL". This is the limit to state
 * plainly, because the obvious misreading is available and wrong: dServo counts
 * what the firmware EMITTED, not what the drive DID. A servo drive that fails
 * to honour its commanded position when the pulse train cuts off abruptly --
 * ending up somewhere and holding it rigidly -- is indistinguishable here from
 * drivetrain coast, and is fully consistent with finding (2) above. Step/dir is
 * open loop from this side; the drive closes its own loop on its own encoder
 * and never reports back. Separating those two needs the drive's own following-
 * error readout, or a companion experiment (an abrupt JOG stop with the ELS out
 * of the picture, which would exonerate this code path entirely if the same
 * overshoot appears).
 *
 * Evan, 2026-08-28: "It's at least *possible* that the servo itself is doing
 * something wonky, e.g. not honoring position when the steps cut off abruptly.
 * I doubt it, but it's not impossible; just wouldn't assume so until proving it
 * beyond a reasonable doubt."
 *
 * ---- WHY IT CAPTURES EVERY TICK -------------------------------------------
 * elsDiagCapturing() returns true unconditionally so elsDiagTick runs on every
 * ISR tick, which is the only way to see the elsStop.active edge from inside a
 * probe -- the framework has no stop-trigger hook and adding one would change
 * the entry-point contract for every other probe. The cost is the dZ/dServo
 * arithmetic in Ramps.c's guarded block running always. That is a DIAGNOSTIC
 * build; it is exactly the trade the scratchpad exists to make, and the tick
 * budget doubled to 2000 cycles on 2026-08-28.
 */
#ifndef ELS_DIAG_STOP_OVERSHOOT_H
#define ELS_DIAG_STOP_OVERSHOOT_H

#include <stdint.h>
#include <stdbool.h>
#include "els_isr_rate.h"

/* END REASONS, THIS PROBE'S OWN. The numeric space is shared across probes but
 * the MEANINGS are per-schema -- which is precisely what diagSchema exists to
 * disambiguate, and why a reader must check it before interpreting anything
 * here. takeup-settle-v3 uses 1 = END_PULSE / 2 = END_WINDOW with END_WINDOW as
 * its success case; this probe's success case is the opposite end of the same
 * numbers, so they are named for what they mean HERE rather than reused by
 * spelling.
 *
 * SETTLED (1) is the complete measurement: the carriage stopped moving and
 * stayed stopped, so diagNetCounts is the whole overshoot.
 * WINDOW (2) means the trace ran out while Z was still moving -- read
 * diagNetCounts as a FLOOR, not a result, and look at why 20 ms was not
 * enough. */
#define ELS_DIAG_STOP_END_SETTLED 1
#define ELS_DIAG_STOP_END_WINDOW  2

/* 400 us per bucket, as a duration so it keeps its width across a rate change.
 * 50 buckets = 20 ms of trace, comfortably past the longest settle ever
 * observed on this machine (17.9 ms). */
#define ELS_DIAG_BUCKET_TICKS ELS_US_TO_TICKS(400)

/* Quiet run that ends a capture. 2 ms of no Z motion means the carriage has
 * stopped; long enough not to end a capture between two counts of a slow
 * decay, short enough that a pass's capture is finished well before the next
 * one starts. */
#define ELS_DIAG_STOP_QUIET_TICKS ELS_MS_TO_TICKS(2)

/* Probe-private state. Sanctioned by the elsDiagCtx_t comment in Ramps.h: two
 * shared words for state/captureTick, anything more lives in the probe. */
static int32_t  elsDiagStopServoSteps;   /* steps emitted since the trigger */
static int32_t  elsDiagStopQuietRun;     /* consecutive ticks with dZ == 0 */
static uint16_t elsDiagStopPrevActive;   /* for the 0 -> 1 edge */

static inline void elsDiagStopReset(elsDiagCtx_t *ctx, elsStop_t *stop) {
  ctx->state       = 0;
  ctx->captureTick = 0;
  elsDiagStopServoSteps = 0;
  elsDiagStopQuietRun   = 0;
  stop->diagSettleTicks  = 0;
  stop->diagNetCounts    = 0;
  stop->diagCaptureTicks = 0;
  stop->diagEndReason    = 0;
  stop->diagReserved[0]  = 0;
  stop->diagReserved[1]  = 0;
  for (int32_t i = 0; i < ELS_DIAG_TRACE_BUCKETS; i++) {
    stop->diagTrace[i] = 0;
  }
}

static inline void elsDiagInit(elsDiagCtx_t *ctx, elsStop_t *stop) {
  stop->diagSchema      = ELS_DIAG_SCHEMA_STOP_OVERSHOOT;
  stop->diagBucketTicks = (uint16_t)ELS_DIAG_BUCKET_TICKS;
  stop->diagBucketCount = (uint16_t)ELS_DIAG_TRACE_BUCKETS;
  elsDiagStopPrevActive = 0;
  elsDiagStopReset(ctx, stop);
}

/* Take-up initiation. Nothing to do: this probe triggers off the STOP, which
 * is the other end of the pass. Present because the contract is fixed. */
static inline void elsDiagArm(elsDiagCtx_t *ctx, elsStop_t *stop) {
  (void)ctx; (void)stop;
}

static inline void elsDiagCaptureStart(elsDiagCtx_t *ctx) {
  (void)ctx;
}

/* Always true -- see the header comment. The probe needs every tick to see the
 * elsStop.active edge. */
static inline bool elsDiagCapturing(const elsDiagCtx_t *ctx) {
  (void)ctx;
  return true;
}

static inline int32_t elsDiagExtraDwell(const elsDiagCtx_t *ctx) {
  (void)ctx;
  return 0;   /* this probe does not hold any gate open */
}

static inline void elsDiagTick(elsDiagCtx_t *ctx, elsStop_t *stop,
                               int32_t dZ, int32_t dServo) {
  uint16_t activeNow = stop->active;

  /* ---- the trigger edge --------------------------------------------------
   * Rising edge of elsStop.active IS the stop firing: Ramps.c sets it in the
   * same tick the Z crossing is detected. Starting here makes diagNetCounts
   * the travel AFTER the trigger by construction, with no need to know the
   * stop position or to subtract two absolute readings. */
  if (activeNow != 0 && elsDiagStopPrevActive == 0) {
    elsDiagStopReset(ctx, stop);
    ctx->state = 2;
  }
  elsDiagStopPrevActive = activeNow;

  if (ctx->state != 2) {
    return;
  }

  /* ---- accumulate -------------------------------------------------------- */
  int32_t bucket = (int32_t)(ctx->captureTick / (uint32_t)ELS_DIAG_BUCKET_TICKS);
  if (bucket < ELS_DIAG_TRACE_BUCKETS) {
    int32_t v = (int32_t)stop->diagTrace[bucket] + dZ;
    if (v >  32767) v =  32767;
    if (v < -32768) v = -32768;
    stop->diagTrace[bucket] = (int16_t)v;
  }

  stop->diagNetCounts   += dZ;
  elsDiagStopServoSteps += dServo;

  if (dZ != 0) {
    stop->diagSettleTicks = (int32_t)ctx->captureTick;
    elsDiagStopQuietRun = 0;
  } else {
    elsDiagStopQuietRun++;
  }

  ctx->captureTick++;

  /* ---- two ways to finish ------------------------------------------------
   * SETTLED is the success case: the carriage stopped and stayed stopped, so
   * diagNetCounts is the complete overshoot and diagSettleTicks is when it
   * finished. WINDOW means it was still moving when the trace ran out --
   * read diagNetCounts as a FLOOR, not a result. */
  bool quiet  = (elsDiagStopQuietRun >= ELS_DIAG_STOP_QUIET_TICKS);
  bool full   = (ctx->captureTick >=
                 (uint32_t)ELS_DIAG_TRACE_BUCKETS * (uint32_t)ELS_DIAG_BUCKET_TICKS);

  if (quiet || full) {
    stop->diagCaptureTicks = (uint16_t)ctx->captureTick;
    stop->diagEndReason    = quiet ? ELS_DIAG_STOP_END_SETTLED
                                   : ELS_DIAG_STOP_END_WINDOW;
    /* The discriminator, published last of the payload and before diagSeq --
     * the ORDERING INVARIANT in Ramps.h: diagSeq must not advertise a capture
     * whose payload is not yet written. */
    stop->diagReserved[0] = (uint16_t)((uint32_t)elsDiagStopServoSteps & 0xFFFFu);
    stop->diagReserved[1] = (uint16_t)(((uint32_t)elsDiagStopServoSteps >> 16) & 0xFFFFu);
    ctx->state = 0;
    stop->diagSeq++;
  }
}

static inline bool elsDiagServoGate(elsDiagCtx_t *ctx, elsStop_t *stop,
                                    uint16_t servoModeNow) {
  (void)ctx; (void)stop; (void)servoModeNow;
  return false;
}

static inline void elsDiagTaskTick(elsDiagCtx_t *ctx, rampsSharedData_t *shared,
                                   uint16_t calRunning) {
  (void)ctx; (void)shared; (void)calRunning;
}

#endif /* ELS_DIAG_STOP_OVERSHOOT_H */
