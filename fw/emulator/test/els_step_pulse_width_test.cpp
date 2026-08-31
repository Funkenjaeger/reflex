/*
 * STEP pulse width instrument: min-hold + runt count (Ramps.h tail).
 *
 * The measurement is a DWT delta between the emission-site set and the next
 * entry's timestamp. The emulator's DWT is a plain struct the harness
 * advances, so widths here are DETERMINISTIC: the inter-tick CYCCNT
 * increment IS the modelled tick spacing, and shrinking it models exactly
 * what an overrun does on hardware -- the next entry (and its pin reset)
 * arriving early relative to a late set. This file tests the MEASUREMENT
 * MECHANICS, not the overrun physics; els_resync_powered_test and the
 * machine own those.
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
/* Fixture: minimal jog-free configuration that makes the servo emit. */

static int failures = 0;
static void check(bool ok, const char *what) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) failures++;
}

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];

    void init() {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        servoCycles        = 1;
        servoCyclesCounter = 0;
        for (int i = 0; i < SCALES_COUNT; i++) {
            htim[i].Instance = &tim[i];
            ramps_timer_handles[i] = &htim[i];
            data.shared.scales[i].timerHandleSlot = (uint32_t)i;
            data.shared.scales[i].scaleDir     = 1;
            data.shared.scales[i].syncRatioNum = 1;
            data.shared.scales[i].syncRatioDen = 100;
        }
        data.shared.servo.maxSpeed     = 100000.0f;
        data.shared.servo.acceleration = 20000000.0f;
        data.shared.servo.servoDir     = 1;
        data.shared.fastData.servoMode = 1;
    }

    /* One ISR pass with a chosen inter-tick CYCCNT spacing. */
    void step(uint32_t tickCycles) {
        emu_dwt.CYCCNT += tickCycles;
        SynchroRefreshTimerIsr(&data);
    }

    /* One guaranteed pulse on the NEXT tick: drive desiredSteps directly.
     * Routing demand through stepsToGo instead rides the acceleration ramp,
     * which emits nothing for its first ~30 ticks -- the first draft of this
     * file did exactly that, and its "runt" tick had no pulse armed while
     * two of its healthy sections passed vacuously. The emitter chases
     * desiredSteps - currentSteps with no ramp in the way. */
    void demandOne() { data.shared.servo.desiredSteps += 1; }
};

int main() {
    printf("=== STEP pulse width: min-hold + runt count ===\n\n");

    /* -------- 1. a healthy pulse is measured, and only when one exists --- */
    printf("-- 1. width is measured only for pulses that happened --\n");
    {
        Rig r; r.init();
        for (int i = 0; i < 5; i++) r.step(1000);   /* idle: no emission */
        check(r.data.shared.elsStop.stepPulseMinCycles == 0,
              "no pulses -> min stays 0 (the nothing-measured sentinel)");
        check(r.data.shared.elsStop.stepPulseRuntCount == 0,
              "no pulses -> no runts");

        for (int i = 0; i < 40; i++) { r.demandOne(); r.step(1000); }
        uint32_t min1 = r.data.shared.elsStop.stepPulseMinCycles;
        printf("   min width at 1000-cycle ticks: %u\n", (unsigned)min1);
        check(min1 > ELS_STEP_RUNT_CYCLES && min1 <= 1000,
              "healthy spacing measures a healthy width");
        check(r.data.shared.elsStop.stepPulseRuntCount == 0,
              "healthy widths count no runts");
    }

    /* -------- 2. compressed entry -> runt measured and counted ----------- */
    printf("\n-- 2. a compressed next entry squeezes the measured width --\n");
    {
        Rig r; r.init();
        for (int i = 0; i < 10; i++) { r.demandOne(); r.step(1000); }
        uint32_t runts0 = r.data.shared.elsStop.stepPulseRuntCount;
        check(r.data.shared.elsStop.stepPulseMinCycles > 0,
              "pulses really were emitted before the compressed gap");

        /* One pulse whose NEXT entry arrives only 100 cycles later -- the
         * hardware shape of a pending-overrun back-to-back re-entry. */
        r.demandOne();
        r.step(1000);        /* emits, arms */
        r.step(100);         /* early entry: width ~100 < 250 */
        uint32_t min2 = r.data.shared.elsStop.stepPulseMinCycles;
        printf("   min after one compressed gap: %u\n", (unsigned)min2);
        check(min2 <= 100 + 32, "the runt width was captured by the min-hold");
        check(r.data.shared.elsStop.stepPulseRuntCount == runts0 + 1,
              "and counted exactly once");
    }

    /* -------- 3. min-hold semantics ------------------------------------- */
    printf("\n-- 3. the min HOLDS: later healthy pulses do not erase it --\n");
    {
        Rig r; r.init();
        for (int i = 0; i < 5; i++) { r.demandOne(); r.step(1000); }
        r.demandOne(); r.step(1000);
        r.step(100);                       /* one runt gap */
        uint32_t runt = r.data.shared.elsStop.stepPulseMinCycles;
        check(runt > 0 && runt < 250, "the runt width really was captured");
        for (int i = 0; i < 100; i++) { r.demandOne(); r.step(1000); }
        check(r.data.shared.elsStop.stepPulseMinCycles == runt,
              "100 healthy pulses later the narrowest is still reported");
    }

    /* -------- 4. host reset arms a fresh measurement --------------------- */
    printf("\n-- 4. writing 0 arms a fresh measurement --\n");
    {
        Rig r; r.init();
        for (int i = 0; i < 5; i++) { r.demandOne(); r.step(1000); }
        r.demandOne(); r.step(1000);
        r.step(100);
        check(r.data.shared.elsStop.stepPulseRuntCount == 1,
              "there was a runt on record before the reset");
        r.data.shared.elsStop.stepPulseMinCycles = 0;   /* host write */
        r.data.shared.elsStop.stepPulseRuntCount = 0;
        for (int i = 0; i < 10; i++) { r.demandOne(); r.step(1000); }
        uint32_t after = r.data.shared.elsStop.stepPulseMinCycles;
        printf("   after reset + healthy pulses: min %u, runts %u\n",
               (unsigned)after,
               (unsigned)r.data.shared.elsStop.stepPulseRuntCount);
        check(after > ELS_STEP_RUNT_CYCLES,
              "the reset took and the first healthy width repopulated it");
        check(r.data.shared.elsStop.stepPulseRuntCount == 0,
              "the old runt did not survive the reset");
    }

    printf("\n=== %s === (%d failing check%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
