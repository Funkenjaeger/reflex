/*
 * ISR-LEVEL tests for the closed-loop backlash take-up confirmation and the
 * calibration driver. Drives the real SynchroRefreshTimerIsr() against a
 * simulated drivetrain with lash.
 *
 * WHY THIS EXISTS ALONGSIDE els_backlash_cal_test
 * -----------------------------------------------
 * That test covers the pure decision layer (els_backlash_cal.h). This one
 * covers the wiring: that the ISR actually captures the Z baseline at take-up
 * initiation, actually withholds completion when the carriage does not respond,
 * actually publishes result/seq codes, and that the enable escape hatch really
 * releases a withheld take-up. None of that is exercised by the pure tests, and
 * none of it was exercised by els_stop_resume_relatch_test either — that
 * scenario runs exactly ONE ISR pass after the resume and asserts on take-up
 * INITIATION, so the completion path had no coverage at all before this file.
 *
 * THE PROPERTY THAT MATTERS MOST
 * ------------------------------
 * The take-up must NOT report complete just because the firmware finished
 * issuing the pulses it decided to issue. If the half-nut is open, currentSteps
 * crosses the target exactly on schedule and the old code called
 * applyPhaseCorrection on a Z snapshot from a drivetrain that was never
 * coupled. The coupled/uncoupled pair below is what distinguishes a real gate
 * from a gate-shaped comment.
 */

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
/* Stubs for every Ramps.c external (same set as the relatch test)      */
/* ------------------------------------------------------------------ */
extern "C" {

GPIO_TypeDef    emu_gpioa, emu_gpiob, emu_gpioc;
RCC_TypeDef     emu_rcc;
DWT_Type        emu_dwt;
CoreDebug_Type  emu_coreDebug;
EmulatorHardwareState emu_hw;

void emu_log_trace(const char *fmt, ...) { (void)fmt; }
void emu_log_event(const char *fmt, ...) { (void)fmt; }

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
    printf("   %-70s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

static void checkEq(int32_t got, int32_t want, const char *what) {
    bool ok = (got == want);
    printf("   %-70s %s (got %d, want %d)\n", what, ok ? "ok" : "FAIL",
           (int)got, (int)want);
    if (!ok) failures++;
}

/* ------------------------------------------------------------------ */
/* Fixture: the relatch rig plus a lash model driven by real step pulses */
/* ------------------------------------------------------------------ */

static const int32_t SPINDLE_PER_PASS = 40;
static const int32_t Z_CLEAR          = 1000;
static const int32_t Z_PAST           = -5;
static const int32_t Z_STOP_POS       = 0;
static const int16_t Z_STOP_DIR       = -1;
static const float   SENTINEL         = 1.0e30f;

/* Lash: the leadscrew traverses `lashSteps` before the carriage moves at all.
 * `coupled = false` is an open half-nut — pulses go out, Z never responds. */
struct Lash {
    int32_t lashSteps      = 60;
    int32_t stepsPerZCount = 3;
    bool    coupled        = true;

    int32_t nutPos   = 0;
    int32_t carriage = 0;
    int32_t prevSteps = 0;

    /* Follow the servo's actual pulse output (currentSteps), not anything the
     * test decides — that is what makes this a wiring test. */
    int32_t follow(int32_t currentSteps, int32_t zBase) {
        int32_t d = currentSteps - prevSteps;
        prevSteps = currentSteps;
        if (!coupled) return zBase + carriage / stepsPerZCount;
        while (d > 0) { nutPos++; if (nutPos > lashSteps) { nutPos = lashSteps; carriage++; } d--; }
        while (d < 0) { nutPos--; if (nutPos < 0)         { nutPos = 0;         carriage--; } d++; }
        return zBase + carriage / stepsPerZCount;
    }
};

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int32_t            spindleCnt = 0;
    int32_t            zCnt = Z_CLEAR;
    Lash               lash;
    bool               lashDriven = false;   /* once true, Z follows the servo */
    int32_t            zBase = 0;

    void init(uint32_t backlashSteps, int32_t motionThresh, bool coupled = true,
              int32_t lashSteps = 60) {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        spindleCnt = 0;
        zCnt       = Z_CLEAR;
        lash = Lash{};
        lash.lashSteps = lashSteps;
        lash.coupled   = coupled;
        lashDriven = false;
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
        data.shared.elsStop.backlashSteps        = backlashSteps;
        data.shared.elsStop.hysteresis           = 500;
        data.shared.elsStop.calMotionThreshCounts = motionThresh;
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

    void step(int32_t zTarget) {
        spindleCnt += SPINDLE_PER_PASS;
        zCnt        = zTarget;
        tim[0].CNT  = (uint32_t)spindleCnt;
        tim[1].CNT  = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }

    /* Hand Z over to the lash model: from here Z is whatever the servo's own
     * pulses produce through the drivetrain. */
    void beginLashDriven() {
        lashDriven = true;
        zBase = zCnt;
        lash.prevSteps = (int32_t)data.shared.servo.currentSteps;
        lash.carriage = 0;
        /* Seat the nut against the wall the take-up drives AWAY from, so the
         * take-up must traverse the full lash before the carriage moves. That is
         * the worst case and the only one that exercises the thing under test.
         * Starting at nutPos = 0 (the take-up direction's own wall) makes the
         * carriage move on the very first step, so every take-up "confirms"
         * regardless of lash — a fixture that cannot fail. Cutting direction
         * here is negative (syncRatioNum < 0), so the far wall is lashSteps.
         * Mirrors the emulator's own half-nut model, which deliberately drops
         * the nut on the opposite wall for the same reason. */
        lash.nutPos = lash.lashSteps;
    }

    /* Move the carriage WITHOUT the servo driving it — an operator pushing it
     * with the half-nut open. This is the degree of freedom the production
     * emulator still lacks (see reflex-fw todo.md), and its absence is why the
     * hardware defect below was unreachable by test: the model could express
     * "coupled" and "never moves", but not "moving for a reason that isn't us". */
    void nudgeCarriage(int32_t zCounts) {
        zBase += zCounts;
    }

    void stepDriven() {
        spindleCnt += SPINDLE_PER_PASS;
        zCnt = lash.follow((int32_t)data.shared.servo.currentSteps, zBase);
        tim[0].CNT = (uint32_t)spindleCnt;
        tim[1].CNT = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }

    /* Step until the servo has emitted no pulse for `quietTicks` consecutive
     * ticks, i.e. the COMMANDED motion is finished -- not merely past the
     * crossing test that ends it on paper.
     *
     * The distinction is the whole of the 2026-08-22 finding: takeupReached is
     * a crossing test on currentSteps, and the decel ramp overshoots it and
     * keeps emitting the residual with gaps that stretch as it slows (observed:
     * 5 steps past target, the last pair 262 ticks apart). Anything that wants
     * to inject motion "after the take-up" has to wait for quiet, not for the
     * crossing and not for a gate verdict. Returns the tick it settled on, or
     * -1 if the servo never went quiet. */
    int runUntilServoQuiet(int quietTicks, int maxTicks = 60000) {
        int quiet = 0;
        int32_t prev = (int32_t)data.shared.servo.currentSteps;
        for (int i = 0; i < maxTicks; i++) {
            stepDriven();
            int32_t now = (int32_t)data.shared.servo.currentSteps;
            quiet = (now == prev) ? quiet + 1 : 0;
            prev  = now;
            /* Quiet ALONE is not proof the motion is over, and assuming it was
             * cost a wrong test result: the decel ramp's inter-pulse gaps keep
             * stretching as it slows, so any fixed quiet threshold eventually
             * gets beaten by a later residual step. The drained queue is the
             * real signal -- nothing left to command AND the generator caught
             * up to what was already commanded. */
            bool drained = data.shared.servo.stepsToGo == 0
                        && data.shared.servo.desiredSteps == data.shared.servo.currentSteps;
            if (drained && quiet >= quietTicks) return i;
        }
        return -1;
    }

    /* Arm a job, trigger the stop, then resume — the point at which the
     * firmware initiates a backlash take-up. */
    void armAndTrigger() {
        step(Z_CLEAR);
        data.shared.elsStop.enable = 1;
        for (int i = 0; i < 3; i++) step(Z_CLEAR);
        step(Z_PAST);
        step(Z_PAST);
        step(Z_CLEAR);                    /* retract clear so the resume edge lands */

        /* Let the pulse generator catch up before resuming. Sync is gated while
         * active == 1, so desiredSteps is static and currentSteps closes the
         * gap. Without this the take-up inherits a large pulse BACKLOG and the
         * carriage moves much further than the take-up commanded, which would
         * mask exactly the under-motion this file tests for.
         *
         * The backlog is a FIXTURE artifact, not firmware behaviour: this rig
         * advances the spindle 40 counts per tick against a 2/15 ratio (~5.3
         * steps/tick) while the generator emits 1 pulse/tick, so desiredSteps
         * runs away. On a real machine sync is gated for the whole retract and
         * the operator takes seconds to press Cut, so the servo is long settled. */
        for (int i = 0; i < 20000
             && data.shared.servo.currentSteps != data.shared.servo.desiredSteps; i++) {
            step(Z_CLEAR);
        }

        data.shared.elsStop.lastIdealAdvance = SENTINEL;
        data.shared.elsStop.active = 0;   /* SW resume */
    }

    /* Arm a FRESH job the way reflex-ui has since 2026-08-17 -- active first,
     * then enable (els_arm_past_stop_test pins why that order matters) -- and
     * press Cut. No stop has fired, so referenceLatched is 0: this is the
     * first pass of the job, the one that will CREATE the datum every later
     * pass is measured against. Until 2026-08-21 the resume path skipped the
     * take-up entirely here, so the datum pass was the only pass an open or
     * partially engaged half-nut could slip through unannounced. */
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

/* ------------------------------------------------------------------ */

int main() {
    printf("=== ELS take-up Z confirmation + calibration (ISR level) ===\n\n");

    /* ---------------- Coupled: take-up completes -------------------- */
    printf("-- coupled drivetrain: take-up confirms and phase correction runs --\n");
    {
        Rig rig;
        rig.init(/*backlashSteps*/ 90, /*motionThresh*/ 2, /*coupled*/ true, /*lash*/ 60);
        rig.armAndTrigger();
        rig.step(Z_CLEAR);                       /* initiates the take-up */
        check(rig.data.shared.elsStop.takeupPending == 1, "take-up initiated");
        rig.beginLashDriven();

        for (int i = 0; i < 60000 && rig.data.shared.elsStop.takeupPending; i++)
            rig.stepDriven();

        checkEq(rig.data.shared.elsStop.takeupPending, 0, "take-up completed");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_CAL_OK, "takeupResult is OK");
        check(rig.data.shared.elsStop.takeupSeq >= 1, "takeupSeq incremented");
        check(rig.data.shared.elsStop.lastIdealAdvance != SENTINEL,
              "applyPhaseCorrection ran");
        check(rig.data.shared.elsStop.lastTakeupZDelta != 0,
              "lastTakeupZDelta recorded real motion");
    }

    /* ---------------- Uncoupled: THE point of the feature ------------ */
    printf("\n-- OPEN HALF-NUT: take-up is withheld, correction never runs --\n");
    {
        /* MUTATION: delete the elsZMotionSeen() branch in the take-up block (i.e.
         * always complete) and every assertion here flips — which is precisely
         * the pre-fix behaviour this file exists to pin. */
        Rig rig;
        rig.init(90, 2, /*coupled*/ false, 60);
        rig.armAndTrigger();
        rig.step(Z_CLEAR);
        check(rig.data.shared.elsStop.takeupPending == 1, "take-up initiated");
        rig.beginLashDriven();

        for (int i = 0; i < 60000; i++) rig.stepDriven();

        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_UNCONFIRMED,
                "takeupResult reports UNCONFIRMED");
        check(rig.data.shared.elsStop.lastIdealAdvance == SENTINEL,
              "applyPhaseCorrection did NOT run on an uncoupled drivetrain");
        checkEq(rig.data.shared.elsStop.takeupSeq, 1,
                "takeupSeq reported the outcome ONCE, not once per tick");

        /* The pass ABORTS back to the stopped state once the confirmation
         * window closes. Holding the machine was the earlier behaviour and it
         * was worse than useless: the only way out was the enable toggle, which
         * clears referenceLatched on re-engage — so recovering from a FALSE
         * alarm cost the operator their thread phase reference. */
        checkEq(rig.data.shared.elsStop.takeupPending, 0, "not holding the machine");
        checkEq(rig.data.shared.elsStop.active, 1, "back to stopped-at-shoulder");
        checkEq(rig.data.shared.elsStop.referenceLatched, 1,
                "phase reference preserved: retry is free");
    }

#ifdef ELS_DIAG_PROBE
    /* ---------------- takeup-settle-v3: the capture survives a real take-up -- */
    /* THE TARGET'S REASON TO EXIST. v2 could not measure a confirmed take-up at
     * all: its capture started at the crossing and was ended either by the
     * ramp's own residual pulses or by the post-confirmation jog ~51 ticks in,
     * which is what every one of the 148 captures taken on the lathe under v2
     * shows. v3 re-arms on a pulse while takeupPending (so t=0 is the LAST
     * pulse) and holds the gate's dwell until the capture publishes (so the jog
     * cannot cut it short). Both halves are asserted here; removing either one
     * reddens this block. */
    printf("\n-- v3 PROBE: full-window capture across a confirmed take-up --\n");
    {
        Rig rig;
        rig.init(/*backlashSteps*/ 90, /*motionThresh*/ 2, /*coupled*/ true, /*lash*/ 60);
        rig.armAndTrigger();
        rig.step(Z_CLEAR);
        rig.beginLashDriven();

        /* Wait for the COMMANDED motion to finish, not for the crossing: the
         * residual steps are hundreds of ticks apart at the tail of the ramp and
         * each one re-arms the capture. 400 quiet ticks clears the widest gap
         * observed (262) with margin, and still lands the nudge deep inside the
         * 2000-tick window. */
        int settledAt = rig.runUntilServoQuiet(400);
        check(settledAt > 0, "v3: the servo went quiet after the take-up");
        checkEq(rig.data.shared.elsStop.takeupPending, 1,
                "v3: the gate is STILL HOLDING the machine while the capture runs "
                "(without the hold it would have confirmed and driven away by now)");
        checkEq(rig.data.shared.elsStop.diagSeq, 0,
                "v3: nothing published yet -- the capture is still open");

        /* The positive control DIAG.md demands: a condition known to move Z
         * during the window, injected after the last pulse so it is settle and
         * not ramp. */
        rig.nudgeCarriage(20);

        for (int i = 0; i < 60000 && rig.data.shared.elsStop.diagSeq == 0; i++)
            rig.stepDriven();

        checkEq(rig.data.shared.elsStop.diagSeq, 1, "v3: exactly one capture published");
        checkEq(rig.data.shared.elsStop.diagNetCounts, 20,
                "v3 POSITIVE CONTROL: known Z motion after the last pulse is captured exactly");
        checkEq(rig.data.shared.elsStop.diagEndReason, ELS_DIAG_END_WINDOW,
                "v3: the capture ran to the END OF ITS WINDOW -- under v2 this was END_PULSE at ~51 ticks");
        checkEq(rig.data.shared.elsStop.diagCaptureTicks,
                ELS_DIAG_TRACE_BUCKETS * ELS_DIAG_BUCKET_TICKS,
                "v3: the full window was measured, not a truncated head of it");
        check(rig.data.shared.elsStop.diagSettleTicks > 0,
              "v3: settle_ticks names WHEN the last motion arrived -- the measurement itself");

        /* And the hold RELEASES. A probe that measured beautifully and left the
         * machine held would be worse than no probe. */
        for (int i = 0; i < 60000 && rig.data.shared.elsStop.takeupPending; i++)
            rig.stepDriven();
        checkEq(rig.data.shared.elsStop.takeupPending, 0,
                "v3: the gate evaluated as soon as the capture published");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_CAL_OK,
                "v3: ...and a coupled take-up still CONFIRMS through the hold");
    }
#endif

    /* ---------------- FIRST PASS and TURNING are gated too (2026-08-21) -- */
    /* MUTATIONS this block exists to catch: (A) put referenceLatched / the
     * thread-geometry terms back in front of the take-up initiation -- every
     * "take-up initiated" below goes red; (B) apply the phase correction
     * unconditionally on confirmation again -- the "did NOT run" checks go
     * red. Both shapes were the shipped code before 2026-08-21. */
    printf("\n-- FIRST PASS, coupled: take-up confirms, NO correction, datum latched at the stop --\n");
    {
        Rig rig;
        rig.init(/*backlashSteps*/ 90, /*motionThresh*/ 2, /*coupled*/ true, /*lash*/ 60);
        rig.armFirstPass();
        rig.step(Z_CLEAR);                       /* the Cut edge */
        checkEq(rig.data.shared.elsStop.referenceLatched, 0, "first pass: no reference exists yet");
        checkEq(rig.data.shared.elsStop.takeupPending, 1,
                "first pass: take-up initiated (skipped entirely before 2026-08-21)");
        rig.beginLashDriven();

        for (int i = 0; i < 60000 && rig.data.shared.elsStop.takeupPending; i++)
            rig.stepDriven();

        checkEq(rig.data.shared.elsStop.takeupPending, 0, "first pass: take-up completed");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_CAL_OK, "first pass: confirmed");
        checkEq(rig.data.shared.elsStop.takeupSeq, 1, "first pass: one outcome reported");
        check(rig.data.shared.elsStop.lastIdealAdvance == SENTINEL,
              "first pass: applyPhaseCorrection did NOT run -- nothing to correct against");
        checkEq(rig.data.shared.elsStop.referenceLatched, 0,
                "first pass: confirmation latched nothing; only a stop trigger may");

        int32_t before = (int32_t)rig.data.shared.servo.desiredSteps;
        for (int i = 0; i < 5; i++) rig.stepDriven();
        check((int32_t)rig.data.shared.servo.desiredSteps != before,
              "first pass: sync released once confirmed");

        rig.step(Z_PAST);
        rig.step(Z_PAST);
        checkEq(rig.data.shared.elsStop.active, 1, "first pass: the stop fires at the threshold");
        checkEq(rig.data.shared.elsStop.referenceLatched, 1,
                "first pass: ...and latches the datum from a drivetrain PROVEN coupled");
    }

    printf("\n-- FIRST PASS, open half-nut: refused before any datum can exist --\n");
    {
        Rig rig;
        rig.init(90, 2, /*coupled*/ false, 60);
        rig.armFirstPass();
        rig.step(Z_CLEAR);
        checkEq(rig.data.shared.elsStop.takeupPending, 1, "first pass, open nut: take-up initiated");
        rig.beginLashDriven();

        for (int i = 0; i < 60000; i++) rig.stepDriven();

        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_UNCONFIRMED,
                "first pass, open nut: REFUSED -- the message every later pass already got");
        checkEq(rig.data.shared.elsStop.takeupSeq, 1, "first pass, open nut: reported once");
        check(rig.data.shared.elsStop.lastIdealAdvance == SENTINEL,
              "first pass, open nut: no correction");
        checkEq(rig.data.shared.elsStop.takeupPending, 0, "first pass, open nut: not holding the machine");
        checkEq(rig.data.shared.elsStop.active, 1,
                "first pass, open nut: aborted back to armed-idle, the state before Cut");
        checkEq(rig.data.shared.elsStop.referenceLatched, 0,
                "first pass, open nut: NO datum was latched from an uncoupled drivetrain");
    }

    /* Turning: the host writes threadPitchSteps = 0 (no thread phase to correct
     * to) and, since 2026-08-21, keeps zCountsPerPitch SIGNED so the take-up
     * direction still carries the Z polarity. The rig's zCountsPerPitch stays
     * as initialised; only the pitch is cleared. */
    printf("\n-- TURNING (pitch 0), coupled: take-up confirms, no correction --\n");
    {
        Rig rig;
        rig.init(90, 2, true, 60);
        rig.data.shared.elsStop.threadPitchSteps = 0.0f;
        rig.armAndTrigger();                     /* a later pass: reference IS latched */
        rig.step(Z_CLEAR);
        checkEq(rig.data.shared.elsStop.takeupPending, 1,
                "turning: take-up initiated (never ran in turning before 2026-08-21)");
        rig.beginLashDriven();

        for (int i = 0; i < 60000 && rig.data.shared.elsStop.takeupPending; i++)
            rig.stepDriven();

        checkEq(rig.data.shared.elsStop.takeupPending, 0, "turning: take-up completed");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_CAL_OK, "turning: confirmed on the floor threshold");
        check(rig.data.shared.elsStop.lastIdealAdvance == SENTINEL,
              "turning: applyPhaseCorrection did NOT run -- no pitch to correct to");
    }

    printf("\n-- TURNING, open half-nut: refused --\n");
    {
        Rig rig;
        rig.init(90, 2, /*coupled*/ false, 60);
        rig.data.shared.elsStop.threadPitchSteps = 0.0f;
        rig.armAndTrigger();
        rig.step(Z_CLEAR);
        checkEq(rig.data.shared.elsStop.takeupPending, 1, "turning, open nut: take-up initiated");
        rig.beginLashDriven();
        for (int i = 0; i < 60000; i++) rig.stepDriven();
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_UNCONFIRMED,
                "turning, open nut: REFUSED");
        checkEq(rig.data.shared.elsStop.active, 1, "turning, open nut: aborted to stopped");
        check(rig.data.shared.elsStop.lastIdealAdvance == SENTINEL, "turning, open nut: no correction");
    }

    printf("\n-- TURNING polarity: sign(zCountsPerPitch) steers the take-up with pitch == 0 --\n");
    {
        /* Same machine, two hosts: threading writes pitch > 0 and a signed
         * zCountsPerPitch; turning writes pitch = 0 and the same signed
         * zCountsPerPitch. The take-up must go the same way in both, and a
         * flipped sign must flip it. Initiation only -- the direction is
         * decided on the Cut edge. */
        Rig thr; thr.init(90, 2, true, 60);
        thr.data.shared.elsStop.zCountsPerPitch = -846.667f;
        thr.armAndTrigger(); thr.step(Z_CLEAR);

        Rig trn; trn.init(90, 2, true, 60);
        trn.data.shared.elsStop.threadPitchSteps = 0.0f;
        trn.data.shared.elsStop.zCountsPerPitch  = -846.667f;
        trn.armAndTrigger(); trn.step(Z_CLEAR);

        Rig pos; pos.init(90, 2, true, 60);
        pos.data.shared.elsStop.threadPitchSteps = 0.0f;
        pos.armAndTrigger(); pos.step(Z_CLEAR);

        checkEq(thr.data.shared.elsStop.takeupPending, 1, "polarity: threading rig initiated");
        checkEq(trn.data.shared.elsStop.takeupPending, 1, "polarity: turning rig initiated");
        checkEq(trn.data.elsStopTakeupSign, thr.data.elsStopTakeupSign,
                "polarity: turning (pitch 0) takes up the same way as threading on the same wiring");
        checkEq(pos.data.elsStopTakeupSign, -trn.data.elsStopTakeupSign,
                "polarity: flipping sign(zCountsPerPitch) flips the turning take-up direction");
    }

    /* ---------------- Partial engagement: moves, but not enough ------ */
    /* The pair below is the whole point of the derived threshold. BOTH cases
     * clear the bare 2-count detection floor, so both would have been confirmed
     * under a floor-only rule. Only the second is actually healthy.
     *
     * Geometry is made self-consistent here: the rig's default registers encode
     * the emulator's zPerStep while the lash model advances 1 Z count per 3
     * servo steps, so zCountsPerPitch is set to match the model. Otherwise the
     * threshold would be derived from geometry the simulated drivetrain does
     * not obey. */
    /* Numbers are chosen with generous separation on BOTH sides of the
     * threshold. The ramp overshoots slightly past the take-up target, and each
     * overshoot step is extra carriage motion, so a case sitting one or two
     * counts from the boundary measures the overshoot rather than the guard.
     * calMeasured 65, commanded 105 (margin 40), zPerStep 0.5, floor 2
     *   => expected 40*0.5 + 2 = 22 counts, threshold 11. */
    printf("\n-- PARTIAL ENGAGEMENT: carriage moves, but less than it should --\n");
    {
        Rig rig;
        rig.init(/*backlashSteps*/ 105, /*motionThresh*/ 2, /*coupled*/ true,
                 /*true lash*/ 95);                    /* worn / partly engaged */
        rig.lash.stepsPerZCount = 2;
        rig.data.shared.elsStop.zCountsPerPitch =
            rig.data.shared.elsStop.threadPitchSteps / 2.0f;
        for (int i = 0; i < 3; i++) rig.data.shared.elsStop.calMeasured[i] = 65;

        rig.armAndTrigger();
        rig.step(Z_CLEAR);
        rig.beginLashDriven();
        for (int i = 0; i < 60000; i++) rig.stepDriven();

        /* commanded 105 - true lash 95 = 10 steps = 5 Z counts. Comfortably
         * clears the bare 2-count floor; nowhere near the derived 11. */
        checkEq(rig.data.shared.elsStop.takeupThreshCounts, 11,
                "threshold derived from expected motion, not the bare floor");
        check(rig.data.shared.elsStop.lastTakeupZDelta > 2,
              "carriage DID move, and would have cleared the bare 2-count floor");
        check(rig.data.shared.elsStop.lastTakeupZDelta < 11,
              "...but moved less than a calibrated take-up should");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_UNCONFIRMED,
                "REFUSED - moved less than a calibrated take-up should");
        checkEq(rig.data.shared.elsStop.active, 1, "aborted back to stopped");
        check(rig.data.shared.elsStop.lastIdealAdvance == SENTINEL,
              "no phase correction on a partially engaged take-up");
    }

    printf("\n-- positive control: same setup, healthy lash --\n");
    {
        Rig rig;
        rig.init(/*backlashSteps*/ 105, /*motionThresh*/ 2, /*coupled*/ true,
                 /*true lash*/ 65);                    /* what it was calibrated at */
        rig.lash.stepsPerZCount = 2;
        rig.data.shared.elsStop.zCountsPerPitch =
            rig.data.shared.elsStop.threadPitchSteps / 2.0f;
        for (int i = 0; i < 3; i++) rig.data.shared.elsStop.calMeasured[i] = 65;

        rig.armAndTrigger();
        rig.step(Z_CLEAR);
        rig.beginLashDriven();
        for (int i = 0; i < 60000 && rig.data.shared.elsStop.takeupPending; i++)
            rig.stepDriven();

        checkEq(rig.data.shared.elsStop.takeupThreshCounts, 11, "same derived threshold");
        checkEq(rig.data.shared.elsStop.takeupPending, 0,
                "CONFIRMED - a healthy lash clears the stricter demand");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_CAL_OK, "takeupResult OK");
    }

    /* The threshold is a LOWER BOUND, never a window. Where the nut sits when a
     * take-up starts decides how much lash is left to traverse, so a well-seated
     * drivetrain moves the carriage MUCH further than the worst-case estimate --
     * up to the entire commanded distance. All of that is healthy. */
    printf("\n-- nut already seated: carriage moves far more, still confirmed --\n");
    {
        Rig rig;
        rig.init(/*backlashSteps*/ 105, /*motionThresh*/ 2, /*coupled*/ true,
                 /*true lash*/ 65);
        rig.lash.stepsPerZCount = 2;
        rig.data.shared.elsStop.zCountsPerPitch =
            rig.data.shared.elsStop.threadPitchSteps / 2.0f;
        for (int i = 0; i < 3; i++) rig.data.shared.elsStop.calMeasured[i] = 65;

        rig.armAndTrigger();
        rig.step(Z_CLEAR);
        rig.beginLashDriven();
        rig.lash.nutPos = 0;      /* already against the take-up's own wall */
        for (int i = 0; i < 60000 && rig.data.shared.elsStop.takeupPending; i++)
            rig.stepDriven();

        check(rig.data.shared.elsStop.lastTakeupZDelta > 11,
              "moved far beyond the expected minimum");
        checkEq(rig.data.shared.elsStop.takeupPending, 0,
                "CONFIRMED - excess motion is not a fault (lower bound, not a window)");
    }

    /* ---------------- The confirmation WINDOW ------------------------ */
    /* Regression pair for the 2026-08-08 hardware finding: a correctly withheld
     * take-up was released by the operator nudging the carriage by hand with the
     * half-nut open, because the gate re-evaluated forever against the original
     * baseline and accepted Z motion from any source at any time.
     *
     * The fix is a bounded window, not an instant latch — the carriage does not
     * stop dead when the servo does, so genuinely late motion must still count. */
    printf("\n-- late motion INSIDE the window still confirms (inertia/compliance) --\n");
    {
        Rig rig;
        rig.init(90, 2, /*coupled*/ false, 60);   /* nut open: no driven motion */
        rig.armAndTrigger();
        rig.step(Z_CLEAR);
        rig.beginLashDriven();

        /* Run until the gate has evaluated and withheld. */
        for (int i = 0; i < 60000
             && rig.data.shared.elsStop.takeupResult != ELS_TAKEUP_ERR_UNCONFIRMED; i++)
            rig.stepDriven();
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_UNCONFIRMED,
                "withheld first");
        check(rig.data.elsStopTakeupLatched == 0, "window still open at this point");

        rig.nudgeCarriage(20);          /* well past the threshold */
        /* Several ticks: the gate runs near the TOP of the ISR and the scale
         * positions are refreshed further down, so it sees the previous tick's
         * Z. One tick is not enough for injected motion to become visible. */
        for (int i = 0; i < 5; i++) rig.stepDriven();

