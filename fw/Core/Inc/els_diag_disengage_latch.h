/* Disengage feed-latch detector -- diagnostic probe, schema 3.
 *
 * THIS PROBE INTERVENES. It is the only one that does, and that is deliberate.
 * Read this before using it.
 *
 * WHAT IT WATCHES. servoEnableTask sets servoMode = 1 whenever
 * (any syncEnable) && !elsStop.active. Dropping elsStop.enable clears active,
 * so for as long as any syncEnable is still set after a disengage, that task is
 * entitled to switch the feed back ON -- and nothing in the firmware ever
 * switches it off again. Measured in the emulator on 2026-08-16: roughly one
 * disengage in seven left the carriage feeding, 1.825 mm in 3 s and still going,
 * with the ELS stop simultaneously disarmed (the stop check is gated on enable).
 *
 * WHY IT SUPPRESSES RATHER THAN MERELY COUNTING. The only way to catch this on
 * the real machine by observation alone is to let the carriage actually run away
 * and watch it happen, on a lathe, with the stop disarmed. That is not a test,
 * it is the accident. So this probe REFUSES the re-assert whenever enable == 0
 * and records that it did. The dangerous outcome cannot occur while it is
 * compiled in, and the counter still proves whether the condition arises.
 *
 * A non-zero diagSeq is therefore the finding: it means the race DID occur on
 * this machine and was caught, not that anything went wrong during the run.
 *
 * IT IS ALSO MORE SENSITIVE THAN THE END-TO-END TEST, which is what motivated
 * it. The system test can only fail when the timing escalates all the way to
 * visible carriage travel; a quiet machine reproduces it rarely enough that an
 * A/B over 20 runs showed zero failures in BOTH arms and proved nothing. This
 * fires on the condition itself, whether or not it would have escalated.
 *
 * SCOPE OF THE INTERVENTION. Suppression is conditional on enable == 0, i.e. no
 * live ELS job. That is a deliberate behaviour change and it is why this belongs
 * in a diagnostic build only: with no ELS job, sync motion started by some other
 * path (axis sync) would also stop being auto-enabled while this is compiled in.
 * A release build carries none of this.
 */
#ifndef ELS_DIAG_DISENGAGE_LATCH_H
#define ELS_DIAG_DISENGAGE_LATCH_H

#include <stdint.h>
#include <stdbool.h>

/* Unused by this probe, but the geometry registers are published in every build
 * so a reader never has to infer them. Bucket width is meaningless here. */
#define ELS_DIAG_BUCKET_TICKS 0

/* Field meanings for schema 3 -- deliberately NOT the same as schema 2's, which
 * is exactly why diagSchema exists and why a reader must check it:
 *
 *   diagSeq          events caught. THE RESULT. 0 = never happened on this run
 *   diagNetCounts    same count, 32-bit, so a long run cannot wrap unnoticed
 *   diagCaptureTicks elsStop.active at the most recent event (expected 0)
 *   diagEndReason    1 once any event has been seen, else 0
 *   diagSettleTicks  servoMode observed at the most recent event
 *   diagTrace[]      unused, stays zero
 */
#define ELS_DIAG_LATCH_SEEN 1

static inline void elsDiagInit(elsDiagCtx_t *ctx, elsStop_t *stop) {
  stop->diagSchema       = ELS_DIAG_PROBE;
  stop->diagBucketTicks  = ELS_DIAG_BUCKET_TICKS;
  stop->diagBucketCount  = 0;
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

/* The ISR-side entry points are no-ops: this probe watches a FreeRTOS task, not
 * the tick. Every probe implements the whole contract so Ramps.c never learns
 * which one it is calling. */
static inline void elsDiagArm(elsDiagCtx_t *ctx, elsStop_t *stop) {
  (void)ctx; (void)stop;
}

static inline void elsDiagCaptureStart(elsDiagCtx_t *ctx) {
  (void)ctx;
}

static inline bool elsDiagCapturing(const elsDiagCtx_t *ctx) {
  (void)ctx;
  return false;   /* never; keeps the ISR's dZ/dServo arithmetic out of the build */
}

static inline void elsDiagTick(elsDiagCtx_t *ctx, elsStop_t *stop,
                               int32_t dZ, int32_t dServo) {
  (void)ctx; (void)stop; (void)dZ; (void)dServo;
}

/* THE ONE THAT MATTERS. Called from servoEnableTask at the point where it has
 * decided to assert servoMode = 1. Returns true to SUPPRESS that assert.
 *
 * enable == 0 means no live ELS job, so nothing should be re-enabling a
 * spindle-synced feed. Recording servoMode as it stood distinguishes "the feed
 * was already running and would have been kept on" (1) from "the feed was off
 * and this would have started it" (0) -- the second is the dangerous one, and
 * is what a post-disengage window produces. */
static inline bool elsDiagServoGate(elsDiagCtx_t *ctx, elsStop_t *stop,
                                    uint16_t servoModeNow) {
  (void)ctx;
  if (stop->enable != 0u) {
    return false;                       /* live job: normal behaviour */
  }
  stop->diagCaptureTicks = stop->active;
  stop->diagSettleTicks  = (int32_t)servoModeNow;
  stop->diagEndReason    = ELS_DIAG_LATCH_SEEN;
  stop->diagNetCounts++;
  stop->diagSeq++;    /* published LAST: the ack that the record above is complete */
  return true;                          /* refuse the re-assert */
}

#endif /* ELS_DIAG_DISENGAGE_LATCH_H */
