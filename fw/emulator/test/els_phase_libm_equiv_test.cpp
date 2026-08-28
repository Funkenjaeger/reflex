/*
 * The libm-free phase math must be the SAME MATH.
 *
 * fmodf and lroundf were the only library calls reachable from
 * SynchroRefreshTimerIsr, and both sat in elsComputePhaseCorrection -- which
 * runs on the resume edge, i.e. cut-start, i.e. when the Modbus link died on
 * 2026-08-23. Neither is an instruction on Cortex-M4F; both are software
 * routines, and fmodf's cost is not even constant -- newlib iterates over the
 * exponent difference between its operands, and the dividend here is spindle
 * advance accumulated since the latch, which grows for as long as the job runs.
 *
 * Replacing them is only worth anything if the answer does not change. The
 * existing els_phase_test checks specific permutations of a lossless model;
 * this file checks the substitution itself, by computing every case BOTH ways
 * -- once through a reference that still calls fmodf/lroundf, once through
 * production -- and comparing the only output that moves metal, stepsToAdd.
 *
 * The sweep deliberately reaches the magnitudes that motivated the change:
 * deltaSpindle out to tens of millions of counts, which is where fmodf got
 * expensive and where float32 cancellation is worst.
 *
 * WHAT THE MERGE OF 2026-08-28 DID TO THIS FILE. This test was written on
 * perf/els-isr-load, where elsComputePhaseCorrection did NOT yet reduce
 * deltaSpindle. Merging integration put elsReduceDeltaSpindle (04dd1f9) in
 * front of the fold, so "production" now removes whole multiples of the TRUE
 * pitch before folding modulo the ROUNDED pitch register -- and the reference
 * below, which is the genuinely pre-change code, does neither. On CONSISTENT
 * geometry those two agree to within the one step this file already tolerates,
 * because the reduction removes exact multiples of the period. On geometry
 * where the sync ratio and threadPitchSteps DISAGREE they do not agree at all,
 * by whole pitches, and section 2 was silently sweeping exactly that case.
 * See section 2b: the disagreement is real, reproducible, and is now
 * characterised rather than either tolerated or deleted.
 */
#include "els_phase.h"

#include <cstdio>
#include <cmath>
#include <cstdint>
#include <cstdlib>

static int failures = 0;
static long compared = 0;
static long differed = 0;
static int32_t worst_diff = 0;

/* Section 2b compares a geometry that is KNOWN to diverge and exists to measure
 * how far. Its cases are counted separately and never touch failures/worst_diff
 * -- otherwise section 8's "never differ by 2 steps" bound would be reporting a
 * number drawn from a case it does not cover, which is the shape of a check
 * that has stopped meaning what it says. */
static bool characterizing = false;
static long char_compared = 0;
static long char_differed = 0;
static int32_t char_worst = 0;

