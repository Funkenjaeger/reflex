/*
 * Unit tests for the pure backlash-calibration / take-up-confirmation layer
 * (Core/Inc/els_backlash_cal.h). No HAL, no ISR, no emulator physics — this
 * exercises the decision logic directly, which is the whole reason it was
 * extracted as pure functions.
 *
 * WHAT THIS FILE IS GUARDING
 * --------------------------
 * The take-up used to be 100% open loop: completion was a crossing test against
 * servo.currentSteps, the firmware's own count of pulses SENT. An open half-nut
 * produced a confidently "completed" take-up and a phase correction computed
 * from an uncoupled drivetrain. The gate these functions implement is the fix,
 * and the properties below are the ones that make it correct rather than merely
 * present:
 *
 *   - an unconfigured motion threshold must FAIL CLOSED, not wave everything
 *     through (a permissive default here silently restores the original bug);
 *   - a leg that finds motion on its very last commanded step is a SUCCESS, not
 *     a failure — the ordering of the motion check against the exhaustion check
 *     is load-bearing;
 *   - the take-up command is measured + max(pct, floor), never trimmed toward
 *     the minimum, because at small lash a flat percentage collapses into the
 *     measurement's own quantization uncertainty (see els_backlash_cal.h);
 *   - a machine whose real lash EXCEEDS the configured ceiling must fail rather
 *     than record the distance it drove, because a lash short by an unknown
 *     amount is worse than an error — it under-takes-up on every pass
 *     thereafter and relaxes the gate that would have caught it. See the final
 *     section, which also pins that this failure is NOT distinguishable from an
 *     open half-nut.
 *
 * Mutation-tested: see the MUTATION notes on the individual cases for the exact
 * edit that must make each one fail. A test that cannot fail is worse than no
 * test, because it reads as coverage.
 */
#include <cstdio>
#include <cstdlib>

#include "els_backlash_cal.h"

static int failures = 0;

