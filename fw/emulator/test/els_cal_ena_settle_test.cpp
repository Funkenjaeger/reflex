/*
 * ISR-level tests: a backlash calibration does not step until the drive has
 * been enabled long enough to obey.
 *
 * THE DEFECT. The UI starts a calibration by writing servoMode = 1 and then
 * calCommand, back to back. The ISR took the request on its next pass and
 * commanded the SEAT leg at once, but ENA follows servoMode only in
 * servoEnableTask, every 100 ms, and the closed-loop drive then ignores STEP
 * for roughly a second after it is enabled. Every calibration recording on
 * the machine shows the SEAT leg sending 700 to 900 steps over 1.0 to 1.2 s
 * before Z moves, against a lash of about 395 steps: the drive was dropping
 * them. The firmware counts commanded steps, not executed ones, so the
 * unmeasured SEAT leg normally swallowed the loss. It stops swallowing it in
 * two ways, both of which this file reproduces:
 *
 *   - the drive jolts the leadscrew a few steps when it is enabled. That
 *     motion lands while SEAT is pulsing, so the slip accumulator attributes
 *     it to SEAT and the leg ends early. MEASURE leg 1 then starts inside the
 *     dead window and reads the dropped pulses as lash (567 and 963 against a
 *     true 395).
 *   - the window eats ~780 steps of SEAT's 1008-step ceiling, so a nut that
 *     starts more than ~230 steps from its wall fails NO_MOTION.
 *
 * THE FIX (Ramps.c, servoEnableTask and elsCalUpdate). servoEnableTask, which
 * owns the ENA pin, records when it took ENA from off to on and raises
 * enaSettle.settled once ENA has stayed on for ELS_CAL_ENA_SETTLE_MS. A taken
 * request waits in ELS_CAL_WAIT_DRIVE, commanding nothing, until settled
 * is up; the abort conditions apply throughout the wait.
 *
 * THE DRIVE MODEL is an assumption, not a measurement: the drive ignores
 * every pulse while ENA is off and for DEAD_MS after it goes on, and the
 * enable jolt is applied at the ENA edge. The 880 ms window and the jolts
 * (-5 and +12 steps on the first two calibrations) are the values that made
 * a rig built from the real ISR reproduce the 2026-10-19 legs: [567, 401,
 * 398], [963, 398, 396], NO_MOTION, then two passes. Case 1 replays that
 * sequence; on the unfixed firmware it reads [568, 399, 398], [964, 400,
 * 400] and NO_MOTION.
 *
 * Geometry is the machine's: lash 395 steps, 2.52 steps per Z count, a
 * 1008-step ceiling and a 2-count motion threshold. Time is the ISR tick at
 * ELS_ISR_TICK_HZ; HAL_GetTick() is derived from it, and servoEnableTask is
 * run every 100 ms of it, as RampsPoll runs it on the chip.
 */

extern "C" {
#include "Ramps.h"
#include "Scales.h"
#include "els_command.h"
#include "els_machine_mode.h"
#include "emulator_state.h"
}

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

/* ------------------------------------------------------------------ */
/* Shim / HAL / Modbus stubs -- Ramps.c's complete external set, with  */
/* the ENA pin watched and HAL_GetTick() driven by the ISR clock.      */
/* ------------------------------------------------------------------ */

static const int TICKS_PER_MS = ELS_ISR_TICK_HZ / 1000;

static int      g_enaOff     = 1;   /* ENA pin level: high = drive disabled */
static uint64_t g_tick       = 0;
static uint64_t g_enaOnSince = 0;
static int      g_enaEdge    = 0;   /* set on each off -> on edge, consumed by the rig */

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
    if (p == ENA_GPIO_PORT && pin == ENA_PIN) {
        int off = (s == GPIO_PIN_SET);
        if (g_enaOff && !off) { g_enaOnSince = g_tick; g_enaEdge = 1; }
        g_enaOff = off;
    }
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
uint32_t HAL_GetTick(void) { return (uint32_t)(g_tick / TICKS_PER_MS); }

