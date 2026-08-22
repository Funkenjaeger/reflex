/*
 * Diagnostic scratchpad: the probe must not leak into an unflagged build.
 *
 * THE RULE THIS ENFORCES. No build that reaches dev-staging, dev or main may
 * define ELS_DIAG_SCRATCH. That rule is meant to be structural rather than
 * documentary -- release builds omit the flag, so the writes do not exist and
 * diagSchema reads 0 -- and this test is what makes the claim checkable instead
 * of merely stated.
 *
 * WHY THE INIT PATH IS THE WHOLE ATTACK SURFACE, and why that is not a gap.
 * The capture code cannot leak on its own: its working state (diagState,
 * diagCaptureTick) lives inside the same #ifdef in rampsHandler_t, so deleting
 * the guard around the capture block does not produce a quiet release build
 * that records data -- it produces a compile error. The one place a leak can
 * happen SILENTLY is RampsStart(), which sets diagSchema in both configurations
 * via #ifdef/#else. Get that wrong and a release build advertises a probe it
 * does not have, which is the failure diagSchema exists to prevent.
 *
 * DELIBERATELY TWO-SIDED. Asserting only "reads 0 without the flag" would pass
 * just as happily against a build where the flag does nothing at all -- a check
 * that cannot distinguish "correctly suppressed" from "wired up wrong" is not
 * worth running. So the flagged build asserts the opposite: schema present, and
 * the trace geometry actually published. Both halves run in CI, because CI
 * builds the emulator suite in the default configuration and this file compiles
 * into either.
 *
 * The reserved BLOCK is unconditional in both configurations by design -- that
 * is what keeps the register offsets stable -- so its presence is not what is
 * under test here. Its size is pinned by reflex-ui's cross-repo register
 * contract test instead.
 *
 * Build/run: `els_diag_scratch_test` CTest target (see CMakeLists).
 */
extern "C" {
#include "Ramps.h"
#include "Scales.h"
#include "emulator_state.h"
}

#include <cstdio>
#include <cstdint>
#include <cstring>

/* Stubs for every Ramps.c external. Same set as els_takeup_confirm_test. */
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

} /* extern "C" */

static int failures = 0;

static void check(bool ok, const char *what)
{
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) failures++;
}

