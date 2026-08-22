/*
 * Arming the ELS stop with Z already past it — the write order IS the safety.
 *
 * WHY THIS TEST EXISTS
 * --------------------
 * reflex-ui's arm_idle_stop() carried a Z-past-stop refusal justified by a
 * heritage comment: "arming when past stop_z would cause immediate ELS fire
 * -> backlash takeup". Read against the actual ISR (2026-08-17) that claim
 * is false FOR THE ORDER THE UI ACTUALLY WRITES: active = 1 is written
 * before enable = 1, the trigger test requires !active (Ramps.c stop-fire
 * gate), so the firmware cannot fire during arming — it just holds. The
 * refusal was therefore not protecting anything; it was CREATING the only
 * unprotected engaged state in the system (no hold, sync armed, banner
 * apologizing for it). The refusal is deleted on the strength of this test.
 *
 * WHAT IS PINNED
 * --------------
 *  A. THE CLAIM. Arm past the stop via the real order (active first, an ISR
 *     pass between the two register writes, like two Modbus transactions):
 *     the machine holds (sync gated, nothing banked, nothing latched), a
 *     retract + resume then behaves exactly like a fresh job's first pass —
 *     no phase correction (referenceLatched is 0: the enable rising edge
 *     reset it and no fire ever ran), sync feeds, and the stop fires
 *     properly at the real threshold with a fresh latch. A and B run with
 *     backlashSteps = 0: since 2026-08-21 a first pass WITH a configured
 *     backlash does take up and confirm before sync releases (pinned in
 *     els_takeup_confirm_test, which has the lash model this rig lacks);
 *     that is orthogonal to the write-order claim made here.
 *
 *  B. THE CONTROL. The identical flow armed from the CLEAR side. Every
 *     observable checkpointed in A must match — arming past the stop is
 *     indistinguishable from arming clear of it, which is the exact claim
 *     that justifies deleting the UI refusal.
 *
 *  C. THE HERITAGE HAZARD, kept executable. Arm with enable ALONE (active
 *     left 0 — the order the old comment was presumably written against)
 *     with Z past the stop and hysteresis 0: the firmware fires on the next
 *     pass and latches the thread reference AT THE BOGUS POSITION, and the
 *     next resume banks a backlash takeup against it. The comment described
 *     a real hazard of a DIFFERENT write order. This scenario is why
 *     active-before-enable in arm_idle_stop is load-bearing and must not be
 *     "simplified" away.
 *
 * Drives the REAL Core/Src/Ramps.c ISR with stubbed externals — same
 * pattern as els_stop_resume_relatch_test.cpp, and the Rig here is copied
 * from it.
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
/* Fixture — copied from els_stop_resume_relatch_test.cpp              */
/* ------------------------------------------------------------------ */

static const int32_t SPINDLE_COUNTS_PER_PASS = 40;
static const int32_t Z_CLEAR    = 1000;
static const int32_t Z_PAST     = -5;      /* just past a stop at 0 */
static const int32_t Z_STOP_POS = 0;
static const int16_t Z_STOP_DIR = -1;      /* stop when Z <= stopPosition */
static const float   SENTINEL   = 1.0e30f;
static const uint32_t BACKLASH  = 300;

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int32_t            spindleCnt;
    int32_t            zCnt;

    void init(int32_t zStart, int32_t hysteresisCounts = 500,
              uint32_t backlash = BACKLASH) {
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
        data.shared.elsStop.threadPitchSteps = 533.333f;
        data.shared.elsStop.zCountsPerPitch  = 846.667f;
        data.shared.elsStop.backlashSteps    = backlash;
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

    void step(int32_t zTarget) {
        spindleCnt += SPINDLE_COUNTS_PER_PASS;
        zCnt        = zTarget;
        tim[0].CNT  = (uint32_t)spindleCnt;
        tim[1].CNT  = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }
};

static int failures = 0;

static void check(bool ok, const char *label) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", label);
    if (!ok) failures++;
}