extern uint16_t servoCycles;
extern uint16_t servoCyclesCounter;

} /* extern "C" */

/* ------------------------------------------------------------------ */
/* Drivetrain: one lash window, a carriage, and a drive that may drop  */
/* pulses.                                                             */
/* ------------------------------------------------------------------ */

static const double  LASH            = 395.0;
static const double  STEPS_PER_COUNT = 2.52;
static const int32_t CEILING         = 1008;
static const int32_t THRESH          = 2;
static const int     DEAD_MS         = 880;

struct Drivetrain {
    double  nut       = 0;   /* 0 = on the - wall, LASH = on the + wall */
    double  carriage  = 0;   /* in steps; + steps push it + */
    int32_t prevSteps = 0;
    int64_t lost      = 0;   /* pulses commanded that the drive did not turn */

    int32_t z(int32_t z0) const {
        return z0 - (int32_t)std::floor(carriage / STEPS_PER_COUNT);
    }
    void turn(int s) {
        nut += s;
        if (nut > LASH) { carriage += nut - LASH; nut = LASH; }
        if (nut < 0)    { carriage += nut;        nut = 0; }
    }
    /* the leadscrew turns `steps` that nobody commanded */
    void jolt(int steps) {
        for (int i = 0; i < std::abs(steps); i++) turn(steps > 0 ? 1 : -1);
    }
    /* the carriage pushed with the leadscrew held still: the nut moves the
     * other way inside the lash window */
    void pushCarriage(double dSteps) {
        carriage += dSteps;
        nut -= dSteps;
        if (nut < 0)    nut = 0;
        if (nut > LASH) nut = LASH;
    }
    void follow(int32_t cur, bool accepts) {
        int32_t d = cur - prevSteps;
        prevSteps = cur;
        while (d != 0) {
            int s = (d > 0) ? 1 : -1;
            d -= s;
            if (accepts) turn(s); else lost++;
        }
    }
};

struct Rig {
    rampsHandler_t    data;
    TIM_TypeDef       tim[SCALES_COUNT];
    TIM_HandleTypeDef htim[SCALES_COUNT];
    Drivetrain        dt;
    int32_t           z0 = 4164;
    double            spindleAcc = 0;
    int32_t           spindleCnt = 0;
    double            spindleRate = 0;  /* counts per tick */
    int               deadMs = -1;      /* <0: an ideal drive */
    int               joltOnEdge = 0;   /* leadscrew steps at the next ENA edge */
    const float       machineMaxSpeed = 10000.0f, machineAccel = 30000.0f;

    rampsSharedData_t *sh() { return &data.shared; }

    void init(int dead) {
        std::memset(&data, 0, sizeof(data));
        std::memset(tim, 0, sizeof(tim));
        std::memset(htim, 0, sizeof(htim));
        dt = Drivetrain();
        deadMs = dead;
        joltOnEdge = 0;
        spindleAcc = 0; spindleCnt = 0; spindleRate = 0;
        g_enaOff = 1; g_enaEdge = 0; g_enaOnSince = 0;
        for (int i = 0; i < SCALES_COUNT; i++) {
            htim[i].Instance = &tim[i];
            ramps_timer_handles[i] = &htim[i];
            sh()->scales[i].timerHandleSlot = (uint32_t)i;
            sh()->scales[i].scaleDir     = 1;
            sh()->scales[i].syncRatioNum = 1;
            sh()->scales[i].syncRatioDen = 100;
        }
        sh()->scales[0].syncRatioNum = 127;
        sh()->scales[0].syncRatioDen = 500;
        sh()->servo.maxSpeed     = machineMaxSpeed;
        sh()->servo.acceleration = machineAccel;
        sh()->servo.servoDir     = 1;
        sh()->elsStop.scaleIndex            = 1;
        sh()->elsStop.threadPitchSteps      = 0.0f;   /* turning */
        sh()->elsStop.zCountsPerPitch       = 203.2f;
        sh()->elsStop.calCeilingSteps       = CEILING;
        sh()->elsStop.calMotionThreshCounts = THRESH;
        sh()->elsStop.backlashSteps         = 484;
        sh()->elsStop.calBasisSteps         = 404;
        int32_t z = dt.z(z0);
        tim[1].CNT = (uint32_t)(uint16_t)z;
        data.scalesDeltaPos[1].position = (uint16_t)z;
        sh()->scales[1].position = z;
        data.scalesSyncDeltaPos[1].oldPosition = z;
        speedTick();
        servoCyclesCounter = 0;
        run(500);   /* boot: tasks run, servoMode 0, ENA off */
    }

