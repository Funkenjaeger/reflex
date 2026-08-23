/*
 * Carriage settle model (LathePhysics): does the emulated drivetrain have a
 * settle tail, and can a test TELL?
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Until 2026-08-22 onStepPulse() moved carriage_mm the instant the backlash nut
 * hit a wall. The last commanded step pulse and the last Z count arrived on the
 * same tick, so the simulated carriage had no settle behaviour of any kind.
 * fw/todo.md records the two things that blocked on that:
 *
 *  - ELS_SLIP_SETTLE_TICKS (the horizon over which post-pulse Z motion is still
 *    credited to the servo) could not be exercised here, because every horizon
 *    behaves identically against a drivetrain that never lags.
 *  - The take-up confirmation gate asks "did the carriage move far enough?" and
 *    never "has it STOPPED?" — and NO test could make a quiescence gate fail,
 *    because in the emulator the carriage was always already stopped. A check
 *    that cannot fail is a defect in its own right in this codebase, so the
 *    missing model was itself the bug.
 *
 * WHAT THIS FILE IS FOR, AND WHAT IT IS NOT FOR
 * ---------------------------------------------
 * It proves the model is OBSERVABLE — that a test can distinguish the settle
 * model from the instantaneous behaviour it replaced. A settle model no test
 * could detect would be strictly worse than none, because it would look like
 * the todo item was done.
 *
 * It does NOT claim a settle time. z_settle_tau_s is an UNMEASURED parameter
 * (physics.h says so at length): nobody has watched Z counts arrive after the
 * last pulse of a real take-up on elspi. Every number below is a property of
 * the model at a stated tau, never a statement about the lathe. Do not read a
 * tick count out of this file and write it into Core/Src/Ramps.c.
 *
 * MUTATION-PROVEN. The detectability claim is not an argument, it was run:
 *   M1  z_settle_tau_s default -> 0.0 in config.cpp  -> S1 and S4 go red
 *                                                       (S2/S3 set tau
 *                                                        explicitly and stay
 *                                                        green, as designed)
 * S2 additionally carries the tau = 0 control INSIDE the file, so the
 * instantaneous case is asserted rather than merely assumed absent.
 *
 * Links physics.cpp + config.cpp with local stubs for the shim externals, the
 * same shape as els_halfnut_test — hal_shim.c is NOT linked. Pulses are fed in
 * the production order used by isrThreadFunc: tick(dt) first, then
 * onStepPulse().
 */
#include "physics.h"

extern "C" {
#include "emulator_state.h"
#include "Ramps.h"
}

#include <cstdio>
#include <cstdarg>
#include <cmath>

/* --- Shim stubs (physics.cpp's only externals) --- */

EmulatorHardwareState emu_hw;

extern "C" void emu_log_event(const char *fmt, ...) { (void)fmt; }
extern "C" void emu_update_timer_counters(void) {}

static int failures = 0;
#define CHECK(cond, ...) do { \
    if (cond) { printf("  [PASS] " __VA_ARGS__); printf("\n"); } \
    else { failures++; printf("  [FAIL] " __VA_ARGS__); printf("\n"); } \
} while (0)

/* --- Fixture -------------------------------------------------------- */

/* Reference lathe.toml geometry, identical to els_halfnut_test so the two
 * files describe the same machine: 8 TPI leadscrew, 0.00396875 mm/step,
 * 400 counts/mm. One step is therefore 1.5875 Z counts — a step is coarser
 * than a count, which is what makes a lagging step observable at all. */
static const double STEP        = 0.00396875;   /* mm per leadscrew step */
static const double COUNTS_PER_MM = 400.0;
static const double LASH        = 0.635;        /* = 160 steps */
static const double DT          = 1e-4;         /* 10 kHz ISR tick, the emulator default */

/* The firmware constants this model exists to make reachable. Quoted here as
 * literals ON PURPOSE rather than included: Core/ is out of scope for the
 * emulator build, and a silent divergence should show up as a red test in this
 * file rather than as a test that quietly re-derives whatever Ramps.c now says.
 * If Ramps.c changes these, S4 is where you find out. */
static const int FW_ELS_SETTLE_TICKS      = 50;    /* gate dwell before its first verdict */
static const int FW_ELS_SLIP_SETTLE_TICKS = 1000;  /* motion-attribution horizon */

/* Zeroed firmware shared state for tick(). Nothing in the settle path reads it;
 * the parameter is kept for signature compatibility with the production caller. */
static rampsSharedData_t g_shared;

