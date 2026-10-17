/*
 * ISR-GAP PROBE -- schema 8. How late the motion ISR runs: the worst
 * tick-to-tick gap between entries to SynchroRefreshTimerIsr, a small
 * distribution of late ticks, and the machine state at the worst one.
 *
 * ---- WHY ---------------------------------------------------------------------
 * TIM9 sits at NVIC priority 5, equal to configLIBRARY_MAX_SYSCALL_INTERRUPT_
 * PRIORITY, so every FreeRTOS critical section and every same-priority
 * interrupt can hold a tick back. executionCyclesPeak measures how long the ISR
 * RUNS; nothing measured how late it STARTS. The ISR already computes the gap
 * between entries every tick (shared->executionInterval, DWT cycles), and this
 * probe peak-holds it over a window the host can reset, so the late ticks the
 * current Modbus stack causes are on record before that stack is replaced.
 *
 * ---- READING IT ----------------------------------------------------------------
 * A tick delayed by D cycles shows as one gap of nominal + D followed by one of
 * nominal - D: the timer keeps its own time, so only the ENTRY moves. A gap of
 * 2x nominal or more means a whole update was lost (TIM9's pending flag holds
 * one update, not two). The worst gap's snapshot says what the machine was
 * doing, and the duration of the tick BEFORE it separates the two causes:
 *
 *   prev-tick cycles near or above nominal -> the ISR overran its own budget;
 *   prev-tick cycles normal               -> something outside the ISR held
 *                                            the entry back (a critical
 *                                            section, a same-priority IRQ).
 *
 * ---- REGISTERS (meaningful only when diagSchema == 8) --------------------------
 *   diagSettleTicks   worst gap, CPU cycles
 *   diagNetCounts     ticks measured in this window (uint32; wraps after ~23.8 h
 *                     at 50 kHz)
 *   diagCaptureTicks  longest ISR run in this window, CPU cycles (saturates at
 *                     65535). Window-scoped, unlike executionCyclesPeak.
 *   diagEndReason     RESET REQUEST. The host writes ELS_DIAG_GAP_RESET_REQ;
 *                     the next tick clears the window and writes 0 back, which
 *                     is the acknowledgement.
 *   diagBucketTicks   0: no time buckets in this probe
 *   diagBucketCount   ELS_DIAG_GAP_WORDS, the diagTrace words in use
 *   diagTrace[]       ELS_DIAG_GAP_W_* below; a uint32 is two words, low first.
 *                     The words are raw uint16 bits in an int16 register.
 *   diagSeq           bumps when the worst gap grows, on a reset, and every
 *                     ELS_DIAG_GAP_HEARTBEAT_TICKS, so an edge-detecting reader
 *                     records the worst gap's moments and a periodic
 *                     distribution without polling the block.
 *
 * The block is written by the ISR while the host reads it in pieces, so a
 * 32-bit counter can be read torn across its carry. Read it twice and compare.
 *
 * ---- COST ------------------------------------------------------------------------
 * The hook runs on EVERY tick, before executionCycles is taken, so its cost
 * shows up in executionCyclesPeak on the machine. The common path is 30
 * instructions, about 45 to 75 cycles; DIAG.md has the count.
 */
#ifndef ELS_DIAG_ISR_GAP_H
#define ELS_DIAG_ISR_GAP_H

#include <stdint.h>
#include <stdbool.h>
#include "els_isr_rate.h"

/* Nominal tick period in CPU cycles: one tick at the 100 MHz core clock. */
#define ELS_DIAG_GAP_NOMINAL   ((uint32_t)ELS_ISR_CYCLE_BUDGET)

/* Late-tick thresholds, as fractions of nominal. A gap counts when it is
 * STRICTLY greater; the counts are cumulative (a 2x gap counts in all four).
 *   +5%  : entry jitter beyond the ISR's own (1 us at 50 kHz)
 *   +25% : a tick held back a quarter period
 *   +50% : held back half a period
 *   2x   : an update lost outright */
#define ELS_DIAG_GAP_THRESH_0  (ELS_DIAG_GAP_NOMINAL + ELS_DIAG_GAP_NOMINAL / 20u)
#define ELS_DIAG_GAP_THRESH_1  (ELS_DIAG_GAP_NOMINAL + ELS_DIAG_GAP_NOMINAL / 4u)
#define ELS_DIAG_GAP_THRESH_2  (ELS_DIAG_GAP_NOMINAL + ELS_DIAG_GAP_NOMINAL / 2u)
#define ELS_DIAG_GAP_THRESH_3  (ELS_DIAG_GAP_NOMINAL * 2u)
#define ELS_DIAG_GAP_THRESHOLDS 4

/* The value the host writes to diagEndReason to start a fresh window. */
#define ELS_DIAG_GAP_RESET_REQ 1u

/* diagSeq heartbeat: one minute of ticks. */
#define ELS_DIAG_GAP_HEARTBEAT_TICKS ((uint32_t)ELS_MS_TO_TICKS(60000))

