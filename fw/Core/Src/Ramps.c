/**
 * Copyright © 2024 <Stefano Bertelli>
 * Permission is hereby granted, free of charge, to any person obtaining a copy of this software
 * and associated documentation files (the “Software”), to deal in the Software without restriction,
 * including without limitation the rights to use, copy, modify, merge, publish, distribute,
 * sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all copies or substantial
 * portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
 * LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NON-INFRINGEMENT.
 * IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
 * WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
 * SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 */
#include <math.h>
#include "Ramps.h"
#include "Scales.h"
#include "els_phase.h"

/* Post-takeup settle dwell: after the backlash takeup reaches its commanded
 * target step count, the step/dir servo may still be closing following error /
 * settling. Snapshotting the Z DRO then yields a takeup-speed-dependent (hence
 * backlash-dependent) phase error. Hold position for this many ISR ticks before
 * sampling Z in applyPhaseCorrection. ISR runs at ~100 kHz (TIM9, 10 us/tick),
 * so 100000 ticks ~= 1 s. The settling hypothesis was REFUTED on hardware (the
 * 1 s dwell changed nothing) and in the emulator (the carriage genuinely doesn't
 * move during a lash-absorbed takeup, so there is nothing to settle), so this is
 * now a short, behaviour-neutral guard rather than a real settle window. Keep it
 * small so the emulator's bounded-guard scenario loop doesn't time out. */
#define ELS_SETTLE_TICKS 50

/* ---- QUIESCENCE: has the carriage actually STOPPED? --------------------
 *
 * The Z confirmation gate asks whether the carriage moved FAR ENOUGH. It has
 * never asked whether it has stopped moving, and it has no channel through
 * which it could: els_takeup_settle_gate_test drives the real ISR against the
 * emulator's carriage settle model and shows the firmware behaving
 * BIT-IDENTICALLY -- same release tick, same verdict -- whether the carriage is
 * stationary or still owes 1.36 Z counts that take another 360 ticks to arrive.
 * So this is a missing INPUT, not a mis-tuned constant, and no amount of
 * adjusting ELS_SETTLE_TICKS would have found it.
 *
 * DEFAULT OFF, and deliberately so. Enabling it changes the release condition
 * of EVERY pass, and the machine it would run on cannot currently be measured
 * for it: elspi's 18 takeup-settle-v3 captures show the carriage never moving
 * more than ONE encoder count after a take-up, which is the resolution floor of
 * a 200 counts/mm scale. At that floor "still moving" and "stopped" are not
 * distinguishable, so switching this on there would buy nothing and could cost
 * spurious refusals. It is built now because the emulator can finally make it
 * fail; it is enabled when a measurement says it should be.
 *
 * What those captures DO establish is that a quiescence test is implementable
 * at all: across 900 trace buckets, 893 were exactly 0 and 7 were exactly -1,
 * with not a single +1. Encoder dither would have produced both signs and would
 * have made a stillness test unusable. It is real motion, in one direction.
 *
 * ELS_QUIESCENT_TICKS is sized against the EMULATOR, not that field data --
 * those single counts arrive scattered across the whole window and may be drift
 * rather than settle. Treat it as provisional and commission it with the
 * inter-pulse-gap measurement (todo.md), the same way ELS_SLIP_SETTLE_TICKS
 * must be. The confirm window still bounds the wait, so a machine that never
 * goes quiet aborts rather than hangs. */
#ifndef ELS_REQUIRE_QUIESCENCE
#define ELS_REQUIRE_QUIESCENCE 0
#endif
#define ELS_QUIESCENT_TICKS 200

/* NOTE (2026-08-27): a companion ELS_QUIESCENT_NET_TOL_COUNTS, widening the
 * tracker above from exact equality to a net-displacement window, was written
 * and then backed out. The reasoning and the measurements are at the tracker
 * itself; the short version is that any tolerance above zero contradicts the
 * >=200-ticks-since-the-last-pulse invariant els_takeup_quiescence_test pins,
 * and that is Evan's call to make rather than a refactor. */

/* Backstop for a backlash takeup that never reaches its commanded target. ISR
 * runs at ~100 kHz (TIM9, 10 us/tick), so this is ~5 s — far longer than any
 * legitimate takeup (tens to low hundreds of steps) even at a slow maxSpeed, and
 * deliberately generous because tripping it early would be worse than not having
 * it. It is a DIAGNOSTIC, not a control: it does not release the sync gate (see
 * the takeup block for why), it just names the failure so the UI can. The known
 * way in is servoMode != 1, where stepsToGo is never consumed at all. */
#define ELS_TAKEUP_TIMEOUT_TICKS 500000

/* How long the Z confirmation gate keeps LOOKING after the commanded take-up
 * motion has finished, before latching its verdict. ISR is ~100 kHz, so this is
 * ~250 ms.
 *
 * Neither extreme is safe. Re-evaluating FOREVER (the original behaviour) means
 * a withheld take-up is satisfied by the first Z motion from ANY source — on
 * hardware 2026-08-08 an operator nudging the carriage by hand, with the
 * half-nut open, released a correctly-withheld gate. But latching the instant
 * the move completes assumes the carriage stops dead when the servo does, and
 * it does not: there is real inertia and drivetrain compliance.
 *
 * Note the existing ELS_SETTLE_TICKS evidence does NOT cover this. That 1 s
 * dwell was tried when a take-up was fully absorbed by lash and moved the
 * carriage not at all, so there was nothing to settle. A take-up sized at
 * measured + margin deliberately DOES move the carriage, which is a different
 * regime and newer than that experiment.
 *
 * 250 ms sits well above any plausible mechanical settle at these speeds
 * (sub-millimetre moves, heavy carriage, high friction) and well below the time
 * it takes a person to reach a handwheel. It bounds the exposure; it does not
 * eliminate it — only correlating Z motion against commanded steps does that,
 * and that is the real fix (see todo.md).
 *
 * THAT FIX NOW EXISTS (els_slip.h), and it changed what this constant is FOR.
 * The window no longer carries the safety property — attribution does. What is
 * left here is purely RECOVERY LATENCY: how long the gate keeps re-evaluating a
 * late-but-genuine take-up before giving up and aborting the pass. It can stay
 * generous precisely because a hand nudge inside it no longer counts as
 * evidence. Shortening it would only make the machine give up sooner. */
#define ELS_TAKEUP_CONFIRM_WINDOW_TICKS 25000

/* Mechanical settle allowance for motion attribution: how long after a commanded
 * step pulse Z motion may still be credited to the servo rather than to whatever
 * else moved the carriage. This is the constant that replaces the 250 ms window
 * as the actual exposure bound, so it is the number an attacker — or an
 * unlucky operator — has to hit.
 *
 * COMMISSIONED 2026-08-27 against elspi, and lowered 1000 -> 700 as a result.
 * It still cannot be derived from the emulator -- that lash model moves the
 * carriage instantaneously with the pulse, so it has no settle behaviour at all
 * -- so the number comes from the machine, via the takeup-settle-v3 probe
 * (els_diag_takeup_settle.h) whose t=0 is the tick after the LAST take-up pulse.
 *
 * THE DATA: 18 captures, every one ending END_WINDOW at the full 2000 ticks, so
 * none was truncated. ELEVEN were completely still -- zero in every bucket. The
 * other SEVEN each delivered EXACTLY ONE count (net -1, a single nonzero bucket)
 * at 79, 545, 571, 656, 1165, 1399 and 1786 ticks. At the measured 103.8 kHz
 * that longest tail is 17.2 ms. So the carriage stops essentially dead; what
 * looks like a settle tail is one 5 um count arriving late, one-directional,
 * and in most take-ups not at all.
 *
 * WHY 700 SPECIFICALLY. The observations clump into <=656 and >=1165 with
 * NOTHING in between, and 700 sits inside that empty gap: lowering from 1000
 * therefore discards no motion the old value was crediting. The four early
 * counts stay attributed; the three late ones were already outside 1000 and are
 * single counts against a confirm threshold measured in TENS, so failing to
 * attribute them cannot refuse a healthy take-up. What it buys is a 30% smaller
 * window for a hand nudge to be credited to the servo, which is the whole point
 * of this constant.
 *
 * READ THE CAPTURES' ONE CAVEAT BEFORE EXTENDING THIS. They come from a
 * diagnostic build whose hold computes the gate's verdict LATER than release,
 * making that build MORE PERMISSIVE on a marginal take-up. That bears on
 * whether the GATE would confirm -- never read "the diagnostic build confirmed"
 * as "release would have". It does not touch the settle measurement itself,
 * which is what the hold exists to make possible.
 *
 * Constraints any replacement value must satisfy:
 *  - MUST exceed ELS_SETTLE_TICKS (50). The gate's first evaluation happens that
 *    many ticks after the last pulse; a shorter horizon rejects the inertial
 *    settle this whole mechanism exists to accept.
 *  - MUST exceed the live pulse pacing period (servoCycles), or genuine coupled
 *    motion mid-burst is discarded and a HEALTHY machine refuses to start. Not
 *    left to this constant — elsSlipSettleTicks() floors it at runtime — but a
 *    value below the pacing period means that floor is doing all the work and
 *    the number here is decorative.
 *  - Ticks, not milliseconds. At the measured 103.8 kHz ISR rate 700 ticks is
 *    ~6.7 ms. The emulator's real-time serve loop runs the same ISR ~10x slower,
 *    so anything tuned by watching wall-clock there is 10x wrong here
 *    (els_slip.h has the full unit-trap list).
 *
 * Smaller is safer and only becomes unsafe in one direction: too small starts
 * refusing healthy take-ups. Tune it DOWN from here against a machine that still
 * confirms reliably, never up to make a refusal go away. The commissioning data
 * above is what any further move must argue against, and it is executable:
 * els_slip_horizon_commission_test.cpp encodes those seven observations and the
 * gap the current value sits in, so a change made without revisiting them turns
 * that test red. */
#define ELS_SLIP_SETTLE_TICKS 700

/* Diagnostic probes live in Core/Inc/els_diag_*.h, dispatched by els_diag.h.
 * Their bodies are NOT in this file; their four call sites are, and each one
 * documents the instant it must fire at. See DIAG.md. */

#ifdef EMULATOR_BUILD
#include "emulator_state.h"

/* Step_6 instrumentation: trace leadscrew step accounting from
 * applyPhaseCorrection completion to the next ELS stop trigger. Kept as a
 * permanent emulator-only debug aid for ELS phase regressions — the
 * per-pass `step6 #N start / t=... / end` log lines expose dCur, dDes,
 * dSpindle, dZ, direction-flutter counts, and per-direction step pulses,
 * which together fully describe the firmware's step accounting during the
 * window where phase is determined. See DEBUGGING.md for the original
 * investigation that motivated this instrumentation. */
