/*
 * ISR-level tests for the THREAD-PHASE OFFSET register/command half.
 *
 * WHAT THE FEATURE IS
 * -------------------
 * A way to cut a thread groove WIDER than the tool that cuts it. The operator
 * cuts the groove, shifts the CONTROLLER's idea of phase by a step-over smaller
 * than the cutter, and cuts again, until the groove reaches the width he wants
 * -- no re-indexing, and the datum is never re-established.
 * elsStop.phaseOffsetSteps is summed into phaseError at every phase correction,
 * so the carriage returns to the thread displaced by that amount.
 * els_phase_offset_test already pins the arithmetic (els_phase.h); THIS file
 * pins the register plumbing around it -- the half that decides whether the
 * number ever reaches the math. Nothing below depends on the use case: the
 * register is a distance, and the same plumbing carries the X-depth-derived
 * compound-infeed offset els_phase.h names as its second source.
 *
 * CONTRACT UNDER TEST (Ramps.c: the phaseOffsetCommand block, the enable-edge
 * reset, and the applyPhaseCorrection() call site)
 * -----------------------------------------------------------------------
 *  1. ACCEPT: with enable == 1, a pending command is cleared in one ISR pass,
 *     Pending becomes the live total, and phaseOffsetSeq increments once.
 *  2. REFUSE: with enable == 0 the command is consumed with NO seq increment
 *     and no change to the total. The missing ack IS the refusal -- an offset
 *     outside a job would be wiped by the next enable edge anyway.
 *  3. PENDING IS INERT WITHOUT THE COMMAND: this is what makes a 32-bit value
 *     cross a 16-bit register bus without a lock. If the ISR ever read Pending
 *     unprompted, a host mid-write would be visible as a half-written int32.
 *  4. REPLACE, NOT ACCUMULATE: the firmware holds ONE absolute total.
 *     Accumulation is the host's job (read total, add entry, write back), which
 *     is what lets the UI show a running total it can also clear.
 *  5. CLEARED BY A NEW JOB, SURVIVES A PASS: the enable 0->1 edge zeroes it
 *     alongside referenceLatched, because an offset is meaningless without the
 *     datum it offsets. Per-pass stop/resume must NOT disturb it -- a groove is
 *     widened over many passes, several of them at each offset as the tool is
 *     fed to depth.
 *  6. REACHES THE MATH: the correction computed at the next resume differs from
 *     the zero-offset correction by exactly the offset. Without this case every
 *     other assertion here could pass against a call site still passing 0.
 *  7. THE FRAME IS THE MACHINE, NOT THE CUT: pinned for BOTH cuttingDir signs.
 *     See the SIGN LIVES IN THE MACHINE FRAME note on the register in Ramps.h.
 *     This case exists to make that property VISIBLE and its future correction
 *     deliberate; it is not an endorsement that the frame is the right one.
 *
 * Drives the REAL Core/Src/Ramps.c ISR. Stub set and fixture geometry are
 * lifted verbatim from els_manual_latch_test.cpp.
 *
 * NOTE THE FIXTURE POLARITY: SYNC_NUM is negative and threadPitchSteps *
 * zCountsPerPitch is positive, so the inherited rig is a cuttingDir == -1
 * machine. Case 7 builds the +1 variant explicitly rather than assuming.
 *
 * MUTATION-TESTED 2026-08-22. Every mutation below was actually applied to
 * Ramps.c one at a time, the listed failure count observed, and the mutation
 * reverted. All eight were killed; none survived.
 *
 *   M1 drop phaseOffsetSeq++                      -> 4 failures (1, 4)
 *   M2 drop Steps = Pending                       -> 9 failures (1, 4, 5, 6, 7)
 *   M3 apply regardless of enable                 -> 2 failures (2)
 *   M4 accumulate in firmware (Steps += Pending)  -> 2 failures (4)
 *   M5 read Pending outside the command guard     -> 3 failures (2, 3, 5)
 *   M6 never clear on the enable 0->1 edge        -> 1 failure  (5)
 *   M7 call site passes 0                         -> 3 failures (6, 7)
 *   M8 call site converts to the cutting frame    -> 2 failures (6, 7)
 *
 * M7 is the one worth staring at: it leaves the register plumbing perfect and
 * only severs the wire to the math, and 23 of 26 assertions still pass under
 * it. Cases 6 and 7 are the entire defense against that.
 *
 * M8 kills case 7's cuttingDir == -1 assertion and case 6 (whose fixture is
 * also cuttingDir == -1) while leaving the +1 assertion passing -- so the pair
 * does not merely detect that the frame changed, it names WHICH polarity moved.
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

int main() {
    printf("=== ELS thread-phase offset (register/command half) ===\n\n");

    /* ---------------- 1. ACCEPT --------------------------------------- */
    /* MUTATION: drop the phaseOffsetSeq++ -> the ack assertion fails here and
     * in case 4. Drop the assignment of Steps = Pending -> the total
     * assertions fail here, in 4, and in 6. */
    printf("-- 1. accept: apply inside a job --\n");
    {
        Rig rig;
        rig.init(0);
        rig.armJob();
        check(rig.data.shared.elsStop.phaseOffsetSteps == 0, "total starts at zero");

        rig.data.shared.elsStop.phaseOffsetPending = 177;
        rig.data.shared.elsStop.phaseOffsetCommand = 1;
        rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.phaseOffsetCommand == 0, "command cleared in one pass");
        check(rig.data.shared.elsStop.phaseOffsetSeq == 1, "phaseOffsetSeq acked exactly once");
        check(rig.data.shared.elsStop.phaseOffsetSteps == 177, "pending became the live total");

        /* The ack is monotonic and one-per-apply: a second pass must not
         * re-consume a command the firmware already cleared. */
        rig.step(Z_CLEAR);
        check(rig.data.shared.elsStop.phaseOffsetSeq == 1, "no second ack from a stale command");
    }

    /* ---------------- 2. REFUSE outside a job -------------------------- */
    /* MUTATION: hoist the apply out of the `enable != 0` guard -> all three
     * assertions fail: the total takes, the seq acks, and an offset exists
     * with no job to own it. */
    printf("\n-- 2. refuse: apply with enable == 0 consumes without ack --\n");
    {
        Rig rig;
        rig.init(0);
        rig.step(Z_CLEAR);              /* settle; enable stays 0 */
        rig.data.shared.elsStop.phaseOffsetPending = 250;
        rig.data.shared.elsStop.phaseOffsetCommand = 1;
        rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.phaseOffsetCommand == 0, "command still consumed (not left pending)");
        check(rig.data.shared.elsStop.phaseOffsetSeq == 0, "no ack -- the missing edge IS the refusal");
        check(rig.data.shared.elsStop.phaseOffsetSteps == 0, "total untouched");
    }

    /* ---------------- 3. PENDING IS INERT WITHOUT THE COMMAND ---------- */
    /* THE LOCK-FREE PROPERTY, stated as a test. The host writes Pending as two
     * 16-bit registers and only then sets Command; the ISR reading Pending at
     * any other time is precisely the torn read that ordering prevents.
     * MUTATION: assign Steps = Pending unconditionally (outside the command
     * guard) -> this case fails while cases 1 and 2 still pass, which is the
     * whole reason it is separate from them. */
    printf("\n-- 3. pending alone changes nothing (the lock-free property) --\n");
    {
        Rig rig;
        rig.init(0);
        rig.armJob();
        rig.data.shared.elsStop.phaseOffsetPending = 999;   /* mid-write, no command */
        for (int i = 0; i < 5; i++) rig.step(Z_CLEAR);

        check(rig.data.shared.elsStop.phaseOffsetSteps == 0, "total ignores an uncommanded pending");
        check(rig.data.shared.elsStop.phaseOffsetSeq == 0, "no ack for an uncommanded pending");
    }

    /* ---------------- 4. REPLACE, NOT ACCUMULATE ----------------------- */
    /* MUTATION: change the apply to `Steps += Pending` -> this case fails with
     * 800 instead of 500. Accumulating in firmware would ALSO break the host's
     * clear-to-zero, since writing 0 would then be a no-op rather than a
     * reset. */
    printf("\n-- 4. the firmware holds ONE absolute total --\n");
    {
        Rig rig;
        rig.init(0);
        rig.armJob();

        rig.data.shared.elsStop.phaseOffsetPending = 300;
        rig.data.shared.elsStop.phaseOffsetCommand = 1;
        rig.step(Z_CLEAR);
        check(rig.data.shared.elsStop.phaseOffsetSteps == 300, "first apply");

        rig.data.shared.elsStop.phaseOffsetPending = 500;
        rig.data.shared.elsStop.phaseOffsetCommand = 1;
        rig.step(Z_CLEAR);
        check(rig.data.shared.elsStop.phaseOffsetSteps == 500,
              "second apply REPLACES (host accumulates, firmware does not)");
        check(rig.data.shared.elsStop.phaseOffsetSeq == 2, "two applies, two acks");

        /* The host's Clear is just an apply of 0 -- it must not be swallowed. */
        rig.data.shared.elsStop.phaseOffsetPending = 0;
        rig.data.shared.elsStop.phaseOffsetCommand = 1;
        rig.step(Z_CLEAR);
        check(rig.data.shared.elsStop.phaseOffsetSteps == 0, "an apply of 0 clears the total");
        check(rig.data.shared.elsStop.phaseOffsetSeq == 3, "and still acks (a clear is not a refusal)");
    }

    /* ---------------- 5. CLEARED BY A NEW JOB, SURVIVES A PASS --------- */
    /* MUTATION: delete `phaseOffsetSteps = 0` from the enable rising edge ->
     * the new-job assertion fails, and the machine would carry a half-pitch
     * shift into the first pass of the NEXT thread with nothing on screen to
     * explain it. Conversely, clearing it on the ACTIVE edge instead -> the
     * survives-a-pass assertion fails, and a groove could not be widened over
     * more than one pass -- i.e. not at all, since it takes several. */
    printf("\n-- 5. dies with the job, survives the pass --\n");
    {
        Rig rig;
        rig.init(0);
        rig.armJob();
        rig.data.shared.elsStop.phaseOffsetPending = 420;
        rig.data.shared.elsStop.phaseOffsetCommand = 1;
        rig.step(Z_CLEAR);
        check(rig.data.shared.elsStop.phaseOffsetSteps == 420, "offset set for this job");

        /* Feed to the shoulder: the stop fires. Same job, next pass. */
        rig.step(Z_PAST);
        rig.step(Z_PAST);
        check(rig.data.shared.elsStop.active == 1, "stop trigger fired");
        check(rig.data.shared.elsStop.phaseOffsetSteps == 420, "offset survives the stop");

        rig.step(Z_CLEAR);                       /* retract clear */
        rig.data.shared.elsStop.active = 0;      /* SW resume */
        rig.step(Z_CLEAR);
        check(rig.data.shared.elsStop.phaseOffsetSteps == 420, "offset survives the resume");

        /* End the job and start a new one. */
        rig.data.shared.elsStop.enable = 0;
        rig.step(Z_CLEAR);
        rig.data.shared.elsStop.enable = 1;
        rig.step(Z_CLEAR);
        check(rig.data.shared.elsStop.phaseOffsetSteps == 0, "cleared by the enable 0->1 edge");
        check(rig.data.shared.elsStop.referenceLatched == 0, "alongside the datum it offset");
    }

    /* ---------------- 6. REACHES THE MATH ------------------------------ */
    /* Two IDENTICAL rigs differing only in the offset applied. Same step
     * sequence, so every other input to the correction is bit-identical, and
     * the difference in lastPhaseError is attributable to the offset alone.
     * The control rig applies an offset of ZERO rather than skipping the
     * apply, so both rigs burn the same number of ISR passes and their spindle
     * positions stay in lockstep.
     *
     * MUTATION: revert the call site to passing 0 -> delta becomes 0 and this
     * case fails ALONE. Every other case in this file passes against a
     * perfectly plumbed register that no math ever reads, which is exactly the
     * shape this case exists to catch. */
    printf("\n-- 6. the offset reaches applyPhaseCorrection --\n");
    {
        const int32_t OFFSET = 177;
        float phaseErr[2];

        for (int variant = 0; variant < 2; variant++) {
            Rig rig;
            rig.init(0);                 /* inline correction branch */
            rig.armJob();
            rig.data.shared.elsStop.latchCommand = 1;
            rig.step(Z_CLEAR);           /* datum */

            rig.data.shared.elsStop.phaseOffsetPending = variant ? OFFSET : 0;
            rig.data.shared.elsStop.phaseOffsetCommand = 1;
            rig.step(Z_CLEAR);

            rig.step(Z_PAST);            /* feed to the stop */
            rig.step(Z_PAST);
            rig.step(Z_CLEAR);           /* retract clear */

            rig.data.shared.elsStop.lastPhaseError = SENTINEL;
            rig.data.shared.elsStop.active = 0;      /* SW resume */
            rig.step(Z_CLEAR);

            check(rig.data.shared.elsStop.lastPhaseError != SENTINEL,
                  variant ? "correction ran (offset rig)" : "correction ran (control rig)");
            phaseErr[variant] = rig.data.shared.elsStop.lastPhaseError;
        }

        float delta = phaseErr[1] - phaseErr[0];
        printf("   control phaseError %.3f, offset phaseError %.3f, delta %.3f\n",
               (double)phaseErr[0], (double)phaseErr[1], (double)delta);
        check(std::fabs(delta - (float)OFFSET) < 0.01f,
              "phaseError shifted by exactly the offset, in leadscrew steps");
    }

    /* ---------------- 7. THE FRAME IS THE MACHINE, NOT THE CUT --------- */
    /* Pins the property documented on the register: the offset is summed into
     * phaseError raw, so it displaces phase in the MACHINE frame, and on a
     * cuttingDir == -1 machine an entry of X acts as pitch-X -- the same groove
     * (one turn along the same helix), widened on the OTHER flank. Asserted for
     * both polarities so that changing to a cutting-frame convention
     * (multiplying by cuttingDir) has to break a test on purpose rather than
     * pass unnoticed.
     *
     * The printed corrections are for the bench: they are what the carriage
     * actually does, and comparing them against a real widening cut -- which
     * flank of the groove actually opened up -- is the only thing that can
     * settle whether this frame is the right one.
     * MUTATION: multiply the offset by cuttingDir at the call site -> the
     * cuttingDir == -1 assertion fails and the +1 one passes, naming the
     * polarity that changed. */
    printf("\n-- 7. polarity pin: offset lands in the machine frame --\n");
    {
        const int32_t OFFSET = 177;

        for (int neg = 0; neg < 2; neg++) {
            int32_t syncNum = neg ? -2 : 2;
            float phaseErr[2], corr[2];

            for (int variant = 0; variant < 2; variant++) {
                Rig rig;
                rig.init(0);
                rig.data.shared.scales[0].syncRatioNum = syncNum;
                rig.armJob();
                rig.data.shared.elsStop.latchCommand = 1;
                rig.step(Z_CLEAR);

                rig.data.shared.elsStop.phaseOffsetPending = variant ? OFFSET : 0;
                rig.data.shared.elsStop.phaseOffsetCommand = 1;
                rig.step(Z_CLEAR);

                rig.step(Z_PAST);
                rig.step(Z_PAST);
                rig.step(Z_CLEAR);
                rig.data.shared.elsStop.active = 0;
                rig.step(Z_CLEAR);

                phaseErr[variant] = rig.data.shared.elsStop.lastPhaseError;
                corr[variant]     = rig.data.shared.elsStop.lastCorrection;
            }

            int cuttingDir = (syncNum > 0) ? 1 : -1;   /* pitch*zCounts > 0 in this fixture */
            printf("   cuttingDir %+d: phaseError %+.3f -> %+.3f ; correction %+.3f -> %+.3f\n",
                   cuttingDir, (double)phaseErr[0], (double)phaseErr[1],
                   (double)corr[0], (double)corr[1]);
            check(std::fabs((phaseErr[1] - phaseErr[0]) - (float)OFFSET) < 0.01f,
                  cuttingDir > 0
                    ? "cuttingDir +1: phaseError shifts by +offset (machine frame)"
                    : "cuttingDir -1: phaseError shifts by +offset TOO -- not mirrored (see Ramps.h)");
        }
    }

    printf("\n=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
