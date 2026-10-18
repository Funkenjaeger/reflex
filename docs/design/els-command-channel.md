# ELS command channel

**Status: design only, no code.** One question is open: where `FEED_OFF` is
consumed ("What the census answered"). `DISENGAGE` is built only after
adopt-on-connect (`ui/reflex/fsms/els_adopt.py`) passes its hardware run, so
the two changes to teardown are never verified in the same session.

This design moves transition legality and safety-critical teardown ordering
into the firmware, and register edges stop carrying command meaning.

## The pattern

The calibration subsystem is the template: `calCommand` (host writes,
firmware consumes and clears), `calSeq` (firmware-owned monotonic ack,
bumped last), `calResult` (firmware-owned outcome, refusals included).
`latchCommand`, `phaseOffsetCommand`, `bootCommand` and `clearRefCommand` use
the same idiom, and `registers/els_stop.yaml` declares it: a `kind: command`
field names its `ack:`, a `kind: seq` field names what it `acks:`, and the
generator enforces `seq_precedes_payload` (a seq sits below what it counts, so
a torn FC3 read comes out as stale seq with new payload) and
`command_clears_on_consume`. The command channel is that pattern, generalized
to the registers that move the carriage.

## Register additions

Declared in `registers/els_stop.yaml`, in one `protocolVersion` bump (12 to
13):

| Register | Type | Group | Writer | Meaning |
|---|---|---|---|---|
| `command` | u16 | cold | host | requested command id; firmware consumes and clears it to 0 on its next `servoEnableTask` tick (~100 ms). 0 = none. `kind: command`, `ack: commandSeq` |
| `commandSeq` | u16 | hot | firmware | bumped once per consumed command, AFTER `commandResult` is written: the ack the UI edge-detects in the tick snapshot, as it does `takeupSeq`. `kind: seq`, `acks: [commandResult]` |
| `commandResult` | u16 | hot | firmware | outcome of the most recent command: `OK`, or a named refusal |

`commandSeq` and `commandResult` are `access: ro_firmware`. The layout has
no free pads left, so the two hot registers grow the hot group (56 to 58 of
its 75-register budget) and move every cold register, `bootCommand`
included (absolute register 168 under versions 10 to 12). The flasher finds
`bootCommand` through a per-version table (`APP_BOOT_COMMAND_REG` in
`fw/scripts/modbus-flash.py`, checked against `registers/offsets.json`) and
refuses a version it does not list, so the new row lands in the same commit.

Parameters are **staged in the existing registers** (`stopPosition`,
`scaleIndex`, `stopDirection`, `hysteresis`, `stopOffset`, `threadPitchSteps`,
`zCountsPerPitch`, `backlashSteps`) and read by the firmware **at consumption
time**. That single property retires the arming batch's verdict per write
(`_arming_batch` in `ui/reflex/fsms/els_stop_registers.py`, `enable` written
last) once `ENGAGE` lands: the command's ack is the atomicity, and a
half-landed parameter set is refused by the firmware (`ERR_PARAMS`) instead
of guarded by the UI. `DISENGAGE` stages nothing.

Machine mode (`ELS_MMODE_*`) is the read-only `machineMode` register,
republished every `servoEnableTask` tick in every build
(`elsPublishMachineMode`, `fw/Core/Inc/els_machine_mode.h`).

## `DISENGAGE`, the only v1 command

Semantics, executed entirely inside the firmware:

1. clear `syncEnable` on every scale (motion source off first: the
   sync-first ordering the UI repeats at five call sites becomes this one
   line);
2. `servoMode = 0`;
3. clear `enable`. The ISR's enable falling-edge handler
   (`SynchroRefreshTimerIsr`, `fw/Core/Src/Ramps.c`) already does the rest:
   it clears `active`, consumes the resume edge it just made so no take-up
   starts, abandons an in-flight take-up, and cancels the job's pending
   motion (`stepsToGo`, ramp speed, sync backlog). `DISENGAGE` does not copy that work;
4. once the ISR has taken that edge, write `commandResult = OK` and bump
   `commandSeq`; the mode published on that tick reads `OFF`. If the edge
   has not run by the end of the consuming tick, the ack goes out on the
   next one, so an ack never describes a machine still carrying motion.