#ifndef ELS_DIAG_PROBE
        /* RELEASE TIMING, and deliberately not asserted in a probe build.
         *
         * The nudge is placed by GATE STATE -- the loop above runs until the
         * gate has refused -- which in a release build is ELS_SETTLE_TICKS (50)
         * after the last commanded pulse, comfortably inside
         * elsSlipConfirmed's 1000-tick attribution horizon. That is the
         * scenario's whole premise.
         *
         * takeup-settle-v3 holds the gate's first evaluation until its capture
         * publishes (~2000 ticks), so the same code lands this nudge OUTSIDE
         * the horizon and it is correctly not credited. Nothing about the gate
         * changed: the recovery path this pair pins is a claim about release
         * timing, and a build that deliberately moves the gate's clock cannot
         * make it. The probe build gets its own settle positive control below,
         * which places its nudge by time-since-last-pulse instead. */
        checkEq(rig.data.shared.elsStop.takeupPending, 0,
                "motion arriving while the window is open CONFIRMS");

        /* This nudge lands ~50 ticks after the last commanded pulse — inside the
         * settle horizon, so attribution credits it to the servo. That is the
         * POSITIVE HALF of the pair below: same fixture, same window, same
         * nudge, and the ONLY difference is how long after the last pulse it
         * arrived. If a change ever makes both this and the next case agree,
         * attribution has stopped discriminating and the pair is dead. */
        check(rig.data.elsSlip.attributedZCounts != 0,
              "...and it confirmed on ATTRIBUTED motion, not raw endpoint delta");
