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
 * ---- AND WHY IT IS 20 ms, NOT 7 (2026-08-30, Evan's call) ------------------
 * Because the cost side is measured and the safety side is not binding. At
 * 7 ms, SEVEN of the sixteen observed tails (43.8%) fell outside the horizon
 * and were discarded. Against that, the margin only ever guarded a hand that
 * STARTS moving after the pulses stop: a hand with no recent pulse is refused
 * at any horizon, and a hand already moving during the pulses is credited at
 * any horizon. Between 5x and 14x against a soft 100 ms floor, nothing real
 * changes.
 *
 * So the asymmetry runs the other way from what this file used to say. Too
 * small discards real servo-caused settle; too large widens a sliver that is
 * undefended at any width and costs one count per stranded tail against a
 * threshold of 15. 20 ms clears the longest tail ever observed (17.86 ms),
 * stays at 8% of the confirmation window, and remains 40x the dwell.
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

/* Ramps.c: ELS_SLIP_SETTLE_TICKS = ELS_MS_TO_TICKS(20). Duplicated because that
 * constant is #defined in a .c where no test can see it -- keep in step by
 * hand, and see els_isr_rate_test for the same caveat. */
static const int32_t HORIZON_US = 20000;

/* The take-up confirmation window the horizon lives inside
 * (ELS_TAKEUP_CONFIRM_WINDOW_TICKS, 250 ms). A horizon approaching this stops
 * narrowing anything, which is the real upper bound on raising it. */
static const int32_t CONFIRM_WINDOW_US = 250000;

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
        /* 5x, not the 10x this file used to demand. The 10x was chosen to
         * fit a 7 ms horizon, not derived -- and the margin was always doing
         * less work than its number suggested:
         *
         *   - a hand with NO recent pulse is refused at ANY horizon. That is
         *     the 2026-08-08 defect, and the case actually defended.
         *   - a hand already moving DURING the pulses is credited at ANY
         *     horizon; els_slip.h states this is indistinguishable from
         *     inertial settle using Z and commanded steps alone.
         *
         * So this guards only a hand that STARTS moving after the pulses stop.
         * Between 5x and 14x against a soft, order-of-magnitude floor nothing
         * real changes -- while 7 ms stranded 43.8% of measured settle. */
        check(HORIZON_US * 5 <= HAND_NUDGE_FLOOR_US,
              "the horizon is >=5x below the fastest plausible hand nudge");
        check(!attributedAt(usToTicks(HAND_NUDGE_FLOOR_US), horizon),
              "a count at the hand-nudge floor is NOT attributed");
        /* The bench probe's nudge is seconds out, not 100 ms. */
        check(!attributedAt(usToTicks(1000000), horizon),
              "a count one full second late is NOT attributed");
    }

    /* ---- C3: the distribution is continuous, and 7 ms sat inside it ------ */
    printf("\nC3: the RETRACTION -- there was never a band, and 7 ms was inside the data\n");
    {
        /* The 2026-08-27 argument put the horizon in an apparently empty band
         * between 6560 and 11600 us. A second sample of the same size filled
         * it in. The refutation is kept as an assertion so the hole cannot be
         * rediscovered from the same data and re-adopted. */
        int32_t widestGap = 0;
        for (int i = 0; i + 1 < N_OBSERVED; i++) {
            int32_t g = OBSERVED_SETTLE_US[i + 1] - OBSERVED_SETTLE_US[i];
            if (g > widestGap) widestGap = g;
        }
        printf("   widest gap anywhere in the set: %d us\n", (int)widestGap);
        check(widestGap * 2 < HORIZON_US,
              "no gap in the set is wide enough to hide a horizon in");

        /* The positive case for moving 7 -> 20 ms: how much REAL settle the
         * old value discarded. This is the measurement that beat the margin
         * argument, so it is asserted rather than left in a commit message. */
        int strandedAtOld = 0;
        for (int i = 0; i < N_OBSERVED; i++) {
            if (OBSERVED_SETTLE_US[i] > 7000) strandedAtOld++;
        }
        printf("   a 7 ms horizon would strand %d of %d observations\n",
               strandedAtOld, N_OBSERVED);
        check(strandedAtOld * 3 > N_OBSERVED,
              "the old horizon discarded a large fraction of real settle");
    }

    /* ---- C4: the horizon now covers the measured distribution ----------- */
    printf("\nC4: nothing measured is stranded, and erring high is the cheap direction\n");
    {
        int stranded = 0;
        for (int i = 0; i < N_OBSERVED; i++) {
            if (!attributedAt(usToTicks(OBSERVED_SETTLE_US[i]), horizon)) stranded++;
        }
        printf("   %d of %d observations fall outside the horizon\n",
               stranded, N_OBSERVED);
        check(stranded == 0,
              "every observed settle tail is attributed");
        check(HORIZON_US > OBSERVED_SETTLE_US[N_OBSERVED - 1],
              "the horizon clears the longest tail ever observed");

        /* Zero stranding across 16 observations is NOT proof of zero stranding
         * -- clearing a small sample's maximum is the same species of move as
         * the empty band was. What makes it defensible is the asymmetry the
         * band argument never had: a stranded tail costs exactly one count,
         * against a threshold of 15, while a too-small horizon throws away
         * real servo-caused settle 44% of the time. */
        check(NET_COUNTS_PER_MOVING_CAPTURE == -1,
              "a stranded tail would still cost exactly one count");
        check(1 * 10 < CONFIRM_THRESHOLD_COUNTS,
              "that cost is an order below the confirm threshold");

        /* The real upper bound: a horizon approaching the confirmation window
         * stops narrowing anything, and attribution degrades toward the
         * un-attributed behaviour it replaced (els_slip.h). */
        check(HORIZON_US * 10 <= CONFIRM_WINDOW_US,
              "the horizon stays <=10% of the take-up confirmation window");
        check(horizon > usToTicks(DWELL_US) * 20,
              "and >20x the post-take-up dwell it must outlast");
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
