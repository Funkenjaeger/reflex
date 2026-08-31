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
 * 711.111084 where the true value is 711.111111... .
 *
 * (That figure read 711.111145 until 2026-08-28 and was wrong in the direction
 * as well as the digit -- the nearest float32 to 64000/90 is BELOW the true
 * value, not above, so anyone reasoning about which way the residual pushes
 * from this comment got the sign backwards. Checked against the machine rather
 * than recomputed: elspi's own phase_live.jsonl captures carry
 * threadPitchSteps = 711.111083984375.)
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
 *       20k-revolution job : 0.0554% unreduced -> 0.0003% reduced  (185x)
 *       full int32 span    : 0.9977% unreduced -> 0.0000% reduced
 *
 * RE-MEASURED 2026-08-28 AT THE RIGHT MACHINE. Those two rows replace an
 * earlier pair (0.0454% -> 0.0010%, 45x; 4.7220% -> 0.0010%, 4700x) that was
 * swept at 1000 PPR -- InputDispatcher.encoder_ppr's class default, not this
 * lathe. elspi runs a 6144 PPR spindle encoder (ui/AGENTS.md's commissioned
 * table, and its own diag/phase_live.jsonl captures: syncRatio 25/216,
 * threadPitchSteps 711.111084). The old sweep was self-consistent -- 1000 *
 * 32/45 is also 711.111 -- which is precisely why it never went red while
 * measuring a machine that does not exist.
 *
 * The number that matters is unchanged by the correction: the reduced rate
 * stays flat as the job grows while the unreduced one climbs by more than an
 * order of magnitude -- that is the whole point of the fix.
 *
 * WHAT SURVIVES, AND WHAT IT IS. The residual does NOT come from deltaSpindle;
 * it comes from the deltaZ term folding against the same rounded pitch, so it
 * is a PRE-EXISTING property of the register set that this patch neither causes
 * nor cures. The control that pins that cause is 4 TPI, where threadPitchSteps
 * (3200.0) is exactly representable -- and correcting the geometry split the
 * control's result in two, which the old wording had conflated:
 *
 *   Wrong-groove errors DO vanish at exact pitch: zero, both regimes, both
 *   oracles, 2.8M samples. The cause above is confirmed for the errors that
 *   scrap a thread.
 *
 *   One-step differences DO NOT vanish: ~0.30% of samples, worst +/-1 step, at
 *   4 TPI just as at 18. With an exact pitch there is nothing to round, so
 *   these cannot be the register's doing -- they are float32 cancellation in
 *   the advance terms and round-to-nearest landing either side of a half-step,
 *   and no geometry removes them. One step is 2.0 um here.
 *
 * The previous text said the 4 TPI residual was "exactly zero in both regimes",
 * which was true only of the wrong machine's numbers. els_phase_reduce_test.cpp
 * now asserts the two claims separately.
 *
 * THE EXACT FIX, when the register-append queue allows it: give the firmware
 * the true integer PPR -- either as its own register, or by pushing the sync
 * ratio UNREDUCED so that syncRatioDen carries PPR as a factor. Either one
 * makes P exact by construction and lets the fold use a pitch derived from
 * integers instead of from a rounded float. Both are register-map changes and
 * therefore a coordinated decision, which is why neither is done here.
 *
 * SIDE EFFECT ON elsFmodPitch, measured 2026-08-28 when the two landed
 * together: by bounding deltaSpindle to about one revolution this also keeps
 * phaseError far below the magnitude at which that fold's double-rounding
 * defect appears, so the defect is unreachable through this path today. Do not
 * read that as the fold being fixed by this function -- see the note above
 * elsFmodPitch. It is unreachable from THIS caller, which is a different and
 * much weaker property.
 *
 * THE CONSISTENCY GUARD (added 2026-08-28). This function used to TRUST that
 * threadPitchSteps and the sync ratio describe the same machine. They do
 * whenever reflex-ui writes them, because both come from one spindle_pitch_mm
 * -- but nothing verified it, and if they disagree, P is not a machine period
 * at all and the reduction subtracts multiples of the wrong quantity:
 * whole-pitch errors, where the unreduced code merely degraded. Measured at a
 * sync ratio of 1 against elspi's pitch, 640 of 640 cases came out wrong, worst
 * 690 steps of a 711-step pitch.
 *
 * WHAT IS CHECKED, AND WHY IT IS CHECKABLE AT ALL. The premise this function
 * rests on is that PPR * (syncRatioNum/syncRatioDen) == threadPitchSteps in the
 * rationals, so
 *   pd = threadPitchSteps * syncRatioDen / syncRatioNum
 * must land on an INTEGER -- the true PPR -- and the only thing that moves it
 * off one is the float32 rounding already baked into threadPitchSteps. That
 * makes the premise self-checking: measure how far pd is from the nearest
 * integer and compare it against what that rounding can account for. No new
 * register, no PPR parameter; the inconsistency is visible in the three values
 * already passed in.
 *
 * THE TOLERANCE, DERIVED RATHER THAN PICKED. threadPitchSteps carries at most
 * one float32 rounding, so its relative error is bounded by 2^-24; pd is formed
 * from it in double, whose own error is ~2^-52 and negligible beside that.
 * Hence |pd - PPR| <= pd * 2^-24 for any honest register pair. The bound below
 * is 2^-18 -- 64x that -- so a host that computes the pitch through a couple of
 * float steps still passes. Measured margins at both ends, through the function
 * itself rather than by recomputing the arithmetic beside it:
 *
 *   elspi 18 TPI (25/216, tps 711.111084)  dev 2.34e-4  tol 2.34e-2   100x IN
 *   elspi  4 TPI (25/48,  tps 3200.0)      dev 0        tol 2.34e-2   exact
 *   lossless test geometry (6000/6000)     dev 0        tol 3.81e-3   exact
 *   ratio 1 (216/216, tps 711.111084)      dev 1.11e-1  tol 2.71e-3    41x OUT
 *   class default 360/100                  dev 4.69e-1  tol 7.54e-4   623x OUT
 *
 * Two orders of magnitude of daylight on each side, and every geometry a real
 * machine or an existing test presents lands exactly on its integer.
 *
 * WHICH WAY IT FAILS, DELIBERATELY. A refused pair returns deltaSpindle
 * UNREDUCED -- exactly the pre-04dd1f9 behaviour, which shipped for months and
 * degrades gracefully -- rather than asserting or clamping. This runs on the
 * resume edge off the 100 kHz ISR's path; there is nothing useful to trap to,
 * and reducing by a wrong period is strictly worse than not reducing. So a
 * tolerance that is slightly too TIGHT costs the fix's benefit on a machine
 * that deserved it, while one slightly too LOOSE costs a scrapped thread. The
 * 64x headroom is sized with that asymmetry in mind, not centred.
 *
 * NOT SIGNALLED ANYWHERE, and that is a real gap: a machine whose registers
 * disagree silently loses the reduction with no operator-visible trace. A
 * refusal counter belongs in the register map beside the other diagnostics,
 * which is the same congested append queue the exact-PPR fix above waits on.
 *
 * els_phase_libm_equiv_test.cpp section 2b asserts BOTH halves -- that the
 * guard fires on the inconsistent pair, and that it does NOT fire on elspi's
 * real geometry. The second matters more: a guard that refused everything
 * would make every divergence test go green while silently deleting the fix. */

