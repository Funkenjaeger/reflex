/*
 * els_command_test.cpp -- the ELS command channel (protocolVersion 13):
 * elsStop.command / commandSeq / commandResult, and DISENGAGE, its only
 * command (docs/design/els-command-channel.md).
 *
 * THE STATE x COMMAND TABLE. Every machine mode the firmware publishes
 * (els_machine_mode.h) is set up for real on the real ISR, confirmed by
 * elsDeriveMachineMode, and then sent DISENGAGE. Each row asserts the
 * outcome and the result code:
 *
 *   mode      result            and also
 *   OFF       OK
 *   IDLE      OK
 *   FEEDING   OK                (inside a job: enable 1, spindle turning)
 *   MOVING    OK                stepsToGo cancelled although enable was
 *                               already 0 (no falling edge to do it)
 *   JOG       OK
 *   HELD      OK                no take-up starts from the hold it releases
 *   TAKEUP    OK                the take-up in flight is abandoned
 *   CAL       OK_CAL_ABORTED    calResult = ELS_CAL_ERR_ABORTED on calSeq
 *
 * and after every ack: command 0, commandSeq +1, machineMode published OFF,
 * servoMode 0, every syncEnable 0, enable 0, active 0, takeupPending 0,
 * stepsToGo 0, no sync backlog, and 2000 further ISR passes with the spindle
 * turning and the task's re-assert modeled emit no step and start no
 * take-up.
 *
 * Then the channel's own rules:
 *   - the ack waits for the ISR: a tick with no ISR pass between intake and
 *     finish does not ack, the next one does;
 *   - a host syncEnable write landing inside the consumption (the Modbus task
 *     preempting servoEnableTask) does not get acked over: the ack waits for
 *     the next tick, which clears it again;
 *   - the re-assert is skipped on the consuming tick;
 *   - an unknown command is refused (ERR_UNKNOWN, seq +1) and changes nothing;
 *   - a command written while one is in flight waits in the register;
 *   - command 0 does nothing.
 *
 * The task tick is modeled as servoEnableTask orders it on hardware:
 * elsCommandIntake, the re-assert unless intake said skip, ISR passes (the
 * ISR preempts the task), mode publish, elsCommandFinish.
 */
extern "C" {
#include "Ramps.h"
#include "Scales.h"
#include "els_command.h"
#include "emulator_state.h"
}

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdarg>

extern "C" {

GPIO_TypeDef    emu_gpioa, emu_gpiob, emu_gpioc;
RCC_TypeDef     emu_rcc;
DWT_Type        emu_dwt;
CoreDebug_Type  emu_coreDebug;
EmulatorHardwareState emu_hw;

void emu_log_trace(const char *fmt, ...) { (void)fmt; }
void emu_log_event(const char *fmt, ...) { (void)fmt; }
void HAL_GPIO_Init(GPIO_TypeDef *p, GPIO_InitTypeDef *i) { (void)p; (void)i; }
void HAL_GPIO_WritePin(GPIO_TypeDef *p, uint16_t pin, GPIO_PinState s) { (void)p; (void)pin; (void)s; }
void HAL_GPIO_TogglePin(GPIO_TypeDef *p, uint16_t pin) { (void)p; (void)pin; }
HAL_StatusTypeDef HAL_TIM_Base_Start_IT(TIM_HandleTypeDef *h) { (void)h; return HAL_OK; }
HAL_StatusTypeDef HAL_TIM_Encoder_Start(TIM_HandleTypeDef *h, uint32_t ch) { (void)h; (void)ch; return HAL_OK; }
HAL_StatusTypeDef initScaleTimer(TIM_HandleTypeDef *h) { (void)h; return HAL_OK; }
void mbAppInit(uint16_t *r, uint16_t n, const uint16_t *id) { (void)r; (void)n; (void)id; }
int mbAppService(void) { return 0; }
uint16_t mbAppReplies(void) { return 0; }
uint32_t HAL_GetTick(void) { return 0; }

extern uint16_t servoCycles;
extern uint16_t servoCyclesCounter;

} /* extern "C" */

static int failures = 0;

static void check(bool ok, const char *fmt, ...) {
    va_list ap;
    printf("[%s] ", ok ? "PASS" : "FAIL");
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
    if (!ok) failures++;
}

enum Mode { M_OFF, M_IDLE, M_FEEDING, M_MOVING, M_JOG, M_HELD, M_TAKEUP, M_CAL };

static const char *modeName(Mode m) {
    static const char *n[] = {"OFF", "IDLE", "FEEDING", "MOVING", "JOG", "HELD", "TAKEUP", "CAL"};
    return n[m];
}

