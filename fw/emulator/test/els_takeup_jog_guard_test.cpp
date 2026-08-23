/*
 * The take-up refuses to start in JOG mode, and ONLY in jog mode.
 *
 * THE HAZARD. servo.stepsToGo is consumed only by updateIndexingPosition,
 * which runs in servoMode 1. A take-up commanded in any other mode banks the
 * move and leaves takeupPending set, and takeupPending gates sync -- so the
 * machine sits with the spindle turning and nothing happening, for the ~5 s it
 * takes ELS_TAKEUP_TIMEOUT_TICKS to name it, and with sync still gated after
 * that. Recovery was an undocumented enable 1->0.
 *
 * WHY THE GUARD IS MODE 2 AND NOT `!= 1`. servoEnableTask auto-promotes the
 * mode to 1 whenever sync motion is enabled and the stop is not active -- and
 * that promotion deliberately SKIPS mode 2. So jog is the one mode nothing can
 * rescue, while mode 0 at a resume tick is ordinary: the task runs at ~100 ms
 * and this ISR at ~100 kHz, so a resume can land before the promotion without
 * anything being wrong. A blanket `servoMode != 1` refusal would break normal
 * cuts to guard a case that fixes itself within one task tick.
 *
 * That makes case 3 the most important one here. It is the negative bound --
 * it is what stops this guard from growing into the blanket version, and it
 * fails immediately if anyone widens the condition.
 *
 * The refusal returns the machine to stopped-at-the-shoulder with the
 * reference intact, so recovery is: leave jog, press Cut again (case 4).
 *
 * Drives the REAL Core/Src/Ramps.c ISR; stubs and fixture lifted verbatim from
 * els_manual_latch_test.cpp.
 *
 * MUTATION-TESTED 2026-08-22. Each was applied to Ramps.c alone, the failure
 * count observed, and the mutation reverted. All six killed.
 *
 *   J1 no guard at all (the pre-fix behavior)  -> 6 failures (1, 4)
 *   J2 blanket `servoMode != 1u`               -> 2 failures (3)
 *   J3 refusal wipes referenceLatched          -> 1 failure  (2)
 *   J4 refuse with no backlash configured      -> 2 failures (5)
 *   J5 no takeupSeq ack (silent refusal)       -> 1 failure  (1)
 *   J6 no active = 1 (machine left mid-resume) -> 3 failures (1, 4)
 *
 * J2 is the one that justifies this file's existence. The blanket condition is
 * what this guard was first written as, it looks strictly safer, and case 3 is
 * the only thing between it and refusing ordinary cuts. Note that under J2
 * case 1 still passes -- the mutation makes the guard look MORE correct by
 * every assertion except the negative bound.
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
/* Fixture — geometry copied from els_stop_resume_relatch_test.cpp     */
/* ------------------------------------------------------------------ */

static const int32_t SPINDLE_COUNTS_PER_PASS = 40;
static const int32_t Z_CLEAR    = 1000;
static const int32_t Z_PAST     = -5;
static const int32_t Z_STOP_POS = 0;
static const int16_t Z_STOP_DIR = -1;
static const float   SENTINEL   = 1.0e30f;
static const float   SYNC_NUM   = -2.0f;
static const float   SYNC_DEN   = 15.0f;

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int32_t            spindleCnt;
    int32_t            zCnt;

    void init(uint32_t backlashSteps, int32_t hysteresisCounts = 500) {
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

        data.shared.scales[0].syncRatioNum = (int32_t)SYNC_NUM;
        data.shared.scales[0].syncRatioDen = (int32_t)SYNC_DEN;
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
        data.shared.elsStop.hysteresis       = hysteresisCounts;
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

    /* One ISR pass. Advances the spindle; Z goes wherever the caller says. */
    void step(int32_t zTarget) {
        spindleCnt += SPINDLE_COUNTS_PER_PASS;
        zCnt        = zTarget;
        tim[0].CNT  = (uint32_t)spindleCnt;
        tim[1].CNT  = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }

    /* Arm a fresh job and settle: enable rising edge resets referenceLatched. */
    void armJob() {
        step(Z_CLEAR);
        data.shared.elsStop.enable = 1;
        for (int i = 0; i < 3; i++) step(Z_CLEAR);
    }
};

static int failures = 0;

static void check(bool ok, const char *what) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) failures++;
}

/* Take-up big enough to be unmistakable, small enough to finish fast. */
static const uint32_t LASH_STEPS = 100;

/* Drive a rig from armed to the moment AFTER a software resume, leaving the
 * caller to inspect what the resume tick did. servoMode is set just before the
 * resume so the take-up decision sees it. */
static void resumeInMode(Rig &rig, uint16_t servoMode) {
    rig.armJob();
    rig.step(Z_PAST);          /* feed past the stop... */
    rig.step(Z_PAST);          /* ...trigger reads the previous pass's Z */
    check(rig.data.shared.elsStop.active == 1, "stopped at the shoulder");
    rig.step(Z_CLEAR);         /* retract clear of the stop */

    rig.data.shared.fastData.servoMode = servoMode;
    rig.data.shared.elsStop.active     = 0;   /* SW resume: "go cut" */
    rig.step(Z_CLEAR);
}