/* Bound on |pd - round(pd)| relative to pd, above which the pitch register and
 * the sync ratio are taken to describe different machines. 2^-18 = 64 float32
 * ULPs; see the derivation above before changing it. */
#define ELS_REDUCE_PERIOD_TOL_REL (1.0 / 262144.0)

/* ======================================================================
 * NEVER CALL THIS FROM THE ISR. Measured 2026-08-28 on elspi: it cost the
 * 100 kHz tick 2.6x its entire budget.
 * ======================================================================
 *
 * From 04dd1f9 until 0d0307d this arithmetic lived inside
 * elsComputePhaseCorrection, which applyPhaseCorrection calls from
 * SynchroRefreshTimerIsr. Cortex-M4F has NO FP64 hardware, so every `double`
 * below is a compiler softfp library call. objdump of the flashed image found
 * 22 of them plus 4 __aeabi_ldivmod inside the ISR symbol, against ZERO in the
 * previous release. The ISR peak went 1012 -> 2658 cycles against a 1000-cycle
 * budget: a 2.6x tick overrun, which is exactly the Modbus-starvation condition
 * that lost the link on 6 of 6 cuts on 2026-08-23.
 *
 * The irony is on the record: elsFmodPitch, 100 lines below, already warned
 * "Cortex-M4F has no FP64 hardware; a double divide -- or even a double
 * multiply-subtract -- is a compiler softfp call here, trading one library call
 * for another." That warning and this function shipped on different branches
 * and met in a merge. Nobody who read either one read them together.
 *
 * NOTHING IN THE EMULATOR SUITE COULD SEE IT. Those tests build for x86, where
 * a double is a hardware instruction and free. 32/32 green says nothing about a
 * cost that exists only on ARM. The check that DOES see it is a static one on
 * the ARM binary -- see scripts/check-isr-cost.sh, which fails the build if a
 * softfp call reappears inside the ISR.
 *
 * SO THE PERIOD IS COMPUTED OFF THE HOT PATH AND CACHED. The result depends on
 * three registers that change at job setup, not per tick, so recomputing it per
 * pass was always waste as well as hazard. Ramps.c refreshes it from
 * updateSpeedTask and hands the ISR an integer.
 *
 * Returns the spindle-count period of one thread pitch, or 0 meaning
 * "DO NOT REDUCE" -- degenerate config, or the consistency check below
 * refusing. 0 is the fail-open sentinel and callers must honour it.
 *
 * ---- what the consistency check is, and why it is checkable at all --------
 * The premise the reduction rests on is that PPR * (num/den) ==
 * threadPitchSteps in the rationals, so pd must land on an INTEGER -- the true
 * PPR -- and the only thing that moves it off one is the float32 rounding
 * already baked into threadPitchSteps. Measure the distance to the nearest
 * integer, compare against what that rounding can explain.
 *
 * TOLERANCE DERIVED, NOT PICKED: threadPitchSteps carries at most one float32
 * rounding, so |pd - PPR| <= pd * 2^-24 for any honest pair; the bound is
 * 2^-18, 64x that. Measured through this function:
 *
 *   elspi 18 TPI (25/216, tps 711.111084)  dev 2.34e-4  tol 2.34e-2   100x IN
 *   elspi  4 TPI (25/48,  tps 3200.0)      dev 0        tol 2.34e-2   exact
 *   lossless test geometry (6000/6000)     dev 0        tol 3.81e-3   exact
 *   ratio 1 (216/216, tps 711.111084)      dev 1.11e-1  tol 2.71e-3    41x OUT
 *   class default 360/100                  dev 4.69e-1  tol 7.54e-4   623x OUT
 *
 * REFUSAL FAILS OPEN, deliberately: returning 0 leaves the caller on the
 * pre-04dd1f9 unreduced path, which shipped for months and degrades
 * gracefully. Reducing by a wrong period does not degrade -- it moves the
 * answer by whole pitches. A tolerance slightly too TIGHT costs the fix's
 * benefit on a machine that deserved it; slightly too LOOSE costs a scrapped
 * thread. The 64x headroom is sized for that asymmetry, not centred.
 *
 * STILL NOT SIGNALLED: a machine whose registers disagree loses the reduction
 * with no operator-visible trace. A refusal counter belongs in the register
 * map, on the same congested append queue as the exact-PPR fix above. */