static uint16_t wireMode(Mode m) {
    switch (m) {
    case M_OFF:     return ELS_MMODE_OFF;
    case M_IDLE:    return ELS_MMODE_IDLE;
    case M_FEEDING: return ELS_MMODE_FEEDING;
    case M_MOVING:  return ELS_MMODE_MOVING;
    case M_JOG:     return ELS_MMODE_JOG;
    case M_HELD:    return ELS_MMODE_HELD;
    case M_TAKEUP:  return ELS_MMODE_TAKEUP;
    case M_CAL:     return ELS_MMODE_CAL;
    }
    return 0xFFFF;
}

static const int32_t Z_CLEAR = 1000;

struct Rig {
    rampsHandler_t     data;
    TIM_TypeDef        tim[SCALES_COUNT];
    TIM_HandleTypeDef  htim[SCALES_COUNT];
    int32_t            spindleCnt;
    int32_t            spindleRate;    /* spindle counts per ISR pass */

    rampsSharedData_t *sh() { return &data.shared; }

    void init() {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim,  0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        spindleCnt  = 0;
        spindleRate = 0;
        servoCycles        = 1;
        servoCyclesCounter = 0;
        for (int i = 0; i < SCALES_COUNT; i++) {
            htim[i].Instance = &tim[i];
            ramps_timer_handles[i] = &htim[i];
            sh()->scales[i].timerHandleSlot = (uint32_t)i;
            sh()->scales[i].scaleDir     = 1;
            sh()->scales[i].syncRatioNum = 1;
            sh()->scales[i].syncRatioDen = 100;
        }
        sh()->scales[0].syncRatioNum = -2;
        sh()->scales[0].syncRatioDen = 15;
        sh()->servo.maxSpeed     = 100000.0f;
        sh()->servo.acceleration = 50000.0f;
        sh()->servo.servoDir     = 1;
        sh()->elsStop.scaleIndex       = 1;
        sh()->elsStop.stopPosition     = 0;
        sh()->elsStop.stopDirection    = -1;
        sh()->elsStop.threadPitchSteps = 533.333f;
        sh()->elsStop.zCountsPerPitch  = 846.667f;
        sh()->elsStop.backlashSteps    = 40;
        sh()->elsStop.hysteresis       = 500;
        sh()->elsStop.calCeilingSteps       = 5000;
        sh()->elsStop.calMotionThreshCounts = 2;
        tim[1].CNT = (uint32_t)Z_CLEAR;
        data.scalesDeltaPos[1].position = Z_CLEAR;
        sh()->scales[1].position = Z_CLEAR;
        data.scalesSyncDeltaPos[1].oldPosition = Z_CLEAR;
    }

    /* One ISR pass. The spindle turns at spindleRate; Z stays clear of the
     * stop, so no stop ever fires and nothing moves Z but the test. */
    void isr() {
        spindleCnt += spindleRate;
        tim[0].CNT = (uint32_t)spindleCnt;
        emu_dwt.CYCCNT += 1000;
        SynchroRefreshTimerIsr(&data);
    }
    void isr(int n) { for (int i = 0; i < n; i++) isr(); }

    bool anySync() {
        for (int i = 0; i < SCALES_COUNT; i++)
            if (sh()->scales[i].syncEnable) return true;
        return false;
    }

    /* servoEnableTask's re-assert, as Ramps.c writes it (release build). */
    void reassert() {
        if (anySync() && !sh()->elsStop.active && sh()->fastData.servoMode != 2)
            sh()->fastData.servoMode = 1;
    }

    uint16_t calRunning() { return (uint16_t)(data.elsCal.phase != ELS_CAL_IDLE); }

    /* One servoEnableTask tick, hardware order. `isrPasses` ISR passes land
     * between the task's intake and its finish (the ISR preempts the task);
     * `between` runs at the same point, for a host write that preempts too. */
    bool taskTick(int isrPasses, void (*between)(Rig &) = nullptr) {
        bool skip = elsCommandIntake(&data);
        if (!skip) reassert();
        if (between) between(*this);
        isr(isrPasses);
        elsPublishMachineMode(sh(), calRunning());
        elsCommandFinish(&data);
        return skip;
    }

    /* The ~100 ms between task ticks, shortened: enough ISR passes for a
     * pending edge to be taken many times over. */
    void idle() { isr(50); }

