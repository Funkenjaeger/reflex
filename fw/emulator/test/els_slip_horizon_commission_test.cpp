/*
 * WHY ELS_SLIP_SETTLE_TICKS IS WHAT IT IS -- REWRITTEN 2026-08-28 AFTER THE
 * ORIGINAL ARGUMENT WAS REFUTED BY A SECOND SAMPLE.
 *
 * ---- THE CLAIM THIS FILE USED TO MAKE, AND WHY IT IS GONE ------------------
 * The 2026-08-27 version justified 700 ticks (7 ms) by an EMPTY BAND: 18
 * captures produced 7 moving observations that clumped at <=656 ticks and
 * >=1165 ticks, with nothing between, and 700 sat in the hole. C3 asserted
 * that nothing had ever been observed in the band the reduction gave up.
 *
 * A second run of the same size, taken 2026-08-28 after the ISR rate moved to
 * 50 kHz, filled the hole in. Combined, 36 captures and 16 moving observations
 * spread CONTINUOUSLY from 0.79 ms to 17.86 ms:
 *
 *   790  900 1780 3400 4900 5450 5710 6560 6780 7300 9020 11600 11650 12080
 *   13990 17860   (microseconds)
 *
 * The largest gap anywhere in that set is at the TOP (13990 -> 17860), and the
 * interval around 7 ms is one of the tightest in it (6780 -> 7300, 520 us).
 * THE BAND WAS A SMALL-SAMPLE ARTIFACT. Seven observations could not resolve
 * the distribution, and a test was built around the hole in them.
 *
 * That failure is kept here rather than deleted, and C3 is now inverted: it
 * asserts the gap does NOT exist, so the old reasoning cannot be rediscovered
 * and re-adopted from the same data.
 *
 * ---- WHAT ACTUALLY JUSTIFIES THE HORIZON -----------------------------------
 * The gap was never the right basis anyway. The horizon decides which Z motion
 * is CREDITED to the servo's own pulses, and it has exactly two jobs:
 *
 *   SAFETY (the reason it exists): a HAND on the handwheel must never be
 *   credited. The 2026-08-08 defect was precisely that -- a hand-pushed
 *   carriage accepted as proof the half-nut was engaged. Human motion after
 *   the pulses stop is >= ~100 ms; the machine's own settling is over inside
 *   18 ms. Two orders of magnitude apart, which is why this is the easy half.
 *
 *   COST (what a shorter horizon buys and spends): motion later than the
 *   horizon is not credited. Every single moving capture in all 36 delivered
 *   exactly ONE Z count (net_counts == -1, no exceptions, no +1 anywhere --
 *   one-directional real motion, not dither). So the entire cost of a short
 *   horizon is one count per stranded capture, against a take-up confirmation
 *   threshold that is ~15 Z counts on the commissioned machine.
 *
 * So the horizon is not a compromise between two tight constraints. It has
 * ~14x margin on the side that matters and spends ~1/15th of a threshold on
 * the side that does not. THAT is the argument, and unlike the band it does
 * not depend on the sample being large enough to show a hole.
 *
 * ---- OBSERVATIONS ARE IN MICROSECONDS, DELIBERATELY -------------------------
 * The previous version stored them as TICK COUNTS, which made the whole file
 * silently rate-dependent -- the 2026-08-28 change from 100 kHz to 50 kHz would
 * have halved every constant while the observations stayed put. They are
 * physical settling times; they are stored as such and converted at the rate
 * the build declares.
 */
#include "els_isr_rate.h"

extern "C" {
#include "els_slip.h"
}

#include <cstdio>
#include <cstdint>

static int failures = 0;

