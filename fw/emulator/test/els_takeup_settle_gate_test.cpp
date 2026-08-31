/*
 * CHARACTERIZATION OF A KNOWN DEFECT: what the backlash take-up confirmation
 * gate does when the carriage settle tail outlasts the gate's dwell.
 *
 * READ THIS BEFORE "FIXING" A RED ASSERTION IN THIS FILE
 * -----------------------------------------------------
 * Every assertion below pins the behaviour that ACTUALLY HAPPENS TODAY, not the
 * behaviour anyone wants. The gate in Ramps.c is deliberately left alone; fixing
 * it is separate, sequenced work. When someone does fix it, this file must go
 * red and be updated ON PURPOSE — that is the whole reason it exists. A defect
 * nothing asserts is a defect that gets re-introduced.
 *
 * THE DEFECT
 * ----------
 * Ramps.c's take-up gate runs ELS_SETTLE_TICKS (50) after the commanded step
 * count crosses its target, then asks ONE question: did enough ATTRIBUTED Z
 * motion accumulate (elsSlipConfirmed)? It never asks whether the carriage has
 * STOPPED. If the drivetrain's settle tail outlasts that 50-tick dwell, the gate
 * confirms, clears takeupPending, and releases sync — starting the cut — while
 * the carriage is still moving from the take-up.
 *
 * fw/todo.md ("The gate's dwell and the attribution horizon disagree by 20x")
 * states the two constants: ELS_SETTLE_TICKS is 50 and ELS_SLIP_SETTLE_TICKS is
 * 1000, the same physical settle, 20x apart, and nobody has measured which is
 * right. This file does not answer that. It shows what the CURRENT gate does in
 * the long-settle branch of the question, so that whichever way the measurement
 * lands, the consequence is already written down.
 *
 * WHY THIS COULD NOT BE WRITTEN BEFORE 2026-08-22
 * ----------------------------------------------
 * The emulator's lash model moved the carriage instantaneously with the pulse,
 * so no test could make a quiescence gate fail: the carriage was ALWAYS already
 * stopped. The settle model in physics.cpp (see the z_settle_tau_s note there)
 * is what supplies the missing degree of freedom.
 *
 * THE PAIR IS THE POINT
 * ---------------------
 * G1 runs a LONG tail and G2 runs the config default (a SHORT tail) through the
 * identical fixture — same firmware, same commanded pulse train, only
 * z_settle_tau_s differs. Without G2 this file would only prove that a fixture
 * can be built in which something is still moving; with it, the difference is
 * attributable to the settle time and nothing else.
 *
 * THE TAIL LENGTH HERE IS A CHOICE, NOT A MEASUREMENT. G1 sets a tail long
 * enough to outrun the dwell because that is the branch being characterized.
 * Nobody has watched Z counts arrive after a real take-up on elspi; that is the
 * open commissioning item. Do not read a number out of this file and write it
 * into Core/Src/Ramps.c.
 *
 * HOW IT IS BUILT
 * ---------------
 * Drives the real SynchroRefreshTimerIsr() with the same external-stub strategy
 * as els_takeup_confirm_test, and the arm/trigger/resume fixture is lifted from
 * there. The one difference is the drivetrain: where that file uses its own
 * integer Lash struct, this one puts the PRODUCTION LathePhysics behind Z, so
 * the settle model under test is the one the emulator actually ships rather
 * than a second copy of it living in a test.
 *
 * The SPINDLE counter stays synthetic, exactly as in els_takeup_confirm_test.
 * Advancing it by a fixed amount per tick is what drives the pass/trigger
 * sequence; it is fixture plumbing, not the axis under test. Only Z comes from
 * physics.
 *
 * MUTATION-PROVEN 2026-08-22. The DEFECT assertions are driven by the settle
 * model and not by something structural in the fixture:
 *   M2  LONG_TAU_TICKS 100 -> 1  -> all three DEFECT checks go red (0.00 counts
 *                                   pending at release, 0 residual ticks)
 * applied, the listed result observed, and reverted. Note what M2 does NOT
 * change: the release tick, the last pulse tick, and the confirmation itself
 * are identical either way. That is the blind spot stated as a measurement.
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
    printf("=== Take-up gate vs. carriage settle tail (ISR level, real physics) ===\n");
    printf("=== DOCUMENTS A KNOWN DEFECT. Assertions pin what happens TODAY. ===\n\n");

    /* ---------------------------------------------------------------- */
    printf("-- G1: settle tail LONGER than the gate's %d-tick dwell --\n",
           FW_ELS_SETTLE_TICKS);
    Release longTail;
    {
        EmuConfig cfg = makePhysicsConfig(LONG_TAU_TICKS);
        LathePhysics p(cfg);
        Rig rig;
        printf("   tau = %.1f ticks (%.5f s), gate dwell = %d ticks\n",
               p.getSettleTauS() / DT, p.getSettleTauS(), FW_ELS_SETTLE_TICKS);
        longTail = runTakeupToRelease(p, rig);
        report("G1", longTail);

        /* The gate is satisfied. It found enough attributed Z motion, exactly as
         * designed — the drivetrain IS coupled and it DID move the carriage.
         * Nothing here is a complaint about the confirmation itself. */
        check(longTail.confirmed,
              "the gate CONFIRMED the take-up (it had its evidence)");

        /* THE FINDING. At the instant the gate cleared takeupPending and let the
         * cut begin, the carriage still had whole encoder counts of take-up
         * motion to deliver. The gate never asked. */
        check(std::fabs(longTail.pendingCounts) >= 1.0,
              "DEFECT: at release the carriage still owed >= 1 full Z count of "
              "take-up motion -- it had not stopped");
        check(longTail.residualTicks > 0 && longTail.residualCounts != 0,
              "DEFECT: Z counts genuinely continued to arrive after the cut was "
              "released");

        /* The residual outlasts the entire dwell by a wide margin. This is the
         * number that matters for the todo.md question: waiting the dwell is
         * not close to waiting for the carriage. */
        check(longTail.residualTicks > FW_ELS_SETTLE_TICKS,
              "DEFECT: the motion the gate missed outlasts the whole dwell it "
              "did wait");

        /* The dwell DID elapse before the verdict, so this is the designed path
         * working as written rather than an early exit -- and the release came
         * nowhere near ELS_TAKEUP_CONFIRM_WINDOW_TICKS (25000), so it is the
         * first evaluation talking, not the abort backstop. Characterization,
         * not a contract: these are observations of the current servo ramp, and
         * a change to the ramp may legitimately move them. */
        check(longTail.releaseTick - longTail.lastPulseTick >= FW_ELS_SETTLE_TICKS,
              "the gate did wait its full dwell after the last commanded pulse");
        check(longTail.releaseTick - longTail.lastPulseTick < 1000,
              "released on the dwell's first evaluation, not via the "
              "confirm-window timeout");
    }

    /* ---------------------------------------------------------------- */
    printf("\n-- G2: CONTROL, config-default (short) tail through the same fixture --\n");
    {
        /* -1 leaves cfg.z_settle_tau_s at the config default. Same firmware,
         * same geometry, same commanded pulse train; only the settle time
         * differs. If the carriage is stopped HERE and still moving in G1, the
         * difference is the settle tail and cannot be the fixture. */
        EmuConfig cfg = makePhysicsConfig(-1.0);
        LathePhysics p(cfg);
        Rig rig;
        printf("   tau = %.1f ticks (%.5f s), gate dwell = %d ticks\n",
               p.getSettleTauS() / DT, p.getSettleTauS(), FW_ELS_SETTLE_TICKS);
        Release shortTail = runTakeupToRelease(p, rig);
        report("G2", shortTail);

        check(shortTail.confirmed,
              "control: the gate CONFIRMED the take-up here too");
        check(std::fabs(shortTail.pendingCounts) < 1.0,
              "control: at release the carriage owed less than one Z count -- "
              "as far as any encoder can tell, it had stopped");
        check(shortTail.residualCounts == 0,
              "control: no Z count arrived after the cut was released");
        check(std::fabs(longTail.pendingCounts) > std::fabs(shortTail.pendingCounts),
              "the pair differs in the settle tail and nothing else");
        check(longTail.releaseTick == shortTail.releaseTick
              && longTail.lastPulseTick == shortTail.lastPulseTick,
              "and the firmware behaved IDENTICALLY in both -- same release "
              "tick, same last pulse: the gate never noticed the difference");
    }

    printf("\n%s (%d failures)\n", failures ? "FAILED" : "PASSED", failures);
    return failures ? 1 : 0;
}