static uint32_t emu_step6_active = 0;
static uint32_t emu_step6_pass = 0;
static uint32_t emu_step6_tick = 0;
static const uint32_t emu_step6_log_interval = 2000;  /* mid-cut sample period in ISR ticks */
static int32_t  emu_step6_start_current = 0;
static int32_t  emu_step6_start_desired = 0;
static int32_t  emu_step6_start_spindle = 0;
static int32_t  emu_step6_start_z = 0;
static uint32_t emu_step6_pos_pulses = 0;
static uint32_t emu_step6_neg_pulses = 0;
static uint32_t emu_step6_dir_flips = 0;
static int32_t  emu_step6_prev_change_sign = 0;
#endif


// This variable is the handler for the modbus communication
modbusHandler_t RampsModbusData;

uint16_t servoCycles = 0;
uint16_t servoCyclesCounter = 0;

/* Side-table of real TIM handles, indexed by input_t.timerHandleSlot.
 * The modbus-exposed input_t carries only a uint32_t slot id so the wire
 * layout is identical on STM32 (4-byte pointer) and 64-bit emulator hosts. */
TIM_HandleTypeDef *ramps_timer_handles[SCALES_COUNT] = {0};


//osThreadId_t userLedTaskHandler;
const osThreadAttr_t ledTaskAttributes = {
.name = "UpdateLedTask",
.stack_size = 128 * 4,
.priority = (osPriority_t) osPriorityLow,

};

//osThreadId_t updateSpeedTaskHandler;
const osThreadAttr_t speedTaskAttributes = {
.name = "updateSpeedTask",
.stack_size = 128 * 4,
.priority = (osPriority_t) osPriorityLow,
};

//osThreadId_t updateServoEnableTaskHandler;
const osThreadAttr_t servoEnableTaskAttributes = {
.name = "servoEnableTask",
.stack_size = 128 * 4,
.priority = (osPriority_t) osPriorityLow,
};


void configureOutputPin(GPIO_TypeDef *Port, uint16_t Pin) {
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /*Configure GPIO pin : PtPin */
  GPIO_InitStruct.Pin = Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(Port, &GPIO_InitStruct);
}


void RampsStart(rampsHandler_t *rampsData) {
  rampsData->shared.servo.maxSpeed = 720;
  rampsData->shared.servo.acceleration = 120;
  rampsData->shared.servo.servoDir = 1;

  for (int i = 0; i < SCALES_COUNT; i++) {
    rampsData->shared.scales[i].syncRatioNum = 1;
    rampsData->shared.scales[i].syncRatioDen = 100;
    rampsData->shared.scales[i].scaleDir = 1;
  }

  rampsData->shared.elsStop.referenceLatched = 0;
  rampsData->shared.elsStop.takeupPending = 0;
  /* Register-layout version. Bump this whenever elsStop_t / rampsSharedData_t
   * changes shape; reflex-ui reads it at connect so a firmware/UI map mismatch
   * reports itself by name instead of surfacing as garbled register reads.
   *
   * 3 (2026-08-22): machineMode promoted out of the diagnostic scratchpad
   * into a permanent register, so the rung-2 census can be collected in a
   * build the machine actually runs. See the machineMode comment in Ramps.h.
   *
   * 4 (2026-08-16, landing 2026-08-22): latchCommand/latchSeq appended for the manual reference
   * latch. BOTH parents of this rebase called themselves 2 and meant different
   * maps -- dev-staging's 2 ends at diagReserved[4], the pre-rebase
   * feat/els-thread-resync's 2 ended at latchSeq with no diagnostic block at
   * all. Leaving it at 2 would give three distinct layouts one version number,
   * which is precisely the silent-garbage failure this field exists to prevent.
   *
   * The new pair is appended AFTER the diagnostic scratchpad on purpose, so
   * every offset that has been exercised on the lathe -- the whole diag block
   * included -- keeps the address it was verified at. Only genuinely new
   * registers move.
   *
   * 5 (2026-08-22): the thread-phase offset block (phaseOffsetCommand/Seq/
   * Pending/Steps) appended for the groove-widening offset, same
   * append-at-the-tail discipline.
   *
   * 6 (2026-08-23): executionCyclesPeak, the ISR headroom measurement. Added
   * after the machine lost Modbus on 6 of 6 cuts and the counter that should
   * have shown why turned out to be a spot sampler that could not see the
   * event. */
  rampsData->shared.elsStop.protocolVersion = 7;
  /* Diagnostic scratchpad. diagSchema is the ONLY thing that tells a reader what
   * the rest of the block means, so it is set here in BOTH configurations —
   * explicitly zeroed when no probe is compiled in, rather than left to whatever
   * the struct happened to be initialised with. A reader that finds 0 must not
   * interpret the block; see the note at elsStop_t. */
  elsDiagInit(&rampsData->diag, &rampsData->shared.elsStop);
  rampsData->shared.elsStop.calCommand   = 0;
  rampsData->shared.elsStop.calSeq       = 0;
  rampsData->shared.elsStop.latchCommand = 0;
  rampsData->shared.elsStop.latchSeq     = 0;
  rampsData->shared.elsStop.phaseOffsetCommand = 0;
  rampsData->shared.elsStop.phaseOffsetSeq     = 0;
  rampsData->shared.elsStop.phaseOffsetPending = 0;
  rampsData->shared.elsStop.phaseOffsetSteps   = 0;
  rampsData->shared.elsStop.executionCyclesPeak = 0;
  rampsData->shared.elsStop.calResult    = ELS_CAL_OK;
  rampsData->shared.elsStop.takeupResult = ELS_CAL_OK;
  rampsData->shared.elsStop.takeupSeq    = 0;
  rampsData->elsCal.phase                = ELS_CAL_IDLE;

  // Configure Pins
  configureOutputPin(DIR_GPIO_PORT, DIR_PIN);
  configureOutputPin(ENA_GPIO_PORT, ENA_PIN);
  configureOutputPin(STEP_GPIO_PORT, STEP_PIN);
  configureOutputPin(SPARE_1_GPIO_PORT, SPARE_1_PIN);
  configureOutputPin(SPARE_2_GPIO_PORT, SPARE_2_PIN);
  configureOutputPin(SPARE_3_GPIO_PORT, SPARE_3_PIN);

  // Configure tasks
  osThreadNew(userLedTask, rampsData, &ledTaskAttributes);
  osThreadNew(updateSpeedTask, rampsData, &speedTaskAttributes);
  osThreadNew(servoEnableTask, rampsData, &servoEnableTaskAttributes);


  // Initialize and start encoder timer, reset the sync flags
  for (int j = 0; j < SCALES_COUNT; ++j) {
    initScaleTimer(ramps_timer_handles[rampsData->shared.scales[j].timerHandleSlot]);
    HAL_TIM_Encoder_Start(ramps_timer_handles[rampsData->shared.scales[j].timerHandleSlot], TIM_CHANNEL_ALL);
  }

  // Enable debug cycle counter
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

  // Start Modbus
  RampsModbusData.uModbusType = MB_SLAVE;
  RampsModbusData.port = rampsData->modbusUart;
  RampsModbusData.u8id = MODBUS_ADDRESS;
  RampsModbusData.u16timeOut = 1000;
  RampsModbusData.EN_Port = NULL;
  RampsModbusData.u16regs = (uint16_t *) (&rampsData->shared);
  RampsModbusData.u16regsize = sizeof(rampsData->shared) / sizeof(uint16_t);
  RampsModbusData.xTypeHW = USART_HW;
  ModbusInit(&RampsModbusData);
  ModbusStart(&RampsModbusData);

  // Start synchro interrupt
  HAL_TIM_Base_Start_IT(rampsData->synchroRefreshTimer);
}

static inline void
deltaPositionAndError(int32_t currentValue, int32_t ratioNum, int32_t ratioDen, deltaPosError_t *data) {
  int32_t startValue = (int16_t) (currentValue - data->oldPosition) * ratioNum + data->error;
  data->oldPosition = currentValue;
  data->scaledDelta = (int32_t) (startValue / ratioDen);
  data->error = (int32_t) (startValue % ratioDen);
}

static inline void updateIndexingPosition(rampsHandler_t *data) {
  rampsSharedData_t *shared = &(data->shared);
  float interval = (float) shared->executionInterval / 100000000.0f;
  float stopDistance = (shared->servo.currentSpeed * shared->servo.currentSpeed / shared->servo.acceleration) / 2;

  // Accelerate Pos
  if (shared->servo.stepsToGo > 0) {
    if ((float)shared->servo.stepsToGo > stopDistance && shared->servo.currentSpeed < shared->servo.maxSpeed) {
      shared->servo.currentSpeed += shared->servo.acceleration * interval;
      // max speed
      if (shared->servo.currentSpeed > shared->servo.maxSpeed) {
        shared->servo.currentSpeed = shared->servo.maxSpeed;
      }
    }

    // Decelerate Pos
    if ((float)shared->servo.stepsToGo < stopDistance) {
      shared->servo.currentSpeed -= shared->servo.acceleration * interval;
      if (shared->servo.currentSpeed < 0) {
        shared->servo.currentSpeed = 0;
      }
    }
  }

  if (shared->servo.stepsToGo < 0) {
    // Accelerate Neg
    if (-(float)shared->servo.stepsToGo > stopDistance && -shared->servo.currentSpeed < shared->servo.maxSpeed) {
      shared->servo.currentSpeed -= shared->servo.acceleration * interval;
      if (-shared->servo.currentSpeed > shared->servo.maxSpeed) {
        shared->servo.currentSpeed = -shared->servo.maxSpeed;
      }
    }

    // Decelerate Neg
    if (-(float)shared->servo.stepsToGo < stopDistance) {
      shared->servo.currentSpeed += shared->servo.acceleration * interval;
      if (shared->servo.currentSpeed > 0) {
        shared->servo.currentSpeed = 0;
      }
    }
  }

  if (shared->servo.stepsToGo == 0) {
    // Security measure, end of travel
    shared->servo.currentSpeed = 0;
  } else {
    int32_t positionIncrement = ((int32_t)((float)shared->servo.currentSpeed * (float)shared->executionInterval + (float)data->rampsDeltaPos.error) / 100000000);
    data->rampsDeltaPos.error = ((int32_t)((float)shared->servo.currentSpeed * (float)shared->executionInterval + (float)data->rampsDeltaPos.error) % 100000000);
    shared->servo.desiredSteps += positionIncrement;
    shared->servo.stepsToGo -= positionIncrement;
    shared->fastData.stepsToGo = shared->servo.stepsToGo;
  }
}