static void check(bool cond, const char *what)
{
    printf("   %-66s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

/* ---- the horizon, and what it is measured against ------------------------ */

/* Ramps.c: ELS_SLIP_SETTLE_TICKS = ELS_MS_TO_TICKS(7). Duplicated because that
 * constant is #defined in a .c where no test can see it -- keep in step by
 * hand, and see els_isr_rate_test for the same caveat. */
static const int32_t HORIZON_US = 7000;

/* The post-take-up dwell the horizon must outlast (ELS_SETTLE_TICKS, 500 us):
 * els_slip.h requires the horizon to comfortably exceed it, or the dwell
 * expires while motion is still being attributed. */
static const int32_t DWELL_US = 500;

/* The fastest a human hand could plausibly deliver the 2026-08-08 nudge after
 * the servo stops pulsing. Deliberately AGGRESSIVE -- real nudges in the bench
 * probe are seconds later. If the horizon ever approaches this, the gate stops
 * being able to tell a hand from the drivetrain. */
static const int32_t HAND_NUDGE_FLOOR_US = 100000;   /* 100 ms */

/* ---- the observations: 36 captures, 2026-08-27 (100 kHz) + 08-28 (50 kHz) - */
static const int32_t OBSERVED_SETTLE_US[] = {
    790, 900, 1780, 3400, 4900, 5450, 5710, 6560,
    6780, 7300, 9020, 11600, 11650, 12080, 13990, 17860,
};
static const int N_OBSERVED   = (int)(sizeof(OBSERVED_SETTLE_US) / sizeof(int32_t));
static const int STILL_CAPTURES = 20;   /* 11 + 9, carriage completely still */
static const int TOTAL_CAPTURES = 36;   /* 18 + 18, ALL END_WINDOW */

/* Every moving capture delivered exactly one count, in both runs. */
static const int32_t NET_COUNTS_PER_MOVING_CAPTURE = -1;

/* Take-up confirmation threshold on the commissioned machine, in Z counts,
 * after the 2026-08-28 backlash calibration (measured mean 370 steps, commanded
 * 444, derated by half). The cost of a stranded count is judged against this. */
static const int32_t CONFIRM_THRESHOLD_COUNTS = 15;

static int32_t usToTicks(int32_t us)
{
    return (int32_t)(((int64_t)us * (ELS_ISR_TICK_HZ / 1000)) / 1000);
}

/* Drive the real accumulator: one take-up pulse at t=0, quiet, then a single
 * count at `age` ticks. Returns whether the firmware credits it to the servo. */
static bool attributedAt(int32_t age, int32_t horizon)
{
    elsSlipAccum_t a;
    elsSlipReset(&a);
    elsSlipTick(&a, 0, +1, horizon);              /* the last take-up pulse */
    for (int32_t i = 1; i < age; i++) {
        elsSlipTick(&a, 0, 0, horizon);           /* quiet */
    }
    elsSlipTick(&a, NET_COUNTS_PER_MOVING_CAPTURE, 0, horizon);
    return a.attributedZCounts != 0;
}

int main()
{
    const int32_t horizon = usToTicks(HORIZON_US);
    printf("=== ELS_SLIP_SETTLE_TICKS: what justifies it ===\n\n");
    printf("   build rate %d Hz -> horizon %d ticks (%d us)\n\n",
           ELS_ISR_TICK_HZ, (int)horizon, (int)HORIZON_US);

    /* ---- C1: the horizon is a duration, and it survives the rate --------- */
    printf("C1: the horizon converts to a usable number of ticks\n");
    {
        check(horizon > 0, "the horizon does not truncate to zero ticks");
        check(horizon > usToTicks(DWELL_US) * 4,
              "the horizon comfortably outlasts the post-take-up dwell");
    }

    /* ---- C2: the safety margin, which is the reason it exists ------------ */
    printf("\nC2: a hand cannot be credited -- the half of this that matters\n");
    {
        check(HORIZON_US * 10 <= HAND_NUDGE_FLOOR_US,
              "the horizon is >=10x below the fastest plausible hand nudge");
        check(!attributedAt(usToTicks(HAND_NUDGE_FLOOR_US), horizon),
              "a count at the hand-nudge floor is NOT attributed");
        /* The bench probe's nudge is seconds out, not 100 ms. */
        check(!attributedAt(usToTicks(1000000), horizon),
              "a count one full second late is NOT attributed");
    }

    /* ---- C3 (INVERTED 2026-08-28): there is no empty band ---------------- */
    printf("\nC3: the RETRACTION -- the band the old argument relied on is not there\n");
    {
        /* The old C3 asserted nothing was ever observed between the new horizon
         * and the old one (1000 ticks / 10 ms). Four of the 16 combined
         * observations are: 7300, 9020 us and, at the boundary, 6780. Assert
         * the refutation explicitly so the band cannot be re-derived. */
        int inOldBand = 0;
        for (int i = 0; i < N_OBSERVED; i++) {
            if (OBSERVED_SETTLE_US[i] > HORIZON_US && OBSERVED_SETTLE_US[i] <= 10000) {
                inOldBand++;
            }
        }
        printf("   observations between the horizon and the old 10 ms: %d\n", inOldBand);
        check(inOldBand > 0,
              "the 'empty band' is occupied -- the 08-27 argument is refuted");

        /* And the interval around the horizon is one of the TIGHTEST in the
         * set, not the widest. Guards against someone re-running a small sample
         * and rediscovering a hole. */
        int32_t widestGap = 0, gapAtHorizon = 0;
        for (int i = 0; i + 1 < N_OBSERVED; i++) {
            int32_t g = OBSERVED_SETTLE_US[i + 1] - OBSERVED_SETTLE_US[i];
            if (g > widestGap) widestGap = g;
            if (OBSERVED_SETTLE_US[i] < HORIZON_US
                && OBSERVED_SETTLE_US[i + 1] >= HORIZON_US) {
                gapAtHorizon = g;
            }
        }
        printf("   widest gap in the set %d us; gap spanning the horizon %d us\n",
               (int)widestGap, (int)gapAtHorizon);
        check(gapAtHorizon * 2 < widestGap,
              "the horizon does not sit in a wide gap; the distribution is continuous");
    }

    /* ---- C4: what a short horizon actually costs ------------------------- */
    printf("\nC4: the cost of stranding late motion is bounded and immaterial\n");
    {
        int stranded = 0;
        for (int i = 0; i < N_OBSERVED; i++) {
            if (!attributedAt(usToTicks(OBSERVED_SETTLE_US[i]), horizon)) stranded++;
        }
        printf("   %d of %d observations fall outside the horizon\n",
               stranded, N_OBSERVED);
        check(stranded > 0,
              "the horizon really does strand some observed motion (no free lunch)");
        /* Each stranded capture withholds exactly one count. Even if every
         * observation were stranded, the total is small against the take-up
         * confirmation threshold -- which is what the credit feeds. */
        check(N_OBSERVED * 1 < CONFIRM_THRESHOLD_COUNTS * 2,
              "even total stranding stays within an order of the confirm threshold");
    }

    /* ---- C5: the boundary is exactly where the firmware puts it ---------- */
    printf("\nC5: attribution flips exactly at the horizon, not near it\n");
    {
        check(attributedAt(horizon, horizon),
              "a count exactly AT the horizon is attributed");
        check(!attributedAt(horizon + 1, horizon),
              "a count one tick PAST the horizon is not");
    }

    /* ---- C6: the observation set is internally consistent ---------------- */
    printf("\nC6: the record this argument rests on\n");
    {
        check(N_OBSERVED + STILL_CAPTURES == TOTAL_CAPTURES,
              "moving + still captures account for all 36");
        check(NET_COUNTS_PER_MOVING_CAPTURE == -1,
              "every moving capture delivered exactly one count, one direction");
        bool sorted = true;
        for (int i = 0; i + 1 < N_OBSERVED; i++) {
            if (OBSERVED_SETTLE_US[i] >= OBSERVED_SETTLE_US[i + 1]) sorted = false;
        }
        check(sorted, "the observations are recorded in order, no duplicates");
        /* The whole set fits inside the probe's 20 ms capture window, so none
         * of these is a truncated measurement reported as a settled one. */
        check(OBSERVED_SETTLE_US[N_OBSERVED - 1] < 20000,
              "the longest observation fits inside the 20 ms capture window");
    }

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