    /* updateSpeedTask's servoCycles derivation, the only part of it the ISR
     * reads */
    void speedTick() {
        servoCycles = (uint16_t)std::floor((double)ELS_ISR_TICK_HZ / sh()->servo.maxSpeed);
    }

    void tick() {
        g_tick++;
        spindleAcc += spindleRate;
        int32_t ds = (int32_t)spindleAcc; spindleAcc -= ds; spindleCnt += ds;
        if (g_enaEdge) {
            g_enaEdge = 0;
            if (joltOnEdge != 0) { dt.jolt(joltOnEdge); joltOnEdge = 0; }
        }
        bool accepts = !g_enaOff;
        if (deadMs >= 0) {
            accepts = accepts && (g_tick - g_enaOnSince) >= (uint64_t)deadMs * TICKS_PER_MS;
        }
        dt.follow((int32_t)sh()->servo.currentSteps, accepts);
        tim[0].CNT = (uint32_t)(uint16_t)spindleCnt;
        tim[1].CNT = (uint32_t)(uint16_t)dt.z(z0);
        emu_dwt.CYCCNT += 100000000u / ELS_ISR_TICK_HZ;   /* DWT runs at 100 MHz */
        SynchroRefreshTimerIsr(&data);
        if (g_tick % (100 * TICKS_PER_MS) == 0) { speedTick(); servoEnableTask(&data); }
        else if (g_tick % (50 * TICKS_PER_MS) == 0) speedTick();
    }
    void run(int ms) { for (int i = 0; i < ms * TICKS_PER_MS; i++) tick(); }
    /* run until just after the next servoEnableTask, so a servoMode write
     * lands ~100 ms before ENA follows it, as it did on the machine */
    void toJustAfterTask() { while (g_tick % (100 * TICKS_PER_MS) != 1) tick(); }
    int32_t Z()   { return sh()->scales[1].position; }
    int32_t cur() { return (int32_t)sh()->servo.currentSteps; }
};

/* ------------------------------------------------------------------ */
/* The job before the calibrations: six turning passes, a refused      */
/* request while engaged, DISENGAGE.                                   */
/* ------------------------------------------------------------------ */

static void pass(Rig &r) {
    r.sh()->elsStop.active = 0;                   /* the operator's "go" */
    int guard = 0;
    while (r.sh()->elsStop.active == 0 && guard++ < 60000) r.run(1);
    r.run(300);
    r.sh()->servo.stepsToGo = -(int32_t)std::lround(4064 * STEPS_PER_COUNT) - 484;  /* retract */
    r.run(1);
    guard = 0;
    while ((r.sh()->servo.stepsToGo != 0 || r.sh()->servo.currentSpeed != 0.0f
            || r.sh()->servo.desiredSteps != r.sh()->servo.currentSteps) && guard++ < 60000)
        r.run(1);
    r.run(500);
}

