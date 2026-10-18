/*
 * els_cal_basis_test.cpp -- the calMeasured split (protocolVersion 13).
 *
 * calMeasured is the last calibration run's RESULT (firmware-owned, partial
 * values on a failed run). calBasisSteps is what the take-up confirmation
 * gate derives its threshold from: the host writes it, and the firmware
 * copies a SUCCESSFUL run's mean into it. Before 13 the gate read
 * elsCalMeanValid(calMeasured) directly, so a failed run dropped it to the
 * bare motion floor until the host wrote the legs back.
 *
 * Drives the real SynchroRefreshTimerIsr with the Lash/Rig pair from
 * els_cal_isr_attribution_test.cpp (copied verbatim):
 *   A. a successful run installs mean(calMeasured) as the basis;
 *   B. a successful run replaces a host-written basis;
 *   C. a failed run (open half-nut, NO_MOTION) leaves a host-written basis
 *      intact while calMeasured carries its partial values;
 *   D. a run aborted mid-way (servoMode left 1) leaves the basis intact;
 *   E. the take-up threshold derives from calBasisSteps, not calMeasured:
 *      with calMeasured zeroed by a failure and a host basis written, the
 *      published takeupThreshCounts is elsTakeupConfirmThreshold(backlash,
 *      basis, ...), above the floor; with basis 0 it is the floor.
 *
 * Seen red against Ramps.c with the two split hunks reverted (no basis copy,
 * threshold from calMeasured): A, B and E fail.
 */

extern "C" {
#include "Ramps.h"
#include "Scales.h"
#include "emulator_state.h"
}

