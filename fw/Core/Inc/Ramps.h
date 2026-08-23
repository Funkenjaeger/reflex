/**
 * Copyright © 2022 <Stefano Bertelli>
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
 * LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
 * IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
 * WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
 * SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 */
#ifndef THIRD_PARTY_RAMPS_H_
#define THIRD_PARTY_RAMPS_H_

#include <stdint.h>
#include "stm32f4xx_hal.h"
#include "cmsis_os2.h"
#include "Modbus.h"
#include "Scales.h"
#include "els_backlash_cal.h"
#include "els_slip.h"

#define MODBUS_ADDRESS 17

#define STEP_PIN GPIO_PIN_0
#define STEP_GPIO_PORT GPIOA

#define DIR_PIN GPIO_PIN_14
#define DIR_GPIO_PORT GPIOB

#define ENA_PIN GPIO_PIN_15
#define ENA_GPIO_PORT GPIOB
#define ENA_DELAY_MS 500

#define USR_LED_Pin GPIO_PIN_12
#define USR_LED_GPIO_Port GPIOB

#define SPARE_1_PIN GPIO_PIN_1
#define SPARE_1_GPIO_PORT GPIOA

#define SPARE_2_PIN GPIO_PIN_3
#define SPARE_2_GPIO_PORT GPIOA

#define SPARE_3_PIN GPIO_PIN_4
#define SPARE_3_GPIO_PORT GPIOA

/* Diagnostic scratchpad geometry. These fix the SIZE of the reserved block in
 * elsStop_t and must NEVER change: the block's entire purpose is that its
 * offset is stable, so adding or retiring a probe never moves a register.
 * Change what goes IN the block (and diagSchema with it), never its size. */
#define ELS_DIAG_TRACE_BUCKETS 50

/* Diagnostic scratchpad schema ids -- which probe is compiled into the block.
 * Part of the register CONTRACT, not an implementation detail: reflex-ui
 * mirrors these and refuses any id it does not recognise, so append only and
 * never renumber. A stale reader that recognises an old id must not silently
 * accept a new probe's data under it. 0 means no probe. */
#define ELS_DIAG_SCHEMA_NONE 0
#define ELS_DIAG_SCHEMA_TAKEUP_SETTLE 1     /* RETIRED -- see v2 */
#define ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2 2  /* RETIRED -- see v3 */
#define ELS_DIAG_SCHEMA_DISENGAGE_LATCH 3
#define ELS_DIAG_SCHEMA_MODE_WATCH 4        /* RETIRED -- see v2 */
#define ELS_DIAG_SCHEMA_MODE_WATCH_V2 5
#define ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V3 6

/* WHICH probe is compiled in, selected by the build as
 * -DELS_DIAG_PROBE=ELS_DIAG_SCHEMA_<NAME>. scripts/build.sh --diag=<name> is the
 * supported way to set it; scripts/lib/diag.sh derives the legal names from the
 * schema defines above, so the script cannot offer a probe the firmware does not
 * have.
 *
 * ONE PROBE AT A TIME, AND THAT IS A PROPERTY OF THE SHAPE, NOT A RULE TO
 * REMEMBER. Every probe writes the same 64 reserved registers; two of them
 * compiled in together would interleave their fields and produce a capture that
 * looks well-formed and means nothing. Because the selection is a single macro
 * holding a single value, "two probes at once" is not a state this build system
 * can represent -- there is no combination of flags that expresses it. A
 * documented prohibition would have needed somebody to read the document.
 *
 * ELS_DIAG_SCRATCH is DERIVED, never passed. It is the "some probe is active"
 * umbrella the shared plumbing keys off; the per-probe capture code keys off
 * ELS_DIAG_PROBE. Passing ELS_DIAG_SCRATCH by hand is rejected below rather than
 * silently reserving the block for a probe that does not exist. */
#if defined(ELS_DIAG_SCRATCH) && !defined(ELS_DIAG_PROBE)
#error "ELS_DIAG_SCRATCH is derived, not passed. Use scripts/build.sh --diag=<probe>; see DIAG.md."
#endif

