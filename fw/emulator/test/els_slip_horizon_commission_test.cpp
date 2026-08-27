/*
 * The COMMISSIONING of ELS_SLIP_SETTLE_TICKS, made executable.
 *
 * els_slip_test.cpp proves the horizon MECHANISM: that a count arriving inside
 * the horizon is credited to the servo and one arriving outside it is not, and
 * that elsSlipSettleTicks() floors the horizon at the live pulse pacing. This
 * file proves something different and narrower — that the VALUE now compiled in
 * is the one elspi's own drivetrain justifies, and that lowering it from 1000
 * to 700 on 2026-08-27 discarded no motion the old value was crediting.
 *
 * WHY A SEPARATE FILE. A constant chosen from data is only as good as the data
 * staying attached to it. Until now the number lived in a comment; anyone could
 * move it and nothing would go red. The seven observations below are the whole
 * argument for 700, so they are encoded here as assertions: change the constant
 * without revisiting them and this file tells you.
 *
 * THE DATA (elspi, 2026-08-27, probe takeup-settle-v3 / schema 6). Eighteen
 * captures, EVERY ONE ending END_WINDOW at the full 2000-tick window, so none
 * was truncated and the set is valid on its own terms. Under v3, END_WINDOW is
 * the success case — the gate's dwell is held open for the whole window, so
 * reaching the last bucket with the servo quiet means the trace covered the
 * entire settle horizon. (Under v2 the identical code meant "quiet longer than
 * I could watch", a floor rather than a result. That inversion is why the
 * schema id had to change; do not read v2 captures with v3 rules.)
 *
 * Eleven of the eighteen were COMPLETELY STILL — zero in every bucket. The
 * other seven each delivered EXACTLY ONE count: net_counts -1, a single nonzero
 * bucket, at the tick offsets in OBSERVED_SETTLE_TICKS below. t=0 for those
 * offsets is the tick after the LAST step pulse of the take-up, which v3
 * guarantees by re-arming the capture on every pulse that arrives while
 * takeupPending is still set — so the decel ramp's residual pulses cannot start
 * the clock early.
 *
 * So the physical finding is that the carriage stops essentially dead. What
 * looked like a settle tail is a single 5 um count landing late, one-directional
 * (never oscillation), and in most take-ups not at all.
 *
 * ONE CAVEAT THAT DOES NOT TRANSFER, repeated here because this file is where
 * someone will come looking for the numbers: the captures come from a
 * diagnostic build whose hold computes the gate's verdict LATER than release,
 * which makes that build MORE PERMISSIVE on a marginal take-up. That bears on
 * whether the GATE would confirm — never read "the diagnostic build confirmed"
 * as "release would have". It does not touch the settle measurement itself,
 * which is exactly what the hold exists to make possible.
 *
 * THE MIRROR, AND WHAT IT DOES NOT PROVE. ELS_SLIP_SETTLE_TICKS is defined in
 * Ramps.c, a .c file the emulator build cannot include, so the value is
 * restated here as a literal — the same convention, and the same limitation, as
 * els_carriage_settle_test.cpp. This file therefore proves the DECISION is
 * coherent (the value sits where the data says it should); it cannot by itself
 * prove Ramps.c still holds that value. Keep the two in step by hand, and note
 * that C1 is the line to update when they ever diverge deliberately.
 *
 * Mutation-tested: each case names the exact edit that must make it fail.
 */
#include <cstdio>
#include <cstdint>

#include "els_slip.h"

/* MIRRORS of the firmware constants. See "THE MIRROR" above. */
static const int32_t FW_ELS_SLIP_SETTLE_TICKS = 700;   /* Ramps.c, commissioned 2026-08-27 */
static const int32_t FW_ELS_SETTLE_TICKS      = 50;    /* gate dwell before its first verdict */
static const int32_t PREVIOUS_HORIZON_TICKS   = 1000;  /* what 700 replaced */

