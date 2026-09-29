# ELS command channel

**Status: design only, no code.** "What the census must confirm first" lists
the assumptions that the mode census (`ui/reflex/fsms/els_mode_watch.py`) can
invalidate.

This design moves transition legality and safety-critical teardown ordering
into the firmware, and register edges stop carrying command meaning.

## The pattern

The calibration subsystem is the template: `calCommand` (host writes,
firmware consumes and clears), `calSeq` (firmware-owned monotonic ack,
bumped last), `calResult` (firmware-owned outcome, refusals included). The
command channel is that pattern, generalized to the registers that move the
carriage.

## Register additions

Appended to `elsStop_t` ahead of the reserved diag block, all previously
unused, in one `protocolVersion` bump:

| Register | Type | Writer | Meaning |
|---|---|---|---|
| `command` | u16 | host | requested command id; firmware consumes and clears to 0 within one servoEnableTask tick (~100 ms). 0 = none |
| `commandSeq` | u16 | firmware | bumped once per consumed command, AFTER `commandResult` is written — the ack the UI edge-detects |
| `commandResult` | u16 | firmware | outcome of the most recent command: `OK`, or a named refusal |

Parameters are **staged in the existing registers** (stopPosition,
scaleIndex, stopDirection, hysteresis, thread geometry, backlashSteps) and
read by the firmware **at consumption time**. That single property retires
the `on_enter_cutting` per-write-ACK accumulation: the command's ack is the
atomicity, and a half-landed parameter set is refused by the firmware
(`ERR_PARAMS`) instead of guarded by the UI.

Machine mode (`ELS_MMODE_*`) is the read-only `machineMode` register,
published in every build (`registers/els_stop.yaml`).

## `DISENGAGE`, the only v1 command

One command ships first. Semantics, executed entirely inside the firmware,
atomically with respect to the link:

1. clear `syncEnable` on every scale (motion source off first — the
   three-site UI ordering discipline becomes this one line);
2. `servoMode = 0`;
3. clear `enable` and `active` with the resume/takeup edge machinery
   suppressed (the job's pending motion dies with the job: stepsToGo, ramp
   speed, sync backlog);
4. publish `commandResult = OK`, bump `commandSeq`; mode register reads
   `OFF`.

Legality: `DISENGAGE` is legal from **every** state — it is the safe
direction. From `CAL` it aborts the run (the cal machinery already owns
that path; result notes `OK_CAL_ABORTED`). There is deliberately no state
in which "make the machine inert" can be refused.

UI side: the disengage flow writes one register and edge-detects
`commandSeq`; `on_enter_disabled`'s ordered teardown and its pinning tests
retire for that path. The kv-level `is_feeding` gate stays as affordance
(once the firmware owns teardown, the button's behavior is a UX question).

## Later commands, one per paired release

`ENGAGE` (arm from staged stop params; refuse on missing/half-staged
params or Z past stop — the arm_idle_stop refusals become result codes),
`CUT` (release the hold; refuse unless HELD), `RETRACT(steps staged)`,
`FEED_ON`/`FEED_OFF`, `JOG_ON`/`JOG_OFF`. Each migration deletes the
corresponding boolean-poke path in the same release. `active` stops being
UI-writable once `ENGAGE`/`CUT` land (its meanings by then live in the
mode register), which is where the servoEnableTask re-assert is removed
and every register becomes single-writer.

## What the census must confirm first

- The mode table's priority order matches reality (a census pair that
  contradicts it reorders the table before it becomes a legality input).
- Whether `HELD` must split into armed-idle vs stop-fired **before** `CUT`
  ships (CUT's legality gate reads HELD; if the two halves need different
  answers, the split is a prerequisite, not a follow-up).
- That the ~100 ms command-consumption latency is acceptable for every
  flow (it is for disengage; CUT may want the ISR to consume instead —
  measure before deciding).

## Non-goals

No continuous reconciler (divergence alarms, never auto-correction). No
UI-side legality authority (affordance gating derives from the mirrored
mode). No batching/queuing of commands — one in flight, seq-acked, ever.

## Verification plan

Per command: an emulator state×command table test (every mode × the new
command → asserted outcome and result code), the cross-registry contract
test extended to the command/result enums, and one hardware session
exercising the migrated flow before its old path is deleted from the UI.