int main(void)
{
    static rampsHandler_t data;
    static TIM_TypeDef       tim[SCALES_COUNT];
    static TIM_HandleTypeDef htim[SCALES_COUNT];

    /* Poison the whole handler first. Zeroing it would let a MISSING write pass
     * as a correct zero -- the test would then be incapable of telling
     * "explicitly set to 0" from "never touched", which is exactly the
     * distinction it exists to make. */
    std::memset(&data, 0xA5, sizeof(data));
    std::memset(tim,  0, sizeof(tim));
    std::memset(htim, 0, sizeof(htim));
    for (int i = 0; i < SCALES_COUNT; i++) {
        htim[i].Instance = &tim[i];
        ramps_timer_handles[i] = &htim[i];
        /* Required INPUT to RampsStart -- it indexes ramps_timer_handles by
         * this, so it cannot stay poisoned. Everything the test actually asserts
         * on (the diag block) is left at 0xA5. */
        data.shared.scales[i].timerHandleSlot = (uint32_t)i;
    }
    data.modbusUart = nullptr;

    RampsStart(&data);

    /* Guards the one-time bump. Anything appended to elsStop_t after this must
     * move it again, and reflex-ui's ELS_PROTOCOL_VERSION with it. */
    /* 3 since 2026-08-22: machineMode was promoted out of the diagnostic
     * scratchpad into a permanent register at the tail of elsStop_t, which is
     * a real map change. Bump this WITH the map, never ahead of it -- the
     * whole point of the pin is that a layout change cannot land quietly. */
    check(data.shared.elsStop.protocolVersion == 3,
          "protocolVersion is 3 (permanent machineMode register)");

#ifdef ELS_DIAG_SCRATCH
    /* Pinned to a SPECIFIC schema, not "any nonzero". A probe revision changes
     * what the block's fields mean, and reflex-ui mirrors those meanings -- so
     * bumping it should cost somebody a deliberate edit here rather than sliding
     * through green. This caught the v1 -> v2 bump on 2026-08-16. */
    /* Asserted against the LITERAL wire value, deliberately, not against
     * ELS_DIAG_PROBE. Ramps.c now publishes `diagSchema = ELS_DIAG_PROBE`, so
     * checking those two agree is a tautology that would hold no matter what
     * number came out -- a check structurally incapable of failing. The literal
     * is the number reflex-ui mirrors and refuses when unrecognised, so pinning
     * it here is what makes a renumbering cost a deliberate edit on both sides.
     *
     * The #else is not decoration: it makes "added a probe, wrote no assertions
     * for it" a BUILD failure rather than a target that runs and checks nothing.
     * A new probe must land its own arm here. */
#if ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V3
    check(data.shared.elsStop.diagSchema == 6,
          "take-up settle v3 publishes wire schema 6");
    check(data.shared.elsStop.diagBucketCount == ELS_DIAG_TRACE_BUCKETS,
          "take-up settle v3 publishes its bucket count");
    /* The hold is what makes v3 able to measure at all, and it must be
     * ZERO before a take-up arms the probe -- otherwise a diagnostic build
     * would lengthen the gate's dwell on a machine that is not taking up. */
    check(elsDiagExtraDwell(&data.diag) == 0,
          "no capture in flight at startup, so the gate's dwell is untouched");
#elif ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_DISENGAGE_LATCH
    check(data.shared.elsStop.diagSchema == 3,
          "disengage latch publishes wire schema 3");
    /* Zero events at startup. This probe reports a PROBLEM by counting up, so a
     * non-zero seq out of RampsStart would be a false positive on every run. */
    check(data.shared.elsStop.diagSeq == 0,
          "disengage latch starts with no events recorded");
    check(data.shared.elsStop.diagNetCounts == 0,
          "disengage latch starts with a zero event count");
#elif ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_MODE_WATCH_V2
    check(data.shared.elsStop.diagSchema == 5,
          "mode watch v2 publishes wire schema 5");
    check(data.shared.elsStop.diagCaptureTicks == 0,
          "mode watch boots publishing OFF (mode 0)");
    check(data.shared.elsStop.diagSeq == 0,
          "mode watch starts with no transitions recorded");
    check(data.shared.elsStop.diagNetCounts == 0,
          "mode watch starts with a zero suppression count");

    /* Behavioral half, both duties. The entry points are static inlines from
     * the probe header, so calling them here exercises exactly what the task
     * will run. The derivation reads registers RampsStart does not
     * initialize — on the real part they are BSS-zero, here they are 0xA5
     * poison — so set every input the mode function consumes. */
    data.shared.elsStop.enable = 0;
    data.shared.elsStop.active = 0;
    data.shared.servo.stepsToGo = 0;
    for (int i = 0; i < SCALES_COUNT; i++) data.shared.scales[i].syncEnable = 0;
    data.shared.fastData.servoMode = 2;      /* operator jogs */
    elsDiagTaskTick(&data.diag, &data.shared, 0);
    check(data.shared.elsStop.diagCaptureTicks == 4,   /* ELS_MMODE_JOG */
          "task tick publishes the derived mode (JOG) on change");
    check(data.shared.elsStop.diagSettleTicks == 0,
          "…and the from-side of the transition (OFF)");
    check(data.shared.elsStop.diagSeq == 1,
          "…and bumps the transition counter once");
    elsDiagTaskTick(&data.diag, &data.shared, 0);
    check(data.shared.elsStop.diagSeq == 1,
          "no transition, no seq movement (edge-detect stays honest)");

    /* The v2 accounting split: suppression is unconditional while enable ==
     * 0, but only the servoMode == 0 refusal — the one that would have
     * switched the feed on — is counted. The servoMode == 1 case is the
     * enable-less power-feed no-op that v1 counted 1719 times in one
     * afternoon of hardware time, burying the signal; it must suppress
     * silently. */
    data.shared.elsStop.enable = 0;          /* no live job */
    check(elsDiagServoGate(&data.diag, &data.shared.elsStop, 1) == true,
          "no-op re-assert (servoMode already 1) is still SUPPRESSED");
    check(data.shared.elsStop.diagNetCounts == 0,
          "…but NOT counted (the v1 noise source)");
    check(data.shared.elsStop.diagEndReason == 0,
          "…and leaves no latch-seen verdict");
    check(elsDiagServoGate(&data.diag, &data.shared.elsStop, 0) == true,
          "effective re-assert (servoMode 0) is SUPPRESSED");
    check(data.shared.elsStop.diagNetCounts == 1,
          "…and counted");
    check(data.shared.elsStop.diagTrace[0] == 0,
          "…recording servoMode 0 at the event (anything else is a probe bug)");
    data.shared.elsStop.enable = 1;          /* live job */
    check(elsDiagServoGate(&data.diag, &data.shared.elsStop, 0) == false,
          "re-assert with a live job passes through untouched");
    /* The shared end-reason check below asserts the INIT state; this arm is
     * the only one that exercises behavior, so restore what the suppression
     * wrote before falling through to it. */
    data.shared.elsStop.diagEndReason = 0;
#else
#error "this probe has no assertions in els_diag_scratch_test.cpp -- add an arm above"
#endif
    check(data.shared.elsStop.diagEndReason == 0,
          "end reason starts cleared (no stale verdict beside a fresh trace)");
#if ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V3
    check(data.shared.elsStop.diagBucketTicks > 0,
          "trace probe publishes bucket width (host must not assume the ISR rate)");
#endif
    printf("=== %s (probe build, schema %u) ===\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           (unsigned)data.shared.elsStop.diagSchema);
#else
    /* THE RELEASE ASSERTION. A non-zero schema here means a probe leaked into a
     * build that must not carry one -- and because protocolVersion does not move
     * for a probe change, nothing downstream would catch it. */
    check(data.shared.elsStop.diagSchema == 0,
          "release build advertises NO probe (diagSchema == 0)");
    check(data.shared.elsStop.diagBucketTicks == 0,
          "release build publishes no bucket width");
    check(data.shared.elsStop.diagBucketCount == 0,
          "release build publishes no bucket count");
    printf("=== %s (release build) ===\n",
           failures == 0 ? "ALL PASS" : "FAILURES");
#endif

    return failures == 0 ? 0 : 1;
}
