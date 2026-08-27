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
 * PRECISION. This is deliberate catastrophic cancellation. The absolute error
 * is bounded by the ULP of the operands (about 0.0156 steps at 154934), and the
 * result is rounded to whole steps immediately after.
 *
 * QUALIFIED 2026-08-27. This comment used to go on to argue that a truncation
 * landing one integer off is self-correcting, "because one pitch is the same
 * place on a single-start thread". Measured, that argument survives on its own
 * terms and is why the defect below stayed invisible -- but it answers the
 * wrong question. What it establishes is that the THREAD PHASE comes out the
 * same; what it quietly concedes is that the CORRECTION DOES NOT. In the worst
 * case found (deltaSpindle=13829522, deltaZ=-4487) the shipped fmodf path
 * commands +711 steps and the truncating path commands 0 -- a difference of
 * 0.99984 pitches, so the same groove, but a whole pitch (1/18") difference in
 * how far the carriage jogs before the pass starts feeding. That is a
 * behavioral change introduced by a substitution whose entire justification was
 * that the answer does not change, which is reason enough to not have it.
 *
 * The narrower and more important point: the fold below can only normalize a
 * residual it can SEE, and the double-rounded subtraction returned exactly
 * 0.0f -- an in-range, entirely plausible "phaseError is a whole multiple of
 * pitch" answer that the fold passes straight through untouched. No range check
 * placed after it can distinguish that from a legitimate zero. The error has to
 * be prevented in the residual, not repaired after it.
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
  float q  = x / pitch;
  int32_t qi = (int32_t)q;
  /* q = x/pitch is correctly ROUNDED (IEEE754 float division), but
   * "correctly rounded" is not "correctly truncated". When the true
   * quotient sits a hair below an integer -- e.g. 2234.99997 -- the
   * nearest representable float can BE that integer (2235.0f) once the
   * true value is closer to it than to the next float down, and
   * truncating 2235.0f is one whole pitch off from truncating 2234.99997.
   * (Reproduced at deltaSpindle=13829522, deltaZ=-4487, pitch=711.111:
   * q rounds up to exactly 2235.0f.) This is the same failure the header
   * above already documents for x's own ULP, just one step removed -- it
   * lives in q = x/pitch, not in x.
   *
   * A PLAIN range check on x - pitch*qi is NOT enough to catch this: that
   * subtraction rounds TWICE (once forming pitch*qi, once subtracting),
   * and at this magnitude the second rounding can land EXACTLY on 0.0f --
   * a perfectly plausible-looking "phaseError is a whole multiple of
   * pitch" answer -- even though qi is one too many and the true residual
   * is +711 (measured: naive x - pitch*qi = 0.000000 here, masking the
   * error completely). fmaf computes x - pitch*qi with a SINGLE rounding
   * (VFMA is a hardware instruction on Cortex-M4F, not a software
   * routine -- unlike fmodf/lroundf this is not a libm call), and that
   * single rounding recovers the true sign: -0.0227, correctly revealing
   * that qi overshot. The range check below then repairs it exactly as a
   * truncating division always occasionally needs -- a compare that is
   * essentially always not-taken, and when it IS taken, one FPU add/sub.
   * No libm call, no double (Cortex-M4F has no FP64 hardware; a double
   * divide -- or even a double multiply-subtract -- is a compiler softfp
   * call here, trading one library call for another).
   *
   * PRECONDITION: pitch > 0. The range check below is written for a positive
   * pitch and would push the result the WRONG WAY for a negative one, which
   * narrows this function's domain versus the plain multiply-subtract it
   * replaces. That is safe on every path that reaches here, verified rather
   * than assumed: the host builds threadPitchSteps as
   * Fraction(abs(...), abs(...)) (els_fsm.py push_thread_geometry), so it is
   * never negative, and the only other value it writes is 0.0 for TURNING,
   * which applyPhaseCorrection is gated out of by the threadPitchSteps != 0
   * check in Ramps.c. Keep that true: a host that ever writes a signed pitch
   * needs this fold made sign-agnostic first. */
  float r = fmaf(-pitch, (float)qi, x);
  if (x >= 0.0f) {
    if (r < 0.0f)         r += pitch;
    else if (r >= pitch)  r -= pitch;
  } else {
    if (r > 0.0f)         r -= pitch;
    else if (r <= -pitch) r += pitch;
  }
  return r;
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
