/* Mode watch -- diagnostic probe, schema 5 (mode-watch-v2).
 *
 * WHAT IT PUBLISHES. The firmware-derived machine mode (els_machine_mode.h),
 * recomputed once per servoEnableTask tick (~100 ms) and written to the
 * scratchpad with a transition counter. This is rung 1 of the direction set
 * by the 2026-08-16 architecture review: the firmware states what the
 * machine is doing, as a pure function of its own registers, changing no
 * behavior — so the UI can compare its model against observed reality for
 * weeks ("rung 2") before any authority moves. Promotion to a real register
 * (and a protocolVersion bump) happens only after the taxonomy survives
 * that data.
 *
 * IT ALSO CARRIES schema 3's INTERVENTION, deliberately. One probe fits a
 * build (see DIAG.md, "one probe at a time"), and the machine cannot be
 * flashed remotely — a flash costs a physical session — so the probe that
 * collects mode data for weeks must ALSO keep the disengage-latch counter
 * running: it suppresses servoEnableTask's re-assert while elsStop.enable
 * == 0 and counts the refusals that would have switched the feed on. With
 * the F1/F2 firmware fixes and the UI's sync-first teardown on this branch,
 * that counter is EXPECTED to stay at zero; a nonzero count means some path
 * still leaves sync armed across a disengage and is a finding in its own
 * right (and a divergence datapoint).
 * The suppression is also a safety net while this branch gets its first
 * hardware exposure. Same caveat as schema 3: with no live ELS job,
 * non-ELS axis sync stops being auto-enabled while this is compiled in.
 *
 * WHY v2 EXISTS (schema 4 is RETIRED, its id burned). v1 counted EVERY
 * refusal, and the 2026-08-17 hardware sessions showed what that measures:
 * during an enable-less power feed the re-assert condition is simply true on
 * every 100 ms tick, so the counter climbed 1719 in an afternoon — all with
 * servoMode already 1, where refusing to assert servoMode = 1 changes
 * nothing at all. The one event the counter exists to catch — the re-assert
 * SWITCHING THE FEED ON (servoMode 0) after a disengage — would have been
 * three digits of noise deep. v2 still suppresses every refusal identically
 * (the safety net is untouched); it just no longer counts the no-ops.
 * Changing what an existing schema's number means would silently corrupt the
 * recorded round-1/2 data, hence the new id — same discipline, same reason
 * as takeup-settle v1 -> v2.
 *
 * Field meanings for schema 5 -- NOT schema 2's, 3's, or 4's; check
 * diagSchema:
 *
 *   diagCaptureTicks  CURRENT derived mode (ELS_MMODE_*)
 *   diagSettleTicks   previous mode -- the from-side of the last transition
 *   diagSeq           mode-transition counter. Bumped LAST, after both mode
 *                     fields, so a reader that edge-detects seq sees a
 *                     consistent (from, to) pair
 *   diagNetCounts     EFFECTIVE latch suppressions (32-bit, cumulative):
 *                     refusals where servoMode was 0 and the re-assert would
 *                     have switched the feed on. EXPECT 0, and unlike v1 a
 *                     nonzero value here is ALWAYS a finding
 *   diagEndReason     1 once any counted suppression has been seen, else 0
 *   diagTrace[0]      servoMode at the most recent COUNTED suppression --
 *                     0 by construction; anything else is a probe bug
 *   diagTrace[1]      elsStop.active at the most recent counted suppression
 *   diagTrace[2..]    unused, stay zero
 *
 * A suppression alone does not bump diagSeq (that counts MODE transitions),
 * but every real disengage changes the mode within the same ~100 ms tick,
 * so a seq-edge reader still picks the event up promptly; diagNetCounts is
 * cumulative regardless, so nothing is lost between reads.
 */
#ifndef ELS_DIAG_MODE_WATCH_H
#define ELS_DIAG_MODE_WATCH_H

#include <stdint.h>
#include <stdbool.h>

/* No trace buckets; the geometry registers still publish, as always. */
#define ELS_DIAG_BUCKET_TICKS 0

#define ELS_DIAG_LATCH_SEEN 1

static inline void elsDiagInit(elsDiagCtx_t *ctx, elsStop_t *stop) {
  stop->diagSchema       = ELS_DIAG_PROBE;
  stop->diagBucketTicks  = ELS_DIAG_BUCKET_TICKS;
  stop->diagBucketCount  = 0;
  /* Boot state is all-zero registers, which elsDeriveMachineMode maps to
   * ELS_MMODE_OFF (0) by construction — so publishing zeros here is the
   * correct initial mode, not merely a cleared block. */
  stop->diagCaptureTicks = 0;
  stop->diagSettleTicks  = 0;
  stop->diagEndReason    = 0;
  stop->diagNetCounts    = 0;
  stop->diagSeq          = 0;
  for (int i = 0; i < 2; i++) stop->diagTrace[i] = 0;
  ctx->state             = 0;   /* last published mode */
  ctx->captureTick       = 0;
}

/* ISR-side entry points are no-ops: this probe runs off the 100 ms task.
 * Every probe implements the whole contract so Ramps.c never learns which
 * one it is calling. */
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

/* Schema 3's intervention, carried forward verbatim in BEHAVIOR: every
 * re-assert while enable == 0 is refused, exactly as before (see
 * els_diag_disengage_latch.h — suppression instead of observation is what
 * makes the dangerous outcome impossible). Only the ACCOUNTING narrowed in
 * v2: a refusal with servoMode already 1 is a no-op (asserting servoMode = 1
 * over servoMode 1 changes nothing) and enable-less power feed produces one
 * per tick, so counting them buried the signal — see the header comment.
 * servoMode 2 cannot reach here; the call site excludes jog. */
static inline bool elsDiagServoGate(elsDiagCtx_t *ctx, elsStop_t *stop,
                                    uint16_t servoModeNow) {
  (void)ctx;
  if (stop->enable != 0u) {
    return false;                       /* live job: normal behaviour */
  }
  if (servoModeNow == 0u) {             /* would have SWITCHED THE FEED ON */
    stop->diagTrace[0]  = (int16_t)servoModeNow;
    stop->diagTrace[1]  = (int16_t)stop->active;
    stop->diagEndReason = ELS_DIAG_LATCH_SEEN;
    stop->diagNetCounts++;
  }
  return true;                          /* refuse the re-assert either way */
}

/* THE ONE THAT MATTERS. Called once per servoEnableTask iteration, after
 * the re-assert decision, so the published mode reflects this tick's
 * outcome. Derives the machine mode and publishes it on change; diagSeq
 * bumps last so (diagSettleTicks, diagCaptureTicks) are consistent when the
 * edge is observed. */
static inline void elsDiagTaskTick(elsDiagCtx_t *ctx, rampsSharedData_t *shared,
                                   uint16_t calRunning) {
  uint16_t mode = elsDeriveMachineMode(shared, calRunning);
  if (mode != (uint16_t)ctx->state) {
    shared->elsStop.diagSettleTicks  = (int32_t)ctx->state;
    shared->elsStop.diagCaptureTicks = mode;
    ctx->state = mode;
    shared->elsStop.diagSeq++;   /* published LAST: the ack that the pair is consistent */
  }
}

#endif /* ELS_DIAG_MODE_WATCH_H */
