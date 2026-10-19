# ELS command channel

**Status: `DISENGAGE` is built (protocolVersion 13) and verified on hardware.**
The operator's disengage always uses it. `FEED_OFF` is decided: it stays
immediate ("What the census answered").

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
13), together with the calMeasured split below:

| Register | Type | Group | Writer | Meaning |
|---|---|---|---|---|
| `command` | u16 | cold | host | requested command id (`ELS_CMD_*`, `fw/Core/Inc/els_command.h`); the firmware consumes and clears it on its next `servoEnableTask` tick (~100 ms). 0 = none. `kind: command`, `ack: commandSeq` |
| `commandSeq` | u16 | hot | firmware | bumped once per consumed command, AFTER `commandResult` is written: the ack the UI edge-detects in the tick snapshot, as it does `takeupSeq`. `kind: seq`, `acks: [commandResult]` |
| `commandResult` | u16 | cold | firmware | outcome of the most recent command (`ELS_CMD_RESULT_*`): below 0x100 a success, 0x100 and up a refusal. Read live once `commandSeq` has moved |

`commandSeq` and `commandResult` are `access: ro_firmware`.

**The hot group does not grow.** The board tick reads fastData and the hot
group as one 86-register FC3 request, and
`test_the_tick_read_keeps_its_stated_margin_against_the_firmware` pins that at
70% of the 125-register ceiling. Two more hot registers would make it 88. So
`commandSeq` takes the hot slot `clearRefSeq` held under 12, and `clearRefSeq`
moves to cold: it acks no payload and no tick reader reads it
(`tools/audit_tick_readers.py`). `commandResult` is cold, which lays it out
above `commandSeq`, so the seq still precedes its payload; the UI reads it in a
later frame than the one that showed the seq move, so it is at least as new.

The new cold registers (`clearRefSeq`, `command`, `commandResult`,
`calBasisSteps`, four uint16s) sit right after `bootSeq`. No hot register
moves, `bootCommand` keeps absolute register 168, and the cold fields after
them move by four. The flasher finds `bootCommand` through a per-version table
(`APP_BOOT_COMMAND_REG` in `fw/scripts/modbus-flash.py`, checked against
`registers/offsets.json`) and refuses a version it does not list, so the table
carries a 13 row. The isr-gap bench reader (`fw/scripts/isr-gap.py`) reads the
moved diagnostic block, so it moves to the 13 addresses.

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

`ELS_CMD_DISENGAGE` = 1. Executed entirely inside the firmware
(`elsCommandIntake` and `elsCommandFinish`, `fw/Core/Src/Ramps.c`):

1. clear `syncEnable` on every scale (motion source off first);
2. `servoMode = 0`;
3. clear `enable`, and ask the ISR to run its end-of-job block
   (`elsCommandEndJob`). That block in `SynchroRefreshTimerIsr` clears
   `active`, consumes the resume edge it just made so no take-up starts,
   abandons an in-flight take-up, and cancels the job's pending motion
   (`stepsToGo`, ramp speed, sync backlog). The request runs it even when
   `enable` was already 0 and there is no falling edge, so a commanded move
   (`MOVING`), a jog's ramp, or a take-up started from a hold with `enable`
   0 does not survive the ack.
   Clearing `active` from the task instead would start a take-up, because the
   resume detector is not gated on `enable`;
4. once the ISR has cleared the request, no calibration is running and the
   machine still reads inert (`syncEnable`, `servoMode` and `enable` all 0),
   write `commandResult` and then bump `commandSeq`. If the request has not
   run by the end of the consuming tick, the ack goes out on a later one, so
   an ack never describes a machine still carrying motion. The mode is
   published before the ack in the same tick, so the mode a host reads beside
   the new `commandSeq` is `OFF`.

Legality: `DISENGAGE` is legal from **every** state, because it is the safe
direction. From `CAL`, `servoMode = 0` is the calibration's own abort
condition (`elsCalUpdate`), so the run ends with `calResult =
ELS_CAL_ERR_ABORTED` on `calSeq` and `commandResult` reads `OK_CAL_ABORTED`.
There is deliberately no state in which "make the machine inert" can be
refused. The only refusal is `ERR_UNKNOWN`, for an id the firmware does not
know, which changes nothing. A command written while another is still waiting
for its ack stays in the register and is consumed after that ack: one in
flight, one ack each.

