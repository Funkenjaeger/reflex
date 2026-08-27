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
  int32_t stepsToAdd;   /* lroundf(correction) — added to servo.stepsToGo */
  int32_t cuttingDir;   /* +1/-1, exposed for callers/tests */
  int32_t droSign;      /* stopDirection*cuttingDir, exposed for tests */
} elsCorrResult_t;

/* Reduce deltaSpindle modulo "one whole thread pitch", exactly, in INTEGER
 * spindle counts, before it is ever widened to float. This is the fix for
 * the float32-ULP resolution loss documented above elsComputePhaseCorrection
 * (idealAdvance grows for the whole job since the reference was latched, and
 * above 2^23 its ULP exceeds one leadscrew step).
 *
 * WHY THE MODULUS IS EXACT, NOT APPROXIMATE (this was the open design
 * question -- "pitch in spindle counts is generally not an integer, so this
 * needs care"): pitch in LEADSCREW STEPS (threadPitchSteps) is indeed not
 * generally an integer -- e.g. 711.111... steps at elspi's real 18 TPI. But
 * that is the wrong quantity to reduce by. A thread's sync ratio is BY
 * DEFINITION "Z advance per one spindle revolution equals one pitch" --
 * reflex-ui's push_thread_geometry (els_fsm.py) computes
 *   threadPitchSteps = spindle_pitch_mm / servo_ratio
 *   syncRatioNum/syncRatioDen = spindle_pitch_mm / (PPR * servo_ratio)
 * from the SAME spindle_pitch_mm, so PPR * (syncRatioNum/syncRatioDen) ==
 * threadPitchSteps exactly (in the rationals). Equivalently, the spindle-count
 * period of one pitch is
 *   P = threadPitchSteps * syncRatioDen / syncRatioNum
 * computed here directly from the three registers already passed in --
 * no new register or PPR parameter needed. This P recovers PPR (an exact
 * integer) up to only the pre-existing, negligible float32 rounding already
 * present in the threadPitchSteps register itself (a value in the tens to
 * low thousands for any real thread, nowhere near the ULP-doubling range) --
 * NOT the job-duration-dependent error this patch removes.
 *
 * ------------------------------------------------------------------------
 * THE RESIDUAL. THIS IS NOT EXACT, AND SHIPPING IT ANYWAY WAS A DECISION.
 * ------------------------------------------------------------------------
 * An earlier draft of this comment ended "there is no residual care left to
 * take." That was wrong, and the sweep in els_phase_reduce_test.cpp measures
 * exactly how wrong. What follows is the honest version. Evan approved shape
 * (a) on 2026-08-27 ON CONDITION the residual was written down here, so do not
 * quietly re-tighten this wording back into a correctness claim.
 *
 * THE FIRMWARE HAS NO TRUE PPR. There is no register anywhere in the map
 * carrying an integer pulses-per-revolution (grep confirms). encoder_ppr and
 * gear_ratio_num/den live host-side only, in reflex-ui's InputDispatcher, and
 * never cross Modbus -- axis.py's _set_sync_ratio folds them into a REDUCED
 * Fraction before it is pushed, and reduction destroys PPR as an integer (at
 * elspi's 18 TPI, PPR=1000 arrives as 32/45). So the period P above must be
 * recovered from threadPitchSteps, which is an ALREADY-ROUNDED float32:
 * 711.111145 where the true value is 711.111111... .
 *
 * WHAT THAT COSTS. P itself lands on the right integer with enormous margin
 * (1000.00005 -> 1000; the test asserts this rather than assuming it). The
 * residual is one step further down: the reduction removes whole multiples of
 * the TRUE pitch, while the fold below folds modulo the ROUNDED pitch. Those
 * differ by ~3.4e-5 leadscrew steps per revolution at 18 TPI, so near a fold
 * boundary the decision can flip by a FULL PITCH -- 1.41 mm, a scrapped
 * thread, not a small error. Measured over 811k sweep points, both
 * stopDirection polarities, elspi geometry:
 *
 *     wrong-groove (>pitch/4) rate at 18 TPI, vs true geometry
 *       20k-revolution job : 0.0454% unreduced -> 0.0010% reduced   (45x)
 *       full int32 span    : 4.7220% unreduced -> 0.0010% reduced (4700x)
 *
 * The number that matters is that the reduced rate is FLAT in job length while
 * the unreduced one grows without bound -- that is the whole point of the fix.
 * The 0.0010% that survives does NOT come from deltaSpindle at all; it comes
 * from the deltaZ term folding against the same rounded pitch, so it is a
 * PRE-EXISTING property of the register set that this patch neither causes nor
 * cures. At 4 TPI, where threadPitchSteps (3200.0) is exactly representable,
 * the residual is exactly zero in both regimes -- which is the control that
 * pins the cause on the rounded register rather than on the reduction.
 *
 * THE EXACT FIX, when the register-append queue allows it: give the firmware
 * the true integer PPR -- either as its own register, or by pushing the sync
 * ratio UNREDUCED so that syncRatioDen carries PPR as a factor. Either one
 * makes P exact by construction and lets the fold use a pitch derived from
 * integers instead of from a rounded float. Both are register-map changes and
 * therefore a coordinated decision, which is why neither is done here. */
static inline int32_t elsReduceDeltaSpindle(
    int32_t deltaSpindle, int32_t syncRatioNum, int32_t syncRatioDen,
    float threadPitchSteps)
{
  if (syncRatioNum == 0 || threadPitchSteps == 0.0f) {
    return deltaSpindle;   /* turning, or degenerate config: no reduction */
  }
  double pd = (double)threadPitchSteps * (double)syncRatioDen
              / (double)syncRatioNum;
  if (pd < 0.0) pd = -pd;
  int64_t P = (int64_t)(pd + 0.5);   /* round to nearest; P is ~PPR */
  if (P <= 0) {
    return deltaSpindle;
  }
  int64_t d = (int64_t)deltaSpindle;
  int64_t r = ((d % P) + P) % P;     /* always non-negative; see 08-26 harness */
  return (int32_t)r;
}

static inline elsCorrResult_t elsComputePhaseCorrection(
    int32_t deltaSpindle, int32_t deltaZ,
    int32_t syncRatioNum, int32_t syncRatioDen,
    float threadPitchSteps, float zCountsPerPitch,
    int16_t stopDirection,
    int32_t offsetSteps)   /* phase offset, leadscrew steps; 0 = none (exact pre-feature path) */
{
  elsCorrResult_t r;

  deltaSpindle = elsReduceDeltaSpindle(deltaSpindle, syncRatioNum, syncRatioDen,
                                        threadPitchSteps);

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
  float correction = fmodf(r.phaseError, pitch);
  if (correction >  pitch / 2.0f) correction -= pitch;
  if (correction < -pitch / 2.0f) correction += pitch;

  /* Forward-bias the jog into the cutting direction so it never unloads the
   * lash the takeup just took up (physical requirement, polarity-independent). */
  if ((float)cuttingDir * correction < 0.0f) {
    correction += (float)cuttingDir * pitch;
  }

  r.correction  = correction;
  r.stepsToAdd  = (int32_t)lroundf(correction);
  return r;
}

#endif /* ELS_PHASE_H */
