/*
 * ISR-level tests: the thread reference dies when the drive de-energises.
 *
 * THE RULE, in Evan's words (2026-08-31): "the instant the drive is
 * de-energized we've lost custody of the leadscrew position, period. That's
 * when the ref is invalidated."
 *
 * This firmware has no leadscrew feedback -- it knows only the steps it
 * commanded -- so its model of leadscrew position holds exactly as long as the
 * drive is holding the screw. servoMode == 0 takes ENA high in servoEnableTask
 * and de-energises it; from that instant the position is unknown and a thread
 * phase datum measured against it is worthless.
 *
 * THE DEFECT THIS CLOSES. referenceLatched was cleared ONLY on the elsStop
 * enable 0->1 edge (Ramps.c, "Reset reference latch on ... rising edge"), and
 * stop_sync() on the host clears syncEnable and nothing else. So sync off and
 * back on WITHOUT an engage cycle carried a stale reference across a window in
 * which custody was lost, and the UI went on reporting REF LATCHED. That is
 * the path the 2026-08-30 bench run walked.
 *
 * WHY A LEVEL AND NOT AN EDGE, which is the design decision these cases exist
 * to pin. Custody is a STATE, so the invariant is "no custody, no reference".
 * An edge rule ("clear when servoMode falls to 0") expresses an EVENT and can
 * be defeated by any path that arrives at servoMode == 0 without the ISR
 * observing the transition. Case 5 is the discriminator: it starts from an
 * already-de-energised drive and asserts a reference cannot survive there. An
 * edge implementation passes every other case in this file and fails that one.
 *
 * WHAT MUST NOT BREAK, and case 2 and 3 are here because getting this wrong
 * would be worse than the defect:
 *   * JOG. servoMode == 2 keeps ENA low, so the drive is still energised and
 *     custody is unbroken. Triggering on syncEnable instead of servoMode would
 *     clear the reference on every jog, because entering jog clears syncEnable
 *     (dispatchers/els.py: sync follows servoMode == 1 only).
 *   * THE NORMAL PASS CYCLE. A pass ends with the firmware setting
 *     elsStop.active = 1; servoMode is untouched and the drive keeps holding
 *     the carriage -- that hold IS the stop. The firmware never writes
 *     servoMode = 0 anywhere (only the re-assert to 1); the host writes it at
 *     exactly three sites, all of which end the job. If this test ever starts
 *     clearing between passes, threading is broken outright.
 *
 * Cases 6 and 7 cover the paired latch gate: a latch requested with the drive
 * off is refused through the EXISTING channel (latchCommand consumed, no
 * latchSeq ack, host reads the missing edge as the refusal) rather than being
 * accepted and silently wiped by the invariant a moment later.
 *
 * Same external-stub strategy and fixture geometry as
 * els_manual_latch_test.cpp.
 */

extern "C" {
#include "Ramps.h"
#include "Scales.h"
#include "emulator_state.h"
}

#include <cstdio>
#include <cstdint>
#include <cstring>

/* ------------------------------------------------------------------ */
/* Shim / HAL / RTOS / Modbus stubs -- Ramps.c's complete external set. */
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
/* Fixture -- geometry copied from els_manual_latch_test.cpp           */
/* ------------------------------------------------------------------ */

static const int32_t SPINDLE_COUNTS_PER_PASS = 40;
static const int32_t Z_CLEAR    = 1000;
static const int32_t Z_STOP_POS = 0;
static const int16_t Z_STOP_DIR = -1;
static const float   SYNC_NUM   = -2.0f;
static const float   SYNC_DEN   = 15.0f;

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int32_t            spindleCnt;
    int32_t            zCnt;

    void init() {
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
        data.shared.elsStop.backlashSteps    = 0;
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

    void armJob() {
        step(Z_CLEAR);
        data.shared.elsStop.enable = 1;
        for (int i = 0; i < 3; i++) step(Z_CLEAR);
    }

    /* Establish a reference the way the operator's re-sync does, with the
     * drive energised -- which is the only way the firmware now accepts one. */
    void latchReference() {
        data.shared.fastData.servoMode = 1;
        data.shared.elsStop.latchCommand = 1;
        step(Z_CLEAR);
    }
};

static int failures = 0;

static void check(bool ok, const char *what) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) failures++;
}

