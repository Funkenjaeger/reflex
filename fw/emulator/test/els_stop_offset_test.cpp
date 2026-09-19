/*
 * The stop-overshoot correction (elsStop.stopOffset / stopTriggerOffset,
 * protocolVersion 11, 2026-09-18).
 *
 * WHAT IS BEING PROVEN
 * --------------------
 * The carriage coasts past the ELS stop trigger by an amount that grows with
 * the approach rate. reflex-ui writes stopOffset -- counts to fire EARLY -- live
 * from that rate, and the ISR moves its threshold by the CLAMPED offset:
 *
 *     threshold = stopPosition - sign(stopDirection) * clamp(stopOffset, 0, MAX)
 *
 * and measures the hysteresis clearance from the same threshold. Everything
 * here drives the real Core/Src/Ramps.c ISR with its externals stubbed (same rig
 * as els_stop_trigger_snapshot_test.cpp), stepping Z ONE COUNT PER TICK so the
 * trigger position is exact rather than wherever a teleport happened to land.
 *
 *   1. offset 0 fires exactly at stopPosition, as protocolVersion 10 did;
 *   2. offset N fires N counts early, in BOTH stop directions -- the sign test,
 *      and the one a flipped sign in the ISR fails (seen red 2026-09-18);
 *   3. an offset above ELS_STOP_OFFSET_MAX is clamped to it;
 *   4. a negative offset behaves as 0 (never LATE, past the shoulder);
 *   5. the hysteresis re-arm is measured from the EFFECTIVE threshold: a retract
 *      that clears stopPosition by the hysteresis but not the moved threshold
 *      must NOT re-arm;
 *   6. stopTriggerOffset publishes the clamped value in every case above, and a
 *      live change of stopOffset mid-approach takes effect (it is re-read every
 *      ISR pass, never latched).
 */

extern "C" {
#include "Ramps.h"
#include "Scales.h"
#include "emulator_state.h"
}

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstddef>

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
/* Fixture                                                            */
/* ------------------------------------------------------------------ */

static const int32_t SPINDLE_COUNTS_PER_PASS = 40;
static const int32_t Z_STOP_POS = 5000;   /* nonzero, so an offset "from 0" cannot pass */
static const int32_t HYST       = 300;
static const int32_t CLEAR_BY   = 1000;   /* start this far clear of the stop */
static const int32_t NOT_FIRED  = INT32_MIN;

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int32_t            spindleCnt;
    int32_t            zCnt;
    int16_t            dir;

    /* dir = -1: carriage approaches from ABOVE, stop when Z <= threshold.
     * dir = +1: approaches from BELOW, stop when Z >= threshold. */
    void init(int16_t stopDir) {
        dir = stopDir;
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        spindleCnt = 0;
        zCnt       = clearPos();
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

        data.shared.elsStop.scaleIndex       = 1;
        data.shared.elsStop.stopPosition     = Z_STOP_POS;
        data.shared.elsStop.stopDirection    = stopDir;
        data.shared.elsStop.threadPitchSteps = 0.0f;   /* turning: no phase correction */
        data.shared.elsStop.zCountsPerPitch  = 0.0f;
        data.shared.elsStop.backlashSteps    = 0;      /* no take-up to wait on */
        data.shared.elsStop.hysteresis       = HYST;
        data.shared.elsStop.stopOffset       = 0;
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

    /* A position `by` counts clear of the stop, on the retract side. */
    int32_t clearPos(int32_t by = CLEAR_BY) const {
        return Z_STOP_POS - dir * by;
    }

    void step(int32_t zTarget) {
        spindleCnt += SPINDLE_COUNTS_PER_PASS;
        zCnt        = zTarget;
        tim[0].CNT  = (uint32_t)spindleCnt;
        tim[1].CNT  = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }

    elsStop_t &els() { return data.shared.elsStop; }

    void armAndCut() {
        for (int i = 0; i < 3; i++) step(zCnt);
        els().active = 1;
        step(zCnt);
        els().enable = 1;
        for (int i = 0; i < 3; i++) step(zCnt);
        resume();
    }

    void resume() {
        els().active = 0;
        for (int i = 0; i < 6; i++) step(zCnt);
    }

    /* Move to `to` one count per tick (retracting: no trigger possible). */
    void moveTo(int32_t to) {
        while (zCnt != to) step(zCnt + (to > zCnt ? 1 : -1));
        step(zCnt);
    }

    /* Approach the stop one count per tick, from wherever Z is, until it fires
     * or Z reaches `limit` counts PAST stopPosition. Returns stopTriggerZ, or
     * NOT_FIRED. One-count steps make the trigger position exact: the ISR
     * decides on the previous tick's reference position, and with unit steps
     * that is the first position satisfying the comparison. */
    int32_t approach(int32_t limit = 400) {
        const int32_t end = Z_STOP_POS + dir * limit;
        while (!els().active && zCnt != end) step(zCnt + dir);
        step(zCnt);                      /* the reference-scale lag tick */
        return els().active ? els().stopTriggerZ : NOT_FIRED;
    }

    /* Retract clear by `by`, resume there, ready for another approach. */
    void retractAndResume(int32_t by = CLEAR_BY) {
        moveTo(clearPos(by));
        resume();
    }
};