static inline int32_t elsComputeSpindlePeriod(
    int32_t syncRatioNum, int32_t syncRatioDen, float threadPitchSteps)
{
  if (syncRatioNum == 0 || threadPitchSteps == 0.0f) {
    return 0;              /* turning, or degenerate config: do not reduce */
  }
  double pd = (double)threadPitchSteps * (double)syncRatioDen
              / (double)syncRatioNum;
  if (pd < 0.0) pd = -pd;
  int64_t P = (int64_t)(pd + 0.5);   /* round to nearest; P is ~PPR */
  if (P <= 0 || P > 0x7FFFFFFF) {
    return 0;
  }
  double dev = pd - (double)P;
  if (dev < 0.0) dev = -dev;
  if (dev > pd * ELS_REDUCE_PERIOD_TOL_REL) {
    return 0;              /* registers describe different machines */
  }
  return (int32_t)P;
}

/* The ISR half: integer only, and that is the point.
 *
 * int32 rather than the int64 the first version used. deltaSpindle IS an
 * int32 and a period is ~PPR (6144 on elspi), so the 64-bit form bought
 * nothing and cost 4 __aeabi_ldivmod calls per pass in the ISR -- Cortex-M4
 * has a 32-bit hardware divider and no 64-bit one.
 *
 * period == 0 means DO NOT REDUCE, and returning deltaSpindle untouched is
 * exactly the pre-04dd1f9 behaviour. That single sentinel carries three
 * separate cases: turning, a degenerate register set, and a period the
 * consistency check refused. */