static inline void updateJogPosition(rampsHandler_t *data) {
  rampsSharedData_t *shared = &(data->shared);
  float interval = (float) shared->executionInterval / 100000000.0f;

  // Start Pos
  if (shared->servo.jogSpeed > 0) {
    if (shared->servo.currentSpeed < shared->servo.jogSpeed) {
      shared->servo.currentSpeed += shared->servo.acceleration * interval;
      // max speed
      if (shared->servo.currentSpeed > shared->servo.jogSpeed) {
        shared->servo.currentSpeed = shared->servo.jogSpeed;
      }
    }
  }

  // Start Neg
  if (shared->servo.jogSpeed < 0) {
    if (shared->servo.currentSpeed > shared->servo.jogSpeed) {
      shared->servo.currentSpeed -= shared->servo.acceleration * interval;
      if (shared->servo.currentSpeed < shared->servo.jogSpeed) {
        shared->servo.currentSpeed = shared->servo.jogSpeed;
      }
    }
  }

  // Stop Pos/Neg
  if (shared->servo.currentSpeed > 0) {
    if (shared->servo.currentSpeed > shared->servo.jogSpeed) {
      shared->servo.currentSpeed -= shared->servo.acceleration * interval;
      if (shared->servo.currentSpeed < 0) shared->servo.currentSpeed = 0;
    }
  }

  if (shared->servo.currentSpeed < 0) {
    if (shared->servo.currentSpeed < shared->servo.jogSpeed) {
      shared->servo.currentSpeed += shared->servo.acceleration * interval;
      if (shared->servo.currentSpeed > 0) shared->servo.currentSpeed = 0;
    }
  }

  int32_t positionIncrement = ((int32_t)((float)shared->servo.currentSpeed * (float)shared->executionInterval + (float)data->rampsDeltaPos.error) / 100000000);
  data->rampsDeltaPos.error = ((int32_t)((float)shared->servo.currentSpeed * (float)shared->executionInterval + (float)data->rampsDeltaPos.error) % 100000000);
  shared->servo.desiredSteps += positionIncrement;
}

/* ELS phase correction: jog the carriage to an integer multiple of thread
 * pitch above stopPosition, so the next sync return covers a whole number
 * of thread pitches and the trigger fires at the latched spindle phase.
 *
 * Two leadscrew-step quantities are compared:
 *   idealAdvance  = what pure sync would have done since latch
 *                   (spindle motion via syncRatio)
 *   actualAdvance = what the carriage actually did since latch
 *                   (Z motion via thread-pitch geometry)
 * Their difference, modulo pitch and folded to ±pitch/2, is the shortest
 * pre-cut jog that brings Z to thread-pitch alignment. Workflow-agnostic:
 * the carriage may have gotten to its current Z by any combination of
 * electronic retract, manual jog, half-nut snap, etc.
 *
 * See ARCHITECTURE.md → ELS Shoulder Stop for the conceptual model. */
/* ---- the spindle period, computed off the ISR and cached -----------------
 *
 * elsComputeSpindlePeriod() is double-precision arithmetic, which on this core
 * is a fistful of softfp library calls (see its banner in els_phase.h -- it
 * cost the ISR 2.6x its whole tick budget on 2026-08-28 when it ran inline).
 * It depends only on three registers that change at JOB SETUP, so recomputing
 * it per pass was waste as well as hazard.
 *
 * NOT IN THE REGISTER MAP ON PURPOSE. This is derived firmware-internal state,
 * not something the host reads, so it stays a file static and costs no
 * protocolVersion bump -- that append queue is congested with the take-up gate
 * and the re-sync latch command.
 *
 * THE KEY IS CARRIED WITH THE VALUE, and applyPhaseCorrection re-checks it
 * rather than trusting the refresh to have happened. A cache whose producer
 * runs on a 50 ms task and whose consumer runs at 100 kHz has a staleness
 * window by construction; the key check closes it. On a mismatch the ISR
 * passes 0, which means "do not reduce" -- the pre-04dd1f9 path -- so a stale
 * cache degrades gracefully instead of reducing by a period from the previous
 * job. Both compares are integer and the third is a single-precision float
 * compare (VCMP, hardware); none of this is a library call.
 *
 * A torn read is not possible: aligned 32-bit loads and stores are atomic on
 * Cortex-M4, and the ISR only ever reads. */
static int32_t elsPeriodCache      = 0;
static int32_t elsPeriodKeyNum     = 0;
static int32_t elsPeriodKeyDen     = 0;
static float   elsPeriodKeyTps     = 0.0f;

/* Called from updateSpeedTask (and mirrored in the emulator's main loop --
 * the emulator does not run FreeRTOS tasks). Cheap when nothing changed. */
void elsRefreshSpindlePeriod(rampsSharedData_t *shared) {
  int32_t num = shared->scales[0].syncRatioNum;
  int32_t den = shared->scales[0].syncRatioDen;
  float   tps = shared->elsStop.threadPitchSteps;

  if (num == elsPeriodKeyNum && den == elsPeriodKeyDen && tps == elsPeriodKeyTps) {
    return;
  }
  elsPeriodCache  = elsComputeSpindlePeriod(num, den, tps);
  elsPeriodKeyNum = num;
  elsPeriodKeyDen = den;
  elsPeriodKeyTps = tps;
}

/* The ISR-side read: returns the cached period only if it was computed for
 * exactly these registers, else 0 (= do not reduce). */
static inline int32_t elsCachedSpindlePeriod(const rampsSharedData_t *shared) {
  if (shared->scales[0].syncRatioNum == elsPeriodKeyNum
      && shared->scales[0].syncRatioDen == elsPeriodKeyDen
      && shared->elsStop.threadPitchSteps == elsPeriodKeyTps) {
    return elsPeriodCache;
  }
  return 0;
}

static inline void applyPhaseCorrection(rampsSharedData_t *shared) {
  int32_t deltaSpindle =
    shared->scales[0].position - shared->elsStop.latchedSpindle;
  int32_t deltaZ =
    shared->scales[shared->elsStop.scaleIndex].position - shared->elsStop.latchedZ;

  /* Pure, unit-tested phase-correction math (els_phase.h). The phaseError sign
   * is derived from stopDirection*cuttingDir so the Z-stop lands in phase for
   * BOTH DRO/leadscrew polarities — using the fixed ideal-actual form made the
   * thread phase walk 2x the backlash on machines where they run opposite. */
  elsCorrResult_t r = elsComputePhaseCorrection(
      deltaSpindle, deltaZ,
      shared->scales[0].syncRatioNum, shared->scales[0].syncRatioDen,
      shared->elsStop.threadPitchSteps, shared->elsStop.zCountsPerPitch,
      shared->elsStop.stopDirection,
      /* The live cumulative offset. Zero for every job that never sets one,
       * which is bit-for-bit the pre-feature path (els_phase_offset_test T1). */
      shared->elsStop.phaseOffsetSteps,
      /* Precomputed off the ISR; 0 if the cache does not match these exact
       * registers, which means do not reduce. */
      elsCachedSpindlePeriod(shared));

  shared->servo.stepsToGo += r.stepsToAdd;

  shared->elsStop.lastIdealAdvance  = r.idealAdvance;
  shared->elsStop.lastActualAdvance = r.actualAdvance;
  shared->elsStop.lastPhaseError    = r.phaseError;
  shared->elsStop.lastCorrection    = r.correction;

#ifdef EMULATOR_BUILD
  /* Arm step_6 trace: snapshot starting state so per-tick logs can report
   * deltas. emu_step6_pass identifies the pass we just left (the trigger seq
   * already incremented when this pass started). */
  emu_step6_pass             = emu_hw.els_last_stop_seq;
  emu_step6_tick             = 0;
  emu_step6_start_current    = (int32_t)shared->servo.currentSteps;
  emu_step6_start_desired    = (int32_t)shared->servo.desiredSteps;
  emu_step6_start_spindle    = shared->scales[0].position;
  emu_step6_start_z          = shared->scales[shared->elsStop.scaleIndex].position;
  emu_step6_pos_pulses       = 0;
  emu_step6_neg_pulses       = 0;
  emu_step6_dir_flips        = 0;
  emu_step6_prev_change_sign = 0;
  emu_step6_active           = 1;
  emu_log_trace("step6 #%u start cur=%d des=%d sp=%d z=%d stg=%d corr=%.1f",
                (unsigned)emu_step6_pass,
                (int)shared->servo.currentSteps,
                (int)shared->servo.desiredSteps,
                (int)shared->scales[0].position,
                (int)shared->scales[shared->elsStop.scaleIndex].position,
                (int)shared->servo.stepsToGo,
                (double)r.correction);
#endif
}

/* Leadscrew backlash calibration driver.
 *
 * Measures the lash by driving the carriage against a flank, then reversing and
 * counting the servo steps consumed before the Z scale moves. That step count IS
 * the backlash — see els_backlash_cal.h for why this is the only construction
 * that can distinguish a healthy take-up from an uncoupled drivetrain, and why
 * "Z must move backlashSteps worth" is wrong in the unsafe direction.
 *
 * Three reversals are measured; the HOST judges whether the spread is acceptable
 * and writes the resulting backlashSteps. Measurement here, policy in the UI.
 *
 * The state machine itself is the pure elsCalTick(); this function is only the
 * actuation shell — request intake, servo commands, abort conditions, and
 * publishing results. That split is what makes the logic host-testable without
 * the HAL.
 *
 * SAFETY: refuses to run unless elsStop.enable == 0 (no live job, so sync is not
 * driving the leadscrew and the spindle cannot fight the measurement) and
 * servoMode == 1 (otherwise stepsToGo is never consumed and the run would hang).
 * Total travel is a few lash widths — well under a millimetre — but it IS
 * bidirectional carriage motion with the half-nut engaged, so the operator-facing
 * modal owns "tool clear of the work, spindle stopped". */