    /* Put the machine in mode m, for real, and return whether the derivation
     * agrees it is there. */
    bool setUp(Mode m) {
        init();
        switch (m) {
        case M_OFF:
            sh()->fastData.servoMode = 0;
            isr(5);
            break;
        case M_IDLE:
            sh()->fastData.servoMode = 1;
            isr(5);
            break;
        case M_FEEDING:
            /* Inside a job, spindle turning: the sync feed is live. */
            sh()->fastData.servoMode = 1;
            sh()->scales[0].syncEnable = 1;
            sh()->elsStop.enable = 1;
            spindleRate = 40;
            isr(20);
            break;
        case M_MOVING:
            /* An indexing move with no job: enable is ALREADY 0, so no enable
             * falling edge will cancel it. */
            sh()->fastData.servoMode = 1;
            sh()->servo.stepsToGo = 100000;
            isr(5);
            break;
        case M_JOG:
            sh()->fastData.servoMode = 2;
            sh()->servo.jogSpeed = 2000.0f;
            isr(20);
            break;
        case M_HELD:
            sh()->fastData.servoMode = 1;
            sh()->scales[0].syncEnable = 1;
            sh()->elsStop.active = 1;
            isr(2);
            sh()->elsStop.enable = 1;
            spindleRate = 40;
            isr(20);
            break;
        case M_TAKEUP:
            /* Held, then released: the resume edge starts the take-up. */
            sh()->fastData.servoMode = 1;
            sh()->scales[0].syncEnable = 1;
            sh()->elsStop.active = 1;
            isr(2);
            sh()->elsStop.enable = 1;
            isr(5);
            sh()->elsStop.active = 0;
            isr(3);
            break;
        case M_CAL:
            sh()->fastData.servoMode = 1;
            sh()->elsStop.calCommand = 1;
            isr(5);
            break;
        }
        elsPublishMachineMode(sh(), calRunning());
        return sh()->elsStop.machineMode == wireMode(m);
    }
};

static Rig rig;

/* Everything an ack promises, plus 2000 passes of nothing happening. */
static void checkInert(const char *what) {
    rampsSharedData_t *s = rig.sh();
    check(s->elsStop.command == ELS_CMD_NONE, "%s: command consumed (0)", what);
    check(s->elsStop.machineMode == ELS_MMODE_OFF, "%s: machineMode published OFF on the ack (%u)",
          what, (unsigned)s->elsStop.machineMode);
    check(s->fastData.servoMode == 0, "%s: servoMode 0 (%u)", what, (unsigned)s->fastData.servoMode);
    check(!rig.anySync(), "%s: syncEnable 0 on every scale", what);
    check(s->elsStop.enable == 0, "%s: enable 0", what);
    check(s->elsStop.active == 0, "%s: active 0", what);
    check(s->elsStop.takeupPending == 0, "%s: takeupPending 0", what);
    check(s->servo.stepsToGo == 0, "%s: stepsToGo 0 (%ld)", what, (long)s->servo.stepsToGo);
    check(s->servo.desiredSteps == s->servo.currentSteps, "%s: no sync backlog", what);
    check(rig.data.elsCal.phase == ELS_CAL_IDLE, "%s: no calibration running", what);

    uint32_t steps = s->servo.currentSteps;
    uint16_t takeups = s->elsStop.takeupSeq;
    rig.spindleRate = 40;
    for (int t = 0; t < 20; t++) {
        rig.reassert();     /* the task keeps running */
        rig.isr(100);
    }
    check(s->servo.currentSteps == steps, "%s: no step in 2000 passes with the spindle turning (%ld -> %ld)",
          what, (long)steps, (long)s->servo.currentSteps);
    check(s->elsStop.takeupPending == 0 && s->elsStop.takeupSeq == takeups,
          "%s: no take-up started afterwards", what);
    check(s->fastData.servoMode == 0, "%s: still servoMode 0 after 20 re-assert ticks", what);
}

static void rowDisengage(Mode m) {
    char what[64];
    snprintf(what, sizeof what, "DISENGAGE from %s", modeName(m));
    bool reached = rig.setUp(m);
    check(reached, "%s: rig reaches %s (published %u)", what, modeName(m),
          (unsigned)rig.sh()->elsStop.machineMode);
    uint16_t calSeq0 = rig.sh()->elsStop.calSeq;

    rig.sh()->elsStop.command = ELS_CMD_DISENGAGE;
    int ticks = 0;
    while (rig.sh()->elsStop.commandSeq == 0 && ticks < 5) {
        rig.taskTick(1);
        ticks++;
        if (rig.sh()->elsStop.commandSeq == 0) rig.idle();
    }
    check(rig.sh()->elsStop.commandSeq == 1, "%s: acked exactly once (commandSeq %u after %d ticks)",
          what, (unsigned)rig.sh()->elsStop.commandSeq, ticks);

    uint16_t want = (m == M_CAL) ? ELS_CMD_RESULT_OK_CAL_ABORTED : ELS_CMD_RESULT_OK;
    check(rig.sh()->elsStop.commandResult == want, "%s: commandResult %u (got %u)", what,
          (unsigned)want, (unsigned)rig.sh()->elsStop.commandResult);
    if (m == M_CAL) {
        check(rig.sh()->elsStop.calSeq == (uint16_t)(calSeq0 + 1), "%s: calSeq moved once", what);
        check(rig.sh()->elsStop.calResult == ELS_CAL_ERR_ABORTED, "%s: calResult ABORTED (%u)",
              what, (unsigned)rig.sh()->elsStop.calResult);
    }
    checkInert(what);
}