Legality: `DISENGAGE` is legal from **every** state, because it is the safe
direction. From `CAL`, `servoMode = 0` is the calibration's own abort
condition (`elsCalUpdate`), so the run ends with `calResult =
ELS_CAL_ERR_ABORTED` on `calSeq` and `commandResult` reads `OK_CAL_ABORTED`.
There is deliberately no state in which "make the machine inert" can be
refused.

UI side: the operator's Disengage writes one register and edge-detects
`commandSeq`. For that path, `on_enter_disabled`'s ordered batch
(`hal.disengage()`, `_disengage_batch`) retires, and so do the tests that pin
its order (`test_port_owner.py::test_the_disengage_order_survives_the_queue`,
`test_els_threaded_io.py::test_a_disengage_lands_in_order_with_the_scan_on_the_thread`).
`disengage_refusal()` in `ui_controller.py` stays as affordance: it refuses
while sync is armed with the spindle turning and while a cycle runs. Once the
firmware owns teardown, those refusals are a UX question. The Update screen,
which disengages before a bootloader reboot, can wait on `commandSeq` instead
of reading `enable` back.

The domain FSM enters `disabled` when the command is **written**, not when it
is acked. A session still in `stopped` while it waits would treat a reconnect
inside the wait as a link blip and re-arm the stop (the `stopped` branch of
`reconcile_firmware_on_connect`). A missing ack is a link fault, and the
answer to it is to issue `DISENGAGE` again: it is idempotent.

The other teardown sites issue the same writes in the same order:
`on_enter_alarm`, the `disabled`/`alarm` branch and the refused re-arm in
`reconcile_firmware_on_connect`, and the adopt plan's teardown rows. Each
switches to `DISENGAGE` in a later release, after a hardware run reaches it,
and its ordered writes are deleted then. The Sync Enable button's mid-cut
stop (`request_feed_enable`) is `FEED_OFF`'s, not `DISENGAGE`'s.

## Where the command is consumed

In `servoEnableTask`, **ahead of** its `servoMode` re-assert. Every 100 ms
the task re-asserts (`anySync && !active && servoMode != 2` sets
`servoMode = 1`), publishes `machineMode`, drives ENA and takes
`bootCommand`. `DISENGAGE` goes first in that tick, and the re-assert is
skipped on the tick that consumed it.

It is not the ISR, which consumes `calCommand`, `latchCommand`,
`phaseOffsetCommand` and `clearRefCommand`. The re-assert is a read followed
by a write in the task. An ISR consumption that lands between the read of
`syncEnable` and the write of `servoMode` is overwritten, which leaves
`servoMode = 1` behind an `OFF` ack.

The Modbus slave task (priority Normal) can preempt `servoEnableTask`
(Low), so a host write can land between the consumer's steps. A `syncEnable`
write landing there would reopen the re-assert window. The UI therefore
writes nothing to `elsStop` or `syncEnable` between a command write and its
ack.

Planned firmware work replaces the vendored Modbus slave with the shared RTU
core (`fw/modbus_rtu/`), which rejects host writes to `ro_firmware`
registers, and then replaces FreeRTOS with a superloop. For command
consumption:

- `servoEnableTask`'s body becomes a periodic superloop job, in the same
  order: `DISENGAGE`, the re-assert, mode publish, ENA, `bootCommand`.
- Frames are serviced between jobs, so no host write can land inside a
  consumption.
- The ISR still preempts every job, so step 4's ack-after-edge rule is
  unchanged.
- The period becomes the job's, not `osDelay(100)`. The latency figures
  below assume about 100 ms; a different period means re-reading them.

`DISENGAGE` lands before that work, so its emulator table test is part of the
baseline the replacement has to keep green.

## Connect, and adopt-on-connect

Adopt-on-connect (`els_adopt.adopt_plan`, carried out by
`ElsFsm._adopt_on_connect`) runs only for a session in `disabled`, which is
every session after a UI restart. It keys on the published mode:

| Mode at connect | Plan |
|---|---|
| `OFF`, `IDLE`, `JOG` | stay `disabled`, run the sync-first teardown |
| `HELD` | the same: a new session must not trust a stop it never committed (`ui/tests/system/test_warm_firmware.py`). Keeping the hold across a restart waits for a stop-target readback |
| `FEEDING`, `MOVING`, `TAKEUP`, `CAL`, unknown | stay `disabled`, write nothing, announce |

