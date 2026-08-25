/*
 * CLOSED-LOOP re-sync phase test: does the superimposed correction jog
 * actually converge the thread phase while the spindle is turning?
 *
 * WHY THIS EXISTS (2026-08-24 bench session 3). With the re-sync wizard,
 * powered-spindle passes came out misaligned on more than half of attempts
 * while hand-cranked passes never missed once. The DESIGN says that cannot
 * happen: the correction jog drains through servo.stepsToGo into desiredSteps
 * WHILE sync independently adds spindle deltas to desiredSteps, so the phase
 * error should decay from N to zero over the jog and stay there -- the jog is
 * superimposed, not sequenced. Reading Ramps.c found no defect in that story.
 * So this file runs the story: the PRODUCTION ISR, a spindle that keeps
 * counting through latch -> Cut -> take-up -> confirm -> correction -> sync,
 * and a Z scale that follows the servo's emitted steps the way a coupled
 * drivetrain does. If the design is sound these scenarios converge; if the
 * bench behaviour reproduces, the defect is in this loop and can be bisected
 * at a desk.
 *
 * WHAT THE KINEMATIC MODEL IS, AND IS NOT. Z counts = emitted steps times the
 * exact counts-per-step ratio, quantized, one tick behind. No backlash inside
 * the model (backlashSteps is still configured, so the take-up and its
 * confirmation gate run -- against a drivetrain with zero actual lash the
 * take-up just moves the carriage its whole distance, which the gate happily
 * confirms). No settle tail, no slip. Every one of those is a fidelity this
 * file deliberately does not claim: if THIS loop fails to converge, no amount
 * of added realism will save the machine; if it converges, the bench defect
 * lives in what is missing here (lash state, settle, host-side writes at Cut),
 * and that is a finding too.
 *
 * The phase error is computed in this file from the raw counters, never read
 * from lastPhaseError -- the register under suspicion must not grade itself.
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
/* ------------------------------------------------------------------ */
/* Fixture: geometry from els_stop_resume_relatch_test, stop moved far  */
/* away so a measurement pass never triggers it.                        */
/* ------------------------------------------------------------------ */

static const int32_t SYNC_NUM   = -2;      /* spindle counts -> servo steps */
static const int32_t SYNC_DEN   = 15;
static const float   PITCH      = 533.333f;   /* servo steps per thread pitch */
static const float   ZCPP       = 846.667f;   /* Z counts per thread pitch */
static const int32_t Z_START    = 1000;
static const int32_t BACKLASH   = 160;        /* steps */

/* cuttingDir = sign(SYNC_NUM) = -1 (pitch*zcpp > 0, no flip);
 * droSign = stopDirection * cuttingDir = (-1)*(-1) = +1. A cutting-direction
 * (negative) servo move takes Z negative, so stopDirection -1 means "stop when
 * Z <= stopPosition" -- consistent, and the stop is parked out of reach. */
static const int16_t STOP_DIR   = -1;
static const int32_t STOP_POS   = -2000000;

/* Spindle counts per thread pitch: PITCH steps * DEN/|NUM| = 4000. */
static const int32_t SPINDLE_COUNTS_PER_PITCH = 4000;