static void hostSyncWrite(Rig &r) { r.sh()->scales[0].syncEnable = 1; }

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);

    /* ---- the table ---- */
    const Mode modes[] = {M_OFF, M_IDLE, M_FEEDING, M_MOVING, M_JOG, M_HELD, M_TAKEUP, M_CAL};
    for (Mode m : modes) rowDisengage(m);

    /* ---- the ack waits for the ISR's pass ---- */
    rig.setUp(M_HELD);
    rig.sh()->elsStop.command = ELS_CMD_DISENGAGE;
    bool skipped = rig.taskTick(0);
    check(skipped, "edge: the consuming tick skips the re-assert");
    check(rig.sh()->elsStop.command == ELS_CMD_NONE, "edge: consumed on the first tick");
    check(rig.sh()->elsStop.commandSeq == 0,
          "edge: no ack on a tick the ISR has not run in (enable edge not yet taken)");
    rig.idle();
    rig.taskTick(1);
    check(rig.sh()->elsStop.commandSeq == 1 && rig.sh()->elsStop.commandResult == ELS_CMD_RESULT_OK,
          "edge: acked on the next tick, after the ISR took the edge");
    checkInert("edge");

    /* ---- a host syncEnable write inside the consumption ---- */
    rig.setUp(M_FEEDING);
    rig.sh()->elsStop.command = ELS_CMD_DISENGAGE;
    rig.taskTick(1, hostSyncWrite);
    check(rig.sh()->elsStop.commandSeq == 0,
          "race: a syncEnable written during consumption holds the ack back");
    rig.idle();
    bool skippedAgain = rig.taskTick(1);
    check(skippedAgain, "race: the re-assert stays skipped while the command is in flight");
    check(rig.sh()->elsStop.commandSeq == 1, "race: acked on the next tick");
    checkInert("race");

    /* ---- unknown command: refused, nothing done ---- */
    rig.setUp(M_FEEDING);
    rig.sh()->elsStop.command = 7;
    bool skipUnknown = rig.taskTick(1);
    check(!skipUnknown, "unknown: the re-assert is not skipped");
    check(rig.sh()->elsStop.command == ELS_CMD_NONE, "unknown: consumed");
    check(rig.sh()->elsStop.commandSeq == 1, "unknown: acked (commandSeq 1)");
    check(rig.sh()->elsStop.commandResult == ELS_CMD_RESULT_ERR_UNKNOWN, "unknown: ERR_UNKNOWN (%u)",
          (unsigned)rig.sh()->elsStop.commandResult);
    check(rig.sh()->fastData.servoMode == 1 && rig.anySync() && rig.sh()->elsStop.enable == 1,
          "unknown: the job is untouched");

    /* ---- one in flight: a second command waits in the register ---- */
    rig.setUp(M_HELD);
    rig.sh()->elsStop.command = ELS_CMD_DISENGAGE;
    rig.taskTick(0);                         /* consumed, not acked */
    rig.sh()->elsStop.command = 7;           /* written before the ack */
    rig.idle();
    rig.taskTick(1);
    check(rig.sh()->elsStop.commandSeq == 1 && rig.sh()->elsStop.commandResult == ELS_CMD_RESULT_OK,
          "queue: the first command's ack is its own");
    check(rig.sh()->elsStop.command == 7, "queue: the second waited in the register");
    rig.idle();
    rig.taskTick(1);
    check(rig.sh()->elsStop.commandSeq == 2 && rig.sh()->elsStop.commandResult == ELS_CMD_RESULT_ERR_UNKNOWN,
          "queue: consumed and acked after the first");

    /* ---- command 0 is nothing ---- */
    rig.setUp(M_FEEDING);
    bool skipIdle = rig.taskTick(1);
    check(!skipIdle && rig.sh()->elsStop.commandSeq == 0 && rig.sh()->fastData.servoMode == 1,
          "idle: no command, no ack, nothing changed");

    printf("\n%s (%d failure%s)\n", failures ? "FAILED" : "PASSED", failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