/* Start ENGAGED and on the "+wall", which is what the LathePhysics constructor
 * does: backlash_offset = z_backlash_mm. Driving POSITIVE from there pushes the
 * carriage on every pulse with no lash traversal first. The lash is not the
 * subject of this file and leaving it out keeps the pulse count and the
 * commanded displacement in exact correspondence. */
static EmuConfig makeConfig() {
    EmuConfig cfg;
    cfg.leadscrew_tpi = 8.0;
    cfg.leadscrew_mm_per_step = STEP;
    cfg.z_encoder_counts_per_mm = COUNTS_PER_MM;
    cfg.z_backlash_mm = LASH;
    cfg.z_min_mm = -500.0;
    cfg.z_max_mm = 500.0;
    cfg.z_initial_mm = 0.0;          /* no boot offset, so the exposed-count ramp
                                      * in tick() is caught up from tick 1 and
                                      * cannot be confused with the settle tail */
    cfg.z_half_nut_engaged = true;
    cfg.servo_dir = 1;
    cfg.spindle_initial_rpm = 0.0;
    return cfg;
}

/* The firmware-visible Z counter. tick() writes it; this is the SAME value the
 * ISR reads through __HAL_TIM_GET_COUNTER, so "a count arrived" here means
 * exactly what it means to Ramps.c. Reading carriage_mm instead would measure
 * the model rather than the observable. */
static int32_t exposedZ() {
    return (int32_t)(uint32_t)emu_hw.scale_counters[1];
}

/* Result of watching the carriage after the last commanded pulse. */
struct Tail {
    int    ticks_to_last_count;  /* ticks after the last pulse on which the LAST
                                  * exposed-count change landed. 0 = none ever. */
    int    ticks_with_arrivals;  /* how many of those ticks saw a count change */
    int32_t counts_after;        /* net exposed counts delivered after the pulse */
    int    ticks_to_model_quiet; /* ticks until isCarriageSettling() goes false,
                                  * i.e. including the sub-count crawl no
                                  * encoder can report */
};

/* Drive a paced burst of `npulses` steps, then stop pulsing and watch.
 *
 * `ticks_per_pulse` matters and is not cosmetic: step pulses are PACED (Ramps.c
 * emits at most one per servoCycles ticks), and the pending accumulator reaches
 * a different steady state at every pacing. Any tail length quoted from this
 * fixture is only meaningful alongside the pacing that produced it. */
static Tail driveAndWatch(LathePhysics &p, int npulses, int ticks_per_pulse,
                          int max_watch_ticks = 200000) {
    for (int i = 0; i < npulses; i++) {
        for (int t = 0; t < ticks_per_pulse; t++) p.tick(DT, &g_shared);
        p.onStepPulse(+1);
    }

    Tail r{0, 0, 0, 0};
    int32_t z_at_last_pulse = exposedZ();
    int32_t prev = z_at_last_pulse;

    for (int t = 1; t <= max_watch_ticks; t++) {
        p.tick(DT, &g_shared);
        int32_t now = exposedZ();
        if (now != prev) {
            r.ticks_to_last_count = t;
            r.ticks_with_arrivals++;
            prev = now;
        }
        if (r.ticks_to_model_quiet == 0 && !p.isCarriageSettling())
            r.ticks_to_model_quiet = t;
        /* Stop once the model is quiet AND the encoder has been quiet for long
         * enough that nothing more can arrive — the accumulator is empty, so
         * "long enough" is simply "the accumulator is empty". */
        if (r.ticks_to_model_quiet != 0) break;
    }
    r.counts_after = exposedZ() - z_at_last_pulse;
    return r;
}

/* ------------------------------------------------------------------ */
/* S1: the tail exists, is bounded, and conserves the commanded motion. */
/* ------------------------------------------------------------------ */

/* Runs on the CONFIG DEFAULT tau — deliberately no setSettleTauS() call — so
 * that zeroing the default in config.cpp turns this case red. That is the
 * mutation that proves the model's presence is detectable at all. */