UI side: the operator's Disengage writes one register and edge-detects
`commandSeq` (`ElsFsm._issue_disengage`, `_poll_command_ack`). The baseline is
read live just before the write. On the ack the UI brings its mirrors into
line without writing anything: `servoMode` (and the divergence watchdog's
commanded value, `ServoDispatcher.adopt_feed_off`) and every axis's
`syncEnable`. No ack within about a second (`ACK_TIMEOUT_POLLS`) is a link
fault: the UI issues `DISENGAGE` once more, since it is idempotent, and after
a second silence puts a notice on the status bar. A refusal also shows a
notice, and the UI then turns sync, the feed and the stop off itself, in that
order, with the writes `on_enter_alarm` uses (`stop_sync`, `stop_feed`,
`set_enable(False)`), so the machine is still made inert.

No firmware this UI pairs with should refuse `DISENGAGE` (the only refusal
is `ERR_UNKNOWN`). The refusal response stays because the session is already
`disabled` when the answer arrives, so ignoring a refusal would leave the job
armed while the screen says it is not.
`disengage_refusal()` in
`ui_controller.py` stays as affordance: it refuses while sync is armed with
the spindle turning and while a cycle runs. Once the firmware owns teardown,
those refusals are a UX question. The Update screen, which disengages before a
bootloader reboot, still waits for `enable` to read 0 in a tick after the
disengage; with the command path that takes one or two task ticks, well inside
its timeout.

The domain FSM enters `disabled` when the command is **written**, not when it
is acked. A session still in `stopped` while it waits would treat a reconnect
inside the wait as a link blip and re-arm the stop (the `stopped` branch of
`reconcile_firmware_on_connect`). An operator who engages a new job while a
`DISENGAGE` is still unacked drops it: the firmware consumes a command within
a tick or not at all, and issuing the old one at a later reconnect would end
the new job.

The other teardown sites still issue the ordered writes:
`on_enter_alarm`, the `disabled`/`alarm` branch and the refused re-arm in
`reconcile_firmware_on_connect`, and the adopt plan's teardown rows. Each
switches to `DISENGAGE` in a later release, after a hardware run reaches it,
and its ordered writes are deleted then. The Sync Enable button's mid-cut
stop (`request_feed_enable`) is `FEED_OFF`'s, not `DISENGAGE`'s.

## Where the command is consumed

In `servoEnableTask`, **ahead of** its `servoMode` re-assert. Every 100 ms
the task re-asserts (`anySync && !active && servoMode != 2` sets
`servoMode = 1`), publishes `machineMode`, drives ENA and takes
`bootCommand`. `elsCommandIntake` goes first in that tick, and the re-assert
is skipped while a command is in flight. `elsCommandFinish` runs after ENA is
driven and before `bootCommand`. `servoEnableTask` is a superloop job
(`RampsPoll`, `fw/Core/Src/Ramps.c`); the emulator runs the
same loop on its own thread.

It is not the ISR, which consumes `calCommand`, `latchCommand`,
`phaseOffsetCommand` and `clearRefCommand`. The re-assert is a read followed
by a write in the task. An ISR consumption that lands between the read of
`syncEnable` and the write of `servoMode` is overwritten, which leaves
`servoMode = 1` behind an `OFF` ack.

The superloop services Modbus frames between jobs, never inside one, so no
host write can land between the consumer's steps; the ISR preempts every
job, so step 4's ack-after-the-ISR rule holds. A write can land
between the consuming tick and a later ack tick. The UI writes nothing to
`elsStop` or `syncEnable` between a command write and its ack; a write that
does land there and reopens `syncEnable`, `servoMode` or `enable` holds the
ack back, and the next tick applies the teardown again.

`fw/emulator/test/els_command_test.cpp` covers `DISENGAGE` from every
machine mode against the real ISR.

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

- A fresh session reads `commandSeq` at connect as its baseline, and every
  `DISENGAGE` reads a fresh one just before its write, so a session never
  takes an edge it did not wait for as an ack.
- A `DISENGAGE` consumed before a fresh session connects: the command clears
  within a tick and a UI restart takes about 10 s, so the new session finds
  it consumed: the mode reads `OFF`, and the at-rest row's teardown changes
  nothing.