An engaged or alarmed session keeps the state-keyed reconcile: a reconnect
there is a link blip, not a restart. Where `DISENGAGE` meets this:

- The teardown rows become `DISENGAGE`. The connect-time teardown is the
  same four writes, so once the command is verified a teardown row issues it
  and waits for the ack. The snapshot (`read_motion_in_flight`,
  `read_current_mode`) is still taken first.
- A `DISENGAGE` consumed before a fresh session connects: the command
  clears within a tick and a UI restart takes about 10 s, so the new session
  finds it consumed: the mode reads `OFF`, and the at-rest row's teardown
  changes nothing. A fresh session records `commandSeq` at connect as its
  baseline and never takes an edge it did not wait for as an ack.
- A `DISENGAGE` written just before the link drops: the session is
  already `disabled` (it entered at the write), so its reconnect takes the
  adopt path. If the command never landed, the mode is whatever the session
  disengaged from, and that can be a hands-off row (`FEEDING` with the
  spindle stopped is one `disengage_refusal()` allows), which would leave
  the job running. A session with an unacked `DISENGAGE` therefore issues
  it again at reconnect instead of consulting the table.

## Later commands, one per paired release

`ENGAGE` (arm from staged stop params; refuse on missing or half-staged
params or Z past stop: the `arm_idle_stop` refusals become result codes),
`CUT` (release the hold; refuse unless `HELD` **and** the servo and sync are
in a state that can move), `RETRACT(steps staged)`, `FEED_ON`/`FEED_OFF`,
`JOG_ON`/`JOG_OFF`. Each migration deletes the corresponding boolean-poke
path in the same release. `active` stops being UI-writable once
`ENGAGE`/`CUT` land (its meanings by then live in the mode register), which
is where the `servoEnableTask` re-assert is removed and every register
becomes single-writer.

## What the census answered

The mode census comes from the flight recorder, which files the FSM state
and every input of `elsDeriveMachineMode` at 30 Hz, and the published
`machineMode` whenever it changes: 2.09 M samples and 190 Cuts. It cannot
see `syncEnable` (so
`FEEDING` and `IDLE` are told apart only by the published mode) or the
calibration's running flag.

- The priority order in `els_machine_mode.h` holds: no sample contradicts
  it, and every mismatch lasts at most one publish period (~100 ms). `JOG`,
  `IDLE` and `CAL` were never observed, so their adopt rows and `DISENGAGE`
  from `CAL` rest on the derivation and the emulator.
- `HELD` needs no split for `CUT`. It means armed before any stop, stop
  fired, or take-up aborted back to the shoulder; all three are Cut-legal,
  and the release edge already tells them apart (`referenceLatched`,
  `backlashSteps`). It does hide the servo: 29% of `HELD` samples have
  `servoMode 0`, while all 190 recorded Cuts had `servoMode 1`, so `CUT`'s
  gate checks the servo and sync too.
- About 100 ms of task consumption is fine for `CUT` (release to `FEEDING`
  takes a median 355 ms, and thread registration stays in the ISR) and for
  `DISENGAGE`, which is refused while sync is armed with the spindle turning
  and so runs at rest.
- **Open: `FEED_OFF`.** Sync off is the operator's mid-cut stop. At the
  measured feeds, 100 ms is 0.6 mm of travel at p90 and
  1.2 mm at p99. Whether `FEED_OFF` is consumed in the task or in the ISR
  waits on one hardware measurement: today's time from the Sync Enable press
  to `syncEnable` cleared, which the flight recorder does not capture. ISR
  consumption would also have to close the re-assert race above some other
  way.

## Non-goals

No continuous reconciler (divergence alarms, never auto-correction). No
UI-side legality authority (affordance gating derives from the mirrored
mode). No batching/queuing of commands: one in flight, seq-acked, ever.

## Verification plan

Per command: an emulator state×command table test (every mode × the new
command, asserting outcome and result code), the cross-registry contract
test extended to the command/result enums, and one hardware session
exercising the migrated flow from every mode it can reach before its old path
is deleted from the UI. For `DISENGAGE` the table also covers the `CAL` abort
(`calSeq` and `commandResult` both), the ack waiting for the enable-edge
pass, and a host `syncEnable` write landing during consumption. The UI tests
cover a reconnect with a `DISENGAGE` unacked (issued again) and a fresh
session's `commandSeq` baseline.