/* ------------------------------------------------------------------ */
/* Scenario driver for A and B: full arm/hold/retract/resume/fire      */
/* cycle; the ONLY difference between them is zAtArm.                  */
/* ------------------------------------------------------------------ */

struct Checkpoints {
    /* after arming (active then enable, ISR pass between the writes) */
    uint16_t activeAfterArm;
    uint16_t latchedAfterArm;
    uint16_t takeupAfterArm;
    int32_t  stepsToGoAfterArm;
    /* after several held passes with the spindle turning */
    int32_t  desiredDriftHeld;
    /* the resume pass (SW active = 0, Z clear) */
    uint16_t activeAfterResume;
    uint16_t takeupAfterResume;
    int32_t  stepsToGoAfterResume;
    bool     phaseCorrectionRan;
    /* sync alive after resume */
    int32_t  desiredDriftCutting;
    /* the fire at the real threshold */
    uint16_t activeAfterFire;
    uint16_t latchedAfterFire;
};

static Checkpoints runCycle(int32_t zAtArm) {
    Rig rig;
    rig.init(zAtArm, 500, /*backlash*/ 0);   /* see the header: A/B are about
                                               * write order, not the take-up */
    Checkpoints c{};

    /* Settle passes, disengaged. */
    for (int i = 0; i < 3; i++) rig.step(zAtArm);

    /* --- Arm, real order: active first, a pass, then enable. --- */
    rig.data.shared.elsStop.active = 1;
    rig.step(zAtArm);                       /* Modbus gap between the writes */
    rig.data.shared.elsStop.enable = 1;
    for (int i = 0; i < 3; i++) rig.step(zAtArm);

    c.activeAfterArm    = rig.data.shared.elsStop.active;
    c.latchedAfterArm   = rig.data.shared.elsStop.referenceLatched;
    c.takeupAfterArm    = rig.data.shared.elsStop.takeupPending;
    c.stepsToGoAfterArm = rig.data.shared.servo.stepsToGo;

    /* --- Held, spindle turning: sync must be gated. --- */
    int32_t desiredBefore = (int32_t)rig.data.shared.servo.desiredSteps;
    for (int i = 0; i < 5; i++) rig.step(zAtArm);
    c.desiredDriftHeld = (int32_t)rig.data.shared.servo.desiredSteps - desiredBefore;

    /* --- Operator retracts clear (physical carriage move, still held). --- */
    for (int i = 0; i < 2; i++) rig.step(Z_CLEAR);

    /* --- Cut: SW clears active. --- */
    rig.data.shared.elsStop.lastIdealAdvance = SENTINEL;
    rig.data.shared.elsStop.active = 0;
    rig.step(Z_CLEAR);

    c.activeAfterResume    = rig.data.shared.elsStop.active;
    c.takeupAfterResume    = rig.data.shared.elsStop.takeupPending;
    c.stepsToGoAfterResume = rig.data.shared.servo.stepsToGo;
    c.phaseCorrectionRan   = (rig.data.shared.elsStop.lastIdealAdvance != SENTINEL);

    /* --- Sync must be alive again while cutting. --- */
    desiredBefore = (int32_t)rig.data.shared.servo.desiredSteps;
    for (int i = 0; i < 5; i++) rig.step(Z_CLEAR);
    c.desiredDriftCutting = (int32_t)rig.data.shared.servo.desiredSteps - desiredBefore;

    /* --- Feed to the stop: proper fire, fresh latch. --- */
    rig.step(Z_PAST);
    rig.step(Z_PAST);
    c.activeAfterFire  = rig.data.shared.elsStop.active;
    c.latchedAfterFire = rig.data.shared.elsStop.referenceLatched;
    return c;
}

