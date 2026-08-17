/* Derived machine mode — one register's worth of truth about what the
 * machine is doing, computed BY THE FIRMWARE from its own state.
 *
 * WHY THIS EXISTS. The 2026-08-16 independent architecture review found that
 * machine mode lives implicitly in combinations of four registers (enable,
 * active, syncEnable[], servoMode), which the UI re-derives by hand (its
 * read_motion_in_flight computes `sync && servoMode && !active`), gets
 * subtly wrong, and cannot adopt after a restart. This function is the first
 * step of the agreed direction ("rung 1"): the firmware derives the mode as
 * a PURE FUNCTION of existing state and publishes it read-only, changing no
 * behavior. A UI-side watchdog then compares its own model against this for
 * weeks of real use ("rung 2") before any authority moves.
 *
 * Published today by the mode-watch diagnostic probe (schema 4) in the
 * reserved scratchpad; promoted to a real register at the next paired
 * protocolVersion bump once the taxonomy has survived contact with data.
 *
 * MODE VALUES ARE A WIRE CONTRACT once a build carrying them is flashed:
 * append, never renumber — the same discipline as the diag schema ids.
 *
 * KNOWN HONEST LIMITATION: HELD does not distinguish "armed idle, waiting
 * for Cut" from "stop fired, holding at the shoulder" — the registers
 * genuinely cannot tell them apart today (review §4.1, the elsStop.active
 * overload). The split arrives when that overload is dissolved; publishing
 * the merged state now is more truthful than guessing.
 */
#ifndef ELS_MACHINE_MODE_H
#define ELS_MACHINE_MODE_H

#include <stdint.h>

#define ELS_MMODE_OFF      0u  /* servoMode 0: no pulses possible, driver off */
#define ELS_MMODE_IDLE     1u  /* servoMode 1, nothing commanded, no sync */
#define ELS_MMODE_FEEDING  2u  /* servoMode 1, sync armed and not held: the
                                * spindle drives the leadscrew (or will the
                                * moment it turns) */
#define ELS_MMODE_MOVING   3u  /* servoMode 1, commanded move outstanding
                                * (retract / indexing: stepsToGo != 0) */
#define ELS_MMODE_JOG      4u  /* servoMode 2 */
#define ELS_MMODE_HELD     5u  /* enable && active: held at the stop */
#define ELS_MMODE_TAKEUP   6u  /* backlash takeup in flight (takeupPending) */
#define ELS_MMODE_CAL      7u  /* backlash calibration owns the servo */

/* Priority is part of the contract, most-specific first: the calibration and
 * takeup machineries own the servo outright while they run; a commanded move
 * or a jog IS motion even while held (retract happens while held, and
 * updateJogPosition ignores the hold entirely — the hold gates SYNC, and the
 * ELS stop only fires on sync motion, so jog bypasses both: inherited
 * semantics, documented here rather than changed); a hold otherwise
 * dominates because it is the safety-relevant fact. JOG-above-HELD was
 * learned the hard way on 2026-08-17: round-2 hardware jogging happened
 * while engaged-idle, the ledger truthfully said HELD throughout, and a day
 * of jog testing left zero JOG entries. calRunning is a parameter rather
 * than read from the handler so the function stays a pure map over the
 * shared struct — callers pass (elsCal.phase != ELS_CAL_IDLE). */
static inline uint16_t elsDeriveMachineMode(const rampsSharedData_t *shared,
                                            uint16_t calRunning) {
  uint16_t anySync = 0;
  for (int i = 0; i < SCALES_COUNT; i++) {
    if (shared->scales[i].syncEnable != 0u) { anySync = 1; break; }
  }
  if (calRunning)                          return ELS_MMODE_CAL;
  if (shared->elsStop.takeupPending != 0u) return ELS_MMODE_TAKEUP;
  if (shared->fastData.servoMode == 1u && shared->servo.stepsToGo != 0)
                                           return ELS_MMODE_MOVING;
  if (shared->fastData.servoMode == 2u)    return ELS_MMODE_JOG;
  if (shared->elsStop.enable != 0u && shared->elsStop.active != 0u)
                                           return ELS_MMODE_HELD;
  if (shared->fastData.servoMode == 0u)    return ELS_MMODE_OFF;
  if (anySync)                             return ELS_MMODE_FEEDING;
  return ELS_MMODE_IDLE;
}

#endif /* ELS_MACHINE_MODE_H */
