/*
 * The quiescence gate: does the take-up gate wait for the carriage to STOP?
 *
 * SAME RIG as els_takeup_settle_gate_test, compiled with
 * ELS_REQUIRE_QUIESCENCE=1. That file characterises the defect; this one is the
 * fix, and the pair only means anything because they share a fixture -- two
 * separate rigs could differ for reasons that have nothing to do with the flag.
 *
 * THE DEFECT, restated from that file's measurements: with a settle tail longer
 * than the gate's 50-tick dwell, the gate confirmed and released sync with 1.36
 * Z counts of take-up motion still undelivered, taking 360 further ticks to
 * arrive. And the control was the sharp part -- the firmware behaved
 * BIT-IDENTICALLY with a short tail and a long one, same release tick, same
 * verdict. The gate had no channel through which settle could reach it.
 *
 * WHAT THE FIX ADDS is that channel: a count of consecutive ticks with Z
 * unchanged, ANDed with the existing motion evidence. Never replacing it -- a
 * carriage that is merely stationary has proved nothing about coupling, which
 * is the 2026-08-08 open-half-nut failure all over again.
 *
 * WHY THE FLAG DEFAULTS OFF, and why that is not fence-sitting: enabling this
 * changes the release condition of every pass, and elspi cannot presently be
 * measured for it. Its 18 takeup-settle-v3 captures show the carriage never
 * moving more than ONE encoder count after a take-up -- the resolution floor of
 * a 200 counts/mm scale -- so "still moving" and "stopped" are not
 * distinguishable there. Those captures DID establish the test is implementable
 * at all: 893 of 900 trace buckets exactly 0, 7 exactly -1, not one +1. Dither
 * would have shown both signs and made a stillness test unusable.
 *
 * ELS_QUIESCENT_TICKS is therefore sized against THIS emulator, not that field
 * data, and is provisional until the inter-pulse-gap measurement lands.
 *
 * MUTATION-TESTED 2026-08-23, each applied to Ramps.c alone and reverted:
 *
 *   Q1 gate always true (the pre-fix behaviour)   -> 3 failures (1, 2, 3)
 *   Q2 quiescence window of 1 tick                -> 3 failures (1, 2, 3)
 *   Q3 Z motion does not reset the counter        -> 3 failures (1, 2, 3)
 *   Q4 report UNCONFIRMED while still moving      -> 2 failures (4)
 *   Q5 pre-seed the counter as already satisfied  -> 0 failures, SURVIVED
 *
 * Q5 is reported rather than quietly dropped. It survives because it is not a
 * behavioural change at all: any take-up that moves the carriage resets the
 * counter on its first tick of motion, and one that does not is refused by the
 * motion evidence regardless. The comment at that line in Ramps.c originally
 * claimed the seed point mattered; the mutation proved it does not, and the
 * comment now says so instead.
 */

#include "physics.h"

extern "C" {
#include "Ramps.h"
#include "Scales.h"
#include "emulator_state.h"
}

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cmath>

/* ------------------------------------------------------------------ */
/* Stubs: Ramps.c's complete external set, plus physics.cpp's.          */
/* ------------------------------------------------------------------ */
extern "C" {

GPIO_TypeDef    emu_gpioa, emu_gpiob, emu_gpioc;
RCC_TypeDef     emu_rcc;
DWT_Type        emu_dwt;
CoreDebug_Type  emu_coreDebug;
EmulatorHardwareState emu_hw;

void emu_log_trace(const char *fmt, ...) { (void)fmt; }
void emu_log_event(const char *fmt, ...) { (void)fmt; }

/* physics.cpp calls this at the end of every tick to push its encoder values
 * into the TIM counter registers. Deliberately a NO-OP here: this rig owns
 * tim[].CNT so it can keep the synthetic spindle and the physics-driven Z
 * separate. The rig copies the Z value across itself, in stepDriven(). */
void emu_update_timer_counters(void) {}

void HAL_GPIO_Init(GPIO_TypeDef *p, GPIO_InitTypeDef *i) { (void)p; (void)i; }
void HAL_GPIO_WritePin(GPIO_TypeDef *p, uint16_t pin, GPIO_PinState s) {
    (void)p; (void)pin; (void)s;
}
void HAL_GPIO_TogglePin(GPIO_TypeDef *p, uint16_t pin) { (void)p; (void)pin; }

HAL_StatusTypeDef HAL_TIM_Base_Start_IT(TIM_HandleTypeDef *h) { (void)h; return HAL_OK; }
HAL_StatusTypeDef HAL_TIM_Encoder_Start(TIM_HandleTypeDef *h, uint32_t ch) {
    (void)h; (void)ch; return HAL_OK;
}
HAL_StatusTypeDef initScaleTimer(TIM_HandleTypeDef *h) { (void)h; return HAL_OK; }

void ModbusInit(modbusHandler_t *m)  { (void)m; }
void ModbusStart(modbusHandler_t *m) { (void)m; }

osStatus_t osDelay(uint32_t ticks) { (void)ticks; return osOK; }
osThreadId_t osThreadNew(osThreadFunc_t f, void *arg, const osThreadAttr_t *a) {
    (void)f; (void)arg; (void)a; return nullptr;
}

extern uint16_t servoCycles;
extern uint16_t servoCyclesCounter;

} /* extern "C" */