/* diagTrace word map. Append only within this schema. */
#define ELS_DIAG_GAP_W_MIN            0   /* uint32: shortest gap, cycles */
#define ELS_DIAG_GAP_W_OVER           2   /* uint32 x4: gaps > THRESH_0..3 */
#define ELS_DIAG_GAP_W_THRESH        10   /* uint16 x4: THRESH_0..3, cycles */
#define ELS_DIAG_GAP_W_NOMINAL       14   /* uint16: nominal tick, cycles */
#define ELS_DIAG_GAP_W_WORST_TICK    15   /* uint32: window tick of the worst gap */
#define ELS_DIAG_GAP_W_WORST_PREV    17   /* uint16: cycles of the tick before it */
#define ELS_DIAG_GAP_W_WORST_MMODE   18   /* uint16: elsStop.machineMode */
#define ELS_DIAG_GAP_W_WORST_SERVO   19   /* uint16: fastData.servoMode */
#define ELS_DIAG_GAP_W_WORST_ENABLE  20   /* uint16: elsStop.enable */
#define ELS_DIAG_GAP_W_WORST_ACTIVE  21   /* uint16: elsStop.active */
#define ELS_DIAG_GAP_W_WORST_TAKEUP  22   /* uint16: elsStop.takeupPending */
#define ELS_DIAG_GAP_W_WINDOW        23   /* uint16: windows started since boot */
#define ELS_DIAG_GAP_WORDS           24

/* ctx->state: 0 = the next tick's interval is not a gap (boot), 2 = measuring.
 * ctx->captureTick counts down to the next heartbeat.
 *
 * Probe-private state, as the elsDiagCtx_t comment in Ramps.h allows. Kept here
 * rather than read back from diagTrace so the per-tick path compares against a
 * plain uint32 instead of reassembling two words. */
static uint32_t elsDiagGapMin;
static uint32_t elsDiagGapOver[ELS_DIAG_GAP_THRESHOLDS];
static uint16_t elsDiagGapWindow;

static inline void elsDiagGapPut32(elsStop_t *stop, int w, uint32_t v) {
  stop->diagTrace[w]     = (int16_t)(uint16_t)(v & 0xFFFFu);
  stop->diagTrace[w + 1] = (int16_t)(uint16_t)(v >> 16);
}

static inline uint16_t elsDiagGapSat16(uint32_t v) {
  return (v > 0xFFFFu) ? (uint16_t)0xFFFFu : (uint16_t)v;
}

/* Clear the window and republish the geometry. Leaves the reset request
 * alone; the caller acknowledges it. */
static inline void elsDiagGapClear(elsDiagCtx_t *ctx, elsStop_t *stop) {
  ctx->captureTick = ELS_DIAG_GAP_HEARTBEAT_TICKS;
  elsDiagGapMin = 0;
  for (int i = 0; i < ELS_DIAG_GAP_THRESHOLDS; i++) {
    elsDiagGapOver[i] = 0;
  }
  stop->diagBucketTicks  = 0;
  stop->diagBucketCount  = (uint16_t)ELS_DIAG_GAP_WORDS;
  stop->diagSettleTicks  = 0;
  stop->diagNetCounts    = 0;
  stop->diagCaptureTicks = 0;
  for (int i = 0; i < ELS_DIAG_TRACE_BUCKETS; i++) {
    stop->diagTrace[i] = 0;
  }
  for (int i = 0; i < 4; i++) {
    stop->diagReserved[i] = 0;
  }
  stop->diagTrace[ELS_DIAG_GAP_W_THRESH + 0] = (int16_t)(uint16_t)ELS_DIAG_GAP_THRESH_0;
  stop->diagTrace[ELS_DIAG_GAP_W_THRESH + 1] = (int16_t)(uint16_t)ELS_DIAG_GAP_THRESH_1;
  stop->diagTrace[ELS_DIAG_GAP_W_THRESH + 2] = (int16_t)(uint16_t)ELS_DIAG_GAP_THRESH_2;
  stop->diagTrace[ELS_DIAG_GAP_W_THRESH + 3] = (int16_t)(uint16_t)ELS_DIAG_GAP_THRESH_3;
  stop->diagTrace[ELS_DIAG_GAP_W_NOMINAL]    = (int16_t)(uint16_t)ELS_DIAG_GAP_NOMINAL;
  stop->diagTrace[ELS_DIAG_GAP_W_WINDOW]     = (int16_t)elsDiagGapWindow;
}

/* RampsStart. The window opens at boot, so a session measured from power-on
 * needs no host action; the boot tick itself is skipped (state 0) because
 * its interval runs from wherever DWT was started, not from a previous tick. */
static inline void elsDiagInit(elsDiagCtx_t *ctx, elsStop_t *stop) {
  stop->diagSchema    = ELS_DIAG_SCHEMA_ISR_GAP;
  stop->diagEndReason = 0;
  elsDiagGapWindow    = 0;
  elsDiagGapClear(ctx, stop);
  ctx->state = 0;
}

/* The take-up and capture hooks are not this probe's concern: it measures
 * every tick from the tail hook. Present because the contract is fixed. */
static inline void elsDiagArm(elsDiagCtx_t *ctx, elsStop_t *stop) {
  (void)ctx; (void)stop;
}

