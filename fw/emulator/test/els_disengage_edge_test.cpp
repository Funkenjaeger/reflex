/*
 * KNOWN-FAILING REPRODUCTION — disengage banks a backlash takeup.
 *
 * WHAT THIS TEST CLAIMS
 * ---------------------
 * Dropping `elsStop.enable` while the carriage is held at the stop
 * (`active == 1`) after a threading pass does not leave the machine inert.
 * The enable-fall handler clears `active` — and that self-made 1->0 edge is
 * then seen by the resume detector LATER IN THE SAME ISR PASS, which
 * initiates a full backlash takeup (or, with backlashSteps == 0, applies a
 * phase correction) for a job the operator just ended.
 *
 * WHY (static reading of Core/Src/Ramps.c, confirmed by execution here)
 * --------------------------------------------------------------------
 * Within one ISR pass, in this order:
 *
 *   1. Ramps.c:553  enable-fall handler:
 *          active = 0; takeupPending = 0;   // "abandon any in-flight takeup"
 *   2. Ramps.c:826  resume detector:
 *          if (elsStopPreviousActive && !active) { ...initiate takeup... }
 *      `elsStopPreviousActive` is not updated until Ramps.c:876, so the
 *      detector cannot tell a SOFTWARE resume ("go cut") from the edge the
 *      enable-fall handler itself just created ("job is over").
 *
 * The guard conditions are all satisfied on the NORMAL end of a threading
 * job: referenceLatched is set at stop-fire (Ramps.c:767) and cleared only
 * on the next enable RISING edge (Ramps.c:548); thread geometry and
 * backlashSteps are nonzero on any commissioned threading setup. So every
 * disengage that follows a completed pass re-sets the takeupPending the
 * escape hatch cleared microseconds earlier, and banks a takeup move in
 * `servo.stepsToGo`:
 *   - with servoMode still 1 at that instant (the UI's stop-feed write lands
 *     a serial transaction AFTER its enable write), the move EXECUTES;
 *   - with servoMode 0, the move sits as debt and executes at the next
 *     feed/jog-to-sync enable, as an unexplained lurch in the old cutting
 *     direction.
 *
 * This is INDEPENDENT of scales[].syncEnable — the 2026-08-16 UI fix
 * (sync cleared before enable everywhere) does not reach it. Found by the
 * independent architecture review of 2026-08-16 (finding F2).
 *
 * TEST SHAPE (defect cases + passing control)
 * -------------------------------------------
 * CONTROL: the legitimate resume path (SW writes active = 0 with enable
 * still 1, carriage retracted clear of the hysteresis gate) must initiate a
 * takeup. Proves this harness can observe the resume machinery running —
 * without it, a green defect case would only prove the harness is blind.
 *
 * DEFECT A (mode 1): disengage from held; takeup must NOT initiate and the
 * servo must NOT move. Fails on unfixed code: takeupPending re-latches, a
 * takeup is banked, and pulses emit.
 *
 * DEFECT B (mode 0): same disengage with the feed already off; then the
 * feed is re-enabled later. No stored move may execute. Fails on unfixed
 * code: the banked stepsToGo drains on re-enable (the deferred lurch).
 *
 * DEFECT C (backlashSteps == 0): the inline phase-correction branch must
 * not run on disengage either. SENTINEL trick on lastIdealAdvance.
 *
 * Rig copied from els_stop_resume_relatch_test.cpp (same geometry, same
 * stub-the-externals pattern; drives the REAL SynchroRefreshTimerIsr).
 * Nothing under Core/ is modified by this test.
 *
 * EXPECTED RESULT: FAILS (exit 1) until the enable-fall handler consumes
 * the edge it creates. Registered in CTest deliberately, as a reproduction.
 */

extern "C" {
#include "Ramps.h"
#include "Scales.h"
#include "emulator_state.h"
}

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdarg>