#endif
    }

    /* The bounded window shrank the 2026-08-08 exposure from "forever" to
     * ~250 ms. It did not change its SHAPE: any Z motion from any source inside
     * that window was still accepted as proof the half-nut was engaged. A person
     * can reach a handwheel well inside 250 ms.
     *
     * Attribution is what changes the shape. The nudge below is identical to the
     * one above and lands in the same open window — it is simply no longer
     * adjacent to a commanded pulse, so it is not evidence of coupling. */
    printf("\n-- hand nudge INSIDE the window but long after the last pulse does NOT confirm --\n");
    {
        /* MUTATION 1: delete the `ticksSinceLastPulse <= settleTicks` branch in
         * elsSlipTick() (i.e. attribute every tick's dZ unconditionally) and
         * THIS case goes red while the inertia case above stays green. That
         * selectivity is the proof — a mutation that reddens both only shows the
         * gate stopped confirming at all, which any broken gate achieves.
         *
         * MUTATION 2: raise ELS_SLIP_SETTLE_TICKS above the 5000-tick delay
         * below and this case goes red too, on the same code path. That is not a
         * defect in the test, it IS the constant's meaning: the horizon is the
         * exposure, and this test measures it. */
        Rig rig;
        rig.init(90, 2, /*coupled*/ false, 60);   /* nut open: no driven motion */
        rig.armAndTrigger();
        rig.step(Z_CLEAR);
        rig.beginLashDriven();

        for (int i = 0; i < 60000
             && rig.data.shared.elsStop.takeupResult != ELS_TAKEUP_ERR_UNCONFIRMED; i++)
            rig.stepDriven();
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_UNCONFIRMED,
                "withheld first");

        /* Coast far past any plausible mechanical settle, but nowhere near the
         * 25000-tick window. This is the interval the old code could not judge. */
        for (int i = 0; i < 5000; i++) rig.stepDriven();
        checkEq(rig.data.elsStopTakeupLatched, 0,
                "window is STILL OPEN — the old gate would still be listening");

        rig.nudgeCarriage(20);          /* same shove that confirmed above */
        for (int i = 0; i < 200; i++) rig.stepDriven();

        /* Assert the motion was SEEN before asserting it was refused. Without
         * this, a fixture that silently failed to inject the nudge would pass
         * this test for entirely the wrong reason. */
        checkEq((int32_t)rig.data.elsSlip.unattributedZCounts, 20,
                "the carriage DID move 20 counts, and attribution logged it");