static void check(bool cond, const char *what)
{
    printf("   %-68s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

/* The pre-change implementation, verbatim apart from the two calls. Kept as a
 * local copy on purpose: pointing this at the production function would make
 * the comparison trivially true, which is the classic shape of a check that
 * cannot fail. */
static int32_t reference_steps(int32_t deltaSpindle, int32_t deltaZ,
                               int32_t syncRatioNum, int32_t syncRatioDen,
                               float threadPitchSteps, float zCountsPerPitch,
                               int16_t stopDirection, int32_t offsetSteps)
{
    float idealAdvance  = (float)deltaSpindle * (float)syncRatioNum / (float)syncRatioDen;
    float actualAdvance = (float)deltaZ * threadPitchSteps / zCountsPerPitch;

    int32_t cuttingDir = (syncRatioNum > 0) ? 1 : -1;
    if (threadPitchSteps * zCountsPerPitch < 0.0f) cuttingDir = -cuttingDir;

    int32_t droSign = (int32_t)stopDirection * cuttingDir;
    float phaseError = idealAdvance - (float)droSign * actualAdvance + (float)offsetSteps;

    float pitch      = threadPitchSteps;
    float correction = fmodf(phaseError, pitch);          /* the old call */
    if (correction >  pitch / 2.0f) correction -= pitch;
    if (correction < -pitch / 2.0f) correction += pitch;
    if ((float)cuttingDir * correction < 0.0f) correction += (float)cuttingDir * pitch;

    return (int32_t)lroundf(correction);                  /* the other old call */
}

static void compare(int32_t dSp, int32_t dZ, int32_t num, int32_t den,
                    float tps, float zcpp, int16_t sd, int32_t off)
{
    int32_t want = reference_steps(dSp, dZ, num, den, tps, zcpp, sd, off);
    elsCorrResult_t got = elsComputePhaseCorrection(
        dSp, dZ, num, den, tps, zcpp, sd, off,
        /* Production computes this off the ISR and caches it; computing it here
         * per call is the same value by the same function, so the comparison
         * still exercises the shipping path. */
        elsComputeSpindlePeriod(num, den, tps));
    int32_t diff = got.stepsToAdd - want;

    if (characterizing) {
        char_compared++;
        if (diff != 0) {
            char_differed++;
            int32_t m = diff < 0 ? -diff : diff;
            if (m > char_worst) char_worst = m;
        }
        return;
    }

    compared++;
    if (diff != 0) {
        differed++;
        int32_t mag = diff < 0 ? -diff : diff;
        if (mag > worst_diff) worst_diff = mag;
        if (mag > 1) {
            printf("   MISMATCH dSp=%d dZ=%d num=%d pitch=%.3f off=%d: "
                   "ref=%d new=%d (diff %d)\n",
                   (int)dSp, (int)dZ, (int)num, (double)tps, (int)off,
                   (int)want, (int)got.stepsToAdd, (int)diff);
            failures++;
        }
    }
}

int main()
{
    printf("=== libm-free phase math: same answers as fmodf/lroundf ===\n\n");

    /* Real machine geometry first: elspi at 18 TPI. 12800 steps/inch, so a
     * pitch is 711.11 steps -- the numbers the 2026-08-24 capture was taken
     * against.
     *
     * THE SYNC RATIO IS PART OF THE GEOMETRY, and getting it wrong is how this
     * file missed a real defect. It swept 360/100 until 2026-08-27 -- the
     * AxisDispatcher class default (ui/reflex/dispatchers/axis.py), an
     * uncommissioned ROTARY setting that no lathe runs. elspi's spindle encoder
     * is 6144 counts/rev, and 6144 * 25/216 = 711.111 steps, exactly one
     * leadscrew pitch per spindle revolution at 18 TPI: the real ratio is
     * 25/216, and it is 31x SMALLER than 3.60.
     *
     * That ratio scales idealAdvance, so the wrong one moved the entire sweep
     * off the range under test: at 360/100 a job-length deltaSpindle produces a
     * phaseError of ~5e7, which is above the 2^22 guard in elsFmodPitch and
     * therefore took the fmodf fallback -- the sweep was re-testing the library
     * routine, not the replacement. At the real 25/216 the same deltaSpindle
     * lands at ~1.6e6, inside the guard, on the code that actually ships. */
    const float ELSPI_PITCH = 12800.0f / 18.0f;
    const float ELSPI_ZCPP  = 282.222229f;

    printf("-- 1. elspi geometry, spindle advance swept to job-length scale --\n");
    for (int32_t dSp = 0; dSp < 40000000; dSp += 97391) {
        for (int32_t dZ = -20000; dZ <= 20000; dZ += 5171) {
            compare(dSp, dZ, 25, 216, ELSPI_PITCH, ELSPI_ZCPP, -1, 0);
        }
    }
    check(failures == 0, "no case differs by more than one step");

    /* THE NUMERATOR LIST USED TO BE {25, -25, 216, -216}, ALL OVER den=216.
     * The last two are not a polarity -- paired with that denominator they are
     * a sync RATIO of exactly +/-1, three axes of geometry crossed where the
     * section only ever meant to cross one. Worse, ratio 1 alongside
     * threadPitchSteps = 711.111 is a self-contradictory register set: the sync
     * ratio's definition is "one spindle revolution advances one pitch", so
     * ratio 1 asserts a 711-count encoder while the pitch register says 711.111
     * steps. No lathe presents that; it was a combinatorial accident.
     *
     * Sign is carried by the numerator (cuttingDir is (syncRatioNum > 0)) and
     * by stopDirection, so nums {25,-25} x sds {1,-1} is the complete polarity
     * cross -- what the heading claims and what this now sweeps. The removed
     * cases are not dropped: they move to 2b, where their divergence is the
     * measurement rather than the failure. */
    printf("\n-- 2. both polarities, both cutting directions --\n");
    {
        long before = failures;
        const int32_t nums[] = {25, -25};
        const int16_t sds[]  = {1, -1};
        for (int n = 0; n < 2; n++)
            for (int d = 0; d < 2; d++)
                for (int32_t dSp = -5000000; dSp <= 5000000; dSp += 313337)
                    for (int32_t dZ = -8000; dZ <= 8000; dZ += 3719)
                        compare(dSp, dZ, nums[n], 216,
                                ELSPI_PITCH, ELSPI_ZCPP, sds[d], 0);
        check(failures == before, "sign handling is unchanged in every polarity");
    }

    /* ---- 2b. AN INCONSISTENT SYNC RATIO, CHARACTERISED ------------------
     * This section asserts that the code is WRONG here, which is deliberate.
     *
     * elsReduceDeltaSpindle recovers the spindle-count period of one pitch as
     * P = threadPitchSteps * syncRatioDen / syncRatioNum and rounds it to the
     * nearest integer, on the documented assumption that the two registers came
     * from the same spindle_pitch_mm and therefore agree. Nothing in firmware
     * checks that. Feed it a ratio that does not correspond to the pitch and P
     * is not a machine period at all, so the reduction subtracts multiples of
     * the wrong thing and the answer moves by whole pitches -- not the
     * sub-step noise every other section here bounds.
     *
     * Ratio 1 with elspi's pitch is the cheapest example (P rounds to 711 where
     * the period would have to be 711.111), and it is the same failure mode
     * that the AxisDispatcher class default 360/100 shows at full scale.
     * LATENT, not live: the host computes both registers from one
     * spindle_pitch_mm, so a real machine cannot reach this without a
     * config-load ordering failure. It is written down here because a latent
     * hazard with no executable evidence becomes a rediscovery.
     *
     * THE GUARD WAS ADDED THE SAME DAY, AND THIS IS THE ANSWER TO IT. The
     * paragraph that stood here said that adding a guard was SUPPOSED to turn
     * this section red, so the fix could not land silently against a test that
     * never mentioned the case. It went red exactly as advertised, within the
     * hour, and what follows is the required statement of the new behaviour
     * rather than a softened bound.
     *
     * WHAT THE GUARD DOES: it measures how far pd = tps*den/num sits from the
     * nearest integer and refuses the pair when that exceeds what the float32
     * rounding in threadPitchSteps can explain (2^-18 relative, 64 ULPs). A
     * refusal returns deltaSpindle UNREDUCED -- the pre-04dd1f9 path -- which
     * is why the divergence here does not merely shrink, it disappears: the
     * reference in this file is unreduced too, so refusing makes production
     * agree with it bit for bit.
     *
     * AND THE SECOND CHECK IS THE ONE THAT MATTERS. "Divergence gone" is
     * satisfied just as well by a guard that refuses EVERYTHING -- which would
     * silently delete the reduction from every machine including this one, and
     * leave every other section in this file green, because they all compare
     * against an unreduced reference as well. So the guard is probed directly
     * from both sides: it must pass elspi's real geometry through to the
     * reduction, and it must refuse the inconsistent pair. Those two are what
     * stop this section from passing for the wrong reason. */
    printf("\n-- 2b. an inconsistent sync ratio is refused, a real one is not --\n");
    {
        long before = failures;
        characterizing = true;
        const int32_t nums[] = {216, -216};
        const int16_t sds[]  = {1, -1};
        for (int n = 0; n < 2; n++)
            for (int d = 0; d < 2; d++)
                for (int32_t dSp = -5000000; dSp <= 5000000; dSp += 313337)
                    for (int32_t dZ = -8000; dZ <= 8000; dZ += 3719)
                        compare(dSp, dZ, nums[n], 216,
                                ELSPI_PITCH, ELSPI_ZCPP, sds[d], 0);
        characterizing = false;

        printf("   ratio 1 vs pitch %.3f: %ld cases, %ld differed, worst %d "
               "step(s) of a %d-step pitch\n",
               (double)ELSPI_PITCH, char_compared, char_differed,
               (int)char_worst, (int)(ELSPI_PITCH + 0.5f));

        /* One full period of the REAL geometry must reduce to zero. That is
         * only true if the guard accepted the pair and the reduction ran, so it
         * is the direct probe an over-eager guard cannot survive. */
        bool realAccepted =
            elsComputeSpindlePeriod(25, 216, ELSPI_PITCH) == 6144
            && elsReduceDeltaSpindleBy(6144, 6144) == 0;

        /* The inconsistent pair must come back untouched. Distinguishable from
         * any reduction: 5000000 mod 711 is 2129, nowhere near the input. */
        bool badRefused =
            elsComputeSpindlePeriod(216, 216, ELSPI_PITCH) == 0
            && elsReduceDeltaSpindleBy(5000000, 0) == 5000000;

        check(char_compared >= 600, "the bad geometry was actually swept");
        check(char_differed == 0,
              "refusing the pair makes production match the unreduced reference");
        check(realAccepted,
              "the guard still lets elspi's real geometry reduce");
        check(badRefused,
              "the guard refuses the inconsistent pair, passing it through");
        (void)before;
        (void)char_worst;
    }

    printf("\n-- 3. with a groove-widening offset applied --\n");
    {
        long before = failures;
        const int32_t offs[] = {0, 1, 47, 355, 710, -355, 1422};
        for (int o = 0; o < 7; o++)
            for (int32_t dSp = 0; dSp < 3000000; dSp += 71119)
                compare(dSp, -3200, 25, 216,
                        ELSPI_PITCH, ELSPI_ZCPP, -1, offs[o]);
        check(failures == before, "the offset term folds identically");
    }

    printf("\n-- 4. the lossless geometry the permutation tests use --\n");
    {
        long before = failures;
        for (int32_t dSp = -3000000; dSp <= 3000000; dSp += 60013)
            for (int32_t dZ = -4000; dZ <= 4000; dZ += 1601)
                compare(dSp, dZ, 6000, 6000, 1000.0f, 400.0f, -1, 0);
        check(failures == before, "exactly-divisible geometry is unchanged");
    }

    printf("\n-- 5. the fail-closed and degenerate inputs --\n");
    {
        long before = failures;
        compare(0, 0, 25, 216, ELSPI_PITCH, ELSPI_ZCPP, -1, 0);
        compare(1, 0, 25, 216, ELSPI_PITCH, ELSPI_ZCPP, -1, 0);
        compare(-1, 0, 25, 216, ELSPI_PITCH, ELSPI_ZCPP, -1, 0);
        /* Exactly on a pitch boundary, where a truncation landing one integer
         * off would show up if the fold did not absorb it. */
        for (int k = 1; k < 400; k++) {
            compare((int32_t)(k * 711), 0, 25, 216, ELSPI_PITCH, ELSPI_ZCPP, -1, 0);
        }
        check(failures == before, "boundaries and degenerate inputs agree");
    }

    /* ---- 6. THE ROUNDING RULE, EXACTLY -------------------------------
     * Tested as a unit rather than through the integrated comparison above,
     * and with no tolerance, because the tolerance there exists for float
     * cancellation and would hide this. A mutation replacing the ties-away
     * rule with a plain +0.5f survived the whole integrated sweep: it is
     * wrong by exactly one step on every negative correction, and the
     * cuttingDir == -1 polarity produces negative corrections all day.
     */
    printf("\n-- 6. elsRoundSteps IS lroundf, exactly --\n");
    {
        long before = failures;
        long checked = 0;
        for (float c = -800.0f; c <= 800.0f; c += 0.03125f) {
            if ((int32_t)elsRoundSteps(c) != (int32_t)lroundf(c)) {
                printf("   MISMATCH c=%.5f: lroundf=%ld elsRoundSteps=%d\n",
                       (double)c, lroundf(c), (int)elsRoundSteps(c));
                failures++;
                if (failures - before > 5) break;   /* enough to prove it */
            }
            checked++;
        }
        printf("   %ld values swept, including every exact half\n", checked);
        check(failures == before, "the ties-away-from-zero rule is preserved");
    }

    printf("\n-- 7. the exact halves, called out by name --\n");
    {
        long before = failures;
        const float halves[] = {-711.5f, -2.5f, -1.5f, -0.5f,
                                 0.5f, 1.5f, 2.5f, 711.5f};
        for (int i = 0; i < 8; i++) {
            bool ok = (int32_t)elsRoundSteps(halves[i]) == (int32_t)lroundf(halves[i]);
            if (!ok) {
                printf("   %.1f: lroundf=%ld elsRoundSteps=%d\n",
                       (double)halves[i], lroundf(halves[i]),
                       (int)elsRoundSteps(halves[i]));
                failures++;
            }
        }
        check(failures == before, "a tie rounds away from zero in both signs");
    }

    /* NOT TESTED, because it cannot be: substituting floorf for the truncation
     * in elsFmodPitch is an EQUIVALENT mutation. floor and trunc differ by
     * exactly one pitch for negative dividends, and one pitch is precisely
     * what the mod-pitch fold below normalizes away -- measured, not assumed:
     * trunc(-1000) = -288.889 and floor(-1000) = 422.222, differing by
     * 1.0000 pitches. A test that appeared to kill it would be testing
     * something other than behaviour. */

    printf("\n-- 8. how far apart the two ever got --\n");
    printf("   compared %ld cases, %ld differed, worst difference %d step(s)\n",
           compared, differed, (int)worst_diff);
    /* A one-step disagreement is float rounding either way and is 0.14% of a
     * pitch on this machine -- but it must be BOUNDED and REPORTED, not
     * discovered later. Anything larger is a real behaviour change and fails
     * above. */
    check(worst_diff <= 1, "the two implementations never differ by 2 steps");

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