static inline void elsCalUpdate(rampsHandler_t *data) {
  rampsSharedData_t *shared = &data->shared;

  /* ---- Request intake. calCommand is consumed atomically here and cleared in
   * the same ISR pass, which is the whole point of the command/ack split: a
   * 32-bit host write racing this ISR could otherwise be seen half-applied.
   * calSeq is the ack — SW must edge-detect THAT, never poll calCommand. ---- */
  if (shared->elsStop.calCommand != 0u) {
    shared->elsStop.calCommand = 0u;
    if (data->elsCal.phase == ELS_CAL_IDLE) {
      uint16_t refuse = ELS_CAL_OK;
      if (shared->elsStop.enable != 0u) {
        refuse = ELS_CAL_ERR_ENABLED;
      } else if (shared->fastData.servoMode != 1u) {
        refuse = ELS_CAL_ERR_SERVOMODE;
      } else if (shared->elsStop.calCeilingSteps <= 0
                 || shared->elsStop.calMotionThreshCounts <= 0) {
        refuse = ELS_CAL_ERR_CONFIG;
      }

      if (refuse != ELS_CAL_OK) {
        shared->elsStop.calResult = refuse;
        shared->elsStop.calSeq++;            /* refusals are outcomes too */
      } else {
        /* Cutting direction, same derivation as the takeup and els_phase.h. When
         * no thread geometry is configured (a machine-level calibration on a
         * fresh setup) the product is 0, which is not < 0, so this degrades to
         * sign(syncRatioNum) — a sane default rather than a refusal. */
        int32_t cuttingDir = (shared->scales[0].syncRatioNum > 0) ? 1 : -1;
        if (shared->elsStop.threadPitchSteps * shared->elsStop.zCountsPerPitch < 0.0f) {
          cuttingDir = -cuttingDir;
        }
        shared->servo.stepsToGo    = 0;
        shared->servo.currentSpeed = 0;
        elsCalStart(&data->elsCal, cuttingDir,
                    (int32_t)shared->servo.currentSteps,
                    shared->scales[shared->elsStop.scaleIndex].position);
        shared->servo.stepsToGo = data->elsCal.driveSign * shared->elsStop.calCeilingSteps;
      }
    }
  }

  if (data->elsCal.phase == ELS_CAL_IDLE) return;

  /* ---- Abort. Either condition means the ground shifted under a run that is
   * physically moving the carriage, so stop commanding motion immediately rather
   * than finishing a measurement whose premises no longer hold. ---- */
  if (shared->elsStop.enable != 0u || shared->fastData.servoMode != 1u) {
    shared->servo.stepsToGo    = 0;
    shared->servo.currentSpeed = 0;
    data->elsCal.phase         = ELS_CAL_IDLE;
    shared->elsStop.calResult  = ELS_CAL_ERR_ABORTED;
    shared->elsStop.calSeq++;
    return;
  }

  elsCalAction_t act = elsCalTick(&data->elsCal,
                                  (int32_t)shared->servo.currentSteps,
                                  shared->scales[shared->elsStop.scaleIndex].position,
                                  shared->servo.stepsToGo,
                                  shared->elsStop.calMotionThreshCounts);

  if (act.startPhase) {
    /* Command the full ceiling; the leg ends early the moment Z moves. Draining
     * to zero without motion is exactly the uncoupled failure. */
    shared->servo.stepsToGo = act.driveSign * shared->elsStop.calCeilingSteps;
  }

  if (act.finished) {
    shared->servo.stepsToGo    = 0;
    shared->servo.currentSpeed = 0;
    /* Publish partial measurements on failure too — a run that measured two
     * reversals and then lost the carriage is diagnostically richer than a bare
     * error code, and the host already knows how many to trust from calResult. */
    for (int32_t i = 0; i < ELS_CAL_CYCLES; i++) {
      shared->elsStop.calMeasured[i] = data->elsCal.measured[i];
    }
    shared->elsStop.calResult = data->elsCal.result;
    shared->elsStop.calSeq++;
    data->elsCal.phase = ELS_CAL_IDLE;
  }
}