static int failures = 0;
static void check(bool ok, const char *what) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) failures++;
}

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int64_t            spindleCnt;
    int32_t            zCnt;
    double             countsPerStep;   /* exact ZCPP/PITCH, the model's one truth */
    int64_t            latchSp, latchZ; /* datum recorded independently of the fw */

    void init() {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        spindleCnt = 0;
        zCnt       = Z_START;
        countsPerStep = (double)ZCPP / (double)PITCH;
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
        data.shared.scales[0].syncRatioNum = SYNC_NUM;
        data.shared.scales[0].syncRatioDen = SYNC_DEN;
        data.shared.scales[0].syncEnable   = 1;

        /* Acceleration chosen so a correction jog RAMPS TO THE EMISSION CAP:
         * on elspi the jog's peak demand plus sync demand exceeds the pulse
         * generator's rate cap, and that contention window is part of what is
         * under test. maxSpeed 1e5 steps/s at a 10 us tick = 1 step/tick, the
         * same ceiling the emitter has. */
        data.shared.servo.maxSpeed     = 100000.0f;
        data.shared.servo.acceleration = 20000000.0f;
        data.shared.servo.servoDir     = 1;
        data.shared.fastData.servoMode = 1;

        data.shared.elsStop.scaleIndex           = 1;
        data.shared.elsStop.stopPosition         = STOP_POS;
        data.shared.elsStop.stopDirection        = STOP_DIR;
        data.shared.elsStop.threadPitchSteps     = PITCH;
        data.shared.elsStop.zCountsPerPitch      = ZCPP;
        data.shared.elsStop.backlashSteps        = (uint32_t)BACKLASH;
        data.shared.elsStop.hysteresis           = 0;
        data.shared.elsStop.calMotionThreshCounts= 2;   /* floor: gate confirms */
        data.shared.elsStop.enable               = 0;

        tim[0].CNT = (uint32_t)spindleCnt;
        tim[1].CNT = (uint32_t)zCnt;
        data.scalesDeltaPos[0].position = (int32_t)spindleCnt;
        data.scalesDeltaPos[1].position = zCnt;
        data.shared.scales[0].position  = (int32_t)spindleCnt;
        data.shared.scales[1].position  = zCnt;
        data.scalesSyncDeltaPos[0].oldPosition = (int32_t)spindleCnt;
        data.scalesSyncDeltaPos[1].oldPosition = zCnt;
    }

    /* One ISR pass with the loop CLOSED: Z is wherever the servo's emitted
     * steps put it (one tick behind, as a real scale read is). */
    void step(int32_t spindleCountsThisTick) {
        spindleCnt += spindleCountsThisTick;
        zCnt = Z_START + (int32_t)llround(
            (double)(int32_t)data.shared.servo.currentSteps * countsPerStep);
        tim[0].CNT = (uint32_t)spindleCnt;
        tim[1].CNT = (uint32_t)zCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }

    void run(int ticks, int32_t spindlePerTick) {
        for (int i = 0; i < ticks; i++) step(spindlePerTick);
    }

    /* Phase error in servo steps, folded to +-pitch/2, computed from the RAW
     * counters against the recorded datum. */
    double phaseErr() const {
        double ideal  = (double)(spindleCnt - latchSp) * SYNC_NUM / SYNC_DEN;
        double actual = ((double)(zCnt - latchZ)) * PITCH / ZCPP;
        double e = ideal - actual;              /* droSign = +1 */
        e = fmod(e, (double)PITCH);
        if (e >  PITCH / 2) e -= PITCH;
        if (e < -PITCH / 2) e += PITCH;
        return e;
    }

    /* enable -> stopped-at-shoulder -> manual latch. */
    void armAndLatch() {
        run(3, 0);
        data.shared.elsStop.enable = 1;
        run(3, 0);                       /* rising edge consumed */
        data.shared.elsStop.active = 1;  /* stopped at the shoulder: sync gated */
        run(3, 0);
        data.shared.elsStop.latchCommand = 1;
        step(0);
        latchSp = spindleCnt;
        latchZ  = zCnt;
    }

    /* Operator presses Cut; run the whole resume at the given spindle rate
     * until the take-up confirms (takeupSeq edge), then keep running. Returns
     * ticks the take-up needed, or -1 if it never confirmed. */
    int cut(int32_t spindlePerTick, int maxTicks = 60000) {
        uint16_t seq0 = data.shared.elsStop.takeupSeq;
        data.shared.elsStop.active = 0;
        for (int i = 0; i < maxTicks; i++) {
            step(spindlePerTick);
            if (data.shared.elsStop.takeupSeq != seq0) return i;
        }
        return -1;
    }
};

static void report(const Rig &r, const char *tag) {
    printf("    %-26s phaseErr %+8.2f steps (%.3f pitch)  corr %+8.2f\n",
           tag, r.phaseErr(), r.phaseErr() / PITCH,
           (double)r.data.shared.elsStop.lastCorrection);
}