static void testTailExistsAndIsBounded() {
    printf("S1: Z counts keep arriving after the last commanded pulse (config default tau)\n");

    EmuConfig cfg = makeConfig();
    LathePhysics p(cfg);
    const int PULSES = 120, PACE = 3;

    printf("      tau = %.6f s = %.1f ticks at dt = %.0e s; burst %d pulses, 1 per %d ticks\n",
           p.getSettleTauS(), p.getSettleTauS() / DT, DT, PULSES, PACE);

    Tail r = driveAndWatch(p, PULSES, PACE);

    printf("      MEASURED: last Z count arrived %d ticks after the last pulse; "
           "%d ticks carried a count; %d counts total after the pulse; "
           "model quiet at %d ticks\n",
           r.ticks_to_last_count, r.ticks_with_arrivals,
           (int)r.counts_after, r.ticks_to_model_quiet);

    /* THE detectability assertion. An instantaneous drivetrain delivers every
     * outstanding count on the FIRST tick after the pulse and none after, so it
     * scores exactly 1 here (S2 asserts that control directly). Anything above
     * 1 is a tail no instantaneous model can produce. */
    CHECK(r.ticks_to_last_count > 1,
          "post-pulse Z counts span %d ticks, more than the 1 tick an "
          "instantaneous drivetrain produces", r.ticks_to_last_count);
    CHECK(r.counts_after > 0,
          "%d Z counts arrived strictly after the last commanded pulse",
          (int)r.counts_after);

    /* Bounded, not asymptotic. "Has it stopped?" has to be answerable in finite
     * time or the gate test in els_takeup_settle_gate_test could never assert
     * anything either. */
    CHECK(r.ticks_to_model_quiet > 0 && r.ticks_to_model_quiet < 200000,
          "the tail terminates: model quiet %d ticks after the last pulse",
          r.ticks_to_model_quiet);

    /* CONSERVATION. The settle model adds latency, it does not change the
     * answer: once drained, the carriage sits exactly where the old
     * instantaneous model would have put it. This is what lets every existing
     * position assertion in the suite survive by adding a drain rather than by
     * being re-baselined against new numbers. */
    double expect_mm = PULSES * STEP;   /* no lash traversal: started on the +wall */
    CHECK(std::abs(p.getCarriageMM() - expect_mm) < 1e-9,
          "conserved: carriage at %.6f mm, commanded %.6f mm",
          p.getCarriageMM(), expect_mm);
    CHECK(std::abs(p.getPendingSettleCounts()) == 0.0,
          "accumulator fully drained (%.4g counts pending)",
          p.getPendingSettleCounts());
}

/* ------------------------------------------------------------------ */
/* S2: the time constant is a real knob, with tau = 0 as the control.   */
/* ------------------------------------------------------------------ */

/* A tail that exists but ignores its configuration would pass S1 and still be
 * useless: the gate test needs to CHOOSE a settle regime. This pins that the
 * knob does something monotonic, and carries the instantaneous case as an
 * in-file control so "1 tick" is asserted rather than assumed. */
static void testTauIsARealKnob() {
    printf("S2: tail length tracks the configured time constant (tau = 0 control)\n");

    const int PULSES = 120, PACE = 3;
    const double taus[] = {0.0, 0.001, 0.002, 0.004};
    int measured[4];

    for (int i = 0; i < 4; i++) {
        EmuConfig cfg = makeConfig();
        cfg.z_settle_tau_s = taus[i];
        LathePhysics p(cfg);
        Tail r = driveAndWatch(p, PULSES, PACE);
        measured[i] = r.ticks_to_last_count;
        printf("      tau = %.4f s (%5.1f ticks) -> last Z count %4d ticks after the last pulse\n",
               taus[i], taus[i] / DT, measured[i]);
    }

    /* THE CONTROL. tau <= 0 is the pre-2026-08-22 model: the outstanding
     * displacement lands whole on the first tick after the pulse. It is exactly
     * 1, not 0, because isrThreadFunc ticks physics BEFORE running the ISR and
     * feeds the pulse after — so under the old code too, a pulse first became
     * visible to the firmware on the following tick. The settle model changed
     * the shape of the delivery, not its earliest possible moment. */
    CHECK(measured[0] == 1,
          "tau = 0 delivers everything on the first tick after the pulse (%d)",
          measured[0]);

    CHECK(measured[1] > measured[0] && measured[2] > measured[1]
          && measured[3] > measured[2],
          "tail grows monotonically with tau: %d < %d < %d < %d",
          measured[0], measured[1], measured[2], measured[3]);

    /* Doubling tau should roughly double the tail (the tail is tau * ln(P0/one
     * count), so it grows a little faster than linearly as P0 also grows with
     * tau). A loose factor-of-1.5 floor catches "the knob is read but barely
     * used" without pinning the analytic form. */
    CHECK(measured[3] > (int)(1.5 * measured[1]),
          "4x tau gives a substantially longer tail (%d vs %d)",
          measured[3], measured[1]);
}