static void check(bool cond, const char *what) {
    printf("   %-72s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

static void checkEq(int32_t got, int32_t want, const char *what) {
    bool ok = (got == want);
    printf("   %-72s %s (got %d, want %d)\n", what, ok ? "ok" : "FAIL",
           (int)got, (int)want);
    if (!ok) failures++;
}

/* measured[] is what elsCalUpdate() publishes to the host on EVERY outcome,
 * success or failure (Ramps.c: "Publish partial measurements on failure too").
 * So "what does the host end up holding" is a question about this array, not
 * about the internal phase — hence these two, used by the ceiling section. */
static bool allZero(const int32_t *m, int32_t n) {
    for (int32_t i = 0; i < n; i++) if (m[i] != 0) return false;
    return true;
}

static bool sameMeasured(const int32_t *a, const int32_t *b, int32_t n) {
    for (int32_t i = 0; i < n; i++) if (a[i] != b[i]) return false;
    return true;
}

/* ------------------------------------------------------------------------- *
 * A tiny simulated drivetrain with lash.
 *
 * Models exactly the property that makes this problem hard: while the leadscrew
 * traverses the lash window the carriage does NOT move, and only once the nut
 * reaches a wall does Z follow. `coupled = false` models an open half-nut — the
 * leadscrew turns forever and Z never moves.
 * ------------------------------------------------------------------------- */
struct Drivetrain {
    int32_t lashSteps;        /* true backlash, in servo steps */
    int32_t stepsPerZCount;   /* servo steps per Z encoder count (elspi ~2.5) */
    bool    coupled = true;

    int32_t currentSteps = 0; /* pulses issued */
    int32_t z            = 0; /* Z scale counts */
    int32_t nutPos       = 0; /* position within [0, lashSteps] */
    int32_t carriage     = 0; /* carriage position in servo-step equivalents */

    void advance(int32_t dir) {
        currentSteps += dir;
        if (!coupled) return;
        nutPos += dir;
        if (nutPos > lashSteps) { carriage += (nutPos - lashSteps); nutPos = lashSteps; }
        if (nutPos < 0)         { carriage += nutPos;               nutPos = 0; }
        z = carriage / stepsPerZCount;
    }
};

/* Drive a full calibration run against the model. Returns ticks consumed.
 *
 * ATTRIBUTION TICKING: this fixture has no ISR, so it has to play the same
 * role Ramps.c does for the real machine -- tick ctx.slip itself, AFTER each
 * commanded step, once that step's dZ and dServo are both fresh (the same
 * placement discipline Ramps.c's "MOTION ATTRIBUTION" comment requires; see
 * also els_slip.h). elsCalTick() only RESETS ctx.slip (at the instant a leg
 * arms) and READS it (elsSlipConfirmed) -- it does not tick it, so a caller
 * that never ticks is a caller that never confirms anything, on purpose: an
 * accumulator that silently drifts stale by itself would fail open, not
 * closed, and this primitive is built the other way round. settleTicks uses
 * elsSlipSettleTicks() rather than a bare constant for the same reason the ISR
 * does -- this model issues a step nearly every tick while driving (no pulse
 * pacing gaps), so any positive floor discriminates correctly here. */
static int32_t runCal(elsCalCtx_t &ctx, Drivetrain &dt,
                      int32_t ceiling, int32_t threshCounts,
                      int32_t cuttingDir, int32_t maxTicks = 2000000)
{
    elsCalStart(&ctx, cuttingDir, dt.currentSteps, dt.z);
    int32_t stepsToGo = ctx.driveSign * ceiling;
    const int32_t settleTicks = elsSlipSettleTicks(5, 1);

    for (int32_t t = 0; t < maxTicks; t++) {
        elsCalAction_t act = elsCalTick(&ctx, dt.currentSteps, dt.z,
                                        stepsToGo, threshCounts);
        if (act.finished) return t;
        if (act.startPhase) stepsToGo = act.driveSign * ceiling;

        if (stepsToGo != 0) {                  /* the indexing ramp, simplified */
            int32_t dir = (stepsToGo > 0) ? 1 : -1;
            int32_t stepsBefore = dt.currentSteps, zBefore = dt.z;
            dt.advance(dir);
            stepsToGo -= dir;
            elsSlipTick(&ctx.slip, dt.z - zBefore, dt.currentSteps - stepsBefore,
                        settleTicks);
        }
    }
    return -1;   /* did not terminate */
}

int main() {
    printf("=== ELS backlash calibration / take-up confirmation ===\n\n");

    /* ---------------- Motion test: the fail-closed property -------------- */
    printf("-- elsZMotionSeen --\n");
    check(elsZMotionSeen(100, 90, 5),  "10 counts of motion clears a 5-count threshold");
    check(!elsZMotionSeen(100, 98, 5), "2 counts does not clear a 5-count threshold");
    check(elsZMotionSeen(90, 100, 5),  "motion is magnitude-only (negative dZ counts)");
    /* MUTATION: change the `threshCounts <= 0` guard to `return true` and this
     * fails. That mutation is precisely the original open-loop behaviour, so
     * this single assertion is the regression guard for the whole feature. */
    check(!elsZMotionSeen(100, 0, 0),  "UNCONFIGURED threshold (0) NEVER confirms - fails closed");
    check(!elsZMotionSeen(100, 0, -1), "negative threshold also fails closed");

    /* ---------------- Signed projection ---------------------------------- */
    printf("\n-- elsZMovedAlong --\n");
    checkEq(elsZMovedAlong(110, 100, 1, 1),   10, "moved with the drive direction is positive");
    checkEq(elsZMovedAlong(90, 100, 1, 1),   -10, "moved AGAINST the drive direction is negative (wrong-way fault)");
    checkEq(elsZMovedAlong(90, 100, -1, 1),   10, "reversed drive flips the projection");
    checkEq(elsZMovedAlong(90, 100, 1, -1),   10, "inverted DRO polarity flips the projection");

    /* ---------------- Take-up command: measured + max(pct, floor) -------- */
    printf("\n-- elsCalTakeupCommand (20%% margin, 10-step floor) --\n");
    checkEq(elsCalTakeupCommand(100, 20, 100, 10), 120, "coarse lash: 20%% dominates (100 -> 120)");
    /* At 25 steps (~0.05 mm on elspi) a flat 20% is 5 steps — about the
     * measurement's own blind zone at a 2-count threshold. The floor is what
     * keeps real margin there.
     * MUTATION: drop the floor term (return measured + pctMargin) -> 30, fails. */
    checkEq(elsCalTakeupCommand(25, 20, 100, 10),   35, "fine lash: FLOOR dominates (25 -> 35, not 30)");
    checkEq(elsCalTakeupCommand(50, 20, 100, 10),   60, "crossover point: pct == floor");
    checkEq(elsCalTakeupCommand(0, 20, 100, 10),     0, "no measurement yields no command");
    checkEq(elsCalTakeupCommand(-5, 20, 100, 10),    0, "negative measurement yields no command");

    /* ---------------- Derived take-up confirmation threshold -------------- */
    printf("\n-- elsTakeupConfirmThreshold (expected motion, not the bare floor) --\n");
    {
        /* elspi-shaped geometry: zPerStep = zCountsPerPitch/threadPitchSteps.
         * 1 Z count ~= 2.52 servo steps, so zPerStep ~= 0.397. */
        const float TPS = 533.333f, ZCP = 211.67f;   /* 211.67/533.333 = 0.3969 */

        /* lash 60 -> measured ~65, commanded 78, margin 13.
         * expected = 13*0.3969 + 2 = 7.2 counts; half of that = 3. */
        checkEq(elsTakeupConfirmThreshold(78, 65, TPS, ZCP, 2), 3,
                "calibrated: demands a fraction of expected motion, not the floor");

        /* Bigger lash -> bigger margin -> a proportionally stricter demand. */
        checkEq(elsTakeupConfirmThreshold(127, 106, TPS, ZCP, 2), 5,
                "coarser lash raises the demand");

        /* MUTATION: return motionThreshCounts unconditionally and every
         * assertion in this block collapses to 2 — which is precisely the weak
         * test this function exists to replace. */

        /* ---- fallbacks: each must reproduce the bare floor exactly ---- */
        checkEq(elsTakeupConfirmThreshold(78, 0, TPS, ZCP, 2), 2,
                "NO CALIBRATION on file falls back to the floor");
        checkEq(elsTakeupConfirmThreshold(78, 65, 0.0f, ZCP, 2), 2,
                "turning mode (no thread geometry) falls back to the floor");
        checkEq(elsTakeupConfirmThreshold(78, 65, TPS, 0.0f, 2), 2,
                "zero zCountsPerPitch falls back to the floor");
        checkEq(elsTakeupConfirmThreshold(60, 65, TPS, ZCP, 2), 2,
                "commanded at/below measured falls back to the floor");
        /* The uncalibrated case is the load-bearing fallback: commanded minus
         * zero looks like an enormous margin, and without the guard a machine
         * that has never been calibrated would become un-runnable. */
        checkEq(elsTakeupConfirmThreshold(78, 0, TPS, ZCP, 0), 0,
                "unconfigured floor stays 0 so the fail-closed path is preserved");
    }

    printf("\n-- elsCalMeanValid (an incomplete run is NOT a small calibration) --\n");
    {
        int32_t good[3] = {64, 65, 66};
        int32_t partial[3] = {64, 65, 0};
        checkEq(elsCalMeanValid(good, 3), 65, "complete set averages");
        /* MUTATION: use elsCalMean here and a half-finished run reports a
         * plausible-but-small lash, which then inflates the derived threshold. */
        checkEq(elsCalMeanValid(partial, 3), 0, "incomplete set reports NO calibration");
    }

    /* ---------------- Spread / mean --------------------------------------- */
    printf("\n-- elsCalSpread / elsCalMean --\n");
    { int32_t m[3] = {100, 104, 98};
      checkEq(elsCalSpread(m, 3), 6,   "spread is max-min");
      checkEq(elsCalMean(m, 3),  100,  "mean of a tight set"); }
    { int32_t m[3] = {100, 220, 98};
      checkEq(elsCalSpread(m, 3), 122, "an outlier shows up as a large spread"); }

    /* ---------------- The state machine: a healthy machine ---------------- */
    printf("\n-- calibration run, coupled drivetrain (true lash = 100 steps) --\n");
    {
        elsCalCtx_t ctx{};
        Drivetrain dt{100, 3, true};
        int32_t ticks = runCal(ctx, dt, /*ceiling*/ 400, /*thresh*/ 2, /*cuttingDir*/ 1);

        check(ticks > 0, "run terminates");
        checkEq(ctx.result, ELS_CAL_OK, "result is OK");
        checkEq(ctx.phase, ELS_CAL_DONE, "ends in DONE");
        printf("      measured = %d, %d, %d (true lash 100, threshold 2 counts = 6 steps)\n",
               (int)ctx.measured[0], (int)ctx.measured[1], (int)ctx.measured[2]);
        for (int i = 0; i < ELS_CAL_CYCLES; i++) {
            char buf[96];
            snprintf(buf, sizeof buf, "measurement %d recovers the lash within quantization", i);
            /* Detection needs the carriage to move threshCounts, so each
             * measurement reads slightly HIGH by the detection distance. That
             * bias is real and is exactly why the take-up adds margin rather
             * than trusting the number. */
            check(ctx.measured[i] >= 100 && ctx.measured[i] <= 100 + 2 * 3 + 2, buf);
        }
        checkEq(elsCalSpread(ctx.measured, 3) <= 2, 1, "the three measurements agree (spread <= 2)");
    }

    /* ---------------- The state machine: an OPEN HALF-NUT ---------------- */
    printf("\n-- calibration run, UNCOUPLED drivetrain (open half-nut) --\n");
    {
        elsCalCtx_t ctx{};
        Drivetrain dt{100, 3, false};        /* leadscrew turns, carriage never moves */
        int32_t ticks = runCal(ctx, dt, 400, 2, 1);

        check(ticks > 0, "run terminates rather than hanging");
        /* MUTATION: make elsZMotionSeen permissive, or check exhaustion before
         * motion, and this becomes ELS_CAL_OK with garbage measurements — the
         * exact silent-wrong-answer this whole feature exists to prevent. */
        checkEq(ctx.result, ELS_CAL_ERR_NO_MOTION, "result is NO_MOTION, not OK");
        checkEq(ctx.phase, ELS_CAL_FAILED, "ends in FAILED");
        checkEq(ctx.cycle, 0, "no measurement was recorded");
    }

    /* ---------------- Hand nudge during an OPEN-HALF-NUT leg -------------- *
     * 2026-08-10: elsCalUpdate() used to test the bare cumulative endpoint
     * (elsZMotionSeen), so ANY Z drift since arming -- regardless of source --
     * satisfied a leg. It now tests elsSlipConfirmed(&ctx->slip, ...), the same
     * attribution primitive that closed the analogous take-up hole (see
     * els_slip.h), reused rather than rebuilt per this task's own standing
     * instruction.
     *
     * WHAT THIS DOES AND DOES NOT PROVE -- read before trusting the green below
     * ------------------------------------------------------------------------
     * The take-up gate's exploit (2026-08-08) lands in the POST-drive dwell:
     * the servo has REACHED ITS TARGET and stopped pulsing, the gate is still
     * open re-evaluating for ~250 ms, and a nudge arriving late in that window
     * is genuinely distinguishable from a pulse-adjacent settle because
     * ticksSinceLastPulse has had time to grow past the settle horizon.
     *
     * The calibration leg has NO analogous post-drive window. elsCalTick()
     * checks for motion on EVERY tick WHILE THE LEADSCREW IS STILL ACTIVELY
     * TURNING (that is the whole leg, start to finish -- see the FSM above:
     * there is no dwell state between "still driving" and "leg over"). On an
     * open half-nut the leadscrew keeps issuing step pulses the entire time
     * the leg runs (that is the Drivetrain model's own stated semantics:
     * "the leadscrew turns forever and Z never moves"). elsSlipSettleTicks()'s
     * floor is DELIBERATELY sized to cover the largest gap reachable between
     * consecutive pulses in a live burst (servoCyclePeriod - 1), specifically
     * so a healthy slow machine is never falsely rejected mid-drive. That same
     * floor makes it STRUCTURALLY IMPOSSIBLE for attribution to tell a hand
     * nudge from genuine coupling while the leadscrew keeps turning -- every
     * tick during an active leg has ticksSinceLastPulse <= settleTicks BY
     * CONSTRUCTION, so every Z count that arrives during that time is credited
     * as attributed, whoever actually produced it.
     *
     * Net effect, VERIFIED empirically here: for the realistic exposure (an
     * operator nudging the carriage AT ANY POINT while a leg is actively
     * driving, which is the entire duration of an open-half-nut leg), this
     * patch changes NOTHING. The case below is IDENTICAL, with and without the
     * fix, to the standalone repro at build-manual/repro_cal_defect3.cpp run
     * against the pre-patch code. This is not a corner case being conceded --
     * it IS the exploit the task asked this fix to close, and it is still
     * open. Closing it needs a detector of a different SHAPE (e.g. correlating
     * the RATE of Z motion against the commanded step rate over a sliding
     * window, rather than "was there a pulse recently"), which is design work,
     * not reuse, and needs sign-off before it goes anywhere near elspi. */
    printf("\n-- KNOWN GAP: hand nudge WHILE the leadscrew is still actively turning --\n");
    {
        elsCalCtx_t ctx{};
        ctx.phase     = ELS_CAL_MEASURE;
        ctx.driveSign = 1;
        ctx.armed     = 1;
        elsSlipReset(&ctx.slip);
        Drivetrain dt{100, 3, false};   /* open half-nut: leadscrew turns, Z never follows it */
        int32_t ceiling = 400;
        int32_t stepsToGo = ctx.driveSign * ceiling;
        const int32_t settleTicks = elsSlipSettleTicks(5, 1);
        bool nudged = false;

        for (int32_t t = 0; t < ceiling + 5; t++) {
            elsCalAction_t act = elsCalTick(&ctx, dt.currentSteps, dt.z, stepsToGo, 2);
            if (act.finished || act.startPhase) break;   /* leg decided -- stop here */

            int32_t stepsBefore = dt.currentSteps;
            if (stepsToGo != 0) {
                int32_t dir = (stepsToGo > 0) ? 1 : -1;
                dt.advance(dir);           /* uncoupled: currentSteps moves, dt.z does not */
                stepsToGo -= dir;
            }
            int32_t dZ = 0;
            if (t == 30 && !nudged) {
                dt.z += 5;                 /* hand nudge -- NOT caused by the servo */
                dZ = 5;
                nudged = true;
            }
            elsSlipTick(&ctx.slip, dZ, dt.currentSteps - stepsBefore, settleTicks);
        }

        check(nudged, "the nudge actually landed before the leg decided (fixture sanity)");
        /* This is the KNOWN GAP, asserted so it cannot silently regress into a
         * false sense of safety: attribution credited the nudge (the leadscrew
         * was mid-burst when it arrived), so the leg still completes on it.
         * MUTATION: this assertion's own failure IS the interesting mutation --
         * if a future change to elsSlipSettleTicks() or the ISR wiring ever
         * makes this case correctly refuse (result == ELS_CAL_ERR_NO_MOTION),
         * that is GOOD NEWS and this comment block (and the task text) need
         * updating, not the assertion silently deleted. */
        checkEq(ctx.result, ELS_CAL_OK,
                "GAP CONFIRMED: still completes on a nudge that arrived mid-drive");
        check(ctx.cycle >= 1,
                "...and it recorded a bogus measured[] value from it (not the true lash)");
    }

    /* ---------------- Ordering: motion on the final commanded step ------- */
    printf("\n-- motion detected on the LAST commanded step --\n");
    {
        /* Ceiling sized so the carriage crosses the detection threshold on the
         * very last step of the first measured reversal. If elsCalTick checked
         * exhaustion before motion, this legitimate machine would be condemned.
         * MUTATION: move the `stepsRemaining == 0` block above the `moved`
         * block -> NO_MOTION, fails. */
        elsCalCtx_t ctx{};
        Drivetrain dt{100, 3, true};
        elsCalStart(&ctx, 1, dt.currentSteps, dt.z);

        const int32_t ceiling = 106;       /* 100 lash + 6 steps = 2 Z counts */
        int32_t stepsToGo = ctx.driveSign * ceiling;
        int32_t guard = 0;
        bool sawFailure = false;
        const int32_t settleTicks = elsSlipSettleTicks(5, 1);
        while (guard++ < 100000) {
            elsCalAction_t act = elsCalTick(&ctx, dt.currentSteps, dt.z, stepsToGo, 2);
            if (act.finished) { sawFailure = (ctx.result != ELS_CAL_OK); break; }
            if (act.startPhase) stepsToGo = act.driveSign * ceiling;
            if (stepsToGo != 0) {
                int32_t dir = (stepsToGo > 0) ? 1 : -1;
                int32_t stepsBefore = dt.currentSteps, zBefore = dt.z;
                dt.advance(dir);
                stepsToGo -= dir;
                elsSlipTick(&ctx.slip, dt.z - zBefore, dt.currentSteps - stepsBefore,
                            settleTicks);
            }
        }
        check(!sawFailure, "a machine that responds on the last step is NOT condemned");
        checkEq(ctx.result, ELS_CAL_OK, "result is OK");
    }

    /* ---------------- Threshold of 0 poisons a whole run ----------------- */
    printf("\n-- calibration with an unconfigured threshold --\n");
    {
        elsCalCtx_t ctx{};
        Drivetrain dt{100, 3, true};       /* perfectly healthy machine */
        int32_t ticks = runCal(ctx, dt, 400, /*thresh*/ 0, 1);
        check(ticks > 0, "run terminates");
        checkEq(ctx.result, ELS_CAL_ERR_NO_MOTION,
                "healthy machine + unconfigured threshold REFUSES (fails closed)");
    }

    /* ---------------- Both cutting-direction polarities ------------------ */
    printf("\n-- both cutting directions --\n");
    for (int32_t dir = -1; dir <= 1; dir += 2) {
        elsCalCtx_t ctx{};
        Drivetrain dt{80, 3, true};
        int32_t ticks = runCal(ctx, dt, 400, 2, dir);
        char buf[96];
        snprintf(buf, sizeof buf, "cuttingDir=%+d completes with OK", (int)dir);
        check(ticks > 0 && ctx.result == ELS_CAL_OK, buf);
        snprintf(buf, sizeof buf, "cuttingDir=%+d measures ~80 steps", (int)dir);
        check(ctx.measured[0] >= 80 && ctx.measured[0] <= 92, buf);
    }

    /* ---------------- Real lash EXCEEDS the configured ceiling ----------- *
     * The third machine the calibration checklist asks for, alongside normal
     * take-up and the open half-nut.
     *
     * calCeilingSteps is the per-leg hard ceiling, and elsCalTick()'s own
     * comment names draining it as "the open-half-nut / uncoupled case". This
     * section is the OTHER machine that produces that same symptom: one whose
     * lash is genuinely larger than the ceiling it was configured with. The leg
     * runs out of travel while the nut is still mid-window, so the carriage
     * never became entitled to move — nothing is broken, the sweep was just
     * given less travel than the machine needs.
     *
     * WHY A WRONG NUMBER WOULD BE WORSE THAN AN ERROR. The measurement feeds
     * elsCalTakeupCommand() (how far every subsequent pass drives to take up
     * lash) and elsTakeupConfirmThreshold() (how much carriage motion that
     * take-up must then produce to be believed). A lash short by an unknown
     * amount therefore under-takes-up AND lowers the bar the shortfall would
     * have been caught by — the two failures compound instead of cancelling.
     *
     * WHAT THE FIRMWARE ACTUALLY DOES: it refuses, and it refuses cleanly.
     * measured[] is written only inside elsCalTick()'s `moved` branch, so a leg
     * that drains its ceiling in silence records nothing at all — not the
     * ceiling, not a partial value. Case D sweeps every ceiling from one step
     * up past the boundary, at four nut positions, so that is a property here
     * rather than an anecdote about one hand-picked number.
     *
     * WHAT IT DOES NOT DO: distinguish this from an open half-nut. Case C runs
     * the two machines side by side at the same ceiling and finds every
     * host-visible field identical — same ELS_CAL_ERR_NO_MOTION, same all-zero
     * measured[]. The remedies are opposite (raise the ceiling vs. close the
     * half-nut) and the operator-facing text names only one of them
     * (ui/reflex/utils/devices.py: "Carriage did not move — is the half-nut
     * engaged?"), so an operator with a too-small ceiling is sent to check
     * something that is already correct. Firmware finding, deliberately NOT
     * fixed here: this file is emulator-only.
     *
     * MUTATION-TESTED 2026-08-23. Each was applied to
     * Core/Inc/els_backlash_cal.h alone, the whole emulator suite rebuilt and
     * run, the counts below observed, and the mutation reverted (header
     * restored byte-for-byte, suite back to green). Counts are failing
     * assertions in THIS target / red targets across the 25-target suite.
     *
     *   C1 record the drained ceiling as measured[] before failing
     *      (the naive truncation this section exists to refuse)   4 / 1
     *   C2 delete the post-arm `stepsRemaining == 0` failure
     *      (a leg that can only ever end on motion)              13 / 3
     *   C3 confirm on a fixed 1 count, not motionThreshCounts
     *      (detection gets cheaper, so the boundary moves)        2 / 3
     *   C4 check exhaustion BEFORE motion                         4 / 1
     *
     * Which assertions here died: C1 -> B's cycle and measured[], C's array
     * comparison, D's failure invariant (4 of 4 — nothing outside this section
     * noticed at all). C2 -> A, B, C, D's termination and result assertions (8).
     * C3 -> E's negative half only (1). C4 -> E's positive half (2).
     *
     * C1 is the mutation that justifies the section, and it was verified twice.
     * Under it the run still FAILS: result and phase are untouched, so every
     * assertion about the outcome code stays green while the host quietly
     * receives a fabricated lash — only the measured[]-is-empty assertions see
     * it. Compiling the HEAD version of this file against the C1-mutated header
     * gives PASSED (0 failures), the working-tree version FAILED (4). C1 was
     * invisible to the entire suite before this section existed, which is
     * precisely the shape of hazard the file's header warns about: coverage
     * that reads as coverage.
     *
     * C3 and C4 are here as the negative bounds — they are what stops case E
     * from being loosened into "any ceiling above the lash works". */
    printf("\n-- real lash EXCEEDS the configured ceiling --\n");
    {
        const int32_t LASH = 100, SPZ = 3, THRESH = 2;
        const int32_t TIGHT = 60;                     /* well short of LASH */
        /* Detection costs real travel: the carriage must move THRESH counts
         * before any of this can register, and one Z count is SPZ servo steps.
         * Derived from the fixture's own geometry rather than written as 6,
         * because els_backlash_cal.h's header note is explicit that none of
         * these numbers may be baked in — the emulator, elspi, and this fixture
         * all run different scale/leadscrew ratios. */
        const int32_t DETECT   = SPZ * THRESH;
        const int32_t BOUNDARY = LASH + DETECT;

        /* ---- A: the SEAT leg is what runs out ------------------------------
         * The nut starts against the far wall, so the very first leg has the
         * whole lash to cross and never gets there. This is the shape a machine
         * shows when the ceiling is badly wrong — it fails before measuring
         * anything. */
        elsCalCtx_t seatCtx{};
        Drivetrain seatDt{LASH, SPZ, true};
        int32_t seatTicks = runCal(seatCtx, seatDt, TIGHT, THRESH, 1);
        check(seatTicks > 0, "ceiling short of the lash: run terminates, does not hang");
        checkEq(seatCtx.result, ELS_CAL_ERR_NO_MOTION, "result is NO_MOTION");
        checkEq(seatCtx.phase, ELS_CAL_FAILED, "ends in FAILED");
        checkEq(seatDt.carriage, 0, "fixture sanity: the carriage genuinely never moved");

        /* ---- B: a MEASURE leg is what runs out -----------------------------
         * Same machine, but the nut is parked mid-window — which is the normal
         * state, since it sits wherever the last motion left it. The SEAT leg
         * now has only half the lash to cross and completes, so the run reaches
         * a real measurement leg before running out of ceiling. That matters:
         * it rules out "the FSM only refuses because it never armed" and puts
         * the refusal in the leg whose whole job is to produce a number. */
        elsCalCtx_t tightCtx{};
        Drivetrain tightDt{LASH, SPZ, true};
        tightDt.nutPos = LASH / 2;
        int32_t tightTicks = runCal(tightCtx, tightDt, TIGHT, THRESH, 1);
        check(tightTicks > 0, "nut mid-window: run terminates");
        check(tightDt.carriage != 0,
              "fixture sanity: the SEAT leg DID move the carriage before the failure");
        checkEq(tightCtx.result, ELS_CAL_ERR_NO_MOTION, "a drained MEASURE leg is still NO_MOTION");
        checkEq(tightCtx.cycle, 0, "no cycle is counted");
        /* THE anti-truncation assertion. The leg drove TIGHT steps in the
         * commanded direction and saw nothing; the tempting thing to do with
         * that number is record it, and it would look entirely plausible (60
         * steps is a believable lash). MUTATION C1 does exactly that and this
         * is the assertion that catches it. */
        check(allZero(tightCtx.measured, ELS_CAL_CYCLES),
              "the drained ceiling is NOT recorded as the lash (measured[] stays empty)");

        /* ---- C: side by side with an open half-nut -------------------------
         * Same ceiling, same threshold, same cutting direction; the only
         * difference is which fault the machine has. Everything elsCalUpdate()
         * publishes is calResult + calMeasured[] (calSeq is just the ack), so
         * these two comparisons are the whole of what the host gets to reason
         * from. */
        elsCalCtx_t openCtx{};
        Drivetrain openDt{LASH, SPZ, false};
        int32_t openTicks = runCal(openCtx, openDt, TIGHT, THRESH, 1);
        checkEq(openCtx.result, ELS_CAL_ERR_NO_MOTION, "open half-nut also reports NO_MOTION");
        checkEq(tightCtx.result, openCtx.result,
                "NOT DISTINGUISHABLE: ceiling-exceeded and open half-nut share one code");
        check(sameMeasured(tightCtx.measured, openCtx.measured, ELS_CAL_CYCLES),
              "...and the measured[] arrays are identical too - nothing else is published");
        /* Printed, not asserted: the runs DO differ physically — the
         * ceiling-exceeded one seats successfully first, so it takes longer and
         * leaves a Z trace. The information exists at the machine; it just is
         * not in anything the host is handed. Asserting a tick count would pin
         * fixture arithmetic rather than firmware behaviour. */
        printf("      ceiling-exceeded: %d ticks (seated, then ran out); "
               "open half-nut: %d ticks (never moved)\n",
               (int)tightTicks, (int)openTicks);

        /* ---- D: the invariant across EVERY ceiling -------------------------
         * Case B is one ceiling and one nut position. This sweeps every ceiling
         * from a degenerate one step up past the boundary, at four nut
         * parkings, and pins the property the section is really about — an
         * outcome is either a real measurement or nothing, never a short one:
         *
         *     OK       => three cycles, every measurement AT OR ABOVE the true
         *                 lash (the detection distance biases it high, never low
         *                 — els_backlash_cal.h relies on that direction when it
         *                 sizes the take-up margin);
         *     NO_MOTION => cycle 0 and an empty measured[].
         *
         * Nothing in between, at any ceiling. That is the claim a wrong-number
         * hazard needs, and one hand-picked ceiling cannot make it.
         *
         * (Ceiling 0 is not swept: elsCalUpdate() refuses it up front with
         * ELS_CAL_ERR_CONFIG, so it never reaches this FSM.)
         *
         * Sweeping nut parkings is not padding. The lash a leg must cross is
         * fixed, but the DETECTION distance is not: z = carriage/SPZ, so how
         * far the carriage must travel to register THRESH counts depends on
         * where it is sitting between counts when the leg arms — up to SPZ-1
         * steps of swing. That is why this sweep, unlike case E, cannot state a
         * single boundary; ceilings 104 and 105 succeed here and fail there, on
         * the same machine. */
        bool everHung = false, everShort = false, everRecordedOnFailure = false;
        for (int32_t start = 0; start < LASH; start += LASH / 4) {
            for (int32_t ceil = 1; ceil <= BOUNDARY + 10; ceil++) {
                elsCalCtx_t c{};
                Drivetrain dt{LASH, SPZ, true};
                dt.nutPos = start;
                /* Tight tick budget: a run that cannot end is one of the
                 * interesting failures here (mutation C2), and 20k ticks is
                 * ~40x what a healthy five-leg run at this ceiling consumes. */
                int32_t t = runCal(c, dt, ceil, THRESH, 1, /*maxTicks*/ 20000);
                if (t < 0) { everHung = true; continue; }
                if (c.result == ELS_CAL_OK) {
                    if (c.cycle != ELS_CAL_CYCLES) everShort = true;
                    for (int32_t i = 0; i < ELS_CAL_CYCLES; i++) {
                        if (c.measured[i] < LASH) everShort = true;
                    }
                } else {
                    if (c.cycle != 0 || !allZero(c.measured, ELS_CAL_CYCLES)) {
                        everRecordedOnFailure = true;
                    }
                }
            }
        }
        check(!everHung, "every ceiling terminates, however small");
        check(!everShort,
              "no ceiling produces a SHORT measurement - a run that reports OK measured the lash");
        check(!everRecordedOnFailure,
              "no FAILED run leaves a number behind - refusal and measurement never mix");

        /* ---- E: where the boundary actually is ----------------------------
         * The trap for whoever sets this number. "Ceiling above the lash" is
         * NOT the requirement — the leg also has to buy the detection distance,
         * because the measurement completes when the SCALE moves, not when the
         * nut reaches the wall. So a ceiling set from a dial-indicator reading
         * plus a little still fails, and fails as NO_MOTION, i.e. as a half-nut
         * complaint. Below, BOUNDARY-1 is already 5 steps clear of the true
         * lash and is still refused.
         *
         * Verified over 36 (lash, steps-per-count, threshold) combinations
         * while writing this: FROM A CARRIAGE SITTING EXACTLY ON A COUNT
         * BOUNDARY, the first ceiling that succeeds is exactly
         * lash + steps-per-count * threshold, every time. That qualifier is the
         * whole reason case D exists as a sweep — start the carriage between
         * counts and the requirement drops by up to steps-per-count - 1, so the
         * ceiling a machine needs is not one number even on one machine. The
         * take-up's own margin exists for the same reason
         * (els_backlash_cal.h: "the measurement already reads high by the
         * detection distance"). */
        {
            elsCalCtx_t justShort{};
            Drivetrain shortDt{LASH, SPZ, true};
            runCal(justShort, shortDt, BOUNDARY - 1, THRESH, 1);
            checkEq(justShort.result, ELS_CAL_ERR_NO_MOTION,
                    "a ceiling ABOVE the true lash still fails if it cannot buy detection");

            elsCalCtx_t justEnough{};
            Drivetrain enoughDt{LASH, SPZ, true};
            runCal(justEnough, enoughDt, BOUNDARY, THRESH, 1);
            checkEq(justEnough.result, ELS_CAL_OK,
                    "lash + detection distance is the first ceiling that succeeds");
            check(justEnough.measured[0] >= LASH,
                  "...and it measures at or above the true lash, never under it");
        }
    }

    printf("\n%s (%d failure%s)\n", failures ? "FAILED" : "PASSED",
           failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
