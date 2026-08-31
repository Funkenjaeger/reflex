/*
 * executionCyclesPeak: the ISR headroom measurement.
 *
 * WHY IT EXISTS. SynchroRefreshTimerIsr has 1000 CPU cycles per tick (100 MHz
 * core, 10 us TIM9 tick) and everything else on the chip -- the Modbus task,
 * the USART RX interrupt -- runs in what it leaves. executionCycles has always
 * been measured every tick, but the only copy anyone could see was
 * fastData.cycles, which servoEnableTask samples once per ~10 ms osDelay: about
 * one tick in a thousand, and therefore a TYPICAL tick, never the worst one.
 *
 * On 2026-08-23 the machine lost Modbus on 6 of 6 cuts, every failure a timeout
 * rather than a CRC error -- the Modbus task getting no CPU. The instrument
 * that should have shown that was structurally unable to see it. This register
 * is the fix, and this file is what stops it being another instrument nobody
 * checked: two mutations (never raise it; overwrite instead of max-hold) both
 * survived the entire suite before this existed.
 *
 * MAKING TIME PASS. The emulator's DWT is a plain struct that nothing advances
 * inside the ISR, so executionCycles would be 0 on every tick and the peak
 * would be untestable. The GPIO stub below charges a configurable number of
 * cycles per call, which is a fair model: the ISR's cost really is dominated by
 * the work it does between reading DWT at entry and reading it again at exit.
 */