- A `DISENGAGE` written just before the link drops, or written with no link
  at all: the session is already `disabled` (it entered at the write). If the
  command never landed, the mode is whatever the session disengaged from, and
  that can be a hands-off row (`FEEDING` with the spindle stopped is one
  `disengage_refusal()` allows), which would leave the job running. So a
  session with an unacked `DISENGAGE` issues it again at reconnect, ahead of
  the adopt table, instead of consulting it.
- The teardown rows still run the ordered writes, and take the snapshot
  (`read_motion_in_flight`, `read_current_mode`) first.

## The calMeasured split

The firmware overwrites `calMeasured` at the end of every calibration run,
partial values on a failed one, so a gate derived from it drops to its bare
motion floor after a failure. Protocol 13 splits it. `calMeasured` is `ro_firmware` again: the last run's result.
`calBasisSteps` (u16, cold) is the gate's basis, and
`elsTakeupConfirmThreshold` takes it in place of `elsCalMeanValid(calMeasured)`.
The firmware copies a successful run's mean into it and leaves it alone on a
failed or aborted run. The host writes it at
connect (`reconcile_firmware_on_connect`) and on a gear change
(`_reteach_gear`), in the engaged gear's steps. What the host still writes after a run is the case the
firmware cannot see: a run it reported OK that the host rejects for its
spread, or that the operator closes without accepting. The basis goes back to
the accepted record (0 with none).

**One mean.** The gate reads only the mean. One uint16 is one register, so the ISR can never read
a basis half-written by the host, where three int32 legs are six registers
written one at a time. The UI computes it by the firmware's own rule
(`elsCalMeanValid`: every leg positive, integer mean) from the gear-scaled
legs (`els_cal.cal_basis`). The UI
still stores the three legs, for the spread and drift it reports.

## Later commands, one per paired release

`ENGAGE` (arm from staged stop params; refuse on missing or half-staged
params or Z past stop: the `arm_idle_stop` refusals become result codes),
`CUT` (release the hold; refuse unless `HELD` **and** the servo and sync are
in a state that can move), `RETRACT(steps staged)`, `FEED_ON`,
`JOG_ON`/`JOG_OFF`. Each migration deletes the corresponding boolean-poke
path in the same release. `FEED_OFF` is not on this list: it stays immediate
(below). `active` stops being UI-writable once `ENGAGE`/`CUT` land (its
meanings by then live in the mode register), which is where the
`servoEnableTask` re-assert is removed and every register becomes
single-writer.

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
- **`FEED_OFF` stays immediate.** It is consumed on the ISR tick, or left as
  the direct `syncEnable` write, and never goes through the
  task-consumed command path. Sync off is the operator's mid-cut stop.
  The direct write stops Z within one 30 Hz recorder tick; task consumption would add up to 100 ms, which at the measured feeds
  is 0.6 mm of travel at p90 and 1.2 mm at p99. An ISR consumption has to
  close the re-assert race above some other way.

## Non-goals

No continuous reconciler (divergence alarms, never auto-correction). No
UI-side legality authority (affordance gating derives from the mirrored
mode). No batching/queuing of commands: one in flight, seq-acked, ever.

## Verification plan

Per command: an emulator state×command table test (every mode × the new
command, asserting outcome and result code), the cross-registry contract
test extended to the command/result enums, and one hardware session
exercising the migrated flow from every mode it can reach before its old path
is deleted from the UI. For `DISENGAGE` the table
(`fw/emulator/test/els_command_test.cpp`) also covers the `CAL` abort
(`calSeq` and `commandResult` both), the ack waiting for the ISR's pass, a
host `syncEnable` write landing during consumption, the unknown-command
refusal and one command in flight. The UI tests
(`ui/tests/fsms/test_els_command.py`) cover the write, the ack, the timeout
and its reissue, the refusal, a reconnect with a `DISENGAGE` unacked (issued
again) and a fresh session's `commandSeq` baseline. The calMeasured split is
`fw/emulator/test/els_cal_basis_test.cpp` and the basis tests in
`ui/tests/fsms/test_els_cal.py`, `test_els_fsm.py` and `test_els_gear.py`.