#ifdef ELS_DIAG_PROBE
        /* WINDOW-SIZE MISMATCH, empirically confirmed: the diag capture's own
         * geometry (ELS_DIAG_TRACE_BUCKETS x ELS_DIAG_BUCKET_TICKS = 50x40 =
         * 2000 ticks) is ~12.5x smaller than the confirm gate's own window
         * (ELS_TAKEUP_CONFIRM_WINDOW_TICKS = 25000). A disturbance timed to
         * land inside the CONFIRM gate's window (this test's whole point) can
         * still arrive long after the DIAG capture has already ended -- so a
         * positive control must be timed against the diag window, not the
         * confirm window. This nudge, 5000+ ticks after commanded-complete,
         * lands OUTSIDE the diag capture's lifetime: the capture already
         * ended (diagSeq advanced) before the nudge happened, so the probe
         * carries none of it. That is not a defect in this test or in the
         * probe -- it is why the positive control (see the case above) must
         * be timed close to takeup completion. */
        check(rig.data.shared.elsStop.diagSeq >= 1,
              "DIAG PROBE: the capture had already ended before this late nudge arrived");
        checkEq(rig.data.shared.elsStop.diagNetCounts, 0,
                "DIAG PROBE: ...so a nudge timed for the CONFIRM window is invisible to the (much shorter) diag window");