int main() {
    printf("=== closed-loop re-sync: does the superimposed jog converge? ===\n");
    printf("=== pitch %.1f steps = %d spindle counts; emission cap 1 step/tick ===\n\n",
           (double)PITCH, SPINDLE_COUNTS_PER_PITCH);

    /* -------- T1: hand-crank analog (control) ------------------------- */
    printf("-- T1. spindle STATIONARY through Cut, take-up and jog; spin after --\n");
    {
        Rig r; r.init(); r.armAndLatch();
        int t = r.cut(0);
        check(t >= 0, "take-up confirmed");
        r.run(20000, 0);                 /* let the jog fully drain */
        report(r, "after jog, still parked");
        r.run(150000, 3);                /* now spin: 0.4 steps/tick demand */
        report(r, "after 150k spinning ticks");
        check(std::fabs(r.phaseErr()) <= 2.0,
              "T1 hand-crank analog converges (bench: never missed)");
    }

    /* -------- T2: powered analog -------------------------------------- */
    printf("\n-- T2. spindle SPINNING (40%% of emission cap) through everything --\n");
    {
        Rig r; r.init(); r.armAndLatch();
        int t = r.cut(3);
        check(t >= 0, "take-up confirmed with the spindle turning");
        report(r, "at confirm");
        r.run(2000, 3);   report(r, "+2k ticks");
        r.run(8000, 3);   report(r, "+10k ticks");
        r.run(40000, 3);  report(r, "+50k ticks");
        r.run(100000, 3); report(r, "+150k ticks");
        check(std::fabs(r.phaseErr()) <= 2.0,
              "T2 powered analog converges (bench: failed >half the time)");
    }

    /* -------- T3: powered, latch-to-Cut delay varied -------------------
     * The correction magnitude depends on the spindle angle at confirm, so
     * sweep the delay to sample small and near-pitch corrections alike. */
    printf("\n-- T3. powered, five latch-to-Cut delays (correction magnitudes) --\n");
    {
        bool all = true;
        for (int d = 0; d < 5; d++) {
            Rig r; r.init(); r.armAndLatch();
            r.data.shared.elsStop.active = 1;   /* still at the shoulder */
            r.run(700 * d, 3);                  /* spindle turns, sync gated */
            int t = r.cut(3);
            if (t < 0) { check(false, "take-up confirmed"); all = false; continue; }
            r.run(150000, 3);
            char tag[64];
            snprintf(tag, sizeof tag, "delay %4d ticks", 700 * d);
            report(r, tag);
            if (std::fabs(r.phaseErr()) > 2.0) all = false;
        }
        check(all, "T3 every correction magnitude converges");
    }

    /* -------- T4: Evan's discriminating experiment ---------------------
     * Latch, hand-rotate the spindle visibly out of sync while still at the
     * shoulder, THEN Cut with the spindle stationary. On the bench this always
     * aligned; it is what rules out a stale-datum theory. */
    printf("\n-- T4. latch, rotate out of sync, THEN Cut with spindle stopped --\n");
    {
        Rig r; r.init(); r.armAndLatch();
        r.data.shared.elsStop.active = 1;
        r.run(1100, 3);                  /* 3300 counts ~ 0.8 pitch of rotation */
        int t = r.cut(0);
        check(t >= 0, "take-up confirmed");
        r.run(20000, 0);
        r.run(150000, 3);
        report(r, "after 150k spinning ticks");
        check(std::fabs(r.phaseErr()) <= 2.0,
              "T4 pre-rotated, stationary Cut converges (bench: always aligned)");
    }

    /* -------- T5: powered at 80% of the emission cap ------------------- */
    printf("\n-- T5. spindle at 80%% of emission cap (jog must fight for slots) --\n");
    {
        Rig r; r.init(); r.armAndLatch();
        int t = r.cut(6);
        check(t >= 0, "take-up confirmed");
        r.run(300000, 6);
        report(r, "after 300k ticks");
        check(std::fabs(r.phaseErr()) <= 2.0,
              "T5 converges even with the jog contending for emission slots");
    }

    printf("\n=== %s === (%d failing check%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