int main() {
    printf("=== take-up refuses in JOG mode (and only there) ===\n\n");

    /* ---------------- 1. REFUSE in jog ---------------------------------- */
    /* MUTATION: delete the jog branch entirely -> every assertion here fails
     * and the machine is back to banking a move nothing will consume. */
    printf("-- 1. servoMode 2: refused, machine returned to the shoulder --\n");
    {
        Rig rig;
        rig.init(LASH_STEPS);
        uint16_t seqBefore = rig.data.shared.elsStop.takeupSeq;
        resumeInMode(rig, 2);

        check(rig.data.shared.elsStop.takeupPending == 0,
              "no take-up started (sync is NOT gated on a move nothing consumes)");
        check(rig.data.shared.elsStop.active == 1,
              "back to stopped-at-shoulder, exactly where Cut was pressed");
        check(rig.data.shared.elsStop.takeupResult == ELS_CAL_ERR_SERVOMODE,
              "takeupResult names the servo mode as the cause");
        check(rig.data.shared.elsStop.takeupSeq == (uint16_t)(seqBefore + 1),
              "takeupSeq acked once, so the host sees an OUTCOME not silence");
        check(rig.data.shared.servo.stepsToGo == 0,
              "no banked move left to execute whenever the mode next changes");
    }

    /* ---------------- 2. THE REFERENCE SURVIVES ------------------------- */
    /* A refusal must cost the operator nothing. Wiping referenceLatched would
     * discard the thread phase and make the guard worse than the hazard.
     * MUTATION: clear referenceLatched in the refusal branch -> fails here. */
    printf("\n-- 2. the refusal costs nothing: reference intact --\n");
    {
        Rig rig;
        rig.init(LASH_STEPS);
        resumeInMode(rig, 2);

        check(rig.data.shared.elsStop.referenceLatched == 1,
              "thread reference survives the refusal (retry is free)");
        check(rig.data.shared.elsStop.enable == 1, "the job is still live");
    }

    /* ---------------- 3. MODE 0 IS NOT REFUSED (the negative bound) ----- */
    /* THE CASE THAT KEEPS THE GUARD HONEST. servoEnableTask promotes mode 0 to
     * 1 on its own ~100 ms cadence, so a resume tick that finds mode 0 is
     * ordinary rather than broken. Widening the guard to `servoMode != 1`
     * would refuse a perfectly normal cut.
     * MUTATION: change the condition to `!= 1u` -> this case fails and case 1
     * still passes, which is exactly the confusion it exists to prevent. */
    printf("\n-- 3. servoMode 0: NOT refused, the take-up proceeds --\n");
    {
        Rig rig;
        rig.init(LASH_STEPS);
        resumeInMode(rig, 0);

        check(rig.data.shared.elsStop.takeupPending == 1,
              "take-up STARTED in mode 0 -- the mode promotion may be one task tick away");
        check(rig.data.shared.elsStop.takeupResult != ELS_CAL_ERR_SERVOMODE,
              "and it was not refused for the servo mode");
    }

    /* ---------------- 3b. MODE 1: the ordinary path ---------------------- */
    printf("\n-- 3b. servoMode 1: the ordinary take-up, untouched --\n");
    {
        Rig rig;
        rig.init(LASH_STEPS);
        resumeInMode(rig, 1);

        check(rig.data.shared.elsStop.takeupPending == 1, "take-up started");
        check(rig.data.shared.servo.stepsToGo != 0, "and it commanded a move");
    }

    /* ---------------- 4. RECOVERY --------------------------------------- */
    /* The refusal must not wedge anything: leave jog, press Cut again. If this
     * fails the guard has traded a 5 s hang for a permanent one. */
    printf("\n-- 4. recovery: leave jog, press Cut again --\n");
    {
        Rig rig;
        rig.init(LASH_STEPS);
        resumeInMode(rig, 2);
        check(rig.data.shared.elsStop.takeupPending == 0, "refused, as in case 1");

        rig.data.shared.fastData.servoMode = 1;
        rig.data.shared.elsStop.active     = 0;   /* press Cut again */
        rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.takeupPending == 1,
              "the retry starts a take-up normally");
        check(rig.data.shared.servo.stepsToGo != 0, "and commands the move");
    }

    /* ---------------- 5. NO BACKLASH CONFIGURED ------------------------- */
    /* With backlashSteps == 0 there is no take-up to refuse, and the inline
     * phase-correction branch must still be reachable in jog mode. The guard
     * is chained ahead of that branch, so a mis-chained condition would
     * swallow it -- which is precisely how a refusal grows into a regression
     * nobody notices.
     * MUTATION: drop `backlashSteps != 0u` from the jog condition -> fails
     * here, because the refusal captures a case that has no take-up at all. */
    printf("\n-- 5. no backlash configured: nothing to refuse --\n");
    {
        Rig rig;
        rig.init(0);
        uint16_t seqBefore = rig.data.shared.elsStop.takeupSeq;
        resumeInMode(rig, 2);

        check(rig.data.shared.elsStop.takeupSeq == seqBefore,
              "no spurious outcome reported when there is no take-up");
        check(rig.data.shared.elsStop.takeupResult != ELS_CAL_ERR_SERVOMODE,
              "and no servo-mode refusal");
    }

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