#endif

        checkEq((int32_t)rig.data.elsSlip.attributedZCounts, 0,
                "...but none of it arrived while the servo was driving");

        /* NEGATIVE: this rig's take-up runs the other way, so a +20 count shove
         * is 20 counts the WRONG way. Both nudge cases in this file are, and the
         * one above confirms anyway — because the gate is magnitude-only, by
         * deliberate inheritance from elsZMotionSeen() (els_backlash_cal.h on
         * why detection is polarity-free). Attribution did not change that and
         * is not the place to: a signed gate is a separate strictness increase
         * whose own failure mode is a miscomputed droSign refusing forever.
         * Pinned here so that decision is made with this in view — flipping the
         * gate signed turns the inertia case above red until its nudge is
         * re-aimed, which is a test artifact, not evidence either way. */
        checkEq(rig.data.shared.elsStop.lastTakeupZDelta, -20,
                "raw endpoint delta: 20 counts of motion the OLD gate confirmed on");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_UNCONFIRMED,
                "STILL REFUSED — a hand-pushed carriage is not evidence of coupling");
        checkEq(rig.data.shared.elsStop.takeupPending, 1,
                "still withholding, window still open");
        check(rig.data.shared.elsStop.lastIdealAdvance == SENTINEL,
              "applyPhaseCorrection never ran");
    }

    printf("\n-- late motion AFTER the window does NOT confirm (the HW defect) --\n");
    {
        /* MUTATION: remove the elsStopTakeupLatched guard (i.e. re-evaluate
         * forever, the original behaviour) and this test goes green while the
         * machine goes back to accepting a hand-pushed carriage as proof the
         * half-nut is engaged. */
        Rig rig;
        rig.init(90, 2, /*coupled*/ false, 60);
        rig.armAndTrigger();
        rig.step(Z_CLEAR);
        rig.beginLashDriven();

        /* Run well past ELS_SETTLE_TICKS + ELS_TAKEUP_CONFIRM_WINDOW_TICKS. */
        for (int i = 0; i < 40000; i++) rig.stepDriven();
        checkEq(rig.data.elsStopTakeupLatched, 1, "window has closed");

        /* The pass is ABORTED back to the stopped state, not held. */
        checkEq(rig.data.shared.elsStop.takeupPending, 0, "no longer holding the machine");
        checkEq(rig.data.shared.elsStop.active, 1, "back to stopped-at-shoulder");
        checkEq(rig.data.shared.elsStop.referenceLatched, 1,
                "thread phase reference PRESERVED - a retry must be free");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_UNCONFIRMED,
                "and it still says why");

        rig.nudgeCarriage(200);         /* a big shove, far past any threshold */
        for (int i = 0; i < 200; i++) rig.stepDriven();
        check(rig.data.shared.elsStop.lastIdealAdvance == SENTINEL,
              "a hand-pushed carriage never triggers the phase correction");

        /* Retry: pressing Cut again clears the warning and starts a fresh
         * take-up, with no reset ritual in between. */
        rig.data.shared.elsStop.active = 0;
        rig.stepDriven();
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_CAL_OK,
                "the warning self-clears when the next cut is initiated");
    }

    /* ---------------- Unconfigured threshold fails closed ------------ */
    printf("\n-- unconfigured motion threshold (0) on a HEALTHY machine --\n");
    {
        /* A permissive default here would silently restore the original
         * open-loop behaviour on every machine that had not been commissioned. */
        Rig rig;
        rig.init(90, /*motionThresh*/ 0, /*coupled*/ true, 60);
        rig.armAndTrigger();
        rig.step(Z_CLEAR);
        rig.beginLashDriven();
        for (int i = 0; i < 60000; i++) rig.stepDriven();

        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_UNCONFIRMED,
                "healthy machine + threshold 0 is REFUSED (fails closed)");
        checkEq(rig.data.shared.elsStop.active, 1, "aborted back to stopped");
        check(rig.data.shared.elsStop.lastIdealAdvance == SENTINEL,
              "no phase correction on an unconfirmed take-up");
    }

    /* ---------------- Calibration refusals --------------------------- */
    printf("\n-- calibration refuses unless the machine is in a safe state --\n");
    {
        Rig rig;
        rig.init(90, 2, true, 60);
        rig.data.shared.elsStop.enable = 1;           /* a job is live */
        rig.data.shared.elsStop.calCommand = 1;
        rig.step(Z_CLEAR);
        checkEq(rig.data.shared.elsStop.calResult, ELS_CAL_ERR_ENABLED,
                "refuses while elsStop.enable is set");
        checkEq(rig.data.shared.elsStop.calCommand, 0,
                "calCommand consumed and cleared by the firmware");
        check(rig.data.shared.elsStop.calSeq >= 1, "refusal still bumps calSeq");
    }
    {
        Rig rig;
        rig.init(90, 2, true, 60);
        rig.data.shared.fastData.servoMode = 0;       /* steps would never drain */
        rig.data.shared.elsStop.calCommand = 1;
        rig.step(Z_CLEAR);
        checkEq(rig.data.shared.elsStop.calResult, ELS_CAL_ERR_SERVOMODE,
                "refuses when servoMode != 1");
    }
    {
        Rig rig;
        rig.init(90, /*motionThresh*/ 0, true, 60);
        rig.data.shared.elsStop.calCommand = 1;
        rig.step(Z_CLEAR);
        checkEq(rig.data.shared.elsStop.calResult, ELS_CAL_ERR_CONFIG,
                "refuses when the motion threshold is unconfigured");
    }

    /* ---------------- Calibration measures the lash ------------------ */
    printf("\n-- calibration run against a coupled drivetrain (lash = 60 steps) --\n");
    {
        Rig rig;
        rig.init(0, /*motionThresh*/ 2, /*coupled*/ true, /*lash*/ 60);
        rig.step(Z_CLEAR);
        rig.beginLashDriven();
        rig.data.shared.elsStop.calCommand = 1;

        uint16_t seq0 = rig.data.shared.elsStop.calSeq;
        for (int i = 0; i < 60000 && rig.data.shared.elsStop.calSeq == seq0; i++)
            rig.stepDriven();

        check(rig.data.shared.elsStop.calSeq == (uint16_t)(seq0 + 1), "run finished");
        checkEq(rig.data.shared.elsStop.calResult, ELS_CAL_OK, "calResult is OK");
        printf("      measured = %d, %d, %d (true lash 60)\n",
               (int)rig.data.shared.elsStop.calMeasured[0],
               (int)rig.data.shared.elsStop.calMeasured[1],
               (int)rig.data.shared.elsStop.calMeasured[2]);
        for (int i = 0; i < 3; i++) {
            char buf[80];
            snprintf(buf, sizeof buf, "measurement %d is within quantization of 60", i);
            int32_t m = rig.data.shared.elsStop.calMeasured[i];
            check(m >= 60 && m <= 60 + 3 * 3, buf);
        }
    }

    /* ---------------- Calibration on an open half-nut ---------------- */
    printf("\n-- calibration run with the half-nut OPEN --\n");
    {
        Rig rig;
        rig.init(0, 2, /*coupled*/ false, 60);
        rig.step(Z_CLEAR);
        rig.beginLashDriven();
        rig.data.shared.elsStop.calCommand = 1;

        uint16_t seq0 = rig.data.shared.elsStop.calSeq;
        for (int i = 0; i < 60000 && rig.data.shared.elsStop.calSeq == seq0; i++)
            rig.stepDriven();

        check(rig.data.shared.elsStop.calSeq == (uint16_t)(seq0 + 1),
              "run terminates rather than driving forever");
        checkEq(rig.data.shared.elsStop.calResult, ELS_CAL_ERR_NO_MOTION,
                "calResult is NO_MOTION");
    }

    printf("\n%s (%d failure%s)\n", failures ? "FAILED" : "PASSED",
           failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