/* The seven captures that showed motion, in ticks from the last take-up pulse
 * to the single count each delivered. The other eleven were identically zero
 * and are represented by OBSERVED_STILL_CAPTURES rather than by entries here. */
static const int32_t OBSERVED_SETTLE_TICKS[] = { 79, 545, 571, 656, 1165, 1399, 1786 };
static const int     OBSERVED_MOVING_CAPTURES = 7;
static const int     OBSERVED_STILL_CAPTURES  = 11;
static const int     OBSERVED_TOTAL_CAPTURES  = 18;

/* Each moving capture's net displacement, in Z counts. One count, one
 * direction, every time — this is what makes the late observations harmless. */
static const int32_t OBSERVED_NET_COUNTS_PER_MOVING_CAPTURE = -1;

/* The take-up confirmation threshold is derived per machine and lands in the
 * TENS of counts on elspi (els_backlash_cal.h). The exact value is not needed
 * here; what matters is the order of magnitude against a one-count tail. */
static const int32_t CONFIRM_THRESHOLD_ORDER_COUNTS = 10;

static int failures = 0;

static void check(bool cond, const char *what) {
    printf("   %-72s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

static void checkEq(int64_t got, int64_t want, const char *what) {
    bool ok = (got == want);
    printf("   %-72s %s (got %lld, want %lld)\n", what, ok ? "ok" : "FAIL",
           (long long)got, (long long)want);
    if (!ok) failures++;
}

/* Drive one take-up pulse, wait, then deliver a single Z count `age` ticks
 * after that pulse. Returns whether the count was credited to the servo.
 *
 * Tick accounting, because it is off-by-one-prone: elsSlipTick() increments
 * ticksSinceLastPulse BEFORE testing it against the horizon, so the count
 * delivered on the age-th tick after the pulse is tested as `age <= horizon`. */
static bool attributedAt(int32_t age, int32_t horizon) {
    elsSlipAccum_t a;
    elsSlipReset(&a);
    elsSlipTick(&a, 0, +1, horizon);                 /* the last take-up pulse: t=0 */
    for (int32_t i = 1; i < age; i++) {
        elsSlipTick(&a, 0, 0, horizon);              /* quiet */
    }
    elsSlipTick(&a, OBSERVED_NET_COUNTS_PER_MOVING_CAPTURE, 0, horizon);
    return a.attributedZCounts != 0;
}

/* ---- C1: the compiled value is the commissioned one --------------------- */
/* MUTATION: change FW_ELS_SLIP_SETTLE_TICKS to 1000 (or anything else) and this
 * fails immediately. It exists so that moving the constant is a deliberate act
 * with a place to record the new justification, not an edit that slips through. */
static void testTheValueIsPinnedToTheCommissioning() {
    printf("C1: the horizon is the value elspi's data justifies\n");
    checkEq(FW_ELS_SLIP_SETTLE_TICKS, 700,
            "ELS_SLIP_SETTLE_TICKS is 700, commissioned 2026-08-27");
    check(FW_ELS_SLIP_SETTLE_TICKS < PREVIOUS_HORIZON_TICKS,
          "and it is a REDUCTION -- this constant is only ever tuned down");
}

/* ---- C2: every observed settle count that was credited still is --------- */
/* This is the "too short refuses a healthy machine" direction, checked against
 * real observations rather than against a guess.
 * MUTATION: set FW_ELS_SLIP_SETTLE_TICKS to 600 and the 656-tick observation
 * stops being attributed -- one line red, naming the observation it lost. */
static void testEveryEarlyObservationIsStillAttributed() {
    printf("C2: no observation that was inside the old horizon fell outside the new\n");
    for (int i = 0; i < OBSERVED_MOVING_CAPTURES; i++) {
        int32_t t = OBSERVED_SETTLE_TICKS[i];
        bool wasAttributed = (t <= PREVIOUS_HORIZON_TICKS);
        bool isAttributed  = attributedAt(t, FW_ELS_SLIP_SETTLE_TICKS);
        char what[128];
        snprintf(what, sizeof what,
                 "observation at %d ticks: attributed before=%d, now=%d",
                 (int)t, (int)wasAttributed, (int)isAttributed);
        check(!wasAttributed || isAttributed, what);
    }
}

/* ---- C3: the reduction crossed an EMPTY band --------------------------- */
/* The justification for 700 specifically. The observations clump into <=656 and
 * >=1165; 700 sits in the gap, so the reduction cannot have discarded anything
 * that was ever seen.
 * MUTATION: lower the constant to 600 and the 656 observation lands in the
 * abandoned band, turning this red -- which is the point: 600 would need its
 * own argument, and this test refuses to let it borrow this one. */
static void testTheReductionAbandonedNothingObserved() {
    printf("C3: nothing was ever observed in the band the reduction gave up\n");
    int inAbandonedBand = 0;
    for (int i = 0; i < OBSERVED_MOVING_CAPTURES; i++) {
        int32_t t = OBSERVED_SETTLE_TICKS[i];
        if (t > FW_ELS_SLIP_SETTLE_TICKS && t <= PREVIOUS_HORIZON_TICKS) {
            inAbandonedBand++;
            printf("      observation at %d ticks is in (%d, %d]\n",
                   (int)t, (int)FW_ELS_SLIP_SETTLE_TICKS, (int)PREVIOUS_HORIZON_TICKS);
        }
    }
    checkEq(inAbandonedBand, 0,
            "observations stranded by the 1000 -> 700 reduction");
}

/* ---- C4: the late observations were never credited anyway -------------- */
/* Three observations sit beyond BOTH horizons. They are not evidence this
 * change lost, and -- more importantly -- they cannot matter: each moving
 * capture delivered a single count against a threshold in the tens.
 * MUTATION: note carefully that these checks are against PREVIOUS_HORIZON_TICKS,
 * so moving the CURRENT constant does not touch them -- an earlier draft of this
 * comment claimed a raise would fail here, and the M3 run disproved it. What
 * actually catches "raised" is C1 (the pin) and C6 (the abandoned band inverts).
 * What breaks THIS case is editing the observation data itself, which is the
 * provenance C8 guards. */
static void testLateObservationsAreOutsideBothAndCannotMatter() {
    printf("C4: the late tail is outside both horizons, and is one count\n");
    for (int i = 0; i < OBSERVED_MOVING_CAPTURES; i++) {
        int32_t t = OBSERVED_SETTLE_TICKS[i];
        if (t <= PREVIOUS_HORIZON_TICKS) continue;
        char what[128];
        snprintf(what, sizeof what,
                 "late observation at %d ticks was outside the OLD horizon too",
                 (int)t);
        check(!attributedAt(t, PREVIOUS_HORIZON_TICKS), what);
    }
    int32_t tail = OBSERVED_NET_COUNTS_PER_MOVING_CAPTURE;
    if (tail < 0) tail = -tail;
    check(tail * OBSERVED_MOVING_CAPTURES < CONFIRM_THRESHOLD_ORDER_COUNTS,
          "even ALL seven tails summed stay under the confirm threshold");
}

/* ---- C5: the boundary is exact ----------------------------------------- */
/* MUTATION: change `<=` to `<` in elsSlipTick()'s horizon test and the
 * at-the-horizon case flips. This is the case that pins WHICH tick the
 * boundary falls on, which every number above is quoted against. */
static void testTheBoundaryIsExactlyTheHorizon() {
    printf("C5: a count AT the horizon is attributed, one tick past it is not\n");
    check(attributedAt(FW_ELS_SLIP_SETTLE_TICKS, FW_ELS_SLIP_SETTLE_TICKS),
          "a count arriving exactly ON the horizon tick is credited");
    check(!attributedAt(FW_ELS_SLIP_SETTLE_TICKS + 1, FW_ELS_SLIP_SETTLE_TICKS),
          "a count one tick past the horizon is NOT credited");
}

/* ---- C6: the safety gain is real --------------------------------------- */
/* The whole reason to lower it: a disturbance landing in the abandoned band
 * used to be credited to the servo and now is not. Sampled in the middle of
 * that band so the case does not sit on either boundary.
 * MUTATION: restore the constant to 1000 and this fails -- the nudge is
 * credited again, which is precisely the exposure the reduction removes. */
static void testANudgeInTheAbandonedBandIsNoLongerCredited() {
    printf("C6: a disturbance in the abandoned band is no longer servo-attributed\n");
    int32_t mid = (FW_ELS_SLIP_SETTLE_TICKS + PREVIOUS_HORIZON_TICKS) / 2;
    check(attributedAt(mid, PREVIOUS_HORIZON_TICKS),
          "under the OLD horizon that disturbance WAS credited to the servo");
    check(!attributedAt(mid, FW_ELS_SLIP_SETTLE_TICKS),
          "under the NEW horizon it is not");
}

/* ---- C7: the documented constraints still hold ------------------------- */
/* Ramps.c lists two hard constraints on any replacement value. Both are cheap
 * to check and neither is implied by the data above.
 * MUTATION: set the constant below 50 and the first fails; the second is
 * guarded by elsSlipSettleTicks() at runtime rather than by the constant, and
 * checking it here is what proves that floor is load-bearing. */
static void testRampsConstraintsSurviveTheNewValue() {
    printf("C7: the constraints Ramps.c places on any value still hold at 700\n");
    check(FW_ELS_SLIP_SETTLE_TICKS > FW_ELS_SETTLE_TICKS,
          "horizon exceeds the gate dwell, so inertial settle is still accepted");
    const int32_t slowPacing = FW_ELS_SLIP_SETTLE_TICKS * 3;
    checkEq(elsSlipSettleTicks(FW_ELS_SLIP_SETTLE_TICKS, slowPacing),
            slowPacing + 1,
            "a slow take-up still floors the horizon at the pulse pacing");
    check(elsSlipSettleTicks(FW_ELS_SLIP_SETTLE_TICKS, 10) == FW_ELS_SLIP_SETTLE_TICKS,
          "at ordinary pacing the commissioned value is what is in force");
}

/* ---- C8: the capture set the numbers came from was complete ------------ */
/* Guards the provenance rather than the value: 18 captures, 7 moving and 11
 * still. If someone extends OBSERVED_SETTLE_TICKS without updating the counts,
 * the argument above has silently changed shape and this says so. */
static void testTheObservationSetIsInternallyConsistent() {
    printf("C8: the recorded observation set is complete and self-consistent\n");
    checkEq(OBSERVED_MOVING_CAPTURES + OBSERVED_STILL_CAPTURES,
            OBSERVED_TOTAL_CAPTURES,
            "moving + still captures account for every capture taken");
    checkEq((int)(sizeof OBSERVED_SETTLE_TICKS / sizeof OBSERVED_SETTLE_TICKS[0]),
            OBSERVED_MOVING_CAPTURES,
            "one recorded settle time per moving capture");
}

int main() {
    printf("=== ELS_SLIP_SETTLE_TICKS commissioning (elspi, 2026-08-27) ===\n\n");
    testTheValueIsPinnedToTheCommissioning();          printf("\n");
    testEveryEarlyObservationIsStillAttributed();      printf("\n");
    testTheReductionAbandonedNothingObserved();        printf("\n");
    testLateObservationsAreOutsideBothAndCannotMatter(); printf("\n");
    testTheBoundaryIsExactlyTheHorizon();              printf("\n");
    testANudgeInTheAbandonedBandIsNoLongerCredited();  printf("\n");
    testRampsConstraintsSurviveTheNewValue();          printf("\n");
    testTheObservationSetIsInternallyConsistent();     printf("\n");

    printf(failures ? "=== %d FAILURE(S) ===\n" : "=== all checks passed ===\n",
           failures);
    return failures ? 1 : 0;
}