/* ------------------------------------------------------------------ */
/* Shim / HAL / RTOS / Modbus stubs — Ramps.c's complete external set.  */
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

/* ------------------------------------------------------------------ */
/* Fixture — geometry identical to els_stop_resume_relatch_test.cpp    */
/* ------------------------------------------------------------------ */

static const int32_t SPINDLE_COUNTS_PER_PASS = 40;
static const int32_t Z_CLEAR    = 1000;
static const int32_t Z_PAST     = -5;
static const int32_t Z_STOP_POS = 0;
static const int16_t Z_STOP_DIR = -1;
static const float   SENTINEL   = 1.0e30f;

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int32_t            spindleCnt;
    int32_t            zCnt;

    void init(uint32_t backlashSteps) {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        spindleCnt = 0;
        zCnt       = Z_CLEAR;
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
        data.shared.scales[1].syncEnable   = 0;

        data.shared.servo.maxSpeed     = 100000.0f;
        data.shared.servo.acceleration = 50000.0f;
        data.shared.servo.servoDir     = 1;
        data.shared.fastData.servoMode = 1;

        data.shared.elsStop.scaleIndex       = 1;
        data.shared.elsStop.stopPosition     = Z_STOP_POS;
        data.shared.elsStop.stopDirection    = Z_STOP_DIR;
        data.shared.elsStop.threadPitchSteps = 533.333f;
        data.shared.elsStop.zCountsPerPitch  = 846.667f;
        data.shared.elsStop.backlashSteps    = backlashSteps;
        data.shared.elsStop.hysteresis       = 500;
        data.shared.elsStop.enable           = 0;

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
        spindleCnt += SPINDLE_COUNTS_PER_PASS;
        zCnt        = zTarget;
        tim[0].CNT  = (uint32_t)spindleCnt;
        tim[1].CNT  = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }

    /* Arm a job, feed past the stop, latch. Returns true when the machine is
     * in the held-at-shoulder state every scenario starts from:
     * enable == 1, active == 1, referenceLatched == 1, takeupPending == 0. */
    bool armAndLatch() {
        step(Z_CLEAR);                       /* settle, enable still 0 */
        data.shared.elsStop.enable = 1;      /* rising edge resets referenceLatched */
        for (int i = 0; i < 3; i++) step(Z_CLEAR);
        bool armedClean = (data.shared.elsStop.active == 0);
        step(Z_PAST);                        /* trigger reads Z from the previous */
        step(Z_PAST);                        /* pass: two passes to latch */
        return armedClean
            && data.shared.elsStop.active == 1
            && data.shared.elsStop.referenceLatched == 1
            && data.shared.elsStop.takeupPending == 0;
    }

    /* The UI's disengage, in its FIXED (2026-08-16) wire order: sync cleared
     * on every scale FIRST, then enable dropped. servoMode is whatever the
     * scenario set — the UI's stop-feed write lands after the enable write. */
    void disengage() {
        for (int i = 0; i < SCALES_COUNT; i++)
            data.shared.scales[i].syncEnable = 0;
        data.shared.elsStop.enable = 0;
    }
};

/* ------------------------------------------------------------------ */

static int failures = 0;

static void check(bool ok, const char *fmt, ...) {
    va_list ap;
    printf("[%s] ", ok ? "PASS" : "FAIL");
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
    if (!ok) failures++;
}

