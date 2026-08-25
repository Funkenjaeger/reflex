/*
 * ELS phase-correction math, extracted as a pure function so it can be unit
 * tested in isolation (no HAL / struct deps). Used by applyPhaseCorrection in
 * Ramps.c and by the els_phase permutation tests.
 *
 * Background (2026-06 thread-phase investigation): the previous code computed
 * phaseError = idealAdvance - actualAdvance, which is only correct when the
 * spindle's idealAdvance and the carriage's actualAdvance run in the SAME
 * direction through a cut. On a real machine the Z DRO and the leadscrew can run
 * OPPOSITE (a cutting-direction servo move makes the DRO count the other way);
 * for those setups the quantity that is invariant at the Z-triggered stop is
 * idealAdvance + actualAdvance, and using the minus form makes the thread phase
 * walk 2x the configured backlash. The correct, config-derived sign is
 *
 *     droSign = stopDirection * cuttingDir
 *
 * (the cut feeds the carriage in stopDirection while the servo runs in
 * cuttingDir, so droSign is how a cutting-direction servo move changes the DRO).
 * phaseError = idealAdvance - droSign * actualAdvance handles BOTH polarities.
 *
 * PHASE OFFSET (primitive landed 2026-08-21; the register/UI half is in
 * todo.md). offsetSteps is an additive term, in leadscrew steps, for a
 * persistent phase offset applied ON TOP of the latched reference. The
 * operator-entered groove-widening offset (6a77c5b2) and the X-depth-derived
 * compound-infeed offset (6a77c598) are two sources of this ONE term. It is
 * summed into phaseError BEFORE the mod-pitch fold and the forward-bias, so
 * it inherits both: |offset| >= pitch aliases to (offset mod pitch), and a
 * NEGATIVE offset is not a small backward jog but (pitch - |offset|) in the
 * cutting direction (els_phase_offset_test.cpp T3-T5). The latch itself is
 * never touched; only the per-resume correction moves.
 *
 * DECIDED 2026-08-21 (Evan): entry is CUMULATIVE, with the running total
 * shown. Accumulation belongs to the host: the firmware will hold ONE
 * absolute total (an elsStop_t field set through a command/ack pair in the
 * calCommand idiom), and the UI adds each entered distance to the total it
 * reads back. The total resets on the same enable edge that clears
 * referenceLatched -- an offset is meaningless without the datum it offsets
 * -- and survives per-pass stop/resume within a job. Until those fields
 * exist the only call site passes 0, which is bit-for-bit the pre-feature
 * behavior (T1).
 */
#ifndef ELS_PHASE_H
#define ELS_PHASE_H

#include <stdint.h>
#include <math.h>

typedef struct {
  float   idealAdvance;
  float   actualAdvance;
  float   phaseError;
  float   correction;   /* folded + forward-biased, in leadscrew steps */
  int32_t stepsToAdd;   /* elsRoundSteps(correction) — added to servo.stepsToGo */
  int32_t cuttingDir;   /* +1/-1, exposed for callers/tests */
  int32_t droSign;      /* stopDirection*cuttingDir, exposed for tests */
} elsCorrResult_t;

/* ---- the 100 kHz ISR's path carries no library calls ------------------
 *
 * elsComputePhaseCorrection runs from applyPhaseCorrection, on the resume edge
 * -- which is cut-start, which is when the Modbus link died on 2026-08-23.
 * fmodf and lroundf were the only libm calls reachable from
 * SynchroRefreshTimerIsr, and on Cortex-M4F neither is an instruction: both are
 * software routines.
 *
 * WHY fmodf WAS THE WORSE OF THE TWO. Its cost is not constant. newlib reduces
 * by iterating over the exponent difference between the operands, and the
 * dividend here is phaseError -- spindle advance accumulated since the latch,
 * which grows for as long as the job runs (154934 steps against a 711-step
 * pitch in a sample captured off the machine). So the single most expensive
 * tick in the ISR got more expensive the longer the machine had been cutting.
 *
 * WHY THIS WORK IS MADE CHEAPER IN PLACE RATHER THAN DEFERRED TO A TASK.
 * todo.md lists applyPhaseCorrection first among things to move OUT of the
 * ISR. It must not be. The correction it computes is added to servo.stepsToGo
 * and has to be fully executed BEFORE the pass starts feeding; handing it to a
 * task means the pass can begin while the correction is still queued, which is
 * precisely the out-of-phase-cut signature under investigation. The step pulse
 * is not the only thing here that must happen now.
 */