static bool job(Rig &r) {
    r.sh()->elsStop.stopPosition  = r.z0 - 4064;
    r.sh()->elsStop.stopDirection = -1;
    r.sh()->elsStop.hysteresis    = 0;
    r.sh()->elsStop.enable        = 1;
    r.sh()->elsStop.active        = 1;
    r.sh()->fastData.servoMode    = 1;
    r.sh()->scales[0].syncEnable  = 1;
    r.run(200);
    r.spindleRate = 2858.0 / ELS_ISR_TICK_HZ;     /* 0.010 feed at 340 rpm */
    r.run(500);
    for (int i = 0; i < 3; i++) pass(r);
    r.sh()->scales[0].syncRatioDen = 125;         /* 0.040 feed at 700 rpm */
    r.spindleRate = 5900.0 / ELS_ISR_TICK_HZ;
    r.run(500);
    for (int i = 0; i < 3; i++) pass(r);
    r.spindleRate = 0;
    r.run(2000);

    uint16_t seq0 = r.sh()->elsStop.calSeq;
    r.sh()->elsStop.calCommand = 1;               /* refused: a job is live */
    r.run(50);
    bool refused = r.sh()->elsStop.calSeq == (uint16_t)(seq0 + 1)
                && r.sh()->elsStop.calResult == ELS_CAL_ERR_ENABLED;

    uint16_t cseq = r.sh()->elsStop.commandSeq;
    r.sh()->elsStop.command = ELS_CMD_DISENGAGE;
    int guard = 0;
    while (r.sh()->elsStop.commandSeq == cseq && guard++ < 2000) r.run(1);
    bool off = r.sh()->fastData.servoMode == 0 && r.sh()->elsStop.enable == 0;
    r.run(3000);
    return refused && off;
}

/* ------------------------------------------------------------------ */
/* A calibration bracketed the way the UI's ElsCal.start() brackets it */
/* ------------------------------------------------------------------ */

struct CalOut {
    int     result;
    int32_t m[3];
    int32_t seat;        /* steps the SEAT leg commanded */
    int64_t lost;        /* pulses the drive dropped during the run */
    double  secs;
};

static CalOut calibrate(Rig &r, int jolt) {
    CalOut out{};
    rampsSharedData_t *s = r.sh();
    s->servo.maxSpeed = 800.0f; s->servo.acceleration = 4000.0f;
    r.toJustAfterTask();
    r.joltOnEdge = jolt;
    s->fastData.servoMode   = 1;                  /* set_servo_mode(1) */
    s->scales[0].syncEnable = 1;                  /* the dispatcher's echo */
    r.run(30);                                    /* a few serial transactions */
    uint16_t seq0 = s->elsStop.calSeq;
    s->elsStop.calCommand = 1;
    int lastPhase = -1;
    int32_t legStart = r.cur();
    int64_t lost0 = r.dt.lost;
    int guard = 0;
    while (s->elsStop.calSeq == seq0 && guard++ < ELS_ISR_TICK_HZ * 20) {
        r.tick();
        int ph = r.data.elsCal.phase;
        if (ph != lastPhase) {
            if (lastPhase == ELS_CAL_SEAT) out.seat = std::abs(r.cur() - legStart);
            lastPhase = ph;
            legStart = r.cur();
        }
    }
    out.result = s->elsStop.calResult;
    for (int i = 0; i < 3; i++) out.m[i] = s->elsStop.calMeasured[i];
    out.lost = r.dt.lost - lost0;
    out.secs = (double)guard / ELS_ISR_TICK_HZ;
    /* the UI's restore: stop_sync, then servoMode 0, then the machine speeds */
    s->scales[0].syncEnable = 0;
    s->fastData.servoMode   = 0;
    s->servo.maxSpeed = r.machineMaxSpeed; s->servo.acceleration = r.machineAccel;
    r.run(4000);
    return out;
}

static bool legsClean(const CalOut &c) {
    if (c.result != ELS_CAL_OK) return false;
    for (int i = 0; i < 3; i++) {
        if (c.m[i] < (int32_t)LASH || c.m[i] > (int32_t)LASH + 10) return false;
    }
    return true;
}

static void printCal(int k, const CalOut &c) {
    printf("    cal %d: result=%d measured=[%d, %d, %d] seat=%d lost=%lld (%.2f s)\n",
           k, c.result, c.m[0], c.m[1], c.m[2], c.seat, (long long)c.lost, c.secs);
}

