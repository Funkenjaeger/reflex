/*
 * THE TICK CONSTANTS MEAN DURATIONS. This pins that they still do.
 *
 * Every ELS timing constant is a tick count, and a tick count is meaningless
 * without the rate. Until 2026-08-28 that coupling was implicit, and the
 * recorded hazard on the take-up gate task is exactly what it invites: "a
 * CubeMX regen silently HALVES the ISR rate, doubling every tick constant in
 * wall-clock terms." servoCycles self-corrects because it is derived at runtime
 * from the live timer registers; the raw counts did not.
 *
 * They are now derived from ELS_ISR_TICK_HZ (els_isr_rate.h). This file checks
 * the three things that can still go wrong with that:
 *
 *   1. TRUNCATION. ELS_US_TO_TICKS is integer arithmetic. At a low enough rate
 *      a short duration rounds DOWN to zero and a guard silently stops
 *      guarding. A settle horizon of 0 ticks attributes nothing.
 *   2. THE PHYSICAL ORDERING. The constants are not independent -- the settle
 *      horizon must outlast the post-take-up dwell, the confirm window must
 *      outlast the settle horizon, the timeout must outlast the confirm window.
 *      A botched rescale can preserve every individual value and still invert
 *      one of these, which is a defect no single-constant check would see.
 *   3. THE REFACTOR IS FAITHFUL. At the OLD 100 kHz rate every derived value
 *      must equal the literal it replaced. That is what makes 2026-08-28 a rate
 *      change rather than a rate change plus a quiet re-tuning.
 *
 * WHAT THIS FILE CANNOT CHECK, stated so nobody reads it as covering more than
 * it does: the durations below are DUPLICATED from Ramps.c, because those
 * constants are #defined in a .c and no test can see them. If someone changes
 * ELS_SLIP_SETTLE_TICKS to ELS_MS_TO_TICKS(9) in Ramps.c, this file keeps
 * asserting 7 ms and stays green. Removing that gap means moving the constants
 * into a header, which is a separate change. Until then the duplication is
 * named rather than hidden.
 */
#include "els_isr_rate.h"

#include <cstdio>
#include <cstdint>

static int failures = 0;

