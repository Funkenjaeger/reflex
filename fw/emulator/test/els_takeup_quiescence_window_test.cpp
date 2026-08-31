/*
 * The take-up abort path's VERDICT, plus a characterisation of what the
 * per-tick quiescence test can and cannot see. ELS_REQUIRE_QUIESCENCE=1 -- the
 * whole file is unreachable in a release build, where the flag is off and
 * carriageStopped is a constant true.
 *
 * THE FIX HERE (gap A) -- AN ABORT REPORTED SUCCESS. elsStop.takeupResult is
 * set to ELS_CAL_OK at take-up initiation, and the branch that overwrites it
 * with UNCONFIRMED is gated on carriageStopped. So a take-up whose carriage
 * NEVER goes quiet reaches the confirm-window abort with OK still standing: the
 * pass is aborted, the machine is returned to stopped-at-shoulder, and the UI is
 * told the take-up CONFIRMED. A safety gate failing in the
 * announce-proof-you-never-obtained direction. Fixed with
 * ELS_TAKEUP_ERR_NOT_QUIESCENT (7), set on the abort path when nothing more
 * specific has already been published.
 *
 * WHAT IS *NOT* FIXED HERE (gap B), and why the T2 assertions look backwards.
 * The quiescence test is per-tick EXACT EQUALITY: any nonzero tick-over-tick
 * delta resets the 200-tick counter. Widening it to a net-displacement window
 * was implemented on 2026-08-27 and BACKED OUT, because it is logically
 * incompatible with the invariant els_takeup_quiescence_test:419 pins -- "the
 * gate waited at least the quiescence window after the last pulse". That
 * sentence IS exact equality restated. Measured, with the window in place:
 *
 *   tolerance 2 counts -> released with 1.17 Z counts still undelivered, and
 *                         that is MORE than the whole settle tail the gate
 *                         exists to catch; 3 assertions in that file went red
 *   tolerance 1 count  -> released with 0.83 counts owed (physical property
 *                         preserved) but still 87 ticks after the last pulse
 *                         against a 200-tick window; 1 assertion still red
 *
 * So the window is not a refactor, it is a decision about what "stopped" means,
 * and it belongs to Evan rather than to whoever is next in this file. T2 below
 * therefore pins TODAY's behaviour ON PURPOSE, in the same style as
 * els_takeup_settle_gate_test: implementing the window MUST turn T2 red, which
 * is what forces the decision to be made rather than drifted into.
 *
 * ON THE DITHER STORY, because it is the usual justification and it is not what
 * this machine shows. "Alternating +-1 encoder dither would starve the counter
 * forever" is true as a matter of logic (T2 measures exactly that), but elspi's
 * takeup-settle-v3 captures found 893 of 900 trace buckets exactly 0 and 7
 * exactly -1, with NOT ONE +1 -- one-directional real motion, not dither. Do
 * not repeat the dither story as though it had been observed here.
 *
 * WHY T3 IS THE ONE TO KEEP. T2 alone would be satisfied by deleting the
 * quiescence test altogether. T3 is the bound in the other direction: sustained
 * creep must be REFUSED, and it is what stops any future widening from becoming
 * a rubber stamp.
 *
 * The rig is els_takeup_confirm_test's Lash fixture (real Ramps.c ISR, every
 * external stubbed, no physics.cpp) with one addition: nudgeCarriage()
 * superimposes carriage motion the servo did not command, which is the degree
 * of freedom all three cases are built from.
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
/* Stubs for every Ramps.c external (same set as els_takeup_confirm_test) */
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
    printf("   %-72s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

static void checkEq(int32_t got, int32_t want, const char *what) {
    bool ok = (got == want);
    printf("   %-72s %s (got %d, want %d)\n", what, ok ? "ok" : "FAIL",
           (int)got, (int)want);
    if (!ok) failures++;
}

/* ------------------------------------------------------------------ */
/* Geometry -- identical to els_takeup_confirm_test so the two files    */
/* describe the same job.                                              */
/* ------------------------------------------------------------------ */
static const int32_t SPINDLE_PER_PASS = 40;
static const int32_t Z_CLEAR          = 1000;
static const int32_t Z_STOP_POS       = 0;
static const int16_t Z_STOP_DIR       = -1;
static const float   SENTINEL         = 1.0e30f;

/* Firmware constants, quoted as LITERALS on purpose: they live in a .c file and
 * cannot be included, so re-tuning one has to cost a deliberate edit on both
 * sides rather than silently moving what this file asserts. */
static const int FW_ELS_SETTLE_TICKS         = 50;
static const int FW_ELS_QUIESCENT_TICKS      = 200;
static const int FW_ELS_CONFIRM_WINDOW_TICKS = 25000;

/* Long enough for the take-up, the dwell and the whole confirm window to run
 * out, with margin -- the abort cases have to actually reach the abort. The
 * take-up alone measured ~7000 ticks in this rig (the decel ramp's inter-pulse
 * gaps stretch as it slows), so a bound sized only on the confirm window left
 * the abort cases still pending at the end of the run and read as "never
 * aborted". Matches the 60000 the sibling els_takeup_confirm_test uses. */
static const int RUN_TICKS = 60000;

/* Lash: the leadscrew traverses `lashSteps` before the carriage moves at all. */
struct Lash {
    int32_t lashSteps      = 60;
    int32_t stepsPerZCount = 3;
    bool    coupled        = true;

    int32_t nutPos    = 0;
    int32_t carriage  = 0;
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

    /* Highest quiescent-tick count reached at any point in the run. A refusal
     * otherwise looks the same whether the counter never moved or got most of
     * the way there, and those are different bugs. */
    int32_t            peakQuiescent = 0;

    /* Highest count reached ON A TICK WHERE THE GATE WAS ACTUALLY LOOKING, i.e.
     * after the dwell. This is the one the assertions use, and the distinction
     * is not academic: under the net-displacement window that was trialled,
     * T3's creep transiently reached a full 200 during the take-up's decel
     * ramp, when sparse step pulses happened to cancel the creep for a stretch.
     * The gate is not consulting the counter then, so asserting on the
     * whole-run peak fails for a reason that has nothing to do with the gate's
     * decision. */
    int32_t            gatePeakQuiescent = 0;

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
        peakQuiescent  = 0;
        gatePeakQuiescent = 0;
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

        data.shared.elsStop.scaleIndex            = 1;
        data.shared.elsStop.stopPosition          = Z_STOP_POS;
        data.shared.elsStop.stopDirection         = Z_STOP_DIR;
        data.shared.elsStop.threadPitchSteps      = 533.333f;
        data.shared.elsStop.zCountsPerPitch       = 846.667f;
        data.shared.elsStop.backlashSteps         = backlashSteps;
        data.shared.elsStop.hysteresis            = 500;
        data.shared.elsStop.calMotionThreshCounts = motionThresh;
        data.shared.elsStop.calCeilingSteps       = 400;
        data.shared.elsStop.enable                = 0;

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

    void beginLashDriven() {
        zBase = zCnt;
        lash.prevSteps = (int32_t)data.shared.servo.currentSteps;
        lash.carriage = 0;
        /* Seat the nut on the wall the take-up drives AWAY from: the take-up
         * must then traverse the full lash before the carriage moves, which is
         * the worst case and the only one that exercises the gate. */
        lash.nutPos = lash.lashSteps;
    }

    /* Carriage motion the servo did NOT command -- vibration, a settling
     * machine, an operator leaning on it. The whole point of a quiescence test
     * is that this is a different thing from a driven move, so it is the
     * degree of freedom all three cases below are built from. */
    void nudgeCarriage(int32_t zCounts) { zBase += zCounts; }

    void stepDriven() {
        spindleCnt += SPINDLE_PER_PASS;
        zCnt = lash.follow((int32_t)data.shared.servo.currentSteps, zBase);
        tim[0].CNT = (uint32_t)spindleCnt;
        tim[1].CNT = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        /* Sampled BEFORE the ISR: on the tick the gate confirms, the ISR resets
         * elsStopSettleCount to 0, so reading it afterwards would hide the very
         * tick the gate made its decision on. */
        int32_t settleBefore = data.elsStopSettleCount;
        SynchroRefreshTimerIsr(&data);
        if (data.elsStopQuiescentTicks > peakQuiescent)
            peakQuiescent = data.elsStopQuiescentTicks;
        if (settleBefore >= FW_ELS_SETTLE_TICKS
            && data.elsStopQuiescentTicks > gatePeakQuiescent)
            gatePeakQuiescent = data.elsStopQuiescentTicks;
    }

    /* Arm a FRESH job (active first, then enable) and press Cut. No stop has
     * fired, so this is the datum pass -- chosen because it confirms WITHOUT
     * running applyPhaseCorrection, so no commanded jog lands on top of the
     * carriage motion these cases inject. */
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

/* Injection profiles, applied once per tick for the whole run. */
enum Profile { P_NONE, P_NEVER_QUIET, P_DITHER, P_CREEP };

/* Drive one complete take-up with `prof` superimposed on the carriage.
 * Returns the tick at which takeupPending cleared, or -1 if it never did. */
static int runTakeup(Rig &rig, Profile prof)
{
    rig.armFirstPass();
    rig.step(Z_CLEAR);                    /* initiates the take-up */
    check(rig.data.shared.elsStop.takeupPending == 1, "take-up initiated");
    rig.beginLashDriven();

    int cleared = -1;
    for (int i = 0; i < RUN_TICKS; i++) {
        switch (prof) {
        case P_NONE:
            break;
        case P_NEVER_QUIET:
            /* 5 counts/tick, forever. The carriage is simply never still. */
            rig.nudgeCarriage(+5);
            break;
        case P_DITHER:
            /* Alternating +-1: net displacement never leaves +-1, but every
             * tick differs from the last, which is all exact equality looks at. */
            rig.nudgeCarriage((i % 2 == 0) ? +1 : -1);
            break;
        case P_CREEP:
            /* One count per 50 ticks, one direction, forever -- real motion at
             * 1/50 count per tick. A gate that accepted this would be a gate
             * that accepted a moving carriage. */
            if (i % 50 == 49) rig.nudgeCarriage(+1);
            break;
        }
        rig.stepDriven();
        if (cleared < 0 && rig.data.shared.elsStop.takeupPending == 0) cleared = i;
    }
    printf("   [state] pending=%d active=%d result=%u seq=%u peak=%d gatePeak=%d\n",
           (int)rig.data.shared.elsStop.takeupPending,
           (int)rig.data.shared.elsStop.active,
           (unsigned)rig.data.shared.elsStop.takeupResult,
           (unsigned)rig.data.shared.elsStop.takeupSeq,
           (int)rig.peakQuiescent, (int)rig.gatePeakQuiescent);
    return cleared;
}

int main() {
    printf("=== ELS take-up abort verdict + quiescence characterisation "
           "(ELS_REQUIRE_QUIESCENCE=1) ===\n\n");

#if !ELS_REQUIRE_QUIESCENCE
    printf("[FAIL] this target must be compiled with ELS_REQUIRE_QUIESCENCE=1\n");
    return 1;
#endif

    /* ---- T0 control: an ordinary take-up still confirms --------------- */
    printf("-- T0 control: quiet carriage, take-up confirms --\n");
    {
        Rig rig;
        rig.init(/*backlashSteps*/ 90, /*motionThresh*/ 2);
        int cleared = runTakeup(rig, P_NONE);
        check(cleared >= 0, "take-up completed");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_CAL_OK,
                "takeupResult is OK");
        check(rig.gatePeakQuiescent >= FW_ELS_QUIESCENT_TICKS,
              "quiescence window matured");
        /* Without this the whole file could pass on a build where the take-up
         * never runs at all. */
        check(rig.data.shared.elsStop.lastTakeupZDelta != 0,
              "the carriage actually moved (fixture is live)");
    }

    /* ---- T1 (THE FIX): abort with quiescence never reached ------------- */
    printf("\n-- T1 gap A: carriage never goes quiet -> abort must NOT report OK --\n");
    {
        Rig rig;
        rig.init(/*backlashSteps*/ 90, /*motionThresh*/ 2);
        int cleared = runTakeup(rig, P_NEVER_QUIET);

        check(cleared >= 0, "confirm window closed and the pass aborted");
        checkEq(rig.data.shared.elsStop.active, 1,
                "aborted back to stopped-at-shoulder");
        check(rig.gatePeakQuiescent < FW_ELS_QUIESCENT_TICKS,
              "quiescence was in fact never reached");
        /* THE POINT OF THE FILE. Before the fix this read ELS_CAL_OK: an
         * aborted pass announced as a confirmed take-up. */
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_NOT_QUIESCENT,
                "takeupResult reports NOT_QUIESCENT, not OK");
        check(rig.data.shared.elsStop.takeupResult != ELS_CAL_OK,
              "an aborted take-up does not report success");
        check(rig.data.shared.elsStop.takeupSeq >= 1,
              "the abort published an outcome (takeupSeq bumped)");
        check(rig.data.shared.elsStop.lastIdealAdvance == SENTINEL,
              "no phase correction ran");
    }

    /* ---- T2 CHARACTERISATION of the exact-equality blind spot ---------- */
    printf("\n-- T2 gap B (NOT fixed): +-1 dither starves the per-tick test --\n");
    {
        Rig rig;
        rig.init(/*backlashSteps*/ 90, /*motionThresh*/ 2);
        int cleared = runTakeup(rig, P_DITHER);

        /* THESE ASSERTIONS PIN TODAY'S BEHAVIOUR ON PURPOSE. A carriage
         * oscillating by one count has a net displacement of zero and is, by
         * any physical standard, stopped -- but exact equality sees a change
         * every tick and never accumulates. Landing the net-displacement window
         * MUST turn this case red; that is the signal that the decision
         * described in the file header has been taken. Do not "fix" this by
         * relaxing the assertions. */
        check(cleared >= 0, "the confirm window still closes rather than hanging");
        /* Not "== 0": the counter is observed to reach 1 exactly once, on the
         * tick where a residual decel pulse happens to cancel that tick's
         * dither and leave Z genuinely unchanged. That is a real coincidence of
         * the fixture, not the gate accumulating, so the assertion pins the
         * substantive claim -- the counter never gets ANYWHERE NEAR maturing --
         * rather than an exact value that is one lucky pulse away from being
         * wrong. Landing the window sends this to 200, so it still goes red. */
        check(rig.gatePeakQuiescent < FW_ELS_QUIESCENT_TICKS / 10,
              "the counter never meaningfully accumulates under dither");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_NOT_QUIESCENT,
                "a physically-stopped carriage is refused (the blind spot)");
        /* And note WHICH fix makes this visible at all: without gap A this
         * refusal reported ELS_CAL_OK, so the blind spot was silent. */
        check(rig.data.shared.elsStop.takeupResult != ELS_CAL_OK,
              "gap A is what makes the blind spot announce itself");
    }

    /* ---- T3 THE BOUND: creep must be refused --------------------------- */
    printf("\n-- T3 bound: sustained 1-count-per-50-tick creep is REFUSED --\n");
    {
        Rig rig;
        rig.init(/*backlashSteps*/ 90, /*motionThresh*/ 2);
        int cleared = runTakeup(rig, P_CREEP);

        check(cleared >= 0, "confirm window closed and the pass aborted");
        check(rig.gatePeakQuiescent < FW_ELS_QUIESCENT_TICKS,
              "creep kept the window from ever maturing");
        check(rig.data.shared.elsStop.takeupResult != ELS_CAL_OK,
              "a creeping carriage is NOT confirmed");
        checkEq(rig.data.shared.elsStop.takeupResult, ELS_TAKEUP_ERR_NOT_QUIESCENT,
                "takeupResult reports NOT_QUIESCENT");
        /* The counter must get part-way -- proving the window runs at all --
         * and then be knocked down before maturing. A peak of 0 would mean
         * something else was resetting it and T3 would pass for the wrong
         * reason. THIS is the assertion that keeps any future widening honest:
         * under the trialled net window at tolerance 1 this read 149, and at
         * tolerance 2 it read 199 against a threshold of 200 -- one tick from
         * accepting a carriage that was demonstrably moving. */
        check(rig.gatePeakQuiescent > 0,
              "the window did run and was knocked down, not merely stuck at 0");
    }

    printf("\n=== %s ===\n", failures == 0 ? "ALL PASS" : "FAILURES");
    return failures == 0 ? 0 : 1;
}