static inline void elsDiagCaptureStart(elsDiagCtx_t *ctx) {
  (void)ctx;
}

/* Constant false: this probe needs nothing from the guarded block, so the
 * dZ/dServo arithmetic stays compiled out exactly as in a release build. */
static inline bool elsDiagCapturing(const elsDiagCtx_t *ctx) {
  (void)ctx;
  return false;
}

static inline void elsDiagTick(elsDiagCtx_t *ctx, elsStop_t *stop,
                               int32_t dZ, int32_t dServo) {
  (void)ctx; (void)stop; (void)dZ; (void)dServo;
}

static inline int32_t elsDiagExtraDwell(const elsDiagCtx_t *ctx) {
  (void)ctx;
  return 0;   /* this probe holds no gate open */
}

static inline bool elsDiagServoGate(elsDiagCtx_t *ctx, elsStop_t *stop,
                                    uint16_t servoModeNow) {
  (void)ctx; (void)stop; (void)servoModeNow;
  return false;
}

static inline void elsDiagTaskTick(elsDiagCtx_t *ctx, rampsSharedData_t *shared,
                                   uint16_t calRunning) {
  (void)ctx; (void)shared; (void)calRunning;
}

/* Every tick, at the ISR's tail, before executionCycles is taken: so
 * shared->executionInterval is THIS entry's gap and shared->executionCycles is
 * the PREVIOUS tick's duration. The common path is the tick count, three
 * compares and the heartbeat countdown; everything else runs only on a late
 * tick, a new extreme, or a reset. */
static inline void elsDiagTickTail(elsDiagCtx_t *ctx, rampsSharedData_t *shared) {
  elsStop_t *stop = &shared->elsStop;

  /* Host reset. Clear, acknowledge by writing the request back to 0, then
   * bump diagSeq last so a reader that sees the new seq sees the cleared
   * block. This tick is not measured: the window starts with the next one. */
  if (stop->diagEndReason != 0u) {
    elsDiagGapWindow++;
    elsDiagGapClear(ctx, stop);
    ctx->state = 2;
    stop->diagEndReason = 0;
    stop->diagSeq++;
    return;
  }

  if (ctx->state != 2u) {
    ctx->state = 2;   /* boot tick: interval is not a gap */
    return;
  }

  uint32_t gap   = shared->executionInterval;
  uint32_t prev  = shared->executionCycles;
  uint32_t ticks = (uint32_t)stop->diagNetCounts + 1u;
  bool     bump  = false;

  stop->diagNetCounts = (int32_t)ticks;

  if (prev > (uint32_t)stop->diagCaptureTicks) {
    stop->diagCaptureTicks = elsDiagGapSat16(prev);
  }

  if (gap < elsDiagGapMin || elsDiagGapMin == 0u) {
    elsDiagGapMin = gap;
    elsDiagGapPut32(stop, ELS_DIAG_GAP_W_MIN, gap);
  }

  if (gap > ELS_DIAG_GAP_THRESH_0) {
    elsDiagGapOver[0]++;
    if (gap > ELS_DIAG_GAP_THRESH_1) {
      elsDiagGapOver[1]++;
      if (gap > ELS_DIAG_GAP_THRESH_2) {
        elsDiagGapOver[2]++;
        if (gap > ELS_DIAG_GAP_THRESH_3) {
          elsDiagGapOver[3]++;
        }
      }
    }
    for (int i = 0; i < ELS_DIAG_GAP_THRESHOLDS; i++) {
      elsDiagGapPut32(stop, ELS_DIAG_GAP_W_OVER + 2 * i, elsDiagGapOver[i]);
    }
  }

  if (gap > (uint32_t)stop->diagSettleTicks) {
    /* Snapshot first, the gap itself last of the payload, diagSeq after. */
    elsDiagGapPut32(stop, ELS_DIAG_GAP_W_WORST_TICK, ticks);
    stop->diagTrace[ELS_DIAG_GAP_W_WORST_PREV]   = (int16_t)elsDiagGapSat16(prev);
    stop->diagTrace[ELS_DIAG_GAP_W_WORST_MMODE]  = (int16_t)stop->machineMode;
    stop->diagTrace[ELS_DIAG_GAP_W_WORST_SERVO]  = (int16_t)shared->fastData.servoMode;
    stop->diagTrace[ELS_DIAG_GAP_W_WORST_ENABLE] = (int16_t)stop->enable;
    stop->diagTrace[ELS_DIAG_GAP_W_WORST_ACTIVE] = (int16_t)stop->active;
    stop->diagTrace[ELS_DIAG_GAP_W_WORST_TAKEUP] = (int16_t)stop->takeupPending;
    stop->diagSettleTicks = (int32_t)gap;
    bump = true;
  }

  if (--ctx->captureTick == 0u) {
    ctx->captureTick = ELS_DIAG_GAP_HEARTBEAT_TICKS;
    bump = true;
  }

  if (bump) {
    stop->diagSeq++;
  }
}

#endif /* ELS_DIAG_ISR_GAP_H */