int main() {
    printf("=== ELS disengage: enable-fall edge vs. same-pass resume detector ===\n\n");
    const uint32_t BACKLASH = 300;

    /* ---- CONTROL: the legitimate resume path still initiates takeup ---- */
    /* SW resume: retract clear (beyond hysteresis), write active = 0 with
     * enable STILL 1. The resume detector must fire — this is the feature. */
    {
        Rig rig;
        rig.init(BACKLASH);
        bool armed = rig.armAndLatch();
        rig.step(Z_CLEAR);                       /* retract clear of the stop */
        rig.data.shared.elsStop.active = 0;      /* SW resume, enable == 1 */
        rig.step(Z_CLEAR);
        check(armed, "CONTROL: preconditions armed");
        check(rig.data.shared.elsStop.takeupPending == 1,
              "CONTROL: SW resume initiates takeup (takeupPending == 1), observed %u",
              (unsigned)rig.data.shared.elsStop.takeupPending);
    }

    /* ---- DEFECT A: disengage from held, feed still commanded (mode 1) ---- */
    /* The UI's stop-feed write is a LATER serial transaction; at the enable
     * fall the firmware still holds servoMode == 1. Nothing may move. */
    {
        Rig rig;
        rig.init(BACKLASH);
        bool armed = rig.armAndLatch();
        int32_t stepsToGoBefore  = rig.data.shared.servo.stepsToGo;
        uint32_t servoPosBefore  = rig.data.shared.servo.currentSteps;
        rig.disengage();
        rig.step(Z_PAST);                        /* the pass with both edges */
        uint16_t pendingAfterPass = rig.data.shared.elsStop.takeupPending;
        int32_t  stepsToGoAfter   = rig.data.shared.servo.stepsToGo;
        for (int i = 0; i < 50; i++) rig.step(Z_PAST);   /* let anything banked run */
        uint32_t servoPosAfter    = rig.data.shared.servo.currentSteps;

        check(armed, "DEFECT A: preconditions armed");
        check(pendingAfterPass == 0,
              "DEFECT A: disengage must not re-latch takeupPending, observed %u",
              (unsigned)pendingAfterPass);
        check(stepsToGoAfter == stepsToGoBefore,
              "DEFECT A: disengage must not bank a takeup move, stepsToGo %d -> %d",
              (int)stepsToGoBefore, (int)stepsToGoAfter);
        check(servoPosAfter == servoPosBefore,
              "DEFECT A: servo must not move on disengage, currentSteps %u -> %u",
              (unsigned)servoPosBefore, (unsigned)servoPosAfter);
    }

    /* ---- DEFECT B: disengage with feed off, then feed re-enabled later ---- */
    /* The deferred-lurch variant: any debt banked at disengage executes the
     * next time servoMode goes nonzero. */
    {
        Rig rig;
        rig.init(BACKLASH);
        bool armed = rig.armAndLatch();
        rig.data.shared.fastData.servoMode = 0;  /* feed already off */
        rig.disengage();
        rig.step(Z_PAST);
        int32_t bankedDebt = rig.data.shared.servo.stepsToGo;
        uint32_t servoPosBefore = rig.data.shared.servo.currentSteps;
        rig.data.shared.fastData.servoMode = 1;  /* operator enables a feed later */
        for (int i = 0; i < 50; i++) rig.step(Z_PAST);
        uint32_t servoPosAfter  = rig.data.shared.servo.currentSteps;

        check(armed, "DEFECT B: preconditions armed");
        check(bankedDebt == 0,
              "DEFECT B: disengage with feed off must bank no debt, stepsToGo == %d",
              (int)bankedDebt);
        check(servoPosAfter == servoPosBefore,
              "DEFECT B: later feed-enable must not execute a stored move, "
              "currentSteps %u -> %u",
              (unsigned)servoPosBefore, (unsigned)servoPosAfter);
    }

    /* ---- DEFECT C: backlashSteps == 0, inline phase-correction branch ---- */
    {
        Rig rig;
        rig.init(0);
        bool armed = rig.armAndLatch();
        rig.data.shared.elsStop.lastIdealAdvance = SENTINEL;
        rig.disengage();
        rig.step(Z_PAST);
        bool correctionRan =
            (rig.data.shared.elsStop.lastIdealAdvance != SENTINEL);

        check(armed, "DEFECT C: preconditions armed");
        check(!correctionRan,
              "DEFECT C: disengage must not apply a phase correction, observed %s",
              correctionRan ? "it ran" : "it did not run");
    }

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