static int failures = 0;

static void check(bool ok, const char *label) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", label);
    if (!ok) failures++;
}

static void checkEq(long got, long want, const char *label) {
    bool ok = (got == want);
    printf("[%s] %s (got %ld, want %ld)\n", ok ? "PASS" : "FAIL", label, got, want);
    if (!ok) failures++;
}

/* One fresh job, one approach with the given offset; returns the trigger Z. */
static int32_t firesAt(int16_t dir, int16_t offset, int16_t *publishedOffset) {
    Rig rig;
    rig.init(dir);
    rig.armAndCut();
    rig.els().stopOffset = offset;
    int32_t z = rig.approach();
    if (publishedOffset) *publishedOffset = rig.els().stopTriggerOffset;
    return z;
}

int main() {
    printf("=== stop-overshoot correction (protocolVersion %d, ELS_STOP_OFFSET_MAX %d) ===\n",
           (int)ELS_PROTOCOL_VERSION, (int)ELS_STOP_OFFSET_MAX);

    int16_t pub = -1;

    /* ---- 1. offset 0 is exactly the old stop ---------------------------- */
    printf("\n-- 1. offset 0 fires exactly at stopPosition --\n");
    checkEq(firesAt(-1, 0, &pub), Z_STOP_POS, "dir -1, offset 0: fires AT stopPosition");
    checkEq(pub, 0, "...and stopTriggerOffset publishes 0");
    checkEq(firesAt(+1, 0, &pub), Z_STOP_POS, "dir +1, offset 0: fires AT stopPosition");
    checkEq(pub, 0, "...and stopTriggerOffset publishes 0");

    /* ---- 2. offset N fires N early, both directions (THE SIGN TEST) ----- */
    printf("\n-- 2. offset N fires N counts EARLY in both directions --\n");
    checkEq(firesAt(-1, 25, &pub), Z_STOP_POS + 25,
            "dir -1 (approach from above), offset 25: fires 25 counts ABOVE the stop");
    checkEq(pub, 25, "...and stopTriggerOffset publishes 25");
    checkEq(firesAt(+1, 25, &pub), Z_STOP_POS - 25,
            "dir +1 (approach from below), offset 25: fires 25 counts BELOW the stop");
    checkEq(pub, 25, "...and stopTriggerOffset publishes 25");
    checkEq(firesAt(-1, 44, &pub), Z_STOP_POS + 44,
            "dir -1, offset 44 (the table's top value + margin): 44 early");

    /* ---- 3. above the ceiling: clamped ---------------------------------- */
    printf("\n-- 3. an offset above ELS_STOP_OFFSET_MAX is clamped --\n");
    checkEq(firesAt(-1, 5000, &pub), Z_STOP_POS + ELS_STOP_OFFSET_MAX,
            "dir -1, offset 5000: fires only MAX early");
    checkEq(pub, ELS_STOP_OFFSET_MAX, "...and stopTriggerOffset publishes the CLAMPED value");
    checkEq(firesAt(+1, 32767, &pub), Z_STOP_POS - ELS_STOP_OFFSET_MAX,
            "dir +1, offset INT16_MAX: fires only MAX early");
    checkEq(pub, ELS_STOP_OFFSET_MAX, "...and stopTriggerOffset publishes the CLAMPED value");
    checkEq(firesAt(-1, ELS_STOP_OFFSET_MAX + 1, &pub), Z_STOP_POS + ELS_STOP_OFFSET_MAX,
            "dir -1, offset MAX+1: the boundary clamps");

    /* ---- 4. negative behaves as 0 --------------------------------------- */
    printf("\n-- 4. a negative offset behaves as 0 (never late) --\n");
    checkEq(firesAt(-1, -30, &pub), Z_STOP_POS, "dir -1, offset -30: fires AT stopPosition, not past it");
    checkEq(pub, 0, "...and stopTriggerOffset publishes 0");
    checkEq(firesAt(+1, -32768, &pub), Z_STOP_POS, "dir +1, offset INT16_MIN: fires AT stopPosition");
    checkEq(pub, 0, "...and stopTriggerOffset publishes 0");

    /* ---- 5. hysteresis re-arm measured from the EFFECTIVE threshold ----- */
    printf("\n-- 5. the hysteresis re-arm uses the effective threshold --\n");
    {
        const int16_t N = 100;
        Rig rig;
        rig.init(-1);
        rig.armAndCut();
        rig.els().stopOffset = N;
        checkEq(rig.approach(), Z_STOP_POS + N, "first pass fires N early (precondition)");

        /* Retract to (effective threshold + HYST - 1): clear of STOPPOSITION by
         * N + HYST - 1 >= HYST, but NOT clear of the moved threshold. A gate
         * still measuring from stopPosition would re-arm here; the fixed gate
         * must not, so this approach runs through the threshold unfired. */
        rig.retractAndResume(N + HYST - 1);
        check(rig.els().active == 0, "resumed (precondition)");
        int32_t z = rig.approach(/*limit past stopPosition*/ 50);
        check(z == NOT_FIRED,
              "retract short of threshold+hysteresis: NOT re-armed, the approach does not fire");

        /* The operator stops, retracts properly, and goes again. active is
         * still 0 (nothing fired), so the approach below starts from a real
         * clearance of exactly HYST from the effective threshold. */
        rig.moveTo(rig.clearPos(N + HYST));
        for (int i = 0; i < 3; i++) rig.step(rig.zCnt);
        z = rig.approach();
        checkEq(z, Z_STOP_POS + N,
                "retract to exactly threshold+hysteresis: re-armed, fires at the effective threshold");
        checkEq(rig.els().stopTriggerSeq, 2, "exactly two triggers across the three approaches");
    }

    /* ---- 6. live change mid-approach, and the published value ----------- */
    printf("\n-- 6. stopOffset is re-read every pass (live), stopTriggerOffset records it --\n");
    {
        Rig rig;
        rig.init(+1);
        rig.armAndCut();
        rig.els().stopOffset = 10;
        /* Approach to 60 counts short of the stop: offset 10 would not fire yet. */
        while (rig.zCnt != Z_STOP_POS - 60) rig.step(rig.zCnt + 1);
        check(rig.els().active == 0, "not fired 60 short with offset 10 (precondition)");
        rig.els().stopOffset = 40;       /* the host raises it as the rate climbs */
        checkEq(rig.approach(), Z_STOP_POS - 40, "the NEW offset governs the trigger");
        checkEq(rig.els().stopTriggerOffset, 40, "stopTriggerOffset is the value in effect at the trigger");
        rig.els().stopOffset = 0;         /* host writes after the trigger... */
        for (int i = 0; i < 5; i++) rig.step(rig.zCnt);
        checkEq(rig.els().stopTriggerOffset, 40, "...do not rewrite the latched capture");
    }

    /* ---- 7. seq before payload, by address ------------------------------ */
    printf("\n-- 7. stopTriggerOffset sits behind stopTriggerSeq --\n");
    check(offsetof(elsStop_t, stopTriggerSeq) < offsetof(elsStop_t, stopTriggerOffset),
          "stopTriggerSeq precedes stopTriggerOffset (torn-read safe)");

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