static int failures = 0;

static void check(bool cond, const char *what) {
    printf("   %-72s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

/* ------------------------------------------------------------------ */
/* Geometry                                                            */
/* ------------------------------------------------------------------ */

/* Fixture constants copied from els_takeup_confirm_test so the two files
 * describe the same job. */
static const int32_t SPINDLE_PER_PASS = 40;
static const int32_t Z_CLEAR          = 1000;
static const int32_t Z_STOP_POS       = 0;
static const int16_t Z_STOP_DIR       = -1;
static const float   SENTINEL         = 1.0e30f;

/* The physics geometry is chosen so its steps-to-counts ratio EXACTLY matches
 * the thread geometry the firmware is told about: 0.00396875 mm/step at
 * 400 counts/mm is 1.5875 counts/step, and 846.667 / 533.333 is the same
 * number. If those disagreed, elsTakeupConfirmThreshold() would be sizing its
 * threshold in a different currency than the drivetrain delivers, and every
 * result in this file would be about the mismatch instead of about the gate. */
static const double  STEP_MM        = 0.00396875;
static const double  COUNTS_PER_MM  = 400.0;
static const int32_t LASH_STEPS     = 60;
static const uint32_t BACKLASH_REG  = 90;   /* what the firmware is told to take up */
static const int32_t MOTION_THRESH  = 2;    /* counts; the floor, no calibration on file */
static const double  DT             = 1e-4; /* one ISR tick, 10 kHz — the emulator default */

/* Firmware constants, quoted as literals because Core/ is out of scope for this
 * build. ELS_SETTLE_TICKS is the dwell whose blind spot this file characterizes. */
static const int FW_ELS_SETTLE_TICKS = 50;
/* Mirrors ELS_QUIESCENT_TICKS in Ramps.c. A LITERAL on purpose, the same way
 * FW_ELS_SETTLE_TICKS above is: the constant lives in a .c file and cannot be
 * included, so changing it has to cost a deliberate edit on both sides rather
 * than silently re-tuning what this file asserts. */
static const int FW_ELS_QUIESCENT_TICKS = 200;

/* The long tail G1 runs, in ISR TICKS — the unit the firmware reasons in.
 * Chosen only to be comfortably longer than the dwell, so the branch under
 * characterization is unambiguously the one being exercised. */
static const double LONG_TAU_TICKS = 100.0;

static EmuConfig makePhysicsConfig(double tau_ticks) {
    EmuConfig cfg;
    cfg.leadscrew_tpi = 8.0;
    cfg.leadscrew_mm_per_step = STEP_MM;
    cfg.z_encoder_counts_per_mm = COUNTS_PER_MM;
    cfg.z_backlash_mm = LASH_STEPS * STEP_MM;
    cfg.z_min_mm = -500.0;
    cfg.z_max_mm = 500.0;
    cfg.z_initial_mm = 0.0;
    /* Start ENGAGED. The constructor seats the nut on the "+wall", which is the
     * wall a NEGATIVE take-up has to traverse the whole lash to leave — the
     * worst case, and the same seating els_takeup_confirm_test's rig does by
     * hand for the same reason. The take-up's direction is asserted at
     * handover rather than assumed; see beginPhysicsDriven(). */
    cfg.z_half_nut_engaged = true;
    cfg.servo_dir = 1;
    cfg.spindle_initial_rpm = 0.0;
    if (tau_ticks >= 0.0) cfg.z_settle_tau_s = tau_ticks * DT;
    return cfg;
}

/* ------------------------------------------------------------------ */
/* Rig: els_takeup_confirm_test's fixture with LathePhysics behind Z    */
/* ------------------------------------------------------------------ */

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int32_t            spindleCnt = 0;
    int32_t            zCnt = Z_CLEAR;

    LathePhysics      *phys = nullptr;
    bool               physDriven = false;
    int32_t            zBase = 0;      /* firmware Z at handover */
    int64_t            physZero = 0;   /* physics Z counts at handover */
    int32_t            prevSteps = 0;

    int                tick = 0;       /* ticks since handover */
    int                lastPulseTick = -1;
    int64_t            pulsesFed = 0;

    void init(LathePhysics *p) {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        phys = p;
        spindleCnt = 0;
        zCnt       = Z_CLEAR;
        physDriven = false;
        tick = 0;
        lastPulseTick = -1;
        pulsesFed = 0;
        servoCycles        = 1;
        servoCyclesCounter = 0;

        for (int i = 0; i < SCALES_COUNT; i++) {
            htim[i].Instance = &tim[i];
            ramps_timer_handles[i] = &htim[i];
            data.shared.scales[i].timerHandleSlot = (uint32_t)i;
            data.shared.scales[i].scaleDir     = 1;
            data.shared.scales[i].syncRatioNum = 1;
            data.shared.scales[i].syncRatioDen = 100;
            data.shared.scales[i].syncEnable   = 0;
        }
        data.shared.scales[0].syncRatioNum = -2;
        data.shared.scales[0].syncRatioDen = 15;
        data.shared.scales[0].syncEnable   = 1;

        data.shared.servo.maxSpeed     = 100000.0f;
        data.shared.servo.acceleration = 50000.0f;
        data.shared.servo.servoDir     = 1;
        data.shared.fastData.servoMode = 1;

        data.shared.elsStop.scaleIndex           = 1;
        data.shared.elsStop.stopPosition         = Z_STOP_POS;
        data.shared.elsStop.stopDirection        = Z_STOP_DIR;
        data.shared.elsStop.threadPitchSteps     = 533.333f;
        data.shared.elsStop.zCountsPerPitch      = 846.667f;
        data.shared.elsStop.backlashSteps        = BACKLASH_REG;
        data.shared.elsStop.hysteresis           = 500;
        data.shared.elsStop.calMotionThreshCounts = MOTION_THRESH;
        data.shared.elsStop.calCeilingSteps      = 400;
        data.shared.elsStop.enable               = 0;

        tim[0].CNT = (uint32_t)spindleCnt;
        tim[1].CNT = (uint32_t)zCnt;
        data.scalesDeltaPos[0].position = spindleCnt;
        data.scalesDeltaPos[1].position = zCnt;
        data.shared.scales[0].position  = spindleCnt;
        data.shared.scales[1].position  = zCnt;
        data.scalesSyncDeltaPos[0].oldPosition = spindleCnt;
        data.scalesSyncDeltaPos[1].oldPosition = zCnt;
    }

    /* Synthetic tick: Z is whatever the caller says. Used for arm/trigger/
     * retract, which is plumbing to get the firmware into the take-up state. */
    void step(int32_t zTarget) {
        spindleCnt += SPINDLE_PER_PASS;
        zCnt        = zTarget;
        tim[0].CNT  = (uint32_t)spindleCnt;
        tim[1].CNT  = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }

    /* Hand Z over to LathePhysics: from here Z is whatever the servo's own step
     * pulses produce through the real drivetrain model, settle tail included. */
    void beginPhysicsDriven() {
        physDriven = true;
        zBase      = zCnt;
        physZero   = phys->getCarriageEncoderCounts();
        prevSteps  = (int32_t)data.shared.servo.currentSteps;
        tick = 0;
        lastPulseTick = -1;
        pulsesFed = 0;
    }

    /* Physics-driven tick, in the SAME order as the production ISR thread
     * (emulator/src/main.cpp isrThreadFunc): advance physics, publish its
     * encoder value, run the firmware ISR, then feed the pulses that ISR just
     * emitted back into physics. Getting this order wrong would put the pulse
     * and its own displacement on the same tick and quietly re-create the
     * instantaneous drivetrain this whole file exists to get away from.
     *
     * Z is taken from getCarriageEncoderCounts() rather than the exposed
     * counter physics.cpp ramps for the firmware: that ramp exists to stop a
     * pre-seeded z_initial_mm overflowing Ramps.c's int16 delta cast at boot
     * (els_boot_delta_test), and dragging a boot artifact into a settle
     * measurement would be measuring the wrong lag. */
    void stepDriven() {
        spindleCnt += SPINDLE_PER_PASS;
        phys->tick(DT, &data.shared);

        zCnt = zBase + (int32_t)(phys->getCarriageEncoderCounts() - physZero);
        tim[0].CNT = (uint32_t)spindleCnt;
        tim[1].CNT = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);

        int32_t now = (int32_t)data.shared.servo.currentSteps;
        int32_t d = now - prevSteps;
        prevSteps = now;
        if (d != 0) { lastPulseTick = tick; pulsesFed += (d > 0) ? d : -d; }
        while (d > 0) { phys->onStepPulse(+1); d--; }
        while (d < 0) { phys->onStepPulse(-1); d++; }

        tick++;
    }

    /* Arm a FRESH job (active first, then enable — els_arm_past_stop_test pins
     * why that order matters) and press Cut. No stop has fired, so
     * referenceLatched is 0: this is the datum pass. Chosen over the resume
     * path deliberately — on the datum pass the gate confirms WITHOUT running
     * applyPhaseCorrection, so no new commanded motion lands on top of the
     * settle tail being measured. */
    void armFirstPass() {
        step(Z_CLEAR);
        data.shared.elsStop.active = 1;
        step(Z_CLEAR);                    /* Modbus gap between the writes */
        data.shared.elsStop.enable = 1;
        for (int i = 0; i < 3; i++) step(Z_CLEAR);
        for (int i = 0; i < 20000
             && data.shared.servo.currentSteps != data.shared.servo.desiredSteps; i++) {
            step(Z_CLEAR);
        }
        data.shared.elsStop.lastIdealAdvance = SENTINEL;
        data.shared.elsStop.active = 0;   /* SW Cut */
    }
};