#ifdef ELS_DIAG_PROBE
/* A misspelled macro name expands to an undefined identifier, which the
 * preprocessor evaluates to 0 -- i.e. silently to "no probe" while the build
 * still calls itself diagnostic. Rejecting NONE explicitly is what turns that
 * into a compile error instead of a diagnostic build that measures nothing. */
#if ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_NONE
#error "ELS_DIAG_PROBE is unset, misspelled, or NONE. Use scripts/build.sh --diag=<probe>; see DIAG.md."
#elif ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_TAKEUP_SETTLE
#error "ELS_DIAG_SCHEMA_TAKEUP_SETTLE is RETIRED; use takeup-settle-v2. See DIAG.md."
#elif ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2
#error "ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2 is RETIRED (it could only capture ~50 ticks before the post-confirmation jog ended it, so a confirmed take-up was unmeasurable); use takeup-settle-v3. See DIAG.md."
#elif ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_DISENGAGE_LATCH
/* recognised */
#elif ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_MODE_WATCH
#error "ELS_DIAG_SCHEMA_MODE_WATCH is RETIRED (its diagNetCounts drowned the signal in no-op refusals); use mode-watch-v2. See DIAG.md."
#elif ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_MODE_WATCH_V2
/* recognised */
#elif ELS_DIAG_PROBE == ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V3
/* recognised */
#else
#error "unknown ELS_DIAG_PROBE. Register the schema id in Ramps.h and add it to this chain; see DIAG.md."
#endif
#define ELS_DIAG_SCRATCH 1
#endif

/* Probe capture state, held in rampsHandler_t. Deliberately generic and shared
 * by every probe rather than per-probe: it is two words of ISR-owned scratch,
 * and a per-probe union here would change rampsHandler_t's size from build to
 * build for no benefit. A probe needing more than this can keep its own statics
 * in its own header.
 *
 * 0 = idle, 1 = armed, 2 = capturing -- the states the existing probe uses; a
 * probe is free to mean something else by them. */
typedef struct {
  uint16_t state;
  uint32_t captureTick;   // ISR ticks since capture start
} elsDiagCtx_t;


typedef struct {
  int32_t delta;
  uint32_t oldPosition;
  uint32_t position;
  int32_t scaledDelta;
  int32_t error;
} deltaPosError_t;

typedef struct {
  uint32_t timerHandleSlot;            // init-only: index into ramps_timer_handles[]; held as a 4-byte slot id (not a pointer) so the modbus wire layout is identical on STM32 and 64-bit emulator hosts
  int32_t position;                    // READ-ONLY (firmware-owned): absolute encoder position, updated by ISR
  int32_t speed;                       // READ-ONLY (firmware-owned): encoder speed (counts/s), updated by updateSpeedTask
  int32_t syncRatioNum, syncRatioDen;  // SW write: sync ratio numerator/denominator (output steps per input count)
  uint16_t syncEnable;                 // SW write: 0 = sync disabled, non-zero = sync enabled for this scale
  int16_t scaleDir;                    // SW write: ±1, default +1 (no inversion); applied to encoder delta in ISR
} input_t;

extern TIM_HandleTypeDef *ramps_timer_handles[SCALES_COUNT];

typedef struct {
  float maxSpeed;              // SW write: maximum step rate (steps/s); clamped to 100000 by firmware
  float currentSpeed;          // READ-ONLY (firmware-owned): live ramp speed, managed by ramp algorithm
  float jogSpeed;              // SW write: jog target speed (steps/s); negative = reverse
  float acceleration;          // SW write: ramp acceleration (steps/s²)
  int32_t stepsToGo;          // SW write: remaining steps for indexing move; firmware decrements toward zero
  uint32_t destinationSteps;  // SW write: absolute destination step count for indexing mode
  uint32_t currentSteps;      // READ-ONLY (firmware-owned): step counter incremented/decremented by ISR
  uint32_t desiredSteps;      // READ-ONLY (firmware-owned): accumulated target steps, driven by sync/ramp
  int16_t servoDir;           // SW write: ±1, default +1 (no inversion); applied to DIR pin in ISR
} servo_t;

