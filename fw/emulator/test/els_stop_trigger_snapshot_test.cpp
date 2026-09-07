/*
 * The trigger-instant snapshot (protocolVersion 9, 2026-09-07).
 *
 * WHAT IS BEING PROVEN, AND WHY IT NEEDS AN ISR-LEVEL TEST
 * -------------------------------------------------------
 * The ELS stop is a COMMANDED position; the carriage coasts past it by an
 * amount that depends on approach speed, and compensating for that needs
 * (overshoot, approach speed) pairs. The host cannot supply the trigger end of
 * either: it polls at 30 Hz and the coast lasts about 12 ms, so on 2026-09-07
 * roughly 60% of the coast was already over by the time the host first saw
 * elsStop.active, and in 22% of passes all of it was. elsStop.stopTriggerZ and
 * friends exist because ONLY the ISR knows that instant -- so the only place
 * the register can be checked is against the ISR that writes it, driving the
 * real Core/Src/Ramps.c with its externals stubbed. Same rig shape as
 * els_arm_past_stop_test.cpp, from which the fixture below is copied.
 *
 * THE ASSERTIONS ARE BUILT TO BE ABLE TO FAIL. Every field here is an integer
 * whose "nothing happened" value is 0, and 0 is also a legitimate reading
 * (stopTriggerStepsToGo == 0 is the answer "the firmware commanded nothing",
 * which is the finding the whole exercise is chasing). So a latch that never
 * ran would publish exactly what a real quiet pass publishes. The cases below
 * are arranged so no single defect passes them all:
 *
 *   - case 2 latches a DELIBERATELY NONZERO stepsToGo, so a latch that wrote
 *     constants, or never wrote at all, is visible;
 *   - case 5 latches a genuinely zero stepsToGo and still demands the seq move,
 *     so "no capture" and "captured, nothing commanded" stay distinguishable;
 *   - case 3 changes the live registers AFTER the trigger and demands the
 *     published payload not follow, so a latch that is really a mirror fails;
 *   - case 4 triggers a second pass at a different Z and different speeds, so a
 *     latch that fires once and sticks fails;
 *   - case 6 pins that stopTriggerZ carries information stopPosition does not,
 *     which is the entire reason the register was added rather than reusing the
 *     threshold the host already has;
 *   - case 7 pins the seq-before-payload ADDRESS ORDER, the property that makes
 *     a torn Modbus frame harmless.
 *
 * The abort path that also sets elsStop.active = 1 (the take-up "back to
 * stopped-at-shoulder") must NOT publish a snapshot; that half is pinned in
 * els_takeup_quiescence_window_test.cpp T1, which is the file that can actually
 * reach it (it needs ELS_REQUIRE_QUIESCENCE=1).
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
static const int32_t Z_CLEAR    = 1000;
static const int32_t Z_STOP_POS = 0;
static const int16_t Z_STOP_DIR = -1;      /* stop when Z <= stopPosition */

/* Two different overshoots, so no case can pass by coincidence and so case 6
 * can show the trigger position moving while stopPosition does not. Both are
 * PAST the stop by a nonzero margin: reading the threshold instead of the
 * trigger -- what the host does today -- gets neither number right. */