static inline int32_t elsReduceDeltaSpindleBy(int32_t deltaSpindle,
                                              int32_t spindlePeriodCounts)
{
  if (spindlePeriodCounts <= 0) {
    return deltaSpindle;
  }
  int32_t r = deltaSpindle % spindlePeriodCounts;
  if (r < 0) r += spindlePeriodCounts;   /* always non-negative */
  return r;
}

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
 * RE-MEASURED 2026-08-28, AFTER THE MERGE, AND THE COST ABOVE NO LONGER SHOWS
 * THROUGH THE FRONT DOOR. elsReduceDeltaSpindle now runs BEFORE this fold and
 * bounds deltaSpindle to about one revolution, so phaseError never reaches the
 * magnitude where the double rounding bites. Reverting this hunk and sweeping
 * elsComputePhaseCorrection over the same job-scale grid the equivalence test
 * uses: 3288 cases, ZERO differ. The documented worst case (13829522, -4487)
 * commands 711 steps either way.
 *
 * THAT IS NOT A REASON TO DROP IT, and the distinction matters because the two
 * fixes were each proven with the other absent -- neither test file exercises
 * the other, so each looked individually decisive and neither was measured in
 * company. Calling elsFmodPitch DIRECTLY at the phaseError that case produces
 * (1589333.2, comfortably under the 2^22 guard, so this is the shipping path)
 * still returns 711.0884 with the fmaf and 0.0000 without -- the whole-pitch
 * defect, undiminished. What the reduction changed is the REACHABILITY of the
 * defect from the one caller there is today, not the defect. This fold is a
 * general-purpose fold; the reduction in front of it is one caller's
 * precaution, and it is exactly the thing a future caller, a bypass, or a
 * miscalibrated register set removes.
 *
 * SO: do not cite "reverting fmaf costs a whole pitch" as a property of
 * elsComputePhaseCorrection any more. It is a property of elsFmodPitch, it is
 * still true there, and that is the claim the mutation supports.
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
    int32_t offsetSteps,   /* phase offset, leadscrew steps; 0 = none (exact pre-feature path) */
    /* Spindle counts in one thread pitch, from elsComputeSpindlePeriod OFF the
     * ISR path. 0 = do not reduce. Passed in rather than derived here because
     * deriving it costs 26 softfp calls on Cortex-M4F and this function runs
     * from SynchroRefreshTimerIsr -- see the banner above
     * elsComputeSpindlePeriod for what that cost measured. */
    int32_t spindlePeriodCounts)
{
  elsCorrResult_t r;

  deltaSpindle = elsReduceDeltaSpindleBy(deltaSpindle, spindlePeriodCounts);

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