int main() {
    printf("=== arming the ELS stop with Z already past it ===\n");

    /* ---------------- A: THE CLAIM ------------------------------------- */
    printf("\n-- A: arm PAST the stop, real write order (active first) --\n");
    Checkpoints a = runCycle(Z_PAST);
    check(a.activeAfterArm == 1,  "arm past stop: machine HOLDS (active stays 1)");
    check(a.latchedAfterArm == 0, "arm past stop: no fire ran, so no reference latch");
    check(a.takeupAfterArm == 0,  "arm past stop: nothing pending");
    check(a.stepsToGoAfterArm == 0, "arm past stop: nothing banked in stepsToGo");
    check(a.desiredDriftHeld == 0,
          "held past stop with spindle turning: sync fully gated (desiredSteps still)");
    check(a.activeAfterResume == 0, "resume after retract: 1->0 edge survives");
    check(a.takeupAfterResume == 0,
          "resume: no takeup -- backlashSteps is 0 here; the first-pass take-up is els_takeup_confirm_test's");
    check(a.stepsToGoAfterResume == 0, "resume: nothing banked");
    check(!a.phaseCorrectionRan, "resume: no phase correction against a stale latch");
    check(a.desiredDriftCutting != 0, "cutting: sync alive again (desiredSteps moves)");
    check(a.activeAfterFire == 1,  "feed to threshold: stop fires properly");
    check(a.latchedAfterFire == 1, "…and latches a FRESH thread reference");

    /* ---------------- B: THE CONTROL ----------------------------------- */
    printf("\n-- B: control — identical flow armed from the CLEAR side --\n");
    Checkpoints b = runCycle(Z_CLEAR);
    bool identical =
        b.activeAfterArm    == a.activeAfterArm    &&
        b.latchedAfterArm   == a.latchedAfterArm   &&
        b.takeupAfterArm    == a.takeupAfterArm    &&
        b.stepsToGoAfterArm == a.stepsToGoAfterArm &&
        b.desiredDriftHeld  == a.desiredDriftHeld  &&
        b.activeAfterResume == a.activeAfterResume &&
        b.takeupAfterResume == a.takeupAfterResume &&
        b.stepsToGoAfterResume == a.stepsToGoAfterResume &&
        b.phaseCorrectionRan   == a.phaseCorrectionRan   &&
        (b.desiredDriftCutting != 0) == (a.desiredDriftCutting != 0) &&
        b.activeAfterFire   == a.activeAfterFire   &&
        b.latchedAfterFire  == a.latchedAfterFire;
    check(identical,
          "every checkpoint matches scenario A — arming past the stop is "
          "indistinguishable from arming clear of it");

    /* ---------------- C: THE HERITAGE HAZARD --------------------------- */
    /* enable-only arming (active left 0), loose hysteresis: the fire runs at
     * the bogus position and the next resume banks a takeup against it. This
     * is the hazard the deleted UI refusal's comment described — real, but
     * only for THIS write order. It is why active-before-enable in
     * arm_idle_stop is load-bearing. */
    printf("\n-- C: heritage hazard — enable-ONLY arming, hysteresis 0 --\n");
    Rig rig;
    rig.init(Z_PAST, /*hysteresis*/ 0);
    for (int i = 0; i < 3; i++) rig.step(Z_PAST);
    rig.data.shared.elsStop.enable = 1;     /* active deliberately left 0 */
    rig.step(Z_PAST);
    rig.step(Z_PAST);
    check(rig.data.shared.elsStop.active == 1,
          "firmware fires on its own during enable-only arming past the stop");
    check(rig.data.shared.elsStop.referenceLatched == 1,
          "…and latches the thread reference AT THE BOGUS POSITION");
    rig.step(Z_CLEAR);                      /* operator retracts CLEAR (+Z side —
                                             * stopDirection -1 means more-negative is
                                             * deeper past, not clear), still held */
    rig.data.shared.elsStop.active = 0;     /* the next resume */
    rig.step(Z_CLEAR);                      /* fire test false on the clear side, so
                                             * the 1->0 edge survives to the resume path */
    check(rig.data.shared.elsStop.takeupPending == 1,
          "…and the resume banks a backlash takeup against that bogus latch — "
          "the hazard the heritage comment described, for THIS order only");

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