extern "C" {
#include "Ramps.h"
#include "els_isr_rate.h"
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
/* Cycles charged per GPIO write, so a tick can be made expensive on demand.
 * The ISR reads DWT at entry and at exit, so anything that advances CYCCNT in
 * between shows up as executionCycles exactly as real work would. */
unsigned emu_isr_cost_per_gpio = 0;

void HAL_GPIO_WritePin(GPIO_TypeDef *p, uint16_t pin, GPIO_PinState s) {
    (void)p; (void)pin; (void)s;
    emu_dwt.CYCCNT += emu_isr_cost_per_gpio;
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

extern "C" unsigned emu_isr_cost_per_gpio;

/* The real budget, mirrored as a literal so the assertions read against the
 * machine rather than against nothing: 100 MHz core, 10 us tick. */
/* Derived, not written down: one tick at the 100 MHz core clock. Hardware
 * moved 100 kHz -> 50 kHz on 2026-08-28, which doubles this; the emulator
 * build pins ELS_ISR_TICK_HZ to the old rate (see emulator/CMakeLists.txt)
 * so this file still measures against 1000 here. If those two ever
 * disagree, this is the line that should move, not a literal. */
static const uint32_t ISR_BUDGET_CYCLES = ELS_ISR_CYCLE_BUDGET;

int main() {
    printf("=== ISR peak-hold (executionCyclesPeak) ===\n");
    printf("=== budget is %u cycles per tick ===\n\n", (unsigned)ISR_BUDGET_CYCLES);

    /* ---------------- 1. IT RISES ---------------------------------------- */
    /* MUTATION: delete the peak-hold entirely -> the peak stays 0 and this
     * fails. That mutation survived the whole suite before this file. */
    printf("-- 1. a tick that costs something is recorded --\n");
    {
        Rig rig;
        rig.init(0);
        emu_isr_cost_per_gpio = 0;
        rig.step(Z_CLEAR);
        check(rig.data.shared.elsStop.executionCyclesPeak == 0,
              "a free tick leaves the peak at zero");

        emu_isr_cost_per_gpio = 20;
        rig.step(Z_CLEAR);
        printf("   after a charged tick: peak = %u cycles\n",
               (unsigned)rig.data.shared.elsStop.executionCyclesPeak);
        check(rig.data.shared.elsStop.executionCyclesPeak > 0,
              "a tick that cost cycles raised the peak");
    }

    /* ---------------- 2. IT HOLDS ---------------------------------------- */
    /* THE WHOLE POINT. A spot sample reports whatever the last tick cost; a
     * peak has to survive every cheap tick that follows the expensive one,
     * because the expensive one is a handful of ticks in thousands.
     * MUTATION: assign instead of comparing (overwrite each tick) -> the peak
     * collapses to the cheap value and this fails. Also survived before. */
    printf("\n-- 2. an expensive tick survives the cheap ones after it --\n");
    {
        Rig rig;
        rig.init(0);

        emu_isr_cost_per_gpio = 60;
        rig.step(Z_CLEAR);
        uint32_t spike = rig.data.shared.elsStop.executionCyclesPeak;
        check(spike > 0, "the spike registered");

        emu_isr_cost_per_gpio = 1;
        for (int i = 0; i < 200; i++) rig.step(Z_CLEAR);

        printf("   spike %u cycles, still reported after 200 cheap ticks: %u\n",
               (unsigned)spike,
               (unsigned)rig.data.shared.elsStop.executionCyclesPeak);
        check(rig.data.shared.elsStop.executionCyclesPeak == spike,
              "200 cheap ticks did not erase the expensive one");
    }

    /* ---------------- 3. IT TRACKS THE WORST, NOT THE FIRST -------------- */
    printf("\n-- 3. a later, worse tick replaces an earlier one --\n");
    {
        Rig rig;
        rig.init(0);

        emu_isr_cost_per_gpio = 10;
        rig.step(Z_CLEAR);
        uint32_t small = rig.data.shared.elsStop.executionCyclesPeak;

        emu_isr_cost_per_gpio = 80;
        rig.step(Z_CLEAR);
        uint32_t big = rig.data.shared.elsStop.executionCyclesPeak;

        printf("   %u then %u cycles\n", (unsigned)small, (unsigned)big);
        check(big > small, "the worse tick took the peak");
    }

    /* ---------------- 4. THE HOST CAN ARM A FRESH MEASUREMENT ------------ */
    /* Reset is a plain host write of 0 rather than a command/ack pair -- the
     * ISR only ever raises the value, so the worst a lost write costs is one
     * repeat. Without a working reset the register answers "the worst thing
     * since boot" forever, which cannot isolate one cut. */
    printf("\n-- 4. writing 0 arms a fresh measurement --\n");
    {
        Rig rig;
        rig.init(0);

        emu_isr_cost_per_gpio = 90;
        rig.step(Z_CLEAR);
        check(rig.data.shared.elsStop.executionCyclesPeak > 0, "peak recorded");

        rig.data.shared.elsStop.executionCyclesPeak = 0;   /* the host write */
        emu_isr_cost_per_gpio = 5;
        rig.step(Z_CLEAR);

        uint32_t after = rig.data.shared.elsStop.executionCyclesPeak;
        printf("   after reset and one cheap tick: %u cycles\n", (unsigned)after);
        check(after > 0 && after < 90 * 4,
              "the reset took, and the counter started climbing again from there");
    }

    /* ---------------- 5. IT IS COMPARABLE TO THE BUDGET ------------------ */
    /* The number is only useful read against 1000. This pins that the units
     * are CPU cycles of a single tick and not, say, an accumulated total --
     * an accumulator would sail past the budget within a few ticks and the
     * reading would be meaningless. */
    printf("\n-- 5. it measures ONE tick, not an accumulation --\n");
    {
        Rig rig;
        rig.init(0);
        emu_isr_cost_per_gpio = 2;
        for (int i = 0; i < 500; i++) rig.step(Z_CLEAR);

        uint32_t peak = rig.data.shared.elsStop.executionCyclesPeak;
        printf("   500 ticks at 2 cycles/GPIO: peak = %u (budget %u)\n",
               (unsigned)peak, (unsigned)ISR_BUDGET_CYCLES);
        check(peak < ISR_BUDGET_CYCLES,
              "500 cheap ticks did not accumulate past the per-tick budget");
    }

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