/* x mod pitch, truncated toward zero -- fmodf's exact definition, in two FPU
 * conversions and a multiply-subtract.
 *
 * PRECISION. This is deliberate catastrophic cancellation and it is safe twice
 * over. The absolute error is bounded by the ULP of the operands (about 0.0156
 * steps at 154934), and the result is rounded to whole steps immediately after.
 * And if the truncation ever lands one integer off, the error is exactly ONE
 * PITCH -- which the fold and forward-bias below normalize away completely,
 * because one pitch is the same place on a single-start thread.
 *
 * The out-of-range fallback needs a spindle advance of 2^31 pitches to reach,
 * so it is unreachable on any real geometry; it is here because an int32
 * conversion that overflows is undefined, and a silent wrong answer on this
 * path moves metal.
 */
static inline float elsFmodPitch(float x, float pitch)
{
  /* 2^22. Below this a float32 ULP is under half a step, so the
   * multiply-subtract is sub-step accurate and provably agrees with fmodf
   * (els_phase_libm_equiv_test). ABOVE it the whole computation has already
   * lost step resolution -- at 1.4e8 steps the ULP is 16 -- and that is a
   * separate defect, logged rather than papered over here. Deferring to the
   * library routine above the threshold keeps this change a pure speed-up
   * with no behavioural difference anywhere, and costs nothing in practice:
   * the expensive fmodf case is the one this threshold excludes. */
  if (x < -4194304.0f || x > 4194304.0f) return fmodf(x, pitch);
  float q = x / pitch;
  return x - pitch * (float)(int32_t)q;
}

/* lroundf's rounding rule -- nearest, ties AWAY from zero -- without the call.
 * The FPU's own VCVT rounds ties to even, so this cannot just be a cast; and
 * gcc will not inline lroundf on this core for exactly that reason. Safe
 * because correction is already folded to within one pitch, so the addend
 * cannot push it anywhere near the limits of float precision. */
static inline int32_t elsRoundSteps(float correction)
{
  return (int32_t)(correction + (correction >= 0.0f ? 0.5f : -0.5f));
}

static inline elsCorrResult_t elsComputePhaseCorrection(
    int32_t deltaSpindle, int32_t deltaZ,
    int32_t syncRatioNum, int32_t syncRatioDen,
    float threadPitchSteps, float zCountsPerPitch,
    int16_t stopDirection,
    int32_t offsetSteps)   /* phase offset, leadscrew steps; 0 = none (exact pre-feature path) */
{
  elsCorrResult_t r;

  r.idealAdvance  = (float)deltaSpindle * (float)syncRatioNum / (float)syncRatioDen;
  r.actualAdvance = (float)deltaZ * threadPitchSteps / zCountsPerPitch;

  int32_t cuttingDir = (syncRatioNum > 0) ? 1 : -1;
  if (threadPitchSteps * zCountsPerPitch < 0.0f) {
    cuttingDir = -cuttingDir;
  }
  r.cuttingDir = cuttingDir;

  /* DRO-to-leadscrew polarity for a valid setup (see header note). */
  int32_t droSign = (int32_t)stopDirection * cuttingDir;
  r.droSign = droSign;

  r.phaseError = r.idealAdvance - (float)droSign * r.actualAdvance + (float)offsetSteps;

  float pitch      = threadPitchSteps;
  float correction = elsFmodPitch(r.phaseError, pitch);
  if (correction >  pitch / 2.0f) correction -= pitch;
  if (correction < -pitch / 2.0f) correction += pitch;

  /* Forward-bias the jog into the cutting direction so it never unloads the
   * lash the takeup just took up (physical requirement, polarity-independent). */
  if ((float)cuttingDir * correction < 0.0f) {
    correction += (float)cuttingDir * pitch;
  }

  r.correction  = correction;
  r.stepsToAdd  = elsRoundSteps(correction);
  return r;
}

#endif /* ELS_PHASE_H */