#include <cstdio>
#include <cstdint>
#include <cstring>

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
    printf("   %-72s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

static void checkEq(int32_t got, int32_t want, const char *what) {
    bool ok = (got == want);
    printf("   %-72s %s (got %d, want %d)\n", what, ok ? "ok" : "FAIL",
           (int)got, (int)want);
    if (!ok) failures++;
}

static const int32_t Z_CLEAR = 1000;

struct Lash {
    int32_t lashSteps      = 60;
    int32_t stepsPerZCount = 3;
    bool    coupled        = true;

    int32_t nutPos   = 0;
    int32_t carriage = 0;
    int32_t prevSteps = 0;

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
    int32_t            zBase = 0;

    void init(int32_t motionThresh, bool coupled, int32_t lashSteps = 60,
              int32_t ceilingSteps = 400) {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        spindleCnt = 0;
        zCnt       = Z_CLEAR;
        lash = Lash{};
        lash.lashSteps = lashSteps;
        lash.coupled   = coupled;
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
        data.shared.elsStop.threadPitchSteps     = 533.333f;
        data.shared.elsStop.zCountsPerPitch      = 846.667f;
        data.shared.elsStop.hysteresis           = 500;
        data.shared.elsStop.calMotionThreshCounts = motionThresh;
        data.shared.elsStop.calCeilingSteps      = ceilingSteps;
        data.shared.elsStop.enable               = 0;

        tim[0].CNT = (uint32_t)spindleCnt;
        tim[1].CNT = (uint32_t)zCnt;
        data.scalesDeltaPos[0].position = spindleCnt;
        data.scalesDeltaPos[1].position = zCnt;
        data.shared.scales[0].position  = spindleCnt;
        data.shared.scales[1].position  = zCnt;
        data.scalesSyncDeltaPos[0].oldPosition = spindleCnt;
        data.scalesSyncDeltaPos[1].oldPosition = zCnt;

        zBase = zCnt;
        lash.prevSteps = 0;
        lash.carriage  = 0;
    }

    /* Every tick from the very first: Z is whatever the lash model produces
     * from the servo's own pulses (or, if uncoupled, frozen at zBase until a
     * nudge moves it) -- there is no separate "hand it to the lash" step
     * because calibration legs have no pre-drive settle phase to skip past. */
    void stepDriven() {
        spindleCnt += 1;   /* spindle keeps turning; irrelevant to calibration */
        zCnt = lash.follow((int32_t)data.shared.servo.currentSteps, zBase);
        tim[0].CNT = (uint32_t)spindleCnt;
        tim[1].CNT = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }

    /* Operator pushing the carriage by hand -- uncoupled from any servo pulse. */
    void nudgeCarriage(int32_t zCounts) {
        zBase += zCounts;
    }
};


/* Run one calibration to its calSeq edge. Returns false on no edge. */
static bool runCal(Rig &rig, int abortAfterTicks = -1) {
    rig.data.shared.elsStop.calCommand = 1;
    uint16_t seq0 = rig.data.shared.elsStop.calSeq;
    for (int i = 0; i < 200000 && rig.data.shared.elsStop.calSeq == seq0; i++) {
        if (i == abortAfterTicks) rig.data.shared.fastData.servoMode = 0;
        rig.stepDriven();
    }
    return rig.data.shared.elsStop.calSeq != seq0;
}

static int32_t resultMean(Rig &rig) {
    return elsCalMeanValid(rig.data.shared.elsStop.calMeasured, ELS_CAL_CYCLES);
}

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    printf("=== calMeasured split: calBasisSteps (protocolVersion 13) ===\n");

    printf("\n-- A. a successful run installs its mean as the basis --\n");
    {
        Rig rig;
        rig.init(2, true, 60);
        rig.data.shared.scales[0].syncEnable = 0;
        check(runCal(rig), "run finished");
        checkEq(rig.data.shared.elsStop.calResult, ELS_CAL_OK, "calResult OK");
        check(resultMean(rig) > 0, "calMeasured holds a complete result");
        checkEq(rig.data.shared.elsStop.calBasisSteps, resultMean(rig),
                "calBasisSteps = mean(calMeasured)");
    }

    printf("\n-- B. a successful run replaces a host-written basis --\n");
    {
        Rig rig;
        rig.init(2, true, 60);
        rig.data.shared.scales[0].syncEnable = 0;
        rig.data.shared.elsStop.calBasisSteps = 777;
        check(runCal(rig), "run finished");
        checkEq(rig.data.shared.elsStop.calResult, ELS_CAL_OK, "calResult OK");
        checkEq(rig.data.shared.elsStop.calBasisSteps, resultMean(rig),
                "calBasisSteps replaced by the new mean");
    }

    printf("\n-- C. a failed run leaves the basis alone --\n");
    {
        Rig rig;
        rig.init(2, false, 60);          /* open half-nut: Z never moves */
        rig.data.shared.scales[0].syncEnable = 0;
        rig.data.shared.elsStop.calBasisSteps = 61;
        check(runCal(rig), "run finished");
        checkEq(rig.data.shared.elsStop.calResult, ELS_CAL_ERR_NO_MOTION, "calResult NO_MOTION");
        checkEq(resultMean(rig), 0, "calMeasured is an incomplete result (mean not valid)");
        checkEq(rig.data.shared.elsStop.calBasisSteps, 61, "calBasisSteps untouched");
    }

    printf("\n-- D. an aborted run leaves the basis alone --\n");
    {
        Rig rig;
        rig.init(2, true, 60);
        rig.data.shared.scales[0].syncEnable = 0;
        rig.data.shared.elsStop.calBasisSteps = 61;
        check(runCal(rig, 40), "run finished");
        checkEq(rig.data.shared.elsStop.calResult, ELS_CAL_ERR_ABORTED, "calResult ABORTED");
        checkEq(rig.data.shared.elsStop.calBasisSteps, 61, "calBasisSteps untouched");
    }

    printf("\n-- E. the take-up threshold derives from calBasisSteps --\n");
    for (int withBasis = 1; withBasis >= 0; withBasis--) {
        Rig rig;
        rig.init(2, true, 60);
        rig.data.shared.scales[0].syncEnable = 0;
        /* A failed run's zeros in the result, and a host-written basis. */
        for (int i = 0; i < 3; i++) rig.data.shared.elsStop.calMeasured[i] = 0;
        const uint16_t basis = withBasis ? 60 : 0;
        rig.data.shared.elsStop.calBasisSteps = basis;
        rig.data.shared.elsStop.backlashSteps = 100;
        /* Held, then released: the resume edge starts a take-up. */
        rig.data.shared.elsStop.active = 1;
        rig.stepDriven();
        rig.data.shared.elsStop.enable = 1;
        for (int i = 0; i < 5; i++) rig.stepDriven();
        rig.data.shared.elsStop.active = 0;
        uint16_t seq0 = rig.data.shared.elsStop.takeupSeq;
        for (int i = 0; i < 400000 && rig.data.shared.elsStop.takeupSeq == seq0; i++) rig.stepDriven();
        check(rig.data.shared.elsStop.takeupSeq != seq0, "take-up reached its gate");
        int32_t want = elsTakeupConfirmThreshold(100, (int32_t)basis,
                                                 rig.data.shared.elsStop.threadPitchSteps,
                                                 rig.data.shared.elsStop.zCountsPerPitch, 2);
        checkEq(rig.data.shared.elsStop.takeupThreshCounts, want,
                withBasis ? "threshold from basis 60 with calMeasured zeroed"
                          : "basis 0: threshold is the floor");
        if (withBasis) check(want > 2, "that threshold is above the floor (the case is not vacuous)");
    }

    printf("\n%s (%d failure%s)\n", failures ? "FAILED" : "PASSED", failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