int main() {
    printf("=== ELS drive custody: no drive, no thread reference ===\n\n");

    /* ---------------- 1. THE DEFECT ------------------------------------ */
    /* MUTATION: delete the servoMode == 0 clear in Ramps.c -> this fails and
     * so do cases 4 and 5. */
    printf("-- 1. dropping the drive clears the reference --\n");
    {
        Rig rig;
        rig.init();
        rig.armJob();
        rig.latchReference();
        check(rig.data.shared.elsStop.referenceLatched == 1,
              "precondition: a reference is latched with the drive on");

        rig.data.shared.fastData.servoMode = 0;   /* operator kills sync */
        rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.referenceLatched == 0,
              "reference cleared once the drive de-energises");
    }

    /* ---------------- 2. NEGATIVE CONTROL: JOG ------------------------- */
    /* servoMode 2 keeps ENA low. Custody is unbroken, so the reference must
     * survive. MUTATION: trigger on syncEnable, or on servoMode != 1, and this
     * fails. */
    printf("\n-- 2. jog does NOT clear it (drive stays energised) --\n");
    {
        Rig rig;
        rig.init();
        rig.armJob();
        rig.latchReference();

        rig.data.shared.fastData.servoMode = 2;   /* jog */
        for (int i = 0; i < 5; i++) rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.referenceLatched == 1,
              "reference survives jog");
    }

    /* ---------------- 3. NEGATIVE CONTROL: PASS CYCLE ------------------ */
    /* A pass ends with active = 1 and servoMode untouched. If this ever goes
     * red, threading is broken -- the reference is what puts the next pass in
     * the same groove. */
    printf("\n-- 3. a normal stopped-at-shoulder pass does NOT clear it --\n");
    {
        Rig rig;
        rig.init();
        rig.armJob();
        rig.latchReference();

        rig.data.shared.elsStop.active = 1;       /* stopped at the shoulder */
        for (int i = 0; i < 20; i++) rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.referenceLatched == 1,
              "reference survives being stopped at the shoulder");
        check(rig.data.shared.fastData.servoMode == 1,
              "and the drive was never dropped by the firmware");
    }

    /* ---------------- 4. THE OFFSET DIES WITH THE DATUM ---------------- */
    printf("\n-- 4. phaseOffsetSteps dies with the reference --\n");
    {
        Rig rig;
        rig.init();
        rig.armJob();
        rig.latchReference();
        rig.data.shared.elsStop.phaseOffsetSteps = 250;

        rig.data.shared.fastData.servoMode = 0;
        rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.phaseOffsetSteps == 0,
              "offset cleared -- it is meaningless without its datum");
    }

    /* ---------------- 5. LEVEL, NOT EDGE ------------------------------- */
    /* THE DISCRIMINATOR. Starts from an already-de-energised drive, so no
     * falling transition occurs during the test. An edge implementation passes
     * every other case in this file and fails this one. */
    printf("\n-- 5. a reference cannot EXIST while the drive is off --\n");
    {
        Rig rig;
        rig.init();
        rig.armJob();
        rig.data.shared.fastData.servoMode = 0;
        rig.step(Z_CLEAR);                        /* settle; no edge from here on */

        /* Plant one directly, as a stale value from before a reset would be. */
        rig.data.shared.elsStop.referenceLatched = 1;
        rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.referenceLatched == 0,
              "cleared with no falling edge in sight (invariant, not event)");
    }

    /* ---------------- 6. LATCH REFUSED WITH THE DRIVE OFF -------------- */
    /* Refused through the EXISTING channel: command consumed, no latchSeq ack,
     * host reads the missing edge as the refusal. */
    printf("\n-- 6. a latch requested with the drive off is refused --\n");
    {
        Rig rig;
        rig.init();
        rig.armJob();
        rig.data.shared.fastData.servoMode = 0;
        rig.step(Z_CLEAR);

        uint16_t seqBefore = rig.data.shared.elsStop.latchSeq;
        rig.data.shared.elsStop.latchCommand = 1;
        rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.latchCommand == 0,
              "command consumed in one pass");
        check(rig.data.shared.elsStop.latchSeq == seqBefore,
              "NO latchSeq ack -- the missing edge is the refusal");
        check(rig.data.shared.elsStop.referenceLatched == 0,
              "and no reference was created");
    }

    /* ---------------- 7. THE GATE DID NOT BREAK PICK-UP ---------------- */
    /* Picking up an existing thread seats the carriage against a HELD
     * leadscrew, so it runs with the drive energised. Regression guard for the
     * servoMode gate added in case 6. */
    printf("\n-- 7. a latch with the drive ON still works --\n");
    {
        Rig rig;
        rig.init();
        rig.armJob();

        uint16_t seqBefore = rig.data.shared.elsStop.latchSeq;
        rig.data.shared.fastData.servoMode = 1;
        rig.data.shared.elsStop.latchCommand = 1;
        rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.referenceLatched == 1,
              "reference latched");
        check(rig.data.shared.elsStop.latchSeq == (uint16_t)(seqBefore + 1),
              "and acked exactly once");
    }

    printf("\n%s (%d failure%s)\n", failures ? "FAILED" : "ALL PASS",
           failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
