/*
 * The stop-overshoot probe (schema 7), driven directly through its entry
 * points.
 *
 * WHY THIS FILE HAS TO EXIST. The probe's whole job is to distinguish "the
 * firmware commanded the overshoot" from "it did not", and the second answer
 * is reported as a ZERO. A probe that silently never accumulated anything
 * would publish exactly that zero and look like a finding. So the tests below
 * are built around making the probe COUNT things and checking it counted them
 * -- the discriminator is exercised in both directions, and no assertion here
 * is satisfied by a probe that does nothing.
 *
 * These call elsDiagTick directly rather than through the ISR. That is the
 * whole point: the ISR path is what supplies dZ and dServo, and this file
 * exercises what the probe DOES with them, at tick granularity, with no
 * emulator timing in the way.
 */
extern "C" {
#include "Ramps.h"
}

#include <cstdio>
#include <cstring>
#include <cstdint>

static int failures = 0;

static void check(bool cond, const char *what)
{
    printf("   %-64s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

/* The int32 the probe packs into two uint16 reserved registers. */
static int32_t servoStepsFrom(const elsStop_t &s)
{
    return (int32_t)((uint32_t)s.diagReserved[0]
                     | ((uint32_t)s.diagReserved[1] << 16));
}

struct Rig {
    elsDiagCtx_t ctx;
    elsStop_t    stop;
    Rig() {
        std::memset(&ctx, 0, sizeof(ctx));
        std::memset(&stop, 0, sizeof(stop));
        elsDiagInit(&ctx, &stop);
    }
    /* One ISR tick. `active` is the live elsStop.active the ISR would show. */
    void tick(uint16_t active, int32_t dZ, int32_t dServo) {
        stop.active = active;
        elsDiagTick(&ctx, &stop, dZ, dServo);
    }
};

int main()
{
    printf("=== stop-overshoot probe (schema 7) ===\n\n");
    printf("   bucket %d ticks, %d buckets, quiet run %d ticks\n\n",
           (int)ELS_DIAG_BUCKET_TICKS, (int)ELS_DIAG_TRACE_BUCKETS,
           (int)ELS_DIAG_STOP_QUIET_TICKS);

    /* ---- 1. it advertises itself ---------------------------------------- */
    printf("-- 1. the block names what is in it --\n");
    {
        Rig r;
        check(r.stop.diagSchema == ELS_DIAG_SCHEMA_STOP_OVERSHOOT,
              "diagSchema identifies this probe, not a stale one");
        check(r.stop.diagBucketTicks == (uint16_t)ELS_DIAG_BUCKET_TICKS,
              "bucket width is published, so no reader assumes the ISR rate");
        check(r.stop.diagBucketCount == ELS_DIAG_TRACE_BUCKETS,
              "bucket count is published");
    }

    /* ---- 2. nothing is captured before the stop fires -------------------- */
    printf("\n-- 2. motion BEFORE the trigger is not overshoot --\n");
    {
        Rig r;
        for (int i = 0; i < 200; i++) r.tick(0, -5, 3);   /* cutting along */
        check(r.stop.diagSeq == 0, "no capture published while active == 0");
        check(r.stop.diagNetCounts == 0,
              "pre-trigger Z motion is not accumulated");
        check(servoStepsFrom(r.stop) == 0,
              "pre-trigger servo steps are not accumulated");
    }

    /* ---- 3. THE DISCRIMINATOR, case A: the firmware kept stepping -------- */
    printf("\n-- 3. firmware-commanded overshoot is reported as such --\n");
    {
        Rig r;
        r.tick(0, -4, 2);            /* last tick of the cut */
        r.tick(1, -3, 2);            /* TRIGGER, and the servo is still stepping */
        for (int i = 0; i < 20; i++) r.tick(1, -1, 1);
        for (int i = 0; i < (int)ELS_DIAG_STOP_QUIET_TICKS + 2; i++) r.tick(1, 0, 0);

        check(r.stop.diagSeq == 1, "exactly one capture published");
        check(r.stop.diagEndReason == ELS_DIAG_STOP_END_SETTLED,
              "ended SETTLED -- the carriage stopped and stayed stopped");
        check(r.stop.diagNetCounts == -23,
              "net counts is the post-trigger travel only (-3 + 20x-1)");
        check(servoStepsFrom(r.stop) == 22,
              "servo steps after the trigger are counted (2 + 20x1)");
        check(servoStepsFrom(r.stop) > 0,
              "NONZERO -- this reads as 'the firmware commanded it'");
    }

    /* ---- 4. THE DISCRIMINATOR, case B: the firmware sent nothing --------- */
    printf("\n-- 4. carriage moving with no pulses reads as NOT commanded --\n");
    {
        Rig r;
        r.tick(0, -4, 2);
        r.tick(1, 0, 0);                                   /* TRIGGER, quiet */
        for (int i = 0; i < 15; i++) r.tick(1, -1, 0);      /* coasts, no pulses */
        for (int i = 0; i < (int)ELS_DIAG_STOP_QUIET_TICKS + 2; i++) r.tick(1, 0, 0);

        check(r.stop.diagSeq == 1, "one capture published");
        check(r.stop.diagNetCounts == -15,
              "the carriage travel is still measured");
        check(servoStepsFrom(r.stop) == 0,
              "ZERO steps -- the firmware sent nothing");
        /* The pair is what makes this readable: travel without pulses. A probe
         * that failed to accumulate EITHER would also report zero steps, which
         * is why case 3 above has to pass for this one to mean anything. */
        check(r.stop.diagNetCounts != 0 && servoStepsFrom(r.stop) == 0,
              "travel nonzero AND steps zero -- the diagnostic pair");
    }

    /* ---- 5. settle timing ------------------------------------------------ */
    printf("\n-- 5. settle ticks names when the carriage actually stopped --\n");
    {
        Rig r;
        r.tick(0, -4, 2);
        r.tick(1, -1, 0);                                  /* trigger, tick 0 */
        for (int i = 0; i < 9; i++)  r.tick(1, -1, 0);      /* through tick 9 */
        for (int i = 0; i < (int)ELS_DIAG_STOP_QUIET_TICKS + 2; i++) r.tick(1, 0, 0);
        check(r.stop.diagSettleTicks == 9,
              "settle_ticks is the LAST tick that saw motion, not the capture length");
        check(r.stop.diagCaptureTicks > r.stop.diagSettleTicks,
              "capture ran past the last motion, which is how quiet was proven");
    }

    /* ---- 6. the window bound, and what it means -------------------------- */
    printf("\n-- 6. motion that never stops ends WINDOW, not SETTLED --\n");
    {
        Rig r;
        r.tick(0, -4, 2);
        r.tick(1, -1, 0);
        /* Never quiet: one count every tick for the whole trace. */
        for (int i = 0; i < (int)ELS_DIAG_TRACE_BUCKETS * (int)ELS_DIAG_BUCKET_TICKS + 5; i++) {
            r.tick(1, -1, 0);
        }
        check(r.stop.diagSeq >= 1, "a capture published");
        check(r.stop.diagEndReason == ELS_DIAG_STOP_END_WINDOW,
              "ended WINDOW -- read net counts as a floor, not a result");
    }

    /* ---- 7. one capture per stop, re-armed for the next pass ------------- */
    printf("\n-- 7. a second pass captures again --\n");
    {
        Rig r;
        for (int pass = 0; pass < 3; pass++) {
            r.tick(0, -4, 2);                              /* cutting */
            r.tick(1, -2, 1);                              /* trigger */
            for (int i = 0; i < 5; i++) r.tick(1, -1, 0);
            for (int i = 0; i < (int)ELS_DIAG_STOP_QUIET_TICKS + 2; i++) r.tick(1, 0, 0);
            r.tick(0, 0, 0);                               /* stop released */
        }
        check(r.stop.diagSeq == 3, "three passes produced three captures");
        check(r.stop.diagNetCounts == -7,
              "the last capture is the last pass alone, not a running total");
    }

    /* ---- 8. the trace shows the shape ------------------------------------ */
    printf("\n-- 8. the trace localises the motion in time --\n");
    {
        Rig r;
        r.tick(0, -4, 2);
        r.tick(1, 0, 0);                                   /* trigger, quiet */
        /* Stay quiet through bucket 0, then move during bucket 1. */
        for (int i = 1; i < (int)ELS_DIAG_BUCKET_TICKS; i++) r.tick(1, 0, 0);
        for (int i = 0; i < (int)ELS_DIAG_BUCKET_TICKS; i++) r.tick(1, -1, 0);
        for (int i = 0; i < (int)ELS_DIAG_STOP_QUIET_TICKS + 2; i++) r.tick(1, 0, 0);
        check(r.stop.diagTrace[0] == 0, "bucket 0 is empty -- nothing moved yet");
        check(r.stop.diagTrace[1] != 0, "bucket 1 carries the motion");
    }

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
