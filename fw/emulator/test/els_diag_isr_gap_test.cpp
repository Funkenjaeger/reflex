/*
 * The isr-gap probe (schema 8): the worst tick-to-tick gap of the motion ISR.
 *
 * Two halves. Sections 1-8 drive elsDiagTickTail directly with chosen
 * executionInterval / executionCycles values, so each assertion is about what
 * the probe DOES with a gap. Section 9 runs SynchroRefreshTimerIsr itself with
 * the emulator's DWT advanced by chosen amounts, which proves the hook is wired
 * into every tick and sees the interval the ISR computed. A probe that never
 * ran would publish zeros, and zero is also what a healthy machine's late-tick
 * counts look like, so every section below makes the probe count something
 * and checks it did.
 *
 * The emulator pins the ISR to 100 kHz (emulator/CMakeLists.txt), so nominal
 * is 1000 cycles here and 2000 on the machine. Everything below is written
 * against ELS_DIAG_GAP_NOMINAL, never a literal.
 */
extern "C" {
#include "Ramps.h"
#include "els_isr_rate.h"
#include "Scales.h"
#include "emulator_state.h"
}

#include <cstdio>
#include <cstdint>
#include <cstring>

/* ------------------------------------------------------------------ */
/* Shim / HAL / RTOS / Modbus stubs: Ramps.c's external set, as in     */
/* els_isr_peak_test.cpp.                                              */
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
/* Cycles charged per GPIO write, so a tick can be given a duration. */
unsigned emu_isr_cost_per_gpio = 0;
void HAL_GPIO_WritePin(GPIO_TypeDef *p, uint16_t pin, GPIO_PinState s) {
    (void)p; (void)pin; (void)s;
    emu_dwt.CYCCNT += emu_isr_cost_per_gpio;
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

static void check(bool cond, const char *what)
{
    printf("   %-66s %s\n", what, cond ? "ok" : "FAIL");
    if (!cond) failures++;
}

static uint16_t w16(const elsStop_t &s, int w) { return (uint16_t)s.diagTrace[w]; }
static uint32_t w32(const elsStop_t &s, int w)
{
    return (uint32_t)(uint16_t)s.diagTrace[w]
         | ((uint32_t)(uint16_t)s.diagTrace[w + 1] << 16);
}
static uint32_t worst(const elsStop_t &s)  { return (uint32_t)s.diagSettleTicks; }
static uint32_t ticks(const elsStop_t &s)  { return (uint32_t)s.diagNetCounts; }
static uint32_t over(const elsStop_t &s, int i) { return w32(s, ELS_DIAG_GAP_W_OVER + 2 * i); }

static const uint32_t NOM = ELS_DIAG_GAP_NOMINAL;

/* Direct rig: a shared block and a ctx, driven one tail call per tick. */
struct Rig {
    elsDiagCtx_t      ctx;
    rampsSharedData_t sh;
    Rig() {
        std::memset(&ctx, 0, sizeof(ctx));
        std::memset(&sh, 0, sizeof(sh));
        elsDiagInit(&ctx, &sh.elsStop);
    }
    /* One tick whose entry came `gap` cycles after the previous one, where
     * the previous tick ran `prevCycles`. */
    void tick(uint32_t gap, uint32_t prevCycles = 300) {
        sh.executionInterval = gap;
        sh.executionCycles   = prevCycles;
        elsDiagTickTail(&ctx, &sh);
    }
    /* Boot tick (not a gap) and `n` nominal ticks. */
    void warm(int n) {
        tick(123456);
        for (int i = 0; i < n; i++) tick(NOM);
    }
};

int main()
{
    printf("=== isr-gap probe (schema 8) ===\n");
    printf("   nominal %u cycles, thresholds %u/%u/%u/%u, heartbeat %u ticks\n\n",
           (unsigned)NOM, (unsigned)ELS_DIAG_GAP_THRESH_0,
           (unsigned)ELS_DIAG_GAP_THRESH_1, (unsigned)ELS_DIAG_GAP_THRESH_2,
           (unsigned)ELS_DIAG_GAP_THRESH_3, (unsigned)ELS_DIAG_GAP_HEARTBEAT_TICKS);

    /* ---- 1. the block names what is in it ----------------------------- */
    printf("-- 1. the block names what is in it --\n");
    {
        Rig r;
        const elsStop_t &s = r.sh.elsStop;
        check(s.diagSchema == 8, "diagSchema is wire value 8");
        check(s.diagBucketTicks == 0, "no time buckets advertised");
        check(s.diagBucketCount == ELS_DIAG_GAP_WORDS, "trace words in use are published");
        check(w16(s, ELS_DIAG_GAP_W_NOMINAL) == NOM,
              "nominal tick is published, so no reader assumes the rate");
        check(w16(s, ELS_DIAG_GAP_W_THRESH + 0) == ELS_DIAG_GAP_THRESH_0
              && w16(s, ELS_DIAG_GAP_W_THRESH + 3) == ELS_DIAG_GAP_THRESH_3,
              "thresholds are published");
        check(ELS_DIAG_GAP_THRESH_0 < ELS_DIAG_GAP_THRESH_1
              && ELS_DIAG_GAP_THRESH_1 < ELS_DIAG_GAP_THRESH_2
              && ELS_DIAG_GAP_THRESH_2 < ELS_DIAG_GAP_THRESH_3
              && ELS_DIAG_GAP_THRESH_3 == 2 * NOM,
              "thresholds ascend and the top one is a lost update");
        check(worst(s) == 0 && ticks(s) == 0 && s.diagSeq == 0,
              "nothing measured before the first tick");
    }

    /* ---- 2. the boot tick is not a gap -------------------------------- */
    printf("\n-- 2. the first tick's interval is not a gap --\n");
    {
        Rig r;
        r.tick(5000000);   /* DWT was started mid-boot: a huge bogus interval */
        check(worst(r.sh.elsStop) == 0, "boot interval is not recorded as the worst gap");
        check(ticks(r.sh.elsStop) == 0, "and is not counted as a tick");
        r.tick(NOM);
        check(ticks(r.sh.elsStop) == 1 && worst(r.sh.elsStop) == NOM,
              "the next tick is measured");
    }

    /* ---- 3. worst-gap capture and its snapshot ----------------------- */
    printf("\n-- 3. the worst gap and the state at it --\n");
    {
        Rig r;
        r.warm(100);
        elsStop_t &s = r.sh.elsStop;
        s.machineMode = 6; s.enable = 1; s.active = 0; s.takeupPending = 1;
        r.sh.fastData.servoMode = 1;
        r.tick(NOM + 777, 1234);          /* the late tick: window tick 101 */
        r.tick(NOM - 777);
        s.machineMode = 2; s.takeupPending = 0; r.sh.fastData.servoMode = 2;
        for (int i = 0; i < 50; i++) r.tick(NOM + 300);   /* late, but not worst */
        check(worst(s) == NOM + 777, "worst gap is the largest gap seen");
        check(w32(s, ELS_DIAG_GAP_W_WORST_TICK) == 101, "its window tick is recorded");
        check(w16(s, ELS_DIAG_GAP_W_WORST_PREV) == 1234,
              "duration of the tick before it is recorded");
        check(w16(s, ELS_DIAG_GAP_W_WORST_MMODE) == 6, "machineMode at the worst gap");
        check(w16(s, ELS_DIAG_GAP_W_WORST_SERVO) == 1, "servoMode at the worst gap");
        check(w16(s, ELS_DIAG_GAP_W_WORST_ENABLE) == 1
              && w16(s, ELS_DIAG_GAP_W_WORST_ACTIVE) == 0
              && w16(s, ELS_DIAG_GAP_W_WORST_TAKEUP) == 1,
              "enable / active / takeupPending at the worst gap");
        check(ticks(s) == 152, "every measured tick is counted");
        check(w32(s, ELS_DIAG_GAP_W_MIN) == NOM - 777, "shortest gap is recorded");
        check(s.diagCaptureTicks == 1234, "window ISR-duration peak");
    }

    /* ---- 4. thresholds: strictly greater, cumulative ------------------ */
    printf("\n-- 4. late-tick counts --\n");
    {
        Rig r;
        r.warm(10);
        const uint32_t T[4] = { ELS_DIAG_GAP_THRESH_0, ELS_DIAG_GAP_THRESH_1,
                                ELS_DIAG_GAP_THRESH_2, ELS_DIAG_GAP_THRESH_3 };
        for (int i = 0; i < 4; i++) r.tick(T[i]);        /* AT each: counts below it only */
        for (int i = 0; i < 4; i++) r.tick(T[i] + 1);    /* just above each */
        r.tick(10 * NOM);                                /* far above all */
        const elsStop_t &s = r.sh.elsStop;
        /* Gaps > T0: T1,T2,T3 (at), T0+1..T3+1 (4), 10x  -> 3 + 4 + 1 = 8
         * Gaps > T1: T2,T3, T1+1..T3+1 (3), 10x          -> 2 + 3 + 1 = 6
         * Gaps > T2: T3, T2+1, T3+1, 10x                 -> 1 + 2 + 1 = 4
         * Gaps > T3: T3+1, 10x                           -> 2            */
        check(over(s, 0) == 8, "gaps > +5% counted, strictly greater");
        check(over(s, 1) == 6, "gaps > +25% counted");
        check(over(s, 2) == 4, "gaps > +50% counted");
        check(over(s, 3) == 2, "gaps > 2x (a lost update) counted");
        check(worst(s) == 10 * NOM, "worst gap tracks the largest");
    }

    /* ---- 5. 32-bit counters cross the 16-bit word boundary ------------ */
    printf("\n-- 5. counts survive past 65535 --\n");
    {
        Rig r;
        r.warm(0);
        for (uint32_t i = 0; i < 70000; i++) r.tick(ELS_DIAG_GAP_THRESH_3 + 1);
        check(ticks(r.sh.elsStop) == 70000, "tick count is a full uint32");
        check(over(r.sh.elsStop, 3) == 70000, "late-tick count is a full uint32");
    }

    /* ---- 6. ISR-duration peak saturates instead of wrapping ----------- */
    printf("\n-- 6. duration peak saturates --\n");
    {
        Rig r;
        r.warm(1);
        r.tick(NOM, 70000);
        check(r.sh.elsStop.diagCaptureTicks == 65535, "a 70000-cycle tick reads 65535, not 4464");
    }

    /* ---- 7. host reset ------------------------------------------------- */
    printf("\n-- 7. host reset starts a fresh window --\n");
    {
        Rig r;
        r.warm(20);
        r.tick(5 * NOM, 900);
        elsStop_t &s = r.sh.elsStop;
        uint16_t seqBefore = s.diagSeq;
        check(worst(s) == 5 * NOM && over(s, 3) == 1, "window holds a late tick");

        check(s.diagEndReason == ELS_DIAG_GAP_OPEN, "an open window holds diagEndReason at OPEN");
        uint32_t ticksBefore = ticks(s);
        r.tick(NOM);
        check(ticks(s) == ticksBefore + 1 && s.diagEndReason == ELS_DIAG_GAP_OPEN,
              "a tick with no request measures as usual and leaves the window open");
        s.diagEndReason = ELS_DIAG_GAP_RESET_REQ;      /* the host's write: 0 */
        r.tick(7 * NOM, 950);                          /* the reset tick itself */
        check(s.diagEndReason == ELS_DIAG_GAP_OPEN, "request is acknowledged by writing OPEN back");
        check(worst(s) == 0 && ticks(s) == 0 && over(s, 0) == 0 && over(s, 3) == 0,
              "worst, ticks and counts are cleared; the reset tick is not measured");
        check(w32(s, ELS_DIAG_GAP_W_MIN) == 0 && s.diagCaptureTicks == 0
              && w32(s, ELS_DIAG_GAP_W_WORST_TICK) == 0
              && w16(s, ELS_DIAG_GAP_W_WORST_PREV) == 0,
              "min, duration peak and the snapshot are cleared");
        check(s.diagSeq != seqBefore, "diagSeq moves, so a reader sees the new window");
        check(w16(s, ELS_DIAG_GAP_W_WINDOW) == 1, "window number counts the reset");
        check(w16(s, ELS_DIAG_GAP_W_NOMINAL) == NOM
              && w16(s, ELS_DIAG_GAP_W_THRESH + 2) == ELS_DIAG_GAP_THRESH_2
              && s.diagSchema == 8 && s.diagBucketCount == ELS_DIAG_GAP_WORDS,
              "geometry survives the reset");
        r.tick(NOM + 5);
        check(ticks(s) == 1 && worst(s) == NOM + 5, "the next tick is the window's first");
    }

    /* ---- 8. diagSeq: on a new worst and on the heartbeat --------------- */
    printf("\n-- 8. when diagSeq moves --\n");
    {
        Rig r;
        r.warm(0);
        elsStop_t &s = r.sh.elsStop;
        r.tick(NOM);
        uint16_t a = s.diagSeq;
        check(a != 0, "the window's first gap is a new worst");
        for (int i = 0; i < 1000; i++) r.tick(NOM);
        check(s.diagSeq == a, "steady ticks do not move it");
        r.tick(NOM + 50);
        check(s.diagSeq == (uint16_t)(a + 1), "a new worst moves it once");
        r.tick(NOM + 20);
        check(s.diagSeq == (uint16_t)(a + 1), "a late tick that is not the worst does not");

        /* Heartbeat: counted in ticks from the window start; 1003 so far. */
        uint16_t b = s.diagSeq;
        uint32_t left = ELS_DIAG_GAP_HEARTBEAT_TICKS - 1003u - 1u;
        for (uint32_t i = 0; i < left; i++) r.tick(NOM);
        check(s.diagSeq == b, "no heartbeat one tick early");
        r.tick(NOM);
        check(s.diagSeq == (uint16_t)(b + 1), "heartbeat after one minute of ticks");
        for (uint32_t i = 0; i < ELS_DIAG_GAP_HEARTBEAT_TICKS; i++) r.tick(NOM);
        check(s.diagSeq == (uint16_t)(b + 2), "and every minute after");
    }

    /* ---- 9. wired into the real ISR ----------------------------------- */
    printf("\n-- 9. SynchroRefreshTimerIsr calls it every tick --\n");
    {
        static rampsHandler_t data;
        static TIM_TypeDef        tim[SCALES_COUNT];
        static TIM_HandleTypeDef  htim[SCALES_COUNT];
        std::memset(&data, 0, sizeof(data));
        for (int i = 0; i < SCALES_COUNT; i++) {
            htim[i].Instance = &tim[i];
            ramps_timer_handles[i] = &htim[i];
            data.shared.scales[i].timerHandleSlot = (uint32_t)i;
            data.shared.scales[i].scaleDir = 1;
            data.shared.scales[i].syncRatioDen = 100;
        }
        data.shared.elsStop.scaleIndex = 1;
        servoCycles = 1;
        servoCyclesCounter = 0;
        elsDiagInit(&data.diag, &data.shared.elsStop);

        emu_isr_cost_per_gpio = 100;   /* each tick runs a few hundred cycles */
        uint32_t entry = 777777;       /* boot: an arbitrary counter value */
        for (int i = 0; i < 200; i++) {
            /* Entries land NOM apart, except one held back by 3 periods. The
             * ISR's own GPIO cost moves CYCCNT too, so advance to an absolute
             * entry time rather than by a delta. */
            entry += (i == 120) ? 3 * NOM : NOM;
            emu_dwt.CYCCNT = entry;
            SynchroRefreshTimerIsr(&data);
        }
        const elsStop_t &s = data.shared.elsStop;
        check(ticks(s) == 199, "every tick after the first is measured");
        check(worst(s) == 3 * NOM, "the held-back entry is the worst gap, as the ISR computed it");
        check(over(s, 3) == 1, "and counts as a lost update");
        check(w32(s, ELS_DIAG_GAP_W_WORST_TICK) == 120, "at the tick it happened");
        check(w16(s, ELS_DIAG_GAP_W_WORST_PREV) > 0
              && w16(s, ELS_DIAG_GAP_W_WORST_PREV) < NOM,
              "previous tick's duration comes from the ISR's executionCycles");
        check(s.diagCaptureTicks > 0 && s.diagCaptureTicks < NOM,
              "window duration peak comes from it too");
        emu_isr_cost_per_gpio = 0;
    }

    printf("\n=== %s ===\n", failures == 0 ? "ALL PASS" : "FAILURES");
    return failures == 0 ? 0 : 1;
}