typedef struct {
  uint32_t servoCurrent;               // READ-ONLY (firmware-owned): mirror of servo.currentSteps, updated by updateSpeedTask
  uint32_t servoDesired;               // READ-ONLY (firmware-owned): mirror of servo.desiredSteps, updated by updateSpeedTask
  uint32_t stepsToGo;                  // READ-ONLY (firmware-owned): mirror of servo.stepsToGo, updated by ramp algorithm
  float servoSpeed;                    // READ-ONLY (firmware-owned): output step rate (steps/100ms), updated by servoEnableTask
  int32_t scaleCurrent[SCALES_COUNT];  // READ-ONLY (firmware-owned): mirror of scales[i].position, updated by ISR
  int32_t scaleSpeed[SCALES_COUNT];    // READ-ONLY (firmware-owned): mirror of scales[i].speed, updated by updateSpeedTask
  uint32_t cycles;                     // READ-ONLY (firmware-owned): ISR execution time in CPU cycles, updated by updateSpeedTask
  uint32_t executionInterval;          // READ-ONLY (firmware-owned): ISR interval in CPU cycles, updated by ISR
  uint16_t servoMode;                  // SW write: 0=disabled, 1=sync/index (also set by firmware), 2=jog
} fastData_t;

typedef struct {
  uint16_t enable;            // SW write: 1 = enable ELS stop feature
  uint16_t scaleIndex;        // SW write: which scale (0–3) is the position reference (Z axis)
  int32_t  stopPosition;      // SW write: threshold in encoder counts
  int16_t  stopDirection;     // SW write: 1 = stop when pos >= threshold, -1 = stop when pos <= threshold
  uint16_t active;            // bidirectional: firmware sets to 1 when triggered; SW writes 0 to resume
  float    threadPitchSteps;  // SW write: leadscrew steps per thread pitch (float); 0.0f = turning (no correction)
  int32_t  hysteresis;        // SW write: encoder counts carriage must retract before re-enabling; 0 = no hysteresis
  float    zCountsPerPitch;   // SW write: Z scale encoder counts per thread pitch; 0.0f = correction disabled
   uint32_t backlashSteps;     // SW write: leadscrew backlash takeup magnitude in servo steps; direction derived from sign(syncRatioNum) × sign(threadPitchSteps × zCountsPerPitch); 0 = takeup disabled
  int32_t  latchedZ;          // READ-ONLY (firmware-owned): scales[scaleIndex].position at first trigger of the job
  int32_t  latchedSpindle;    // READ-ONLY (firmware-owned): scales[0].position at first trigger of the job
  uint16_t referenceLatched;  // READ-ONLY (firmware-owned): 0 until first trigger captures the reference, 1 thereafter; reset on enable 0→1
  uint16_t takeupPending;     // READ-ONLY (firmware-owned): 1 while the backlash take-up that starts EVERY pass (first pass and turning included since 2026-08-21) is executing or awaiting Z confirmation; gates sync off meanwhile
  float    lastIdealAdvance;  // READ-ONLY (firmware-owned): last resume's deltaSpindle × syncRatioNum / syncRatioDen
  float    lastActualAdvance; // READ-ONLY (firmware-owned): last resume's deltaZ × threadPitchSteps / zCountsPerPitch
  float    lastPhaseError;    // READ-ONLY (firmware-owned): last resume's idealAdvance − actualAdvance (pre-modulo)
  float    lastCorrection;    // READ-ONLY (firmware-owned): last resume's correction added to stepsToGo (post-modulo)
  /* --- Backlash calibration + closed-loop take-up confirmation. APPENDED AT THE
   * TAIL of elsStop_t, which is itself the last member of rampsSharedData_t, so
   * every pre-existing Modbus register offset is unchanged. uint16s are grouped
   * ahead of the 32-bit fields so the block packs with ZERO padding, and the
   * next feature's registers append behind it the same way (reserved order per
   * the auto-start plan: re-sync latchCommand/latchSeq, then the auto-start
   * block). Algorithm, units, and the physics that constrain all of this live in
   * Core/Inc/els_backlash_cal.h — read that before changing anything here. */
  uint16_t protocolVersion;       // READ-ONLY (firmware-owned): register-layout version, starts at 1. Bump whenever this struct changes; reflex-ui checks it at connect so a map mismatch names itself instead of surfacing as garbled reads
  uint16_t calCommand;            // bidirectional: SW writes 1 to request a calibration run; FIRMWARE CLEARS IT on consume. This is the atomic hand-off. SW must NOT poll it for completion — it clears the instant the ISR picks it up, long before the run finishes. Edge-detect calSeq instead
  uint16_t calSeq;                // READ-ONLY (firmware-owned): increments once per finished run, success OR failure. Monotonic, so a host polling at Modbus rates cannot alias a fast run
  uint16_t calResult;             // READ-ONLY (firmware-owned): outcome of the run counted by calSeq. ELS_CAL_* in els_backlash_cal.h; 0 = OK
  uint16_t takeupResult;          // READ-ONLY (firmware-owned): outcome of the last take-up. ELS_CAL_*/ELS_TAKEUP_* in els_backlash_cal.h; 0 = OK. Replaces a binary fault flag so "carriage never moved" and "never reached target" stay distinguishable
  uint16_t takeupSeq;             // READ-ONLY (firmware-owned): increments once per take-up outcome; lets SW tell completed-normally from host-cleared, which takeupPending alone cannot
  int32_t  calMeasured[3];        // READ-ONLY (firmware-owned): lash measured at each of the 3 reversals, in servo steps. The HOST judges whether the spread is acceptable — measurement lives here, policy lives in the UI
  int32_t  calCeilingSteps;       // SW write: per-leg hard ceiling in servo steps. Driving this far without Z moving IS the open-half-nut / uncoupled failure. MACHINE-SPECIFIC; size it comfortably past the largest credible lash
  int32_t  calMotionThreshCounts; // SW write: Z scale counts that count as real motion. MACHINE-SPECIFIC — ~2 counts on elspi (200 counts/mm, so 1 count ≈ 2.5 servo steps); emulator is 400 counts/mm. 0 disables detection and FAILS CLOSED (never confirms) — deliberate: an unconfigured threshold must refuse, not wave everything through
  int32_t  lastTakeupZDelta;      // READ-ONLY (firmware-owned): signed Z counts moved across the last take-up, projected onto the take-up direction. NEGATIVE means the carriage moved the WRONG way — a distinct fault signature from "didn't move"
  int32_t  takeupThreshCounts;    // READ-ONLY (firmware-DERIVED, not operator-set): Z counts the last take-up had to move to be confirmed. Derived from (backlashSteps - mean(calMeasured)) via elsTakeupConfirmThreshold(); falls back to calMotionThreshCounts with no calibration on file or in turning mode. Published so the UI can say "moved 3, needed 4" instead of just refusing
  /* --- DIAGNOSTIC SCRATCHPAD — RESERVED, AND NEVER MEANINGFUL IN A BASELINE ---
   * A fixed 64-register (128-byte) block for temporary firmware-side
   * instrumentation, so a throwaway probe never has to change the register
   * LAYOUT and never has to bump protocolVersion again.
   *
   * The block is reserved UNCONDITIONALLY — it is part of the map in every
   * build, which is what makes its offset stable — but every WRITE to it is
   * compiled out unless ELS_DIAG_SCRATCH is defined. In a release build the
   * whole block reads zero. That makes "no probe in a real baseline" a property
   * of the binary rather than a promise in a document: release builds omit the
   * flag, so the writes do not exist.
   *
   * WHY protocolVersion CANNOT GUARD THIS, AND WHAT DOES. The point of
   * reserving the block is that adding a probe does NOT change the layout — so
   * the version deliberately does not bump, and nothing would otherwise tell a
   * reader that diagTrace[] meant one thing last build and something else this
   * build. That is a channel which hands back a plausible number with the wrong
   * meaning, which is worse than no channel. diagSchema closes it: it names the
   * probe currently compiled in, 0 means "nothing here", and ANY reader MUST
   * check it and refuse to interpret a value it does not recognise. Same
   * "names itself instead of surfacing as garbled reads" property that
   * protocolVersion gives the map as a whole.
   *
   * READ IT ON DEMAND, NEVER IN THE POLL LOOP. 64 registers is ~12 ms of extra
   * serial time per read at 115200 baud, against ~29 ms for the entire map —
   * a permanent 40% tax on every poll cycle, for a block that is empty in every
   * production build. reflex-ui reads this only when a diagnostic view is open.
   */
  uint16_t diagSchema;         // READ-ONLY (firmware-owned): identifies the probe compiled into the block. 0 = none; do NOT interpret anything below it. Never assume a schema you did not read
  uint16_t diagSeq;            // READ-ONLY (firmware-owned): increments once per COMPLETED capture. Edge-detect this; there is deliberately no "capture in progress" register to poll
  uint16_t diagBucketTicks;    // READ-ONLY (firmware-owned): ISR ticks summed into each diagTrace bucket. PUBLISHED so the host never has to assume the ISR rate — the repo has disagreed with itself about that rate by 10x
  uint16_t diagBucketCount;    // READ-ONLY (firmware-owned): populated diagTrace entries, for the same reason
  int32_t  diagSettleTicks;    // READ-ONLY (firmware-owned): ticks from capture start to the LAST tick that saw nonzero dZ. THE measurement ELS_SLIP_SETTLE_TICKS is a guess at — meaningful in v2, where the capture stops before the pass starts
  int32_t  diagNetCounts;      // READ-ONLY (firmware-owned): signed Z counts summed across the capture
  int16_t  diagTrace[ELS_DIAG_TRACE_BUCKETS];  // READ-ONLY (firmware-owned): per-bucket SIGNED sum of dZ. Signed rather than magnitude on purpose — encoder dither cancels, real motion does not, which is exactly the distinction a quiescence test needs and the reason to prefer net displacement over summed |dZ|
  uint16_t diagCaptureTicks;   // READ-ONLY (firmware-owned): ticks the capture actually ran, i.e. how long the servo stayed silent after the take-up. Distinct from diagSettleTicks, which is when Z last MOVED
  uint16_t diagEndReason;      // READ-ONLY (firmware-owned): ELS_DIAG_END_*. A window-full capture did not finish measuring; treat its tail as a floor, not a result
  uint16_t diagReserved[4];    // pads the block to a fixed 128 bytes so its size never depends on which probe is in it

  /* --- MACHINE MODE. PERMANENT, and deliberately NOT in the scratchpad above.
   *
   * The firmware's own answer to "what is this machine doing right now",
   * derived once per servoEnableTask iteration (~100 ms) by
   * elsDeriveMachineMode() and published here in EVERY build, release
   * included. Values are ELS_MMODE_* in els_machine_mode.h; reflex-ui mirrors
   * them and the register-map contract test pins both.
   *
   * WHY IT MOVED HERE (2026-08-22). It used to exist only while a mode-watch
   * probe was flashed, which republished it into diagCaptureTicks. Probes are
   * one-at-a-time by construction, so flashing any other probe silently chose
   * to collect no rung-2 census -- and the census is what rungs 3 and 4 are
   * blocked on. Two consecutive lathe sessions on 2026-08-21/22 ran
   * takeup-settle probes and recorded zero mode data, while the operator was
   * cutting real passes and had no way to know. A machine property that only
   * exists in a diagnostic build is a property nobody can rely on.
   *
   * APPENDED AFTER the diagnostic block on purpose: the block's whole value is
   * a stable offset, so nothing is inserted ahead of it. This is a real
   * register-map change and it bumps protocolVersion. */
  uint16_t machineMode;        // READ-ONLY (firmware-owned): ELS_MMODE_* (els_machine_mode.h), republished every servoEnableTask tick in every build
  /* EXPLICIT pad, not decoration. elsStop_t is 4-aligned (it holds int32/float),
   * so a lone trailing uint16 makes the compiler add two bytes of IMPLICIT
   * padding -- and this struct is cast wholesale into uint16 Modbus holding
   * registers (RampsModbusData.u16regsize = sizeof(shared)/sizeof(uint16_t)),
   * which turns invisible padding into a phantom register nobody declared and
   * reflex-ui cannot mirror. Naming it keeps both sides computing the same
   * size, which is exactly what the contract test caught here: firmware 436,
   * mirror 434. Same reason diagReserved[4] exists.
   *
   * KEPT even though the re-sync pair below now happens to restore alignment
   * on its own: this block must not depend on what follows it. Delete the pad
   * and the padding comes back the moment anything after it is removed. */
  uint16_t machineModeReserved;
  /* --- Interactive re-sync to an existing thread. The manual latch is the SAME
   * capture as the first-trigger auto-latch, at an operator-chosen point where
   * lash state was established by a cutting-direction jog. It sets
   * referenceLatched, which is exactly what suppresses the auto-latch for the
   * rest of the job. Appended at the tail per the reserved order above; the pair
   * is 4 bytes so no padding. Next append is the auto-start block. */
  uint16_t latchCommand;          // bidirectional: SW writes 1 to request a manual reference latch; FIRMWARE CLEARS IT on consume. Consumed ONLY while enable == 1 (a reference is meaningless outside a job and would be wiped by the next enable 0->1 anyway); when enable == 0 it is cleared with NO latchSeq increment, so an absent ack IS the refusal. SW must edge-detect latchSeq, never poll this
  uint16_t latchSeq;              // READ-ONLY (firmware-owned): increments once per ACCEPTED manual latch. Monotonic; the ack for latchCommand

  /* --- THREAD-PHASE OFFSET. Deliberately displaces where the tool re-enters
   * the thread by a chosen distance. The operator-facing job it was built for
   * is WIDENING A GROOVE PAST THE WIDTH OF THE CUTTER: cut the groove, step the
   * phase over by less than the cutter width, cut again, repeat until the
   * groove is the width wanted -- no re-indexing, and the datum is never
   * re-established. The register itself knows nothing about that; it is a
   * distance, and els_phase.h names a second source (the X-depth-derived
   * compound infeed) that will feed this same term. Appended at the tail per
   * the reserved order above; the uint16s come first and the int32s after, so
   * the block packs with zero padding.
   *
   * THE HAND-OFF is the calCommand idiom exactly: the host writes Pending FIRST,
   * then writes Command. The ISR reads Pending only under a nonzero Command, and
   * that ordering is the whole reason a 32-bit value crosses a 16-bit register
   * bus without a lock -- there is no window in which the ISR can see a half-
   * written Pending. Command is cleared by the FIRMWARE on consume, so the host
   * must NOT poll it for completion; edge-detect phaseOffsetSeq.
   *
   * CONSUMED ONLY WHILE enable == 1, same as latchCommand and for the same
   * reason: the total is cleared by the next enable 0->1 edge, so an offset
   * applied outside a job is a value that silently evaporates. When enable == 0
   * the command is cleared with NO seq increment -- the absent ack IS the
   * refusal.
   *
   * WHAT IT DOES NOT DO: applying an offset moves nothing. It changes the
   * correction computed at the NEXT resume's phase correction, so it is safe to
   * apply at any point in a job, mid-pass included, and its effect appears when
   * the carriage next returns to the thread.
   *
   * SIGN LIVES IN THE MACHINE FRAME, NOT THE CUTTING FRAME. phaseOffsetSteps is
   * summed straight into phaseError (els_phase.h) in leadscrew steps, ahead of
   * the mod-pitch fold and the forward bias. On a machine whose cuttingDir is -1
   * a given entry therefore displaces the tool the OTHER WAY along the helix --
   * an effective pitch-X where the operator pictured X. Both land in the SAME
   * groove, a whole pitch being one turn of the same helix, and the groove ends
   * up wider by the amount entered either way. What changes is WHICH FLANK
   * opens up.
   *
   * THE OLD ARGUMENT FOR WHY THIS IS HARMLESS DOES NOT SURVIVE THE RESTATEMENT,
   * and that is worth being explicit about. This note used to say the -1 machine
   * merely picks the COMPLEMENTARY start of an N-start thread, that every start
   * of a thread is a legitimate start so it cannot be a wrong cut, and that
   * cumulative entry self-corrects -- enter pitch/3 again and you are on the
   * start you wanted. None of the three transfer to widening. There is no
   * equally-good alternative result: the groove opens up on the flank opposite
   * the one intended. And entering more does NOT walk it back; it widens
   * further on the same wrong flank. Still not a safety issue -- the tool stays
   * in the groove and removes what was asked for -- but it is a wrong part, and
   * the only cheap defense is an air pass on the first step-over of a job.
   *
   * UNVERIFIED on real hardware; els_phase_offset_command_test pins today's
   * behavior for both polarities so that a future correction has to be
   * deliberate.
   *
   * NOT FOLDED HERE. The total is stored exactly as the host wrote it; the fold
   * to mod-pitch happens at use, inside the primitive. Folding on write would
   * make the register silently disagree with the number the host displays. */
  uint16_t phaseOffsetCommand;    // bidirectional: SW writes 1 to apply phaseOffsetPending as the new total; FIRMWARE CLEARS IT on consume. Not a completion flag -- edge-detect phaseOffsetSeq
  uint16_t phaseOffsetSeq;        // READ-ONLY (firmware-owned): increments once per ACCEPTED apply. Monotonic; the ack for phaseOffsetCommand
  int32_t  phaseOffsetPending;    // host-written candidate total, leadscrew steps. Read by the ISR ONLY under a nonzero phaseOffsetCommand; write it BEFORE the command, never after
  int32_t  phaseOffsetSteps;      // READ-ONLY (firmware-owned): the live cumulative total in leadscrew steps, applied at every phase correction. Cleared on the enable 0->1 edge that clears referenceLatched -- an offset is meaningless without the datum it offsets -- and survives per-pass stop/resume within a job
} elsStop_t;

