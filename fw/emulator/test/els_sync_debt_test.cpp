/*
 * KNOWN-FAILING REPRODUCTION — sync accumulation is unbounded motion debt.
 *
 * WHAT THIS TEST CLAIMS
 * ---------------------
 * `scales[].syncEnable` accumulates spindle deltas into
 * `servo.desiredSteps` (Ramps.c sync block) gated on the hold conditions
 * (!active, !takeupPending, cal idle) — but NOT on `servoMode`. Pulse
 * emission IS gated on `servoMode != 0`. So any state with sync enabled
 * and the servo commanded off (mode 0), or in jog (mode 2), banks motion
 * debt without bound while the spindle turns — and the servo chases the
 * ENTIRE accumulated backlog the moment mode next becomes 1. On the real
 * machine that is the unexplained lurch/runaway component: the measured
 * 1.825 mm disengage runaway was still accelerating precisely because a
 * backlog was being chased.
 *
 * Sync counts that arrive while the machine is not in sync-follow mode
 * are not a debt to repay later — they must be DISCARDED. Threading does
 * not rely on banked deltas across a pause: the resume machinery
 * re-syncs phase from the SCALE positions (that is what it is for).
 *
 * Review finding F1 (independent architecture review, 2026-08-16).
 *
 * TEST SHAPE (defect cases + passing control)
 * -------------------------------------------
 * CONTROL: sync-follow in mode 1 works — desiredSteps advances with the
 * spindle and pulses emit. Proves the harness observes the sync path.
 *
 * DEFECT A (mode 0): spindle turns with sync enabled and the servo off.
 * No debt may accumulate, and enabling the feed afterwards with the
 * spindle STOPPED must move nothing.
 *
 * DEFECT B (mode 2): same, in jog — sync deltas during jog must not
 * superimpose on the jog nor bank for later.
 *
 * Rig copied from els_disengage_edge_test.cpp / els_stop_resume_relatch_
 * test.cpp (same geometry, same stub-the-externals pattern; drives the
 * REAL SynchroRefreshTimerIsr). Nothing under Core/ is modified.
 *
 * EXPECTED RESULT: FAILS (exit 1) until accumulation is gated on
 * servoMode == 1. Registered in CTest deliberately, as a reproduction.
 *
 * ------------------------------------------------------------------------
 * STATUS UPDATE — the defect described above is FIXED.
 *
 * The sync accumulation gate in Core/Src/Ramps.c now carries a
 * servoMode == 1 term, putting accumulation and emission on the same
 * gate. The DEFECT assertions were written to fail and now PASS; they
 * are left exactly as authored — a repro that flips green is the proof.
 * ------------------------------------------------------------------------
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
/* Fixture — geometry identical to els_disengage_edge_test.cpp, minus   */
/* the ELS stop (enable stays 0: this is the plain power-feed context). */
/* ------------------------------------------------------------------ */

static const int32_t SPINDLE_COUNTS_PER_PASS = 40;

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int32_t            spindleCnt;

    void init() {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        spindleCnt = 0;
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
        data.shared.fastData.servoMode = 0;
        data.shared.elsStop.enable     = 0;

        tim[0].CNT = 0;
        data.scalesDeltaPos[0].position = 0;
        data.shared.scales[0].position  = 0;
        data.scalesSyncDeltaPos[0].oldPosition = 0;
    }

    void step(int32_t spindleAdvance) {
        spindleCnt += spindleAdvance;
        tim[0].CNT  = (uint32_t)spindleCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }

    int32_t debt() const {
        return (int32_t)data.shared.servo.desiredSteps
             - (int32_t)data.shared.servo.currentSteps;
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
    printf("=== ELS sync debt: accumulation vs. emission gating ===\n\n");

    /* ---- CONTROL: sync-follow in mode 1 works ---- */
    {
        Rig rig;
        rig.init();
        rig.data.shared.fastData.servoMode = 1;
        rig.step(0);                              /* settle pass, zero delta */
        int32_t desiredBefore = (int32_t)rig.data.shared.servo.desiredSteps;
        for (int i = 0; i < 30; i++) rig.step(SPINDLE_COUNTS_PER_PASS);
        int32_t desiredAfter  = (int32_t)rig.data.shared.servo.desiredSteps;
        uint32_t emitted = rig.data.shared.servo.currentSteps;

        check(desiredAfter != desiredBefore,
              "CONTROL: sync accumulates in mode 1 (desiredSteps %d -> %d)",
              (int)desiredBefore, (int)desiredAfter);
        check(emitted != 0,
              "CONTROL: pulses emit in mode 1 (currentSteps moved %d)",
              (int)(int32_t)emitted);
    }

    /* ---- DEFECT A: mode 0 — spindle turns, servo off ---- */
    {
        Rig rig;
        rig.init();
        rig.data.shared.fastData.servoMode = 0;
        rig.step(0);
        for (int i = 0; i < 100; i++) rig.step(SPINDLE_COUNTS_PER_PASS);
        int32_t banked = rig.debt();

        check(banked == 0,
              "DEFECT A: no debt accumulates with the servo off, observed %d steps",
              (int)banked);

        /* Operator enables the feed AFTER the spindle has stopped. Nothing
         * may move: there is no live sync delta, only (on unfixed code)
         * the banked backlog. */
        uint32_t posBefore = rig.data.shared.servo.currentSteps;
        rig.data.shared.fastData.servoMode = 1;
        for (int i = 0; i < 600; i++) rig.step(0);
        uint32_t posAfter = rig.data.shared.servo.currentSteps;

        check(posAfter == posBefore,
              "DEFECT A: feed-enable after the fact executes nothing, "
              "currentSteps %u -> %u",
              (unsigned)posBefore, (unsigned)posAfter);
    }

    /* ---- DEFECT B: mode 2 — jog with sync armed ---- */
    {
        Rig rig;
        rig.init();
        rig.data.shared.fastData.servoMode = 2;
        rig.step(0);
        int32_t debtBefore = rig.debt();
        for (int i = 0; i < 100; i++) rig.step(SPINDLE_COUNTS_PER_PASS);
        int32_t debtAfter = rig.debt();

        check(debtAfter == debtBefore,
              "DEFECT B: spindle motion during jog banks nothing, debt %d -> %d",
              (int)debtBefore, (int)debtAfter);

        uint32_t posBefore = rig.data.shared.servo.currentSteps;
        rig.data.shared.fastData.servoMode = 1;
        for (int i = 0; i < 600; i++) rig.step(0);
        uint32_t posAfter = rig.data.shared.servo.currentSteps;

        check(posAfter == posBefore,
              "DEFECT B: leaving jog for sync executes no stored move, "
              "currentSteps %u -> %u",
              (unsigned)posBefore, (unsigned)posAfter);
    }

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
