/*
 * ISR-level test: the published fastData.stepsToGo always equals
 * servo.stepsToGo once an ISR pass has finished.
 *
 * fastData.stepsToGo is the host's view of the commanded-move backlog: the UI
 * reads it from the tick snapshot (the Index bar's re-enable, the flight and
 * phase recorders). Several firmware paths set servo.stepsToGo directly --
 * a calibration finishing or aborting, the end-of-job block on an enable
 * fall or a DISENGAGE, a take-up refused or abandoned -- and a mirror
 * updated only while the indexing ramp steps keeps the old remainder after
 * any of them.
 *
 * Every scenario below checks the mirror after EVERY pass, so a mirror
 * that is right at rest but wrong mid-move fails too.
 * Same stub strategy and Rig shape as els_cal_isr_attribution_test.cpp,
 * duplicated to keep this target standalone.
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

void mbAppInit(uint16_t *r, uint16_t n, const uint16_t *id) { (void)r; (void)n; (void)id; }
int mbAppService(void) { return 0; }
uint16_t mbAppReplies(void) { return 0; }
uint32_t HAL_GetTick(void) { return 0; }


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
    /* Passes whose published mirror differed from servo.stepsToGo. */
    int                mismatches = 0;
    int32_t            firstBadMirror = 0;
    int32_t            firstBadReal = 0;

    void init(bool coupled, int32_t ceilingSteps = 400) {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        spindleCnt = 0;
        zCnt       = Z_CLEAR;
        lash = Lash{};
        lash.coupled = coupled;
        mismatches = 0;
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

        data.shared.servo.maxSpeed     = 100000.0f;
        data.shared.servo.acceleration = 50000.0f;
        data.shared.servo.servoDir     = 1;
        data.shared.fastData.servoMode = 1;

        data.shared.elsStop.scaleIndex            = 1;
        data.shared.elsStop.threadPitchSteps      = 533.333f;
        data.shared.elsStop.zCountsPerPitch       = 846.667f;
        data.shared.elsStop.hysteresis            = 500;
        data.shared.elsStop.calMotionThreshCounts = 2;
        data.shared.elsStop.calCeilingSteps       = ceilingSteps;
        data.shared.elsStop.enable                = 0;

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

    void step() {
        zCnt = lash.follow((int32_t)data.shared.servo.currentSteps, zBase);
        tim[0].CNT = (uint32_t)spindleCnt;
        tim[1].CNT = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
        int32_t mirror = data.shared.fastData.stepsToGo;
        int32_t real   = data.shared.servo.stepsToGo;
        if (mirror != real) {
            if (mismatches == 0) { firstBadMirror = mirror; firstBadReal = real; }
            mismatches++;
        }
    }

    void reportMismatches(const char *what) {
        if (mismatches) {
            printf("      first mismatch: published %d, servo.stepsToGo %d "
                   "(%d passes wrong)\n",
                   (int)firstBadMirror, (int)firstBadReal, mismatches);
        }
        check(mismatches == 0, what);
    }
};

/* ------------------------------------------------------------------ */

int main() {
    printf("=== fastData.stepsToGo mirrors servo.stepsToGo (ISR level) ===\n\n");

    printf("-- a passed calibration: the last leg ends with steps still commanded --\n");
    {
        Rig rig;
        rig.init(/*coupled*/ true);
        rig.data.shared.elsStop.calCommand = 1;
        uint16_t seq0 = rig.data.shared.elsStop.calSeq;
        for (int i = 0; i < 60000 && rig.data.shared.elsStop.calSeq == seq0; i++)
            rig.step();
        checkEq(rig.data.shared.elsStop.calResult, /*ELS_CAL_OK*/ 0, "the run passed");
        checkEq(rig.data.shared.servo.stepsToGo, 0, "servo.stepsToGo is 0 after the run");
        checkEq(rig.data.shared.fastData.stepsToGo, 0, "the published mirror is 0 after the run");
        for (int i = 0; i < 100; i++) rig.step();
        rig.reportMismatches("mirror equals servo.stepsToGo after every pass");
    }

    printf("\n-- a calibration aborted mid-leg by servoMode leaving 1 --\n");
    {
        Rig rig;
        rig.init(/*coupled*/ false, /*ceilingSteps*/ 4000);
        rig.data.shared.elsStop.calCommand = 1;
        for (int i = 0; i < 50; i++) rig.step();
        check(rig.data.shared.servo.stepsToGo != 0, "the leg is driving (steps still commanded)");
        uint16_t seq0 = rig.data.shared.elsStop.calSeq;
        rig.data.shared.fastData.servoMode = 0;
        rig.step();
        check(rig.data.shared.elsStop.calSeq != seq0, "the run ended (ABORTED)");
        checkEq(rig.data.shared.fastData.stepsToGo, 0, "the published mirror is 0 after the abort");
        for (int i = 0; i < 100; i++) rig.step();
        rig.reportMismatches("mirror equals servo.stepsToGo after every pass");
    }

    printf("\n-- an end-of-job (DISENGAGE's request) during a commanded move --\n");
    {
        Rig rig;
        rig.init(/*coupled*/ true);
        rig.data.shared.servo.stepsToGo = -5000;   /* a reverse indexing move */
        for (int i = 0; i < 50; i++) rig.step();
        check(rig.data.shared.fastData.stepsToGo < 0
              && rig.data.shared.fastData.stepsToGo > -5000,
              "mid-move the mirror shows the signed remainder");
        rig.data.elsCommandEndJob = 1;
        rig.step();
        checkEq(rig.data.shared.servo.stepsToGo, 0, "the end-of-job cancelled the move");
        checkEq(rig.data.shared.fastData.stepsToGo, 0, "the published mirror is 0 after it");
        for (int i = 0; i < 100; i++) rig.step();
        rig.reportMismatches("mirror equals servo.stepsToGo after every pass");
    }

    printf("\n-- a move banked with servoMode 0 is published, not hidden --\n");
    {
        Rig rig;
        rig.init(/*coupled*/ true);
        rig.data.shared.fastData.servoMode = 0;
        rig.data.shared.servo.stepsToGo = 1234;
        rig.step();
        checkEq(rig.data.shared.fastData.stepsToGo, 1234,
                "a nonzero backlog the ramp is not consuming still reads nonzero");
        rig.reportMismatches("mirror equals servo.stepsToGo after every pass");
    }

    printf("\n%s (%d failure%s)\n", failures ? "FAILED" : "PASSED",
           failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