/* What the gate looked like at the instant it released the cut. */
struct Release {
    bool    confirmed;
    int     releaseTick;        /* ticks since handover */
    int     lastPulseTick;      /* ticks since handover */
    double  pendingCounts;      /* take-up motion still undelivered, in Z counts */
    int     residualTicks;      /* further ticks over which those counts arrive */
    int32_t residualCounts;     /* Z counts that arrive after the release */
};

/* Run one take-up to the moment the gate releases, then project the tail.
 *
 * The projection is run on the physics object ALONE, with no further pulses,
 * and that is a deliberate and stated limitation: the gate's release UNGATES
 * SYNC, so from the very next tick the firmware starts commanding the cut. The
 * honest question is therefore "how much take-up motion was still owed at the
 * moment the cut was released, and how long would it have taken to arrive",
 * which is exactly what a pulse-free projection answers. Continuing to tick the
 * rig instead would blend the tail with the first pulses of the cut. */
static Release runTakeupToRelease(LathePhysics &p, Rig &rig) {
    rig.init(&p);
    rig.armFirstPass();
    rig.step(Z_CLEAR);                        /* the Cut edge initiates the take-up */
    check(rig.data.shared.elsStop.takeupPending == 1, "take-up initiated");
    check(rig.data.elsStopTakeupSign < 0,
          "take-up drives negative, so the nut starts on the far lash wall");
    rig.beginPhysicsDriven();

    Release r{};
    for (int i = 0; i < 60000 && rig.data.shared.elsStop.takeupPending; i++)
        rig.stepDriven();

    r.confirmed     = (rig.data.shared.elsStop.takeupResult == ELS_CAL_OK);
    r.releaseTick   = rig.tick;
    r.lastPulseTick = rig.lastPulseTick;
    r.pendingCounts = p.getPendingSettleCounts();

    /* Project the remaining tail: how many more ticks would Z counts keep
     * arriving, if nothing else were commanded. */
    int64_t z0 = p.getCarriageEncoderCounts(), prev = z0;
    for (int t = 1; t <= 200000; t++) {
        p.tick(DT, nullptr);
        int64_t now = p.getCarriageEncoderCounts();
        if (now != prev) { r.residualTicks = t; prev = now; }
        if (!p.isCarriageSettling()) break;
    }
    r.residualCounts = (int32_t)(p.getCarriageEncoderCounts() - z0);
    return r;
}