static const int32_t Z_TRIG_PASS1 = -37;
static const int32_t Z_TRIG_PASS2 = -11;

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int32_t            spindleCnt;
    int32_t            zCnt;

    void init(int32_t zStart) {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        spindleCnt = 0;
        zCnt       = zStart;
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
        /* TURNING, not threading: threadPitchSteps 0 means no phase correction
         * on resume, so servo.stepsToGo is whatever THIS test put there and not
         * a leftover correction the ramp is still chewing. The snapshot is
         * indifferent to the mode -- it is latched in the stop-fire block,
         * which is common to both. */
        data.shared.elsStop.threadPitchSteps = 0.0f;
        data.shared.elsStop.zCountsPerPitch  = 0.0f;
        data.shared.elsStop.backlashSteps    = 0;   /* no take-up to wait on */
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

    elsStop_t &els() { return data.shared.elsStop; }

    /* Arm a fresh job (active first, a Modbus gap, then enable) and press Cut,
     * leaving the carriage clear of the stop and cutting. */
    void armAndCut() {
        for (int i = 0; i < 3; i++) step(Z_CLEAR);
        els().active = 1;
        step(Z_CLEAR);                     /* gap between the two register writes */
        els().enable = 1;
        for (int i = 0; i < 3; i++) step(Z_CLEAR);
        resume();
    }

    /* SW clears active: the operator's "go", then a few cutting ticks. */
    void resume() {
        els().active = 0;
        step(Z_CLEAR);
        for (int i = 0; i < 5; i++) step(Z_CLEAR);
    }

    /* Cross the stop, and publish the registers the ISR should latch.
     *
     * TWO TICKS, and the reason is a real property of the register rather than
     * a fixture wrinkle. The trigger test runs inside the scales loop, on the
     * iteration for the SYNC-ENABLED scale (index 0 here), and it reads the
     * reference scale's position with `refPos = scales[scaleIndex].position` --
     * scale 1, whose own iteration has not run yet this tick. So the position
     * the stop DECIDES on, and therefore the position stopTriggerZ publishes,
     * is the reference scale's value as of the previous tick. That is 10 us of
     * lag on hardware and it is inherent to the decision, not introduced by the
     * snapshot: latchedZ has always had it, and overshoot is measured against
     * the position the firmware decided to stop at, which is exactly this one.
     * Here, where a step() teleports the carriage, the lag is a whole jump --
     * so the first step puts Z at the target and the second is the tick that
     * fires. The instant's registers are written between them, so they are what
     * the firing tick sees.
     *
     * The ISR never writes scales[].speed (updateSpeedTask does, and it does
     * not run here), so those are exactly what the latch has to pick up. */
    void triggerAt(int32_t z, int32_t zSpeed, int32_t spindleSpeed,
                   int32_t stepsToGo) {
        step(z);
        data.shared.scales[1].speed = zSpeed;
        data.shared.scales[0].speed = spindleSpeed;
        data.shared.servo.stepsToGo = stepsToGo;
        step(z);
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

struct Snapshot {
    uint16_t seq;
    int32_t  z, zSpeed, stepsToGo, spindleSpeed;
};

static Snapshot readSnapshot(const elsStop_t &s) {
    return Snapshot{ s.stopTriggerSeq, s.stopTriggerZ, s.stopTriggerZSpeed,
                     s.stopTriggerStepsToGo, s.stopTriggerSpindleSpeed };
}

static bool sameSnapshot(const Snapshot &a, const Snapshot &b) {
    return a.seq == b.seq && a.z == b.z && a.zSpeed == b.zSpeed
        && a.stepsToGo == b.stepsToGo && a.spindleSpeed == b.spindleSpeed;
}

int main() {
    printf("=== trigger-instant snapshot (protocolVersion %d) ===\n",
           (int)ELS_PROTOCOL_VERSION);

    Rig rig;
    rig.init(Z_CLEAR);

    /* ---- 1. nothing is published before the stop fires ----------------- */
    printf("\n-- 1. no trigger, no capture --\n");
    rig.armAndCut();
    /* Speeds are live and nonzero here on purpose: a latch that ran every tick
     * would have copied them by now, and this case is what says so. */
    rig.data.shared.scales[1].speed = -9999;
    rig.data.shared.scales[0].speed = 12345;
    for (int i = 0; i < 20; i++) rig.step(Z_CLEAR);
    check(rig.els().active == 0, "fixture precondition: cutting, stop not fired");
    checkEq(rig.els().stopTriggerSeq, 0, "stopTriggerSeq still 0 before any trigger");
    checkEq(rig.els().stopTriggerZ, 0, "stopTriggerZ unwritten");
    checkEq(rig.els().stopTriggerZSpeed, 0, "stopTriggerZSpeed unwritten");
    checkEq(rig.els().stopTriggerStepsToGo, 0, "stopTriggerStepsToGo unwritten");
    checkEq(rig.els().stopTriggerSpindleSpeed, 0, "stopTriggerSpindleSpeed unwritten");

    /* ---- 2. the trigger latches what the ISR saw ----------------------- */
    printf("\n-- 2. the stop fires: the snapshot is what the ISR saw --\n");
    rig.triggerAt(Z_TRIG_PASS1, /*zSpeed*/ -4200, /*spindle*/ 91000,
                  /*stepsToGo*/ 137);
    Snapshot p1 = readSnapshot(rig.els());
    check(rig.els().active == 1, "the stop fired");
    checkEq(p1.seq, 1, "stopTriggerSeq incremented exactly once");
    checkEq(p1.z, Z_TRIG_PASS1, "stopTriggerZ is the position the trigger fired at");
    checkEq(p1.zSpeed, -4200, "stopTriggerZSpeed is the Z speed at that instant");
    checkEq(p1.spindleSpeed, 91000, "stopTriggerSpindleSpeed likewise");
    /* NONZERO by construction. A latch that wrote nothing, or wrote a constant,
     * reads 0 here -- and 0 is the value a quiet pass legitimately publishes
     * (case 5), so this is the case that can tell the two apart. */
    checkEq(p1.stepsToGo, 137,
            "stopTriggerStepsToGo is the backlog the firmware still had");
    check(p1.stepsToGo != 0,
          "…and it is NONZERO, so an unwritten register cannot pass this");

    /* ---- 3. exactly once per trigger ----------------------------------- */
    printf("\n-- 3. the capture is latched, not mirrored --\n");
    rig.data.shared.scales[1].speed = -1;      /* live registers move on... */
    rig.data.shared.scales[0].speed = -2;
    rig.data.shared.servo.stepsToGo = 999;
    for (int i = 0; i < 25; i++) rig.step(Z_TRIG_PASS1 - i);  /* still coasting past */
    Snapshot held = readSnapshot(rig.els());
    check(rig.els().active == 1, "still stopped (no resume yet)");
    checkEq(held.seq, 1, "seq did not move: one capture per trigger, not per tick");
    check(sameSnapshot(held, p1),
          "…and no payload field followed the live registers afterwards");

    /* ---- 4. the next pass captures again, with new values --------------- */
    printf("\n-- 4. a second pass is a second sample --\n");
    for (int i = 0; i < 2; i++) rig.step(Z_CLEAR);   /* operator retracts clear */
    rig.resume();
    rig.triggerAt(Z_TRIG_PASS2, /*zSpeed*/ -1750, /*spindle*/ 38000,
                  /*stepsToGo*/ 41);
    Snapshot p2 = readSnapshot(rig.els());
    check(rig.els().active == 1, "the stop fired again");
    checkEq(p2.seq, 2, "stopTriggerSeq incremented once more");
    checkEq(p2.z, Z_TRIG_PASS2, "stopTriggerZ re-captured at the new position");
    checkEq(p2.zSpeed, -1750, "stopTriggerZSpeed re-captured");
    checkEq(p2.spindleSpeed, 38000, "stopTriggerSpindleSpeed re-captured");
    checkEq(p2.stepsToGo, 41, "stopTriggerStepsToGo re-captured");
    check(p2.z != p1.z && p2.zSpeed != p1.zSpeed,
          "every pass is its own sample -- the block is not write-once");

    /* ---- 5. a quiet trigger is a CAPTURE, not an absence ---------------- */
    printf("\n-- 5. zero steps to go is an answer, and it still gets a seq --\n");
    for (int i = 0; i < 2; i++) rig.step(Z_CLEAR);
    rig.resume();
    check(p2.stepsToGo != 0,
          "fixture precondition: the PREVIOUS capture was nonzero, so the zero "
          "below is a value that arrived and not a register that never moved");
    rig.triggerAt(Z_TRIG_PASS1, /*zSpeed*/ -3000, /*spindle*/ 60000,
                  /*stepsToGo*/ 0);
    checkEq(rig.els().stopTriggerSeq, 3,
            "the seq moves even though the payload is 'nothing commanded'");
    checkEq(rig.els().stopTriggerStepsToGo, 0,
            "stopTriggerStepsToGo publishes the zero -- purely mechanical coast");
    checkEq(rig.els().stopTriggerZSpeed, -3000,
            "…and the rest of the capture is still real");

    /* ---- 6. the register carries what stopPosition cannot --------------- */
    printf("\n-- 6. the trigger position is NOT the threshold --\n");
    checkEq(rig.els().stopPosition, Z_STOP_POS,
            "stopPosition never moved across the three passes");
    check(p1.z != rig.els().stopPosition && p2.z != rig.els().stopPosition,
          "the carriage was already PAST the threshold when the stop fired");
    check(p1.z != p2.z,
          "…by a different amount each pass, which is exactly the term a host "
          "substituting stopPosition throws away");

    /* ---- 7. seq BEFORE payload, by address ------------------------------ */
    printf("\n-- 7. the ordering invariant that makes a torn frame harmless --\n");
    {
        /* Modbus FC3 copies registers in ascending address order and the ISR
         * can land between any two, so a lower-addressed field is sampled
         * EARLIER IN TIME. seq first => a torn frame is (stale seq, new
         * payload), which a host edge-detecting the seq re-reads harmlessly.
         * Reversed, it is (new seq, stale payload) -- an ack vouching for a
         * capture that is not there, which is the 2026-08-22 takeupSeq bug.
         * reflex-ui pins the same property from the other side in
         * ui/tests/test_register_map_contract.py. */
        const size_t seq = offsetof(elsStop_t, stopTriggerSeq);
        const size_t z   = offsetof(elsStop_t, stopTriggerZ);
        const size_t zs  = offsetof(elsStop_t, stopTriggerZSpeed);
        const size_t stg = offsetof(elsStop_t, stopTriggerStepsToGo);
        const size_t sps = offsetof(elsStop_t, stopTriggerSpindleSpeed);
        printf("   offsets: seq=%u z=%u zSpeed=%u stepsToGo=%u spindleSpeed=%u\n",
               (unsigned)seq, (unsigned)z, (unsigned)zs, (unsigned)stg,
               (unsigned)sps);
        /* Not vacuous: five distinct offsets, so "all zero" cannot pass. */
        check(z != seq && zs != z && stg != zs && sps != stg,
              "the five fields are distinct registers");
        check(seq < z,   "stopTriggerSeq precedes stopTriggerZ");
        check(seq < zs,  "stopTriggerSeq precedes stopTriggerZSpeed");
        check(seq < stg, "stopTriggerSeq precedes stopTriggerStepsToGo");
        check(seq < sps, "stopTriggerSeq precedes stopTriggerSpindleSpeed");
    }

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