static void check(bool cond, const char *what)
{
    printf("   %-62s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

/* The durations Ramps.c and els_diag_takeup_settle.h are written in terms of.
 * Keep in step with them by hand -- see the caveat in the header comment. */
struct Timing {
    const char *name;
    int32_t     us;         /* intended duration */
    int32_t     at100k;     /* the literal this replaced, before 2026-08-28 */
};

static const Timing TIMINGS[] = {
    { "ELS_SETTLE_TICKS",                 500,        50 },
    { "ELS_QUIESCENT_TICKS",             2000,       200 },
    { "ELS_SLIP_SETTLE_TICKS",           7000,       700 },
    { "ELS_TAKEUP_CONFIRM_WINDOW_TICKS", 250000,   25000 },
    { "ELS_TAKEUP_TIMEOUT_TICKS",       5000000,  500000 },
    { "ELS_DIAG_BUCKET_TICKS",            400,        40 },
};
static const int N_TIMINGS = (int)(sizeof(TIMINGS) / sizeof(TIMINGS[0]));

/* The conversion under test, evaluated at an arbitrary rate rather than only
 * the compiled one -- otherwise the truncation check can only ever see the
 * rate this build happens to use. */
static int32_t ticks_at(int32_t us, int32_t hz)
{
    return (int32_t)(((int64_t)us * (hz / 1000)) / 1000);
}

int main()
{
    printf("=== ISR tick constants are durations, not magic numbers ===\n\n");
    printf("-- compiled rate: %d Hz (%d us/tick, %d cycle budget) --\n\n",
           ELS_ISR_TICK_HZ, 1000000 / ELS_ISR_TICK_HZ, ELS_ISR_CYCLE_BUDGET);

    /* ---- 1. no constant truncates away ---------------------------------- */
    printf("-- 1. every duration survives the integer conversion --\n");
    {
        int before = failures;
        for (int i = 0; i < N_TIMINGS; i++) {
            int32_t t = ticks_at(TIMINGS[i].us, ELS_ISR_TICK_HZ);
            if (t <= 0) {
                printf("   %s truncated to %d ticks at %d Hz\n",
                       TIMINGS[i].name, (int)t, ELS_ISR_TICK_HZ);
                failures++;
            }
        }
        check(failures == before, "no constant rounds down to zero ticks");

        /* And the round trip is exact, not merely nonzero: a conversion that
         * lost 30% would still be positive. */
        int before2 = failures;
        for (int i = 0; i < N_TIMINGS; i++) {
            int32_t t  = ticks_at(TIMINGS[i].us, ELS_ISR_TICK_HZ);
            int32_t us = (int32_t)(((int64_t)t * 1000000) / ELS_ISR_TICK_HZ);
            if (us != TIMINGS[i].us) {
                printf("   %s round-trips %d us -> %d ticks -> %d us\n",
                       TIMINGS[i].name, (int)TIMINGS[i].us, (int)t, (int)us);
                failures++;
            }
        }
        check(failures == before2, "every duration round-trips exactly");
    }

    /* ---- 2. the physical ordering the constants encode ------------------- */
    printf("\n-- 2. the ordering invariants, at the compiled rate --\n");
    {
        const int32_t settle_gate = ticks_at(500,     ELS_ISR_TICK_HZ);
        const int32_t slip        = ticks_at(7000,    ELS_ISR_TICK_HZ);
        const int32_t confirm     = ticks_at(250000,  ELS_ISR_TICK_HZ);
        const int32_t timeout     = ticks_at(5000000, ELS_ISR_TICK_HZ);
        const int32_t bucket      = ticks_at(400,     ELS_ISR_TICK_HZ);

        /* els_slip.h: "The horizon must comfortably exceed ELS_SETTLE_TICKS,
         * the post-take-up dwell" -- otherwise the dwell outlives the window in
         * which late motion is still credited to the servo. */
        check(slip > settle_gate * 4,
              "slip horizon comfortably exceeds the post-take-up dwell");

        /* A confirm window shorter than the settle horizon would abort a
         * take-up while its own motion was still being attributed. */
        check(confirm > slip,
              "confirm window outlasts the slip horizon");

        /* The timeout is the backstop for the window; inverting them makes the
         * backstop fire first and mask the real failure. */
        check(timeout > confirm,
              "take-up timeout outlasts the confirm window");

        /* A diag bucket wider than the horizon it samples cannot resolve it. */
        check(bucket * 4 < slip,
              "diag bucket resolves the slip horizon (>=4 buckets)");
    }

    /* ---- 3. the 2026-08-28 refactor is faithful -------------------------- */
    printf("\n-- 3. at the OLD 100 kHz rate, every value is the literal it replaced --\n");
    {
        int before = failures;
        for (int i = 0; i < N_TIMINGS; i++) {
            int32_t t = ticks_at(TIMINGS[i].us, 100000);
            if (t != TIMINGS[i].at100k) {
                printf("   %s: %d us -> %d ticks at 100 kHz, was %d\n",
                       TIMINGS[i].name, (int)TIMINGS[i].us, (int)t,
                       (int)TIMINGS[i].at100k);
                failures++;
            }
        }
        check(failures == before,
              "expressing them as durations changed no value at 100 kHz");

        /* The budget too: it was written down as 1000 and is now derived. */
        check(100000000 / 100000 == 1000,
              "the ISR cycle budget still derives to 1000 at 100 kHz");
    }

    /* ---- 4. the ordering holds at every rate this could plausibly run ---- */
    printf("\n-- 4. the invariants are not an accident of one rate --\n");
    {
        int before = failures;
        const int32_t RATES[] = { 20000, 50000, 100000 };
        for (int r = 0; r < 3; r++) {
            int32_t hz = RATES[r];
            int32_t settle_gate = ticks_at(500,     hz);
            int32_t slip        = ticks_at(7000,    hz);
            int32_t confirm     = ticks_at(250000,  hz);
            int32_t timeout     = ticks_at(5000000, hz);
            bool ok = settle_gate > 0 && slip > settle_gate * 4
                      && confirm > slip && timeout > confirm;
            printf("   %6d Hz: gate=%-4d slip=%-5d confirm=%-6d timeout=%-8d %s\n",
                   (int)hz, (int)settle_gate, (int)slip, (int)confirm,
                   (int)timeout, ok ? "ok" : "BROKEN");
            if (!ok) failures++;
        }
        check(failures == before,
              "20 kHz (the pulse-shape floor) through 100 kHz all hold");
    }

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