/* ------------------------------------------------------------------ */
/* S3: manual paths are deliberately NOT lagged.                        */
/* ------------------------------------------------------------------ */

/* The model is of the SERVO-DRIVEN drivetrain. Manual jog and move-to-position
 * write carriage_mm directly and must stay instantaneous — lagging them would
 * perturb tests that have nothing to do with settle, and it would blunt the
 * hand-nudge degree of freedom, which is supposed to be an impulse the servo
 * had no part in. Uses an absurdly long tau so any leak is obvious. */
static void testManualPathsAreNotLagged() {
    printf("S3: manual jog / move-to-position stay instantaneous (servo-only model)\n");

    EmuConfig cfg = makeConfig();
    cfg.z_settle_tau_s = 1.0;          /* 10000 ticks: a leak could not hide */
    cfg.z_half_nut_engaged = false;    /* jog requires the nut open */
    LathePhysics p(cfg);

    double z0 = p.getCarriageMM();
    for (int i = 0; i < 500; i++) {    /* hold the jog key down */
        p.jogCarriage(+1);
        p.tick(DT, &g_shared);
    }
    CHECK(p.getCarriageMM() > z0,
          "jog moved the carriage (%.4f -> %.4f mm)", z0, p.getCarriageMM());
    CHECK(p.getPendingSettleCounts() == 0.0,
          "jog put nothing in the settle accumulator (%.4g counts)",
          p.getPendingSettleCounts());

    p.moveCarriageTo(2.0);
    for (int i = 0; i < 200000 && p.isZMoveTargetActive(); i++) p.tick(DT, &g_shared);
    CHECK(std::abs(p.getCarriageMM() - 2.0) < 1e-9,
          "move-to-position landed exactly on target (%.6f mm)", p.getCarriageMM());
    CHECK(p.getPendingSettleCounts() == 0.0,
          "move-to-position put nothing in the settle accumulator (%.4g counts)",
          p.getPendingSettleCounts());
}

/* ------------------------------------------------------------------ */
/* S4: the DEFAULT declines to answer the open question.                */
/* ------------------------------------------------------------------ */

/* The whole point of the todo item is that nobody knows whether the real settle
 * outruns the firmware's ELS_SETTLE_TICKS (50) dwell. A default whose tail sat
 * past that dwell would have this emulator quietly asserting "yes" — a
 * measurement nobody took, arriving through a config default. So the default is
 * chosen to sit INSIDE the dwell, and this case pins that choice so moving it
 * costs a deliberate edit rather than a shrug.
 *
 * It is a statement about the DEFAULT, not about elspi. A test that wants the
 * long-settle regime calls setSettleTauS() and thereby says so out loud;
 * els_takeup_settle_gate_test is that test. */
static void testDefaultSitsInsideTheGateDwell() {
    printf("S4: the default tau does not pre-judge the unmeasured settle time\n");

    EmuConfig cfg = makeConfig();
    LathePhysics p(cfg);
    Tail r = driveAndWatch(p, 120, 3);

    printf("      default tail %d ticks vs ELS_SETTLE_TICKS %d, "
           "ELS_SLIP_SETTLE_TICKS %d\n",
           r.ticks_to_last_count, FW_ELS_SETTLE_TICKS, FW_ELS_SLIP_SETTLE_TICKS);

    CHECK(r.ticks_to_last_count > 1,
          "default tau produces a real tail (%d ticks) rather than none",
          r.ticks_to_last_count);
    CHECK(r.ticks_to_last_count < FW_ELS_SETTLE_TICKS,
          "default tail (%d) is inside the gate's dwell (%d): the emulator's "
          "default asserts nothing about the open question",
          r.ticks_to_last_count, FW_ELS_SETTLE_TICKS);
    CHECK(r.ticks_to_last_count < FW_ELS_SLIP_SETTLE_TICKS,
          "default tail (%d) is inside the attribution horizon (%d), so a "
          "healthy default take-up is fully attributed",
          r.ticks_to_last_count, FW_ELS_SLIP_SETTLE_TICKS);
}

int main() {
    printf("=== Carriage settle model observability (LathePhysics) ===\n\n");
    testTailExistsAndIsBounded();   printf("\n");
    testTauIsARealKnob();           printf("\n");
    testManualPathsAreNotLagged();  printf("\n");
    testDefaultSitsInsideTheGateDwell();
    printf("\n%s (%d failures)\n", failures ? "FAILED" : "PASSED", failures);
    return failures ? 1 : 0;
}