/* The take-up drives NEGATIVE (the cutting direction on this geometry), so
 * pendingCounts and residualCounts are negative. Every threshold below is on
 * the MAGNITUDE; the signs are printed because a sign flip would mean the
 * carriage was settling the wrong way, which would be a different and much
 * more interesting bug than the one this file is about. */
static void report(const char *label, const Release &r) {
    printf("   %s: released on tick %d, last commanded pulse on tick %d "
           "(%d ticks before release)\n",
           label, r.releaseTick, r.lastPulseTick, r.releaseTick - r.lastPulseTick);
    printf("   %s: %+.2f Z counts still undelivered at release; they take %d more "
           "ticks to arrive (%+d counts)\n",
           label, r.pendingCounts, r.residualTicks, (int)r.residualCounts);
}

int main() {
    printf("=== Take-up gate WITH the quiescence input (ELS_REQUIRE_QUIESCENCE=1) ===\n\n");

#if !ELS_REQUIRE_QUIESCENCE
    printf("[FAIL] this target must be compiled with ELS_REQUIRE_QUIESCENCE=1\n");
    return 1;
#endif

    /* ---------------- 1. THE FIX: a long tail is waited out -------------- */
    /* The case the characterisation test showed releasing early. The gate must
     * now hold until the carriage is actually still, so essentially nothing is
     * left undelivered at release.
     * MUTATION: make carriageStopped unconditionally true (i.e. revert the
     * gate) -> the pending-counts assertion fails, reproducing the defect. */
    printf("-- 1. settle tail longer than the dwell: the gate waits --\n");
    Release longTail;
    {
        EmuConfig cfg = makePhysicsConfig(LONG_TAU_TICKS);
        LathePhysics p(cfg);
        Rig rig;
        printf("   tau = %.1f ticks, gate dwell = %d ticks, quiescence = %d ticks\n",
               p.getSettleTauS() / DT, FW_ELS_SETTLE_TICKS, FW_ELS_QUIESCENT_TICKS);
        longTail = runTakeupToRelease(p, rig);
        report("Q1", longTail);

        check(longTail.confirmed,
              "the take-up is still CONFIRMED -- waiting for stillness must not "
              "turn a good take-up into a refusal");
        check(longTail.pendingCounts > -1.0 && longTail.pendingCounts < 1.0,
              "less than one Z count still owed at release: the carriage HAD "
              "stopped, to the resolution any encoder could see");
        check(longTail.releaseTick - longTail.lastPulseTick >= FW_ELS_QUIESCENT_TICKS,
              "the gate waited at least the quiescence window after the last pulse");
    }

    /* ---------------- 2. IT IS THE FLAG DOING THE WORK ------------------- */
    /* The same geometry through the characterised build released with motion
     * outstanding. Rather than trust that number from another file, assert the
     * relationship this build must satisfy: the wait is materially longer than
     * the bare dwell.
     * MUTATION: set ELS_QUIESCENT_TICKS to 1 -> this fails, and case 1's
     * pending-counts assertion fails with it. */
    printf("\n-- 2. the wait is real, not the old dwell by another name --\n");
    {
        int waited = longTail.releaseTick - longTail.lastPulseTick;
        printf("   waited %d ticks after the last pulse (old dwell was %d)\n",
               waited, FW_ELS_SETTLE_TICKS);
        check(waited > FW_ELS_SETTLE_TICKS,
              "the release is gated on stillness, not on the fixed dwell");
    }

    /* ---------------- 3. NO REGRESSION FOR A QUICK MACHINE --------------- */
    /* A drivetrain that settles fast must not be made slower or less reliable.
     * This is the case that would bite elspi, where the tail is below one count
     * -- if quiescence cost a machine like that anything, the flag would be
     * unshippable regardless of what case 1 proves.
     * MUTATION: require quiescence ticks far above the config-default tail
     * (e.g. 20000) -> this case fails on the confirm-window abort. */
    printf("\n-- 3. a fast-settling machine still confirms, promptly --\n");
    {
        EmuConfig cfg = makePhysicsConfig(10.0);   /* the config default tau */
        LathePhysics p(cfg);
        Rig rig;
        Release quick = runTakeupToRelease(p, rig);
        report("Q3", quick);

        check(quick.confirmed, "fast machine: take-up CONFIRMED");
        check(quick.releaseTick < longTail.releaseTick,
              "and it released SOONER than the slow one -- the gate tracks the "
              "machine rather than imposing a flat delay on everybody");
    }

    /* ---------------- 4. NO PHANTOM REFUSAL WHILE MERELY WAITING --------- */
    /* Withholding is not refusing. If the gate reported UNCONFIRMED during the
     * quiescence wait, the operator would get a take-up failure warning for a
     * take-up that then succeeds -- and takeupSeq counts OUTCOMES, so it would
     * also corrupt the host's edge detection.
     * MUTATION: drop `carriageStopped &&` from the else-if that reports
     * UNCONFIRMED -> this fails with an extra outcome and a stale warning. */
    printf("\n-- 4. waiting is silent: no refusal is reported mid-wait --\n");
    {
        EmuConfig cfg = makePhysicsConfig(LONG_TAU_TICKS);
        LathePhysics p(cfg);
        Rig rig;
        rig.init(&p);
        rig.armFirstPass();
        rig.step(Z_CLEAR);
        rig.beginPhysicsDriven();

        uint16_t seqAtStart = rig.data.shared.elsStop.takeupSeq;
        int sawUnconfirmed = 0;
        for (int i = 0; i < 60000 && rig.data.shared.elsStop.takeupPending; i++) {
            rig.stepDriven();
            if (rig.data.shared.elsStop.takeupResult == ELS_TAKEUP_ERR_UNCONFIRMED)
                sawUnconfirmed++;
        }

        printf("   ticks reporting UNCONFIRMED during the wait: %d; "
               "takeupSeq %u -> %u\n", sawUnconfirmed,
               (unsigned)seqAtStart, (unsigned)rig.data.shared.elsStop.takeupSeq);
        check(sawUnconfirmed == 0,
              "no UNCONFIRMED reported while the carriage was merely still moving");
        check(rig.data.shared.elsStop.takeupSeq == (uint16_t)(seqAtStart + 1),
              "exactly ONE outcome for the whole take-up");
        check(rig.data.shared.elsStop.takeupResult == ELS_CAL_OK,
              "and that outcome is the confirmation");
    }

    /* ---------------- 5. THE RESOLUTION FLOOR, STATED ------------------- */
    /* This case was written expecting an absurdly slow tail to prove the
     * confirm-window abort still bounds the wait. It does not, and why it does
     * not is worth more than the assertion it replaced.
     *
     * A count-based quiescence test cannot see motion slower than one encoder
     * count per window. Drive tau to 40000 ticks and the carriage is still
     * genuinely moving, but produces no count for 200 consecutive ticks -- so
     * the gate reads it as stopped and CONFIRMS. That is not a defect in the
     * gate; it is the floor of what any encoder-based stillness test can do,
     * and it is the same floor that makes this feature pointless on elspi today
     * (one count = 5 um at 200 counts/mm).
     *
     * Stating it here is the point. A future reader tuning ELS_QUIESCENT_TICKS
     * upward will assume a longer window catches slower motion. It does not --
     * it catches motion FASTER than one count per window, and lengthening the
     * window lowers that speed threshold only linearly while adding latency to
     * every pass.
     *
     * The never-confirms path (open half-nut) is covered by
     * els_takeup_confirm_test and is deliberately not re-proved here. */
    printf("\n-- 5. the floor: motion below one count per window reads as stopped --\n");
    {
        EmuConfig cfg = makePhysicsConfig(40000.0);  /* absurd on purpose */
        LathePhysics p(cfg);
        Rig rig;
        rig.init(&p);
        rig.armFirstPass();
        rig.step(Z_CLEAR);
        rig.beginPhysicsDriven();

        int i = 0;
        for (; i < 200000 && rig.data.shared.elsStop.takeupPending; i++)
            rig.stepDriven();

        printf("   verdict reached after %d ticks; result=%u; still owed %+.2f counts\n",
               i, (unsigned)rig.data.shared.elsStop.takeupResult,
               p.getPendingSettleCounts());

        check(rig.data.shared.elsStop.takeupPending == 0,
              "the gate reached a verdict rather than gating sync forever");
        check(rig.data.shared.elsStop.takeupResult == ELS_CAL_OK,
              "and it CONFIRMED -- motion this slow is invisible to a count-based "
              "stillness test, which is the floor being documented");
        check(p.getPendingSettleCounts() < -1.0,
              "while the carriage demonstrably still owed more than a full count: "
              "the gate is blind below its own resolution, by construction");
    }

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