static int failures = 0;

static void check(bool ok, const char *what) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) failures++;
}

/* The 2026-10-19 sequence: the job, the refusal, DISENGAGE, then five
 * calibrations -- an enable jolt on the first two and the carriage shifted
 * 134 counts with the drive off before the third. */
static void sequence(const char *label, int dead, bool expectNoDrop) {
    static Rig r;
    printf("\n%s\n", label);
    r.init(dead);
    check(job(r), "  the job runs, the engaged request is refused, DISENGAGE turns the servo off");
    const int jolts[6] = { 0, -5, 12, 0, 0, 0 };
    bool allClean = true, noDrop = true, seatShort = true;
    for (int k = 1; k <= 5; k++) {
        if (k == 3) { r.dt.pushCarriage(134 * STEPS_PER_COUNT); r.run(700); }
        CalOut c = calibrate(r, jolts[k]);
        printCal(k, c);
        allClean  = allClean && legsClean(c);
        noDrop    = noDrop && c.lost == 0;
        seatShort = seatShort && c.seat <= (int32_t)LASH + 10;
    }
    check(allClean, "  all five calibrations pass with every leg within 10 steps of the lash");
    if (expectNoDrop) {
        check(noDrop, "  the drive drops no pulse in any calibration");
        check(seatShort, "  no SEAT leg commands more than one lash before Z moves");
    }
}

/* Request a calibration with ENA off, as the UI does, and stop at
 * `waitMs` after ENA goes on: inside the wait on fixed firmware. */
static uint16_t requestIntoWait(Rig &r, int waitMs) {
    rampsSharedData_t *s = r.sh();
    s->servo.maxSpeed = 800.0f; s->servo.acceleration = 4000.0f;
    r.toJustAfterTask();
    s->fastData.servoMode = 1;
    r.run(30);
    uint16_t seq0 = s->elsStop.calSeq;
    s->elsStop.calCommand = 1;
    while (g_enaOff) r.tick();
    r.run(waitMs);
    return seq0;
}

static void waitUntilSeq(Rig &r, uint16_t seq0) {
    int guard = 0;
    while (r.sh()->elsStop.calSeq == seq0 && guard++ < 2000) r.run(1);
}