void SynchroRefreshTimerIsr(rampsHandler_t *data) {
  uint32_t start = DWT->CYCCNT;
  // Reset the step pin as soon as possible
  HAL_GPIO_WritePin(STEP_GPIO_PORT, STEP_PIN, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(SPARE_2_GPIO_PORT, SPARE_2_PIN, GPIO_PIN_RESET);
  rampsSharedData_t *shared = &(data->shared);
  shared->executionIntervalPrevious = shared->executionIntervalCurrent;
  shared->executionIntervalCurrent = DWT->CYCCNT;
  shared->executionInterval = shared->executionIntervalCurrent - shared->executionIntervalPrevious;
  shared->fastData.executionInterval = shared->executionInterval;

  /* The STEP pulse that the reset above just ended began at emission last
   * pass; its HIGH width is exactly start - stepPulseSetAt (Ramps.h, the
   * pulse width instrument). `start` was taken before the reset, so the
   * computation sitting here -- after `shared` exists -- measures the same
   * edge the drive saw. ~5 cycles per tick, unconditional: an instrument
   * that exists only in a probe build is a measurement nobody has. */
  if (data->stepPulseArmed) {
    data->stepPulseArmed = 0;
    uint32_t stepPulseWidth = start - data->stepPulseSetAt;
    if (shared->elsStop.stepPulseMinCycles == 0u
        || stepPulseWidth < shared->elsStop.stepPulseMinCycles) {
      shared->elsStop.stepPulseMinCycles = stepPulseWidth;
    }
    if (stepPulseWidth < ELS_STEP_RUNT_CYCLES) {
      shared->elsStop.stepPulseRuntCount++;
    }
  }

  // Reset reference latch on elsStop.enable rising edge (start of a new threading job)
  if (shared->elsStop.enable && !data->elsStopPreviousEnable) {
    shared->elsStop.referenceLatched = 0;
    /* The phase offset dies with the datum it was measured from. A new job means
     * a new reference, so carrying a half-pitch shift into it would put the very
     * first pass of the next thread out of phase for reasons the operator has no
     * way to see. Per-pass stop/resume does NOT come through here, so the offset
     * survives within a job -- which is the entire point of holding a total. */
    shared->elsStop.phaseOffsetSteps = 0;
  }

  // Auto-clear active when enable is deasserted
  if (data->elsStopPreviousEnable && !shared->elsStop.enable) {
    shared->elsStop.active = 0;
    /* Consume the 1->0 active edge this handler just created. The resume
     * detector further down runs LATER IN THIS SAME PASS (its shadow copy
     * elsStopPreviousActive is only refreshed after it), so it cannot
     * otherwise tell a software resume ("go cut") from the job ending here.
     * referenceLatched and the thread geometry survive from the last
     * stop-fire, so without this line every threading disengage after a
     * completed pass initiated a fresh backlash takeup — re-setting the
     * takeupPending cleared below and banking a move in stepsToGo that
     * executed at the next nonzero servoMode. Emulator repro:
     * els_disengage_edge_test. */
    data->elsStopPreviousActive = 0;
    /* Also abandon any in-flight takeup. This is the escape hatch that makes the
     * fail-closed Z confirmation gate below RECOVERABLE: a takeup withheld for
     * want of Z confirmation holds takeupPending = 1 indefinitely, which gates
     * sync off, and nothing else in the ISR clears it. Dropping enable ends the
     * job, so there is nothing left to confirm and no phase correction worth
     * applying. Without this, failing closed would be unrecoverable — a worse
     * defect than the one being fixed. */
    shared->elsStop.takeupPending = 0;
    /* Ending the job cancels its pending MOTION, not just its bookkeeping.
     * A takeup abandoned mid-flight otherwise leaves its remaining
     * stepsToGo as stored debt that executes whenever servoMode next goes
     * nonzero; likewise any sync backlog the pulse generator had not yet
     * emitted (desiredSteps ahead of currentSteps) would keep draining
     * after the job ended, and with servoMode 0 it survives indefinitely
     * as debt for the next feed-enable. Every writer of this falling edge
     * means "make the machine inert", so BOTH commanded-motion channels
     * are cleared with the job. All other desiredSteps writers are
     * incremental (+=), so the snap cannot corrupt a concurrent jog or
     * indexing ramp — they rebuild from their own state next tick. */
    shared->servo.stepsToGo    = 0;
    shared->servo.currentSpeed = 0;
    shared->servo.desiredSteps = shared->servo.currentSteps;
    data->elsStopSettleCount      = 0;
    data->elsStopTakeupTicks      = 0;
    data->elsStopTakeupLatched    = 0;
    data->elsStopCorrectOnConfirm = 0;
  }

  data->elsStopPreviousEnable = shared->elsStop.enable;

  /* Manual reference latch (interactive re-sync to an existing thread). Same
   * command/ack split as calCommand: consumed and cleared in one ISR pass so a
   * host-side two-register write can never be seen half-applied, and the pair is
   * captured in the same tick so spindle and Z are coherent. Only meaningful
   * inside a job — outside one the reference would be wiped on the next enable
   * 0->1 edge, so a latch while disabled is consumed WITHOUT the latchSeq ack
   * and the host reads the missing edge as the refusal. Setting referenceLatched
   * is also what suppresses the first-trigger auto-latch for the rest of the
   * job: the trigger block only captures while referenceLatched == 0. */
  if (shared->elsStop.latchCommand != 0u) {
    shared->elsStop.latchCommand = 0u;
    if (shared->elsStop.enable != 0u) {
      shared->elsStop.latchedZ         = shared->scales[shared->elsStop.scaleIndex].position;
      shared->elsStop.latchedSpindle   = shared->scales[0].position;
      shared->elsStop.referenceLatched = 1;
      shared->elsStop.latchSeq++;
    }
  }

  /* Thread-phase offset apply. The calCommand idiom again: Pending was fully
   * written before Command was set, so reading it here is atomic without a
   * lock. Gated on enable for the same reason as the latch above -- outside a
   * job the total would be wiped by the next enable edge, so accepting it would
   * be a lie. Absent seq increment = refused. NOTHING MOVES as a result of
   * this; the new total is consumed by the next applyPhaseCorrection(). */
  if (shared->elsStop.phaseOffsetCommand != 0u) {
    shared->elsStop.phaseOffsetCommand = 0u;
    if (shared->elsStop.enable != 0u) {
      shared->elsStop.phaseOffsetSteps = shared->elsStop.phaseOffsetPending;
      shared->elsStop.phaseOffsetSeq++;
    }
  }

  // Detect completion of post-resume backlash takeup move, dwell for the servo
  // to settle, CONFIRM THE CARRIAGE ACTUALLY MOVED, then apply phase correction.
  // takeupPending stays set throughout so sync remains gated and the servo holds
  // at the takeup target.
  if (shared->elsStop.takeupPending) {
    data->elsStopTakeupTicks++;
    /* Stillness, tracked every tick the take-up is in flight -- including
     * during the commanded move, so the counter is already meaningful when the
     * dwell ends. Any change in Z resets it: the test is "has it stopped", and
     * one count of movement means it has not.
     *
     * THIS IS PER-TICK EXACT EQUALITY, AND REPLACING IT IS NOT A FREE CHANGE.
     * Widening it to a net-displacement window (tolerate N counts of drift
     * across the window) was attempted on 2026-08-27 and BACKED OUT. It works
     * -- els_takeup_quiescence_window_test T2 shows it makes the gate immune to
     * +-1 dither, which exact equality is starved by forever -- but it is
     * logically incompatible with the invariant els_takeup_quiescence_test
     * pins at its line 419: "the gate waited at least the quiescence window
     * after the last pulse". That invariant IS exact equality restated; any
     * tolerance above zero lets the counter mature while the carriage is still
     * delivering counts, and at a tolerance of 2 the gate released with 1.17 Z
     * counts still undelivered -- more than the entire settle tail this gate
     * was built to catch. Tolerance 1 preserved the physical property (0.83
     * counts owed) but still broke the timing invariant.
     *
     * So the choice is Evan's, not a refactor: keeping the invariant means
     * living with the dither blind spot; taking the window means deciding that
     * "N counts of net drift" is the definition of stopped and rewriting that
     * assertion deliberately. Do not quietly widen this while the assertion
     * stands. See els_takeup_quiescence_window_test T2, which characterises
     * TODAY's behaviour and must go red the day this changes.
     *
     * COMPILED OUT ENTIRELY when the flag is off, not merely ignored. The
     * comparison is cheap, but this is the ~100 kHz ISR whose execution time is
     * itself a published register, and a release build should carry no cost at
     * all for a feature it does not use. Leaving it in cost 64 bytes of flash
     * and made the claim "a release build is bit-for-bit the old gate" false,
     * which is worse than the cycles. */
#if ELS_REQUIRE_QUIESCENCE
    {
      int32_t zQ = shared->scales[shared->elsStop.scaleIndex].position;
      if (zQ != data->elsStopQuiescentZ) {
        data->elsStopQuiescentZ     = zQ;
        data->elsStopQuiescentTicks = 0;
      } else if (data->elsStopQuiescentTicks < ELS_QUIESCENT_TICKS) {
        data->elsStopQuiescentTicks++;
      }
    }
#endif
    // Crossing test (not exact equality): the servo emits one step per servoCycle
    // while desiredSteps can advance several steps per tick, so currentSteps may
    // skip the exact target (esp. across the reversal pulse-skip). Treat the
    // takeup as complete once currentSteps reaches or passes the target in the
    // takeup direction.
    int32_t cur = (int32_t)shared->servo.currentSteps;
    bool takeupReached = (data->elsStopTakeupSign >= 0)
                         ? (cur >= data->elsStopTakeupTargetSteps)
                         : (cur <= data->elsStopTakeupTargetSteps);
    if (takeupReached) {
      /* First tick on which the commanded motion is complete -- t=0 for the
       * settle question. Fires BEFORE the ELS_SETTLE_TICKS dwell and before the
       * gate's first evaluation, so a trace covers the whole of both. */
      elsDiagCaptureStart(&data->diag);
      /* elsDiagExtraDwell() is ZERO in every release build and in every probe
       * but takeup-settle-v3, so this comparison is bit-for-bit the old one
       * unless that probe is compiled in. It exists because the settle this
       * dwell precedes cannot be MEASURED in 50 ticks: the gate confirms, the
       * phase-correction jog drives, and any capture ends. Holding the dwell
       * open is the only way to watch the carriage stop. See
       * els_diag_takeup_settle.h. The same term is added to the abort
       * threshold below so the confirm window keeps its full length. */
      if (data->elsStopSettleCount
          < ELS_SETTLE_TICKS + elsDiagExtraDwell(&data->diag)) {
        data->elsStopSettleCount++;        // dwell after commanded-complete
      } else {
        /* ---- Z CONFIRMATION GATE -------------------------------------------
         * `takeupReached` above is a PURE COMMANDED-STEP-COUNT CROSSING TEST. It
         * compares servo.currentSteps against a target this same firmware
         * computed and assigned at initiation, so it confirms exactly one thing:
         * that the firmware finished issuing the pulses it decided to issue.
         * Nothing in it observes the machine. If the half-nut is open, the servo
         * is disabled or faulted, or the coupling has slipped, currentSteps
         * crosses the target on schedule and the firmware reports a completed
         * takeup into thin air — and applyPhaseCorrection then snapshots a Z
         * from a drivetrain that was never coupled.
         *
         * ("Wait longer" is NOT the fix — see the ELS_SETTLE_TICKS note at the
         * top of this file. A 1 s dwell was tried on the real machine and
         * changed nothing. Dwelling cannot manufacture evidence.)
         *
         * The gate is decidable ONLY because the take-up is commanded past the
         * measured lash (elsCalTakeupCommand's measured + margin). A take-up
         * sized exactly at the lash legitimately produces zero carriage motion,
         * which is indistinguishable from an open half-nut — see the physics
         * note in els_backlash_cal.h. The margin is what creates the evidence.
         *
         * FAIL CLOSED, and note WHERE this runs: at the START of a pass. active
         * has just been cleared, the takeup has consumed stepsToGo, and
         * takeupPending gates the sync accumulator below. The machine is
         * standing still with the tool clear of the work, about to BEGIN a cut.
         * Refusing here declines to start a pass; it does not abandon a tool
         * buried in a groove. Between a machine that visibly refuses to start
         * and one that confidently starts in the wrong groove, the refusal is
         * far the cheaper failure.
         *
         * The gate RE-EVALUATES every tick rather than latching dead, so a
         * takeup whose Z motion is merely late still clears itself;
         * elsStopSettleCount is deliberately left at ELS_SETTLE_TICKS while
         * withheld so no second dwell is imposed once it does. */
        int32_t zNow = shared->scales[shared->elsStop.scaleIndex].position;
        shared->elsStop.lastTakeupZDelta =
            elsZMovedAlong(zNow, data->elsStopTakeupZStart, data->elsStopTakeupZSign, 1);

        /* Confirm against EXPECTED motion, not the bare detection floor. A
         * calibrated take-up is entitled to move the carriage by
         * (margin + detection distance); demanding a fraction of that rejects a
         * take-up that barely twitched when it should have moved several counts
         * — partial half-nut engagement, a slipping nut — which the floor alone
         * would wave through. Falls back to the floor with no calibration on
         * file or in turning mode, so an uncommissioned machine behaves exactly
         * as it did before. Published for the UI so a refusal can say what it
         * wanted, not just that it refused. */
        shared->elsStop.takeupThreshCounts = elsTakeupConfirmThreshold(
            (int32_t)shared->elsStop.backlashSteps,
            elsCalMeanValid(shared->elsStop.calMeasured, ELS_CAL_CYCLES),
            shared->elsStop.threadPitchSteps,
            shared->elsStop.zCountsPerPitch,
            shared->elsStop.calMotionThreshCounts);

        /* ATTRIBUTED motion only. lastTakeupZDelta above still reports the raw
         * endpoint delta — it is the wrong-way diagnostic and the number the UI
         * shows — but the endpoint comparison is NOT what decides any more.
         *
         * The endpoint delta cannot tell "the drivetrain moved the carriage"
         * from "something else moved the carriage during the same 250 ms", and
         * on 2026-08-08 a hand nudge with the half-nut open exploited exactly
         * that. elsSlipConfirmed() counts only the Z counts that arrived while
         * the servo was driving or settling from a pulse (els_slip.h), which is
         * evidence of coupling rather than merely evidence of motion.
         *
         * Same floor, same fail-closed convention, same `>=`: this is a
         * strictly SMALLER numerator against an unchanged threshold, so nothing
         * that was refused before is confirmed now. Read els_slip.h on why this
         * narrows the exposure without closing it — a nudge landing inside the
         * settle horizon of a real pulse is still indistinguishable, and no
         * amount of arithmetic here changes that. */
        /* QUIESCENCE, and note it is an AND against the existing evidence, never
         * a replacement: a carriage that is merely stationary has proved
         * nothing about coupling. Compiled to a constant `true` unless
         * ELS_REQUIRE_QUIESCENCE is set, so a release build reaches the old
         * gate's decision by exactly the old path.
         *
         * NOT literally bit-for-bit, and the difference is stated rather than
         * rounded off: the two tracker fields stay in rampsHandler_t either way
         * -- measured at +16 bytes of flash in the ARM release build -- because
         * making a struct layout depend on a compile flag is a worse trade than
         * 16 bytes. Behaviour is unchanged; the binary is slightly larger.
         *
         * When it withholds, it does so WITHOUT reporting a refusal:
         * the take-up may be perfectly good and simply still settling, and the
         * gate re-evaluates every tick. A carriage that never goes quiet is
         * caught by the confirm window below, which is the honest failure. */
#if ELS_REQUIRE_QUIESCENCE
        bool carriageStopped = (data->elsStopQuiescentTicks >= ELS_QUIESCENT_TICKS);
#else
        bool carriageStopped = true;
#endif
        if (carriageStopped
            && elsSlipConfirmed(&data->elsSlip, shared->elsStop.takeupThreshCounts)) {
          if (shared->elsStop.takeupResult != ELS_CAL_OK) {
            shared->elsStop.takeupResult = ELS_CAL_OK;
          }
          shared->elsStop.takeupSeq++;
          data->elsStopSettleCount      = 0;
          data->elsStopTakeupTicks      = 0;
          shared->elsStop.takeupPending = 0;
          /* Snapshot Z only once CONFIRMED -- and only when there is a
           * reference to correct against. A first pass has none (the stop at
           * the end of THIS pass latches it) and turning has no pitch; both
           * still needed the take-up above to prove the drivetrain is coupled,
           * which is why this branch no longer corrects unconditionally
           * (2026-08-21, see the initiation block). */
          if (data->elsStopCorrectOnConfirm) {
            applyPhaseCorrection(shared);
          }
        } else if (carriageStopped
                   && shared->elsStop.takeupResult != ELS_TAKEUP_ERR_UNCONFIRMED) {
          /* Report once on the transition, not every tick, so takeupSeq stays a
           * count of OUTCOMES rather than a tick counter.
           *
           * Gated on carriageStopped as well: while the carriage is still
           * moving there is no verdict yet, and announcing UNCONFIRMED then
           * would put a refusal on the operator's screen for a take-up that is
           * about to succeed. Silence until the machine has settled enough to
           * be judged. */
          shared->elsStop.takeupResult = ELS_TAKEUP_ERR_UNCONFIRMED;
          shared->elsStop.takeupSeq++;
        }

        /* Close the window once late motion has had its chance, then ABORT THE
         * PASS back to the stopped state rather than holding the machine.
         *
         * Holding takeupPending forever was worse than useless: sync stayed
         * gated with no way out but the enable 1->0 escape hatch, and that hatch
         * clears referenceLatched on the next 0->1 edge — so recovering from a
         * FALSE alarm cost the operator their thread phase reference. The
         * remedy was more expensive than the fault.
         *
         * Aborting to active = 1 returns the machine to exactly where it was
         * before the operator pressed Cut: stopped at the shoulder, reference
         * intact, sync gated because we are stopped rather than because we are
         * stuck. The operator closes the half-nut and presses Cut again. Nothing
         * to reset, nothing lost.
         *
         * takeupResult stays UNCONFIRMED so the UI can say why, and is cleared
         * at the next take-up initiation, which makes the warning self-clearing
         * on retry. */
        if (!data->elsStopTakeupLatched) {
          if (data->elsStopSettleCount
              < (ELS_SETTLE_TICKS + elsDiagExtraDwell(&data->diag)
                 + ELS_TAKEUP_CONFIRM_WINDOW_TICKS)) {
            data->elsStopSettleCount++;
          } else {
            /* AN ABORT MUST NEVER REPORT SUCCESS. takeupResult is set to
             * ELS_CAL_OK at initiation, and the UNCONFIRMED branch above is
             * gated on carriageStopped -- so a take-up whose carriage never
             * went quiet reaches this abort with OK still standing, and the UI
             * reads an ABORTED pass as a CONFIRMED one. That is the wrong
             * direction for a safety gate to fail in: the whole point of the
             * take-up is to prove the drivetrain is coupled before a cut, and
             * announcing proof that was never obtained is worse than
             * announcing nothing.
             *
             * Only rewrite an untouched OK. UNCONFIRMED and TIMEOUT are real
             * verdicts already published with their own takeupSeq bump, and
             * overwriting one would lose the more specific cause.
             *
             * LATENT, NOT LIVE: with ELS_REQUIRE_QUIESCENCE off (the default,
             * and the flag has never shipped on) carriageStopped is a constant
             * true, the UNCONFIRMED branch always fires first, and this cannot
             * be reached. It is fixed now so that turning the flag on is not
             * also the moment this is discovered. */
            if (shared->elsStop.takeupResult == ELS_CAL_OK) {
              shared->elsStop.takeupResult = ELS_TAKEUP_ERR_NOT_QUIESCENT;
              shared->elsStop.takeupSeq++;  /* an outcome, so it gets a seq */
            }
            data->elsStopTakeupLatched    = 1;
            shared->elsStop.takeupPending = 0;   /* stop holding the machine */
            shared->elsStop.active        = 1;   /* back to stopped-at-shoulder */
            shared->servo.stepsToGo       = 0;
            shared->servo.currentSpeed    = 0;
            data->elsStopSettleCount      = 0;
            data->elsStopTakeupTicks      = 0;
            /* referenceLatched deliberately untouched: a retry must be free. */
          }
        }
      }
    } else if (data->elsStopTakeupTicks > ELS_TAKEUP_TIMEOUT_TICKS
               && shared->elsStop.takeupResult != ELS_TAKEUP_ERR_TIMEOUT) {
      /* Backstop for a takeup that never reaches its target at all — the
       * servoMode != 1 hazard being the known way in (stepsToGo is only consumed
       * by updateIndexingPosition, so in mode 0 or 2 the takeup never advances
       * and sync stays gated with nothing to diagnose it). This does NOT release
       * the gate: releasing would start a pass on a takeup we know did not
       * happen. It names the failure so the UI can say so; recovery is the
       * enable 1->0 escape hatch above. */
      shared->elsStop.takeupResult = ELS_TAKEUP_ERR_TIMEOUT;
      shared->elsStop.takeupSeq++;
    }
  }

  for (int i = 0; i < SCALES_COUNT; i++) {
    data->scalesDeltaPos[i].oldPosition = data->scalesDeltaPos[i].position;
    data->scalesDeltaPos[i].position = __HAL_TIM_GET_COUNTER(ramps_timer_handles[data->shared.scales[i].timerHandleSlot]);
    data->scalesDeltaPos[i].delta = (int16_t) (data->scalesDeltaPos[i].position - data->scalesDeltaPos[i].oldPosition);
    shared->scales[i].position += data->scalesDeltaPos[i].delta * shared->scales[i].scaleDir;

    // calculate delta for sync ratio configured for the current scale
    deltaPositionAndError(
      shared->scales[i].position,
      shared->scales[i].syncRatioNum,
      shared->scales[i].syncRatioDen,
      &data->scalesSyncDeltaPos[i]
    );

    // request motion only if sync is enabled
    if (shared->scales[i].syncEnable != 0) {
      // Check ELS stop trigger (only latch when not already active)
      if (!shared->elsStop.active && shared->elsStop.enable) {
        int32_t refPos = shared->scales[shared->elsStop.scaleIndex].position;
        /* Hysteresis gate (elsStop.hysteresis, Ramps.h:102). Distance the axis
         * currently sits CLEAR of the threshold, on the retract side. The flag
         * is sticky-true until the next latch, so a resume issued with the axis
         * still at/past the threshold cannot re-latch in the same ISR pass and
         * swallow the 1->0 edge the resume path (Ramps.c:455) depends on.
         * hysteresis <= 0 sets the flag unconditionally every pass, which is
         * exactly the pre-gate behavior. */
        int32_t clearance = (shared->elsStop.stopDirection >= 0)
                            ? (shared->elsStop.stopPosition - refPos)
                            : (refPos - shared->elsStop.stopPosition);
        if (shared->elsStop.hysteresis <= 0 || clearance >= shared->elsStop.hysteresis) {
          data->elsStopHysteresisCleared = 1;
        }
        bool shouldStop = ((shared->elsStop.stopDirection >= 0)
                          ? (refPos >= shared->elsStop.stopPosition)
                          : (refPos <= shared->elsStop.stopPosition))
                          && data->elsStopHysteresisCleared;
        if (shouldStop) {
          shared->elsStop.active = 1;
          data->elsStopHysteresisCleared = 0;
          if (!shared->elsStop.referenceLatched) {
            shared->elsStop.latchedZ         = shared->scales[shared->elsStop.scaleIndex].position;
            shared->elsStop.latchedSpindle   = shared->scales[0].position;
            shared->elsStop.referenceLatched = 1;
          }
#ifdef EMULATOR_BUILD
          /* Atomic per-pass latch for the emulator dashboard. Lives outside
           * the Modbus map (rampsSharedData_t) so the SW protocol is
           * unaffected. Sequence counter increments so the dashboard can
           * edge-detect new triggers without polling `active` itself. */
          emu_hw.els_last_stop_spindle = shared->scales[0].position;
          emu_hw.els_last_stop_z       = shared->scales[shared->elsStop.scaleIndex].position;
          emu_hw.els_last_stop_seq++;

          /* Close out the step_6 trace if armed. Reports cumulative deltas
           * + direction-flutter counters so the analysis can compare the
           * predicted step_6 motion against what actually happened. */
          if (emu_step6_active) {
            int32_t d_cur   = (int32_t)shared->servo.currentSteps - emu_step6_start_current;
            int32_t d_des   = (int32_t)shared->servo.desiredSteps - emu_step6_start_desired;
            int32_t d_sp    = shared->scales[0].position - emu_step6_start_spindle;
            int32_t d_z     = shared->scales[shared->elsStop.scaleIndex].position - emu_step6_start_z;
            int32_t syncErr = data->scalesSyncDeltaPos[shared->elsStop.scaleIndex].error;
            emu_log_trace("step6 #%u end t=%u dCur=%+d dDes=%+d syncE=%d dSp=%+d dZ=%+d flips=%u P+=%u P-=%u",
                          (unsigned)emu_step6_pass, (unsigned)emu_step6_tick,
                          (int)d_cur, (int)d_des, (int)syncErr,
                          (int)d_sp, (int)d_z,
                          (unsigned)emu_step6_dir_flips,
                          (unsigned)emu_step6_pos_pulses, (unsigned)emu_step6_neg_pulses);
            emu_step6_active = 0;
          }
#endif
        }
      }

      /* Sync paused while stopped, while a backlash takeup is in progress, or
       * while a backlash CALIBRATION is running.
       *
       * The calibration gate is not optional and elsStop.enable == 0 does not
       * imply it: scales[].syncEnable is INDEPENDENT of the ELS stop feature, so
       * a turning spindle drives the leadscrew through sync even with no
       * threading job armed. During a calibration that motion is superimposed on
       * the measurement legs — at the reference geometry it swamps them
       * outright, and the run either measures the spindle or fails to arm at all
       * (observed: measured 0,0,0 / NO_MOTION on a perfectly healthy simulated
       * drivetrain before this gate existed). A calibration leg is only
       * meaningful if the leadscrew is moved by the calibration and nothing
       * else. */
      /* The servoMode == 1 term keeps accumulation and emission on the SAME
       * gate. Emission (further down) only runs with servoMode != 0, so
       * deltas accepted here in mode 0 are not motion — they are stored
       * debt the servo chases wholesale whenever mode next becomes 1, and
       * in mode 2 they superimpose spindle-sync creep onto the jog. Sync
       * counts that arrive while not in sync-follow mode are DISCARDED,
       * not banked: threading does not rely on banked deltas across a
       * pause — the resume machinery re-syncs phase from scale positions.
       * Emulator repro: els_sync_debt_test. */
      if (!shared->elsStop.active && !shared->elsStop.takeupPending
          && data->elsCal.phase == ELS_CAL_IDLE
          && shared->fastData.servoMode == 1) {
        shared->servo.desiredSteps += data->scalesSyncDeltaPos[i].scaledDelta;
      }
    }

    // Update fastData current position
    shared->fastData.scaleCurrent[i] = shared->scales[i].position;
  }

  // Detect SW clearing elsStop.active (1→0): the operator's "go" for a pass.
  if (data->elsStopPreviousActive && !shared->elsStop.active) {
    /* TWO jobs ride this edge and, since 2026-08-21, they are gated SEPARATELY.
     *
     * The backlash take-up plus its Z confirmation proves the drivetrain is
     * COUPLED before a pass starts. That needs only a configured backlash, so
     * it runs on EVERY pass of EVERY job -- the first pass included, turning
     * included. Until 2026-08-21 it sat inside the phase-correction condition
     * below, so exactly those two cases ran ungated: an open half-nut on pass
     * one gave a turning spindle and nothing happening with NO message (versus
     * the explicit refusal on any later pass), and a partially engaged nut on
     * pass one latched a reference from an uncoupled drivetrain that every
     * later pass then take-up-confirmed cleanly against -- the one failure
     * that is invisible afterwards. Emulator pins: els_takeup_confirm_test
     * (first pass and turning, coupled and open).
     *
     * The phase correction needs a latched reference and thread geometry. Pass
     * one has no reference (nothing to correct against; the stop at the end
     * of this pass latches it) and turning has no pitch, so it is skipped
     * there. That is what the old condition was right about, and
     * elsStopCorrectOnConfirm carries it to the confirmation gate's success
     * branch, which used to apply the correction unconditionally. */
    bool canCorrect = shared->elsStop.referenceLatched
                   && shared->elsStop.threadPitchSteps != 0.0f
                   && shared->elsStop.zCountsPerPitch  != 0.0f
                   && shared->scales[0].syncRatioDen   != 0;
    /* REFUSE A TAKE-UP IN JOG MODE, where nothing can ever rescue it.
     *
     * stepsToGo is consumed only by updateIndexingPosition, which runs in
     * servoMode 1. Commanding a take-up in another mode banks the move and
     * leaves takeupPending set, which gates sync -- the machine sits there
     * with the spindle turning and nothing happening.
     *
     * WHY MODE 2 ONLY, and not the blanket `servoMode != 1` this was first
     * written as. servoEnableTask auto-promotes the mode to 1 whenever sync
     * motion is enabled and the stop is not active -- but that promotion
     * explicitly skips mode 2 (see the anySyncMotionEnabled block). So:
     *
     *   mode 2 (JOG): unrescuable. The one thing that would fix it is the one
     *     thing that refuses to. Fail fast, here.
     *   mode 0: transient and legitimate. The task runs at ~100 ms while this
     *     ISR runs at ~100 kHz, so a resume can genuinely land on a tick where
     *     the mode has not been promoted YET. Refusing here would break normal
     *     cuts to guard a case that fixes itself within one task tick; the
     *     ~5 s ELS_TAKEUP_TIMEOUT_TICKS backstop covers the residue where it
     *     never does.
     *
     * The refusal returns the machine to exactly where it was before the
     * operator pressed Cut -- stopped at the shoulder, reference intact --
     * which is the same recovery shape the confirmation-failure abort already
     * uses, and requires nothing of the operator but leaving jog mode.
     * takeupResult reuses ELS_CAL_ERR_SERVOMODE rather than minting a code:
     * same physical cause, same sentence, the way ELS_TAKEUP_ERR_UNCONFIRMED
     * already shares NO_MOTION. */
    if (shared->elsStop.backlashSteps != 0u
        && shared->fastData.servoMode == 2u) {
      shared->elsStop.takeupResult = ELS_CAL_ERR_SERVOMODE;
      shared->elsStop.takeupSeq++;
      shared->elsStop.active       = 1;   /* back to stopped-at-shoulder */
      shared->servo.stepsToGo      = 0;
      shared->servo.currentSpeed   = 0;
    } else if (shared->elsStop.backlashSteps != 0u) {
      shared->servo.stepsToGo    = 0;
      shared->servo.currentSpeed = 0;
      data->elsStopSettleCount   = 0;
      /* Take-up direction: the servo sign of a cut -- sign(syncRatioNum),
       * flipped by the Z polarity the host encodes in the SIGN of
       * zCountsPerPitch. Threading has always expressed that polarity as the
       * sign of threadPitchSteps * zCountsPerPitch (the host writes pitch
       * positive, so the product's sign is zCountsPerPitch's). Turning writes
       * pitch = 0 and, since 2026-08-21, a signed zCountsPerPitch for exactly
       * this read, so the same polarity applies; a zCountsPerPitch of 0 (an
       * older host) falls back to bare sign(syncRatioNum). */
      int32_t cuttingDir = (shared->scales[0].syncRatioNum > 0) ? 1 : -1;
      float polarity = (shared->elsStop.threadPitchSteps != 0.0f)
                       ? shared->elsStop.threadPitchSteps * shared->elsStop.zCountsPerPitch
                       : shared->elsStop.zCountsPerPitch;
      if (polarity < 0.0f) {
        cuttingDir = -cuttingDir;
      }
      int32_t signedTakeup = cuttingDir * (int32_t)shared->elsStop.backlashSteps;
      shared->servo.stepsToGo       += signedTakeup;
      data->elsStopTakeupTargetSteps = (int32_t)shared->servo.currentSteps + signedTakeup;
      data->elsStopTakeupSign        = (signedTakeup >= 0) ? 1 : -1;
      shared->elsStop.takeupPending  = 1;
      /* Baseline for the Z confirmation gate. Captured at INITIATION, before
       * any pulses are issued, so the gate measures the whole takeup. */
      data->elsStopTakeupZStart      = shared->scales[shared->elsStop.scaleIndex].position;
      /* Quiescence tracker, reset with the take-up so no count carries over
       * from the previous one.
       *
       * NOT LOAD-BEARING, and said plainly because the comment here first
       * claimed otherwise: pre-seeding the counter to a fully-satisfied value
       * instead was mutation-tested and changed NOTHING (Q5). Any take-up that
       * actually moves the carriage resets the counter on its first tick of
       * motion, and one that moves the carriage nowhere is refused by
       * elsSlipConfirmed regardless of what this counter says. It is hygiene,
       * not a guard -- do not build a safety argument on it. */
#if ELS_REQUIRE_QUIESCENCE
      data->elsStopQuiescentZ        = data->elsStopTakeupZStart;
      data->elsStopQuiescentTicks    = 0;
#endif
      /* Same instant, same reason, for the attribution accumulator: it must
       * cover the whole take-up. It starts credited with nothing and stays
       * that way until the first pulse fires (els_slip.h), so the Z motion of
       * this initiation tick — which physically predates the take-up — cannot
       * be counted as evidence for it. */
      elsSlipReset(&data->elsSlip);
      /* Arm any diagnostic probe, at INITIATION. A trace probe clears itself
       * here rather than at capture start, because this tick already does a
       * pile of one-off work whereas the capture-start tick is the one whose
       * timing a settle probe exists to measure. Do not move this call. */
      elsDiagArm(&data->diag, &shared->elsStop);
      data->elsStopTakeupTicks       = 0;
      data->elsStopTakeupLatched     = 0;
      shared->elsStop.takeupResult   = ELS_CAL_OK;
      /* Direction the Z scale should count in for this takeup. droSign is
       * els_phase.h's quantity — how a cutting-direction servo move changes the
       * DRO — and the takeup itself runs in cuttingDir, so this reduces
       * algebraically to stopDirection. Kept as the explicit product because
       * the reduction is a coincidence of this call site, not an invariant.
       * Only the MAGNITUDE gates completion (see els_backlash_cal.h on why
       * detection is polarity-free); this sign is what turns lastTakeupZDelta
       * into a wrong-way diagnostic rather than just a distance. */
      data->elsStopTakeupZSign       = data->elsStopTakeupSign
                                     * ((int32_t)shared->elsStop.stopDirection * cuttingDir);
      data->elsStopCorrectOnConfirm  = canCorrect ? 1u : 0u;
    } else if (canCorrect) {
      applyPhaseCorrection(shared);
    }
  }
  data->elsStopPreviousActive = shared->elsStop.active;

  /* Backlash calibration. Deliberately placed AFTER the scale-read loop (so it
   * sees this tick's Z, not last tick's) and BEFORE updateIndexingPosition (so a
   * drive burst it commands takes effect the same tick, and the stepsToGo it
   * reads is the post-drain value from the previous tick). */
  elsCalUpdate(data);

  if (shared->fastData.servoMode == 1) updateIndexingPosition(data);
  if (shared->fastData.servoMode == 2) updateJogPosition(data);

  /* Bracket the pulse generator to recover THIS TICK's signed commanded step
   * delta. The generator's own `direction` is a local that dies at the end of
   * the block, and it is set to 1 even on ticks that emit nothing (it doubles as
   * the DIR pin state), so it is not the quantity wanted here. Differencing the
   * counter the generator actually writes is: it is -1/0/+1 by construction, it
   * cannot disagree with the pulses emitted, and it needs no new state. Unsigned
   * wraparound is intentional and well-defined — the ±1 survives the round
   * trip. */
  uint32_t servoStepsBeforePulse = shared->servo.currentSteps;

  if (shared->fastData.servoMode != 0 && servoCyclesCounter == 0) {
    int32_t change = (int32_t)(shared->servo.desiredSteps) - (int32_t)shared->servo.currentSteps;
    // generate pulses to reach desired position with the motor
    uint32_t direction = 1;

    if (change > 0) {
      direction = 1;
      HAL_GPIO_WritePin(DIR_GPIO_PORT, DIR_PIN, shared->servo.servoDir == 1 ? GPIO_PIN_SET : GPIO_PIN_RESET);
    }
    if (change < 0) {
      HAL_GPIO_WritePin(DIR_GPIO_PORT, DIR_PIN, shared->servo.servoDir == 1 ? GPIO_PIN_RESET : GPIO_PIN_SET);
      direction = -1;
    }

    if (direction == data->servoPreviousDirection && change != 0) {
      HAL_GPIO_WritePin(STEP_GPIO_PORT, STEP_PIN, GPIO_PIN_SET);
      HAL_GPIO_WritePin(SPARE_2_GPIO_PORT, SPARE_2_PIN, GPIO_PIN_SET);
      /* Pulse born here; its width is measured at the next entry's reset.
       * Fresh DWT read, not `start`: the whole point is how late in the tick
       * this set happened. */
      data->stepPulseSetAt = DWT->CYCCNT;
      data->stepPulseArmed = 1;
      shared->servo.currentSteps += direction;
#ifdef EMULATOR_BUILD
      if (emu_step6_active) {
        if (direction == 1) emu_step6_pos_pulses++;
        else                emu_step6_neg_pulses++;
      }
#endif
    }

    data->servoPreviousDirection = direction;
  }

  /* MOTION ATTRIBUTION — and the placement is the whole trick.
   *
   * This is the ONLY point in the ISR where this tick's Z delta and this tick's
   * commanded step delta are both fresh. The confirmation gate above runs at the
   * TOP of the pass, before the scale-refresh loop and long before this block,
   * so a per-tick correlation computed up there would pair LAST tick's Z against
   * THIS tick's pulse — a stale correlation that would look like it worked, and
   * would quietly mis-attribute exactly the motion it exists to judge.
   *
   * Cost of that ordering: the accumulator the gate reads is one tick behind,
   * the same 10 us staleness the gate already had against its own Z sample. It
   * is noise against a 250 ms window and against any settle horizon worth
   * setting.
   *
   * dZ is taken already signed by scaleDir, matching how scales[].position is
   * accumulated in the refresh loop, so attribution and the endpoint diagnostic
   * agree about which way the carriage went. */
  if (shared->elsStop.takeupPending) {
    uint16_t zIdx   = shared->elsStop.scaleIndex;
    int32_t  dZ     = data->scalesDeltaPos[zIdx].delta * shared->scales[zIdx].scaleDir;
    int32_t  dServo = (int32_t)(shared->servo.currentSteps - servoStepsBeforePulse);
    elsSlipTick(&data->elsSlip, dZ, dServo,
                elsSlipSettleTicks(ELS_SLIP_SETTLE_TICKS, (int32_t)servoCycles));
  }

  /* Same motion-attribution primitive, same placement, same reasoning -- for the
   * backlash calibration legs (elsCalUpdate() above, which runs BEFORE this
   * point in the ISR and therefore cannot see this tick's own dServo without
   * the same stale-pairing bug described above). elsCalCtx_t.slip is reset by
   * elsCalTick() itself at the instant a leg arms; from then until the leg ends
   * it is ticked from here, exactly like data->elsSlip is for the take-up.
   * Gated on `armed` (not just phase != IDLE) so the deceleration/reversal ramp
   * before arming -- deliberately excluded from the measurement -- does not
   * accumulate into a window that has not started yet. */
  if (data->elsCal.phase != ELS_CAL_IDLE && data->elsCal.armed) {
    uint16_t zIdx   = shared->elsStop.scaleIndex;
    int32_t  dZ     = data->scalesDeltaPos[zIdx].delta * shared->scales[zIdx].scaleDir;
    int32_t  dServo = (int32_t)(shared->servo.currentSteps - servoStepsBeforePulse);
    elsSlipTick(&data->elsCal.slip, dZ, dServo,
                elsSlipSettleTicks(ELS_SLIP_SETTLE_TICKS, (int32_t)servoCycles));
  }

  /* Diagnostic probe tick. THIS placement, not another: it is the only point
   * in the ISR where dZ is this tick's delta, which is the whole reason the
   * attribution blocks above sit here too. A probe that measures WHEN things
   * happen within a tick measures something else from anywhere else, so this
   * call does not move even though its body now lives in els_diag_*.h. */
  if (elsDiagCapturing(&data->diag)) {
    uint16_t zIdx   = shared->elsStop.scaleIndex;
    int32_t  dZ     = data->scalesDeltaPos[zIdx].delta * shared->scales[zIdx].scaleDir;
    int32_t  dServo = (int32_t)(shared->servo.currentSteps - servoStepsBeforePulse);
    elsDiagTick(&data->diag, &shared->elsStop, dZ, dServo);
  }

  /* Divide-by-zero guard. The zero window is REACHABLE, not theoretical:
   * servoCycles is 0 from reset (Ramps.c:64) and its only writer is
   * updateSpeedTask (Ramps.c:613), but RampsStart() enables the 100 kHz TIM9
   * interrupt as its last act (HAL_TIM_Base_Start_IT, Ramps.c:165) and main.c
   * only reaches osKernelStart() afterwards, so this ISR runs before the
   * scheduler exists at all, and updateSpeedTask then sleeps osDelay(50) before
   * its first assignment. That is >5000 ISR ticks at 10 us with servoCycles == 0.
   * It goes unnoticed on hardware because Cortex-M4 UDIV-by-zero yields 0 with
   * DIV_0_TRP clear (never set here) and servoMode is still 0 so no pulses are
   * emitted; it is still C undefined behavior, and the emulator's x86 build
   * takes SIGFPE on this line. Substituting 0 reproduces the observed hardware
   * result without the UB. Nonzero servoCycles behaves exactly as before.
   * COUPLED WITH updateSpeedTask's newPeriod clamp (Ramps.c:610-613): that
   * clamp now floors at 1, not 0, so the boot window above is the only
   * remaining source of servoCycles == 0. This guard must stay regardless —
   * it is what makes that window survivable. */
  servoCyclesCounter = (servoCycles != 0) ? (servoCyclesCounter + 1) % servoCycles : 0;

#ifdef EMULATOR_BUILD
  /* Step_6 per-tick trace: flutter detection + periodic sample. End-of-tick
   * so post-emission state is captured. Direction flutter = sign flips of
   * (desiredSteps − currentSteps); high flip counts would support the
   * indexing↔sync interaction hypothesis (DEBUGGING.md #1). */
  if (emu_step6_active) {
    int32_t change_now = (int32_t)shared->servo.desiredSteps - (int32_t)shared->servo.currentSteps;
    int32_t cs = (change_now > 0) ? 1 : (change_now < 0 ? -1 : 0);
    if (cs != 0 && emu_step6_prev_change_sign != 0 && cs != emu_step6_prev_change_sign) {
      emu_step6_dir_flips++;
    }
    if (cs != 0) emu_step6_prev_change_sign = cs;

    emu_step6_tick++;
    if ((emu_step6_tick % emu_step6_log_interval) == 0) {
      int32_t d_cur   = (int32_t)shared->servo.currentSteps - emu_step6_start_current;
      int32_t d_des   = (int32_t)shared->servo.desiredSteps - emu_step6_start_desired;
      int32_t d_sp    = shared->scales[0].position - emu_step6_start_spindle;
      int32_t d_z     = shared->scales[shared->elsStop.scaleIndex].position - emu_step6_start_z;
      int32_t syncErr = data->scalesSyncDeltaPos[shared->elsStop.scaleIndex].error;
      emu_log_trace("step6 #%u t=%u dCur=%+d dDes=%+d syncE=%d dSp=%+d dZ=%+d flips=%u P+=%u P-=%u stg=%d",
                    (unsigned)emu_step6_pass, (unsigned)emu_step6_tick,
                    (int)d_cur, (int)d_des, (int)syncErr,
                    (int)d_sp, (int)d_z,
                    (unsigned)emu_step6_dir_flips,
                    (unsigned)emu_step6_pos_pulses, (unsigned)emu_step6_neg_pulses,
                    (int)shared->servo.stepsToGo);
    }
  }
#endif

  shared->executionCycles = DWT->CYCCNT - start;
  /* Peak-hold. Three instructions, and the only reason the cut-start spike is
   * visible at all -- see the executionCyclesPeak comment in Ramps.h. */
  if (shared->executionCycles > shared->elsStop.executionCyclesPeak) {
    shared->elsStop.executionCyclesPeak = shared->executionCycles;
  }
}

_Noreturn void userLedTask(__attribute__((unused)) void *argument) {
  uint16_t oldInCnt = 0;
  uint8_t blinkInterval = 0;

  for (;;) {
    osDelay(50);
    blinkInterval = (blinkInterval + 1) % 10;
    if (blinkInterval == 0) {
      HAL_GPIO_TogglePin(USR_LED_GPIO_Port, USR_LED_Pin);
    }

    if (oldInCnt != RampsModbusData.u16InCnt) {
      oldInCnt = RampsModbusData.u16InCnt;
      HAL_GPIO_WritePin(USR_LED_GPIO_Port, USR_LED_Pin, GPIO_PIN_RESET);
      osDelay(25);
      HAL_GPIO_WritePin(USR_LED_GPIO_Port, USR_LED_Pin, GPIO_PIN_SET);
    }
  }

}

const int32_t updateSpeedTaskTicks = 50;

_Noreturn void updateSpeedTask(void *argument) {
  rampsHandler_t *rampsData = (rampsHandler_t *) argument;

  for (;;) {

    // Update the current speed
    osDelay(updateSpeedTaskTicks);

    /* Keep the spindle period current. Off the ISR deliberately: the
     * computation is double-precision and there is no FP64 hardware on this
     * core. Cheap when the geometry has not moved, which is almost always. */
    elsRefreshSpindlePeriod(&rampsData->shared);

    // Update fast access variables
    rampsData->shared.fastData.cycles = rampsData->shared.executionCycles;
    rampsData->shared.fastData.servoCurrent = rampsData->shared.servo.currentSteps;
    rampsData->shared.fastData.servoDesired = rampsData->shared.servo.desiredSteps;

    // If maximum speed has been changed, update the motor timer accordingly
    float clock_freq = 100000000.0f / ((float) rampsData->synchroRefreshTimer->Init.Prescaler + 1) /
                       (float) (rampsData->synchroRefreshTimer->Init.Period + 1);

    // Clamping value for max speed to the maximum allowed by the current timer refresh rate from the sync routine
    if (rampsData->shared.servo.maxSpeed > 100000) {
      rampsData->shared.servo.maxSpeed = 100000;
    }

    float newPeriod = floorf(clock_freq / rampsData->shared.servo.maxSpeed);
    if (newPeriod > (float) UINT16_MAX) {
      newPeriod = 65535;
    }
    if (newPeriod < 1) {
      // Never clamp to 0: a period of 0 has no meaningful semantics (it
      // would encode "too fast to subdivide" as an impossible state), and
      // the only thing that makes a zero survivable is the ISR divide-by-
      // zero guard at Ramps.c:513. Floor at 1 (the fastest representable
      // subdivision) so this writer stops relying on that guard.
      newPeriod = 1;
    }
    servoCycles = (uint16_t) newPeriod;

    for (int i = 0; i < SCALES_COUNT; i++) {
      // Update scale/spindle speed value
      deltaPositionAndError(
        rampsData->shared.scales[i].position,
        1000,
        updateSpeedTaskTicks,
        &rampsData->scalesSpeed[i]
      );
      rampsData->shared.scales[i].speed = rampsData->scalesSpeed[i].scaledDelta;
      rampsData->shared.fastData.scaleSpeed[i] = rampsData->scalesSpeed[i].scaledDelta;
    }
  }
}

_Noreturn void servoEnableTask(void *argument) {
  rampsHandler_t *rampsData = (rampsHandler_t *) argument;
  rampsSharedData_t *shared = (rampsSharedData_t *) &rampsData->shared;
  uint32_t previousPosition = 0;

  for (;;) {
    osDelay(100);

    bool anySyncMotionEnabled = false;
    for (int i = 0; i < SCALES_COUNT; i++) {
      anySyncMotionEnabled = anySyncMotionEnabled || (shared->scales[i].syncEnable != 0);
    }

    /* The re-assert. elsDiagServoGate is a no-op returning false in every
     * release build; a diagnostic probe may refuse the assert and record that
     * it did (see els_diag_disengage_latch.h, which exists precisely because
     * this line can switch the feed back on after a disengage). */
    if (anySyncMotionEnabled && !shared->elsStop.active && rampsData->shared.fastData.servoMode != 2) {
      if (!elsDiagServoGate(&rampsData->diag, &shared->elsStop,
                            rampsData->shared.fastData.servoMode)) {
        rampsData->shared.fastData.servoMode = 1;
      }
    }

    rampsData->shared.fastData.servoSpeed = (float)(int32_t)(rampsData->shared.servo.currentSteps - previousPosition) * 10;
    previousPosition = rampsData->shared.servo.currentSteps;

    /* THE MACHINE MODE, published unconditionally -- release builds included.
     *
     * Derived here rather than in the ISR because it is a ~100 ms question
     * about what the machine is doing, and the ISR has no business spending
     * cycles on it. Written before the probe hook below so a mode-watch probe
     * and this register can never disagree about the same tick.
     *
     * This used to happen ONLY inside that probe. See the machineMode comment
     * in Ramps.h for why that made the rung-2 census uncollectable in any
     * build the operator would actually cut with. */
    elsPublishMachineMode(shared, (uint16_t)(rampsData->elsCal.phase != ELS_CAL_IDLE));

    /* Probe hook: a mode-watch probe derives and publishes the machine mode
     * once per task tick. No code in a release build or any other probe.
     * AFTER the re-assert above, so the published mode reflects this tick's
     * decision rather than last tick's. */
    elsDiagTaskTick(&rampsData->diag, shared,
                    (uint16_t)(rampsData->elsCal.phase != ELS_CAL_IDLE));

    if (shared->fastData.servoMode != 0) HAL_GPIO_WritePin(ENA_GPIO_PORT, ENA_PIN, GPIO_PIN_RESET);
    if (shared->fastData.servoMode == 0) HAL_GPIO_WritePin(ENA_GPIO_PORT, ENA_PIN, GPIO_PIN_SET);
  }
}