typedef struct {
  uint32_t executionInterval;          // READ-ONLY (firmware-owned): ISR period in CPU cycles (current - previous timestamp)
  uint32_t executionIntervalPrevious;  // READ-ONLY (firmware-owned): DWT timestamp of previous ISR entry
  uint32_t executionIntervalCurrent;   // READ-ONLY (firmware-owned): DWT timestamp of current ISR entry
  uint32_t executionCycles;            // READ-ONLY (firmware-owned): ISR wall-clock duration in CPU cycles
  servo_t servo;
  input_t scales[SCALES_COUNT];
  fastData_t fastData;
  elsStop_t elsStop;
} rampsSharedData_t;


typedef struct {
  // Modbus shared data
  rampsSharedData_t shared;

  // STM32 Related
  TIM_HandleTypeDef *synchroRefreshTimer;
  UART_HandleTypeDef *modbusUart;

  deltaPosError_t scalesDeltaPos[SCALES_COUNT];
  deltaPosError_t scalesSyncDeltaPos[SCALES_COUNT];
  deltaPosError_t scalesSpeed[SCALES_COUNT];
  deltaPosError_t rampsDeltaPos;
  uint32_t servoPreviousDirection;
  uint16_t elsStopPreviousActive;
  uint16_t elsStopPreviousEnable;
  int32_t  elsStopTakeupTargetSteps;  // servo.currentSteps target value at end of post-resume backlash takeup
  int32_t  elsStopTakeupSign;         // direction of the takeup move (+1/-1); completion is a crossing test, not exact equality
  int32_t  elsStopSettleCount;        // ticks elapsed since takeup commanded-complete; dwell before phase-correction Z snapshot
  uint16_t elsStopHysteresisCleared;  // 1 once the axis has cleared stopPosition by >= elsStop.hysteresis counts; cleared when firmware latches active = 1
  int32_t  elsStopTakeupZStart;       // scales[elsStop.scaleIndex].position captured at takeup INITIATION; the baseline the Z confirmation gate measures against
  int32_t  elsStopTakeupZSign;        // +1/-1: sign the Z scale should move in for this takeup; sign(signedTakeup) x droSign. Only the magnitude gates completion — the sign turns lastTakeupZDelta into a wrong-way diagnostic
  int32_t  elsStopTakeupTicks;        // ISR ticks since takeup initiation; backstop against a takeup that never reaches target (see ELS_TAKEUP_TIMEOUT_TICKS)
  int32_t  elsStopQuiescentZ;         // last Z scale position the quiescence tracker saw; any change resets elsStopQuiescentTicks
  int32_t  elsStopQuiescentTicks;     // consecutive ISR ticks with Z unchanged. The "has it STOPPED" input the confirmation gate never had; only consulted when ELS_REQUIRE_QUIESCENCE is set (see Ramps.c)
  uint16_t elsStopTakeupLatched;      // 1 once the Z confirmation window has closed on an unconfirmed takeup; further Z motion can no longer release the gate (see ELS_TAKEUP_CONFIRM_WINDOW_TICKS)
  uint16_t elsStopCorrectOnConfirm;  // 1 if the take-up in flight is to be followed by applyPhaseCorrection() once CONFIRMED, i.e. a reference was latched and thread geometry was set at initiation. 0 on a first pass (no reference yet) and in turning (no pitch): those take-ups exist to prove coupling only. Set at initiation, read at confirmation, cleared with the job (2026-08-21).
  elsCalCtx_t elsCal;                 // backlash calibration run state; non-Modbus, the ISR owns it entirely (els_backlash_cal.h)
  /* Motion attribution for the Z confirmation gate: which of the Z counts seen
   * during a take-up arrived while the servo was actually driving. Non-Modbus,
   * ISR-owned, reset at take-up initiation (els_slip.h).
   *
   * Deliberately NOT published over Modbus yet. elsStop_t's tail has a reserved
   * append order (re-sync, then auto-start) and the attributed/unattributed
   * split is only worth a protocolVersion bump once reflex-ui has something to
   * say with it — "moved 20 counts, none of them ours" is a much better refusal
   * message than the current one, and that is a UI change, not a firmware one. */
  elsSlipAccum_t elsSlip;
  /* Diagnostic probe capture state. Non-Modbus, ISR-owned, and untouched by a
   * release build -- the no-op entry points in els_diag.h ignore it.
   *
   * UNCONDITIONAL, and LAST IN THE STRUCT, both on purpose. Unconditional so
   * every call site in Ramps.c can pass &data->diag with no #ifdef; last so
   * that carrying it in a release build cannot shift the offset of any field
   * above it. It is zero-initialised handler RAM, so it lives in .bss and never
   * reaches the .bin -- which is why this refactor left the release image
   * byte-identical. Keep it here at the tail. */
  elsDiagCtx_t diag;
} rampsHandler_t;

extern modbusHandler_t RampsModbusData;

void RampsStart(rampsHandler_t *rampsData);

void SynchroRefreshTimerIsr(rampsHandler_t *data);

_Noreturn void updateSpeedTask(void *argument);

_Noreturn void userLedTask(__attribute__((unused)) void *argument);

_Noreturn void servoEnableTask(void *argument);

//static void timServoEnableOnCallback(xTimerHandle pxTimer);
//static void timServoEnableOffCallback(xTimerHandle pxTimer);

/* LAST, and it has to be. els_diag.h's entry points take elsStop_t* and
 * elsDiagCtx_t*, so it cannot be included until both exist -- which is why this
 * sits at the foot of the header rather than up with the other includes.
 * els_machine_mode.h needs rampsSharedData_t for the same reason, and must
 * precede els_diag.h because the mode-watch probe calls its function. */
#include "els_machine_mode.h"
#include "els_diag.h"

#endif