int main() {
    printf("els_cal_ena_settle_test (ISR at %d Hz, dead window %d ms)\n",
           ELS_ISR_TICK_HZ, DEAD_MS);

    /* ---- 1. The 2026-10-19 sequence with the modeled dead window ---- */
    sequence("1. 2026-10-19 sequence, drive dead for 880 ms after enable", DEAD_MS, true);

    /* ---- 2. Control: an ideal drive reads the same legs ------------- */
    sequence("2. Control: the same sequence with an ideal drive", -1, false);

    static Rig r;

    /* ---- 3. Abort during the wait: servoMode dropped ----------------- */
    printf("\n3. servoMode dropped to 0 300 ms after ENA goes on\n");
    r.init(DEAD_MS);
    {
        int32_t c0 = r.cur();
        uint16_t seq0 = requestIntoWait(r, 300);
        bool quiet = r.cur() == c0 && r.sh()->servo.stepsToGo == 0;
        check(quiet, "  no step commanded inside the wait");
        check(r.sh()->elsStop.machineMode == ELS_MMODE_CAL,
              "  the machine mode reads CAL during the wait");
        r.sh()->fastData.servoMode = 0;
        waitUntilSeq(r, seq0);
        r.run(2000);
        check(r.sh()->elsStop.calSeq == (uint16_t)(seq0 + 1)
              && r.sh()->elsStop.calResult == ELS_CAL_ERR_ABORTED,
              "  the run reports ABORTED, once");
        check(r.cur() == c0 && r.sh()->servo.stepsToGo == 0,
              "  and commanded no steps, during the wait or after it");
    }

    /* ---- 4. Abort during the wait: a job engaged --------------------- */
    printf("\n4. A job engaged 300 ms after ENA goes on\n");
    r.init(DEAD_MS);
    {
        int32_t c0 = r.cur();
        uint16_t seq0 = requestIntoWait(r, 300);
        r.sh()->elsStop.enable = 1;
        waitUntilSeq(r, seq0);
        r.run(2000);
        check(r.sh()->elsStop.calSeq == (uint16_t)(seq0 + 1)
              && r.sh()->elsStop.calResult == ELS_CAL_ERR_ABORTED,
              "  the run reports ABORTED, once");
        check(r.cur() == c0, "  and commanded no steps");
    }

    /* ---- 5. DISENGAGE during the wait --------------------------------- */
    printf("\n5. DISENGAGE 300 ms after ENA goes on\n");
    r.init(DEAD_MS);
    {
        int32_t c0 = r.cur();
        uint16_t seq0 = requestIntoWait(r, 300);
        uint16_t cseq = r.sh()->elsStop.commandSeq;
        r.sh()->elsStop.command = ELS_CMD_DISENGAGE;
        int guard = 0;
        while (r.sh()->elsStop.commandSeq == cseq && guard++ < 2000) r.run(1);
        r.run(2000);
        check(r.sh()->elsStop.commandResult == ELS_CMD_RESULT_OK_CAL_ABORTED
              && r.sh()->elsStop.calSeq == (uint16_t)(seq0 + 1)
              && r.sh()->elsStop.calResult == ELS_CAL_ERR_ABORTED,
              "  DISENGAGE acks OK_CAL_ABORTED and the run reports ABORTED");
        check(r.cur() == c0, "  and commanded no steps");
    }

    /* ---- 6. The wait runs from the ENA edge, not from the request ---- */
    printf("\n6. Requested 1000 ms after ENA went on\n");
    r.init(DEAD_MS);
    {
        rampsSharedData_t *s = r.sh();
        s->servo.maxSpeed = 800.0f; s->servo.acceleration = 4000.0f;
        s->fastData.servoMode = 1;
        while (g_enaOff) r.tick();
        uint64_t edge = g_tick;
        r.run(1000);
        int32_t c0 = r.cur();
        uint16_t seq0 = s->elsStop.calSeq;
        s->elsStop.calCommand = 1;
        while (r.cur() == c0 && s->elsStop.calSeq == seq0
               && g_tick - edge < (uint64_t)5000 * TICKS_PER_MS) r.tick();
        double firstStepMs = (double)(g_tick - edge) / TICKS_PER_MS;
        printf("    first step %.1f ms after the ENA edge\n", firstStepMs);
        check(firstStepMs >= 1500.0 && firstStepMs < 1610.0,
              "  the first step comes 1500-1610 ms after ENA went on");
        waitUntilSeq(r, seq0);
        int g = 0;
        while (s->elsStop.calSeq == seq0 && g++ < 20000) r.run(1);
        check(s->elsStop.calResult == ELS_CAL_OK && r.dt.lost == 0,
              "  and the run passes with no pulse dropped");
    }

    /* ---- 7. A drive enabled long ago starts at once ------------------ */
    printf("\n7. Requested 3 s after ENA went on\n");
    r.init(DEAD_MS);
    {
        rampsSharedData_t *s = r.sh();
        s->servo.maxSpeed = 800.0f; s->servo.acceleration = 4000.0f;
        s->fastData.servoMode = 1;
        while (g_enaOff) r.tick();
        r.run(3000);
        int32_t c0 = r.cur();
        uint64_t t0 = g_tick;
        s->elsStop.calCommand = 1;
        while (r.cur() == c0 && g_tick - t0 < (uint64_t)1000 * TICKS_PER_MS) r.tick();
        double ms = (double)(g_tick - t0) / TICKS_PER_MS;
        printf("    first step %.1f ms after the request\n", ms);
        check(ms < 50.0, "  the first step comes within 50 ms of the request (the ramp from rest)");
    }

    printf("\n%s (%d failure%s)\n", failures ? "FAILED" : "ALL PASS",
           failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
