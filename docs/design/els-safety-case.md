# ELS safety case

What protects the operator, in which machine state, against which failure,
and where nothing does: an enumeration with citations, plus the decisions it
forces. It is **not** a certification, and nothing here has been through fault
injection.

!!! info "Provenance"
    Citations are file and line as of `bd10c92` plus `7f2191d`, `eedc4da`,
    `a0068d2` and `308b920`. Where a fact could not be confirmed by reading
    code it is marked **UNVERIFIED** rather than asserted; see
    [Open questions](#open-questions).

---

## Divergence watchdog escalation

**The servo-mode divergence watchdog (`ui/reflex/dispatchers/servo.py:232`)
escalates to a notice, never to alarm.**

| rung | what it does | motion risk | status |
|---|---|---|---|
| log-only | writes a line to the journal | none, and no benefit: at the lathe there is a touchscreen and no terminal, so nobody sees it | superseded |
| **notice** | **amber line on the top status bar via `els_uic.notify`** | **none; touches no motion path** | **ADOPTED** |
| alarm | `on_enter_alarm` drops sync, then the feed, then `set_enable(False)` | a false positive stops the feed with the tool in the groove **and de-energizes the drive, freeing the leadscrew** | **NOT TAKEN** |

A false positive on the notice rung costs one amber line. A false positive on
the alarm rung is itself a hazard: the detector would cause the class of
incident it exists to detect.

!!! note "Sync enable controls the servo drive"
    `servoEnableTask` drives a real enable pin (`Ramps.c:1672-1673`):
    `servoMode != 0` takes `ENA` low and the drive is energized;
    `servoMode == 0` takes it high, **the drive is disabled, and the leadscrew
    is free to turn by hand**. That is why `on_enter_alarm`'s ordering
    matters: it drops sync first, so escalating would release the leadscrew
    mid-pass.

    This is a different hold from `elsStop.active`, which gates sync-step
    accumulation; clearing that one is the "go" for a pass (`Ramps.c:1247`).

---

## Machine states

From `ui/reflex/fsms/els_fsm.py:20-24`: five, mirrored into the UI FSM as
`in_cycle.cutting` / `in_cycle.retracting` / `alarm`.

| state | meaning | operator exposure |
|---|---|---|
| `disabled` | no job armed; machine inert | lowest |
| `stopped` | armed and holding at the shoulder | tool may be in the work |
| `retracting` | powered move back toward start Z | **tool dragged along the thread if X is not clear** |
| `cutting` | feeding under spindle sync | highest |
| `alarm` | faulted, disarmed | recovery only |

---

## Coverage matrix

Failure class × state. **✓** = a mechanism gates motion. **◐** = detected and
reported, but nothing is gated. **GAP** = nothing found.

| failure class | `disabled` | `stopped` | `retracting` | `cutting` | `alarm` |
|---|---|---|---|---|---|
| Spindle-encoder loss | GAP | GAP | GAP | **GAP** | GAP |
| Z-scale loss | GAP | GAP | GAP | ✓ at take-up only | GAP |
| Modbus loss | GAP | GAP | GAP | ✓ | GAP |
| Drive fault | GAP | GAP | GAP | GAP | GAP |
| UI death | n/a | GAP | GAP | **GAP** | GAP |
| Firmware re-asserts feed after UI said stop | — | ◐ | ◐ | ◐ | — |
| Leadscrew turned while the drive is off | GAP | **GAP** | GAP | — | GAP |

What protects the operator is **the take-up confirmation gate and the
operator's own hands**, concentrated almost entirely at one moment (the start
of a pass) in one state (`cutting`).

### Required behavior per failure class

**Spindle-encoder loss.** Should stop the feed; nothing detects it in any
state. A dead encoder during `cutting` gives zero sync deltas, which the code
cannot tell from a stopped spindle, a condition `toggle_engage` treats as
*safe* (`ui_controller.py:1181-1214`).

**Z-scale loss.** Should stop the feed. The take-up gate (`Ramps.c:944-1067`),
its confirm-window abort (`:1086-1124`) and its 5 s timeout backstop
(`:1126-1137`) do, but only at the start of a pass; nothing checks Z liveness
*through* a cut.

**Modbus loss.** Should stop the feed. `els_fsm.py:271-276` escalates an
unacknowledged stop-write to `alarm`, but only in `on_enter_cutting`; a link
drop while `retracting`, a powered move, is not covered. A protocol-version
mismatch at connect (`board.py:249-297`) only warns and permits engage.

**Drive fault.** Should stop the feed. Nothing references a driver fault line,
which may not exist in the hardware: step/dir come directly from STM32 pins.

**UI death.** Should stop the feed; a live cut with no supervisor is the worst
cell in the table. Firmware does not time out a feed when the UI stops
polling, and Guard #25 above is UI-*initiated* (it fires on a failed write
ack), so it cannot fire when the UI has died. The firmware's take-up gates run
independently of UI liveness, but only at take-up.

**Leadscrew turned while the drive is off.** Should invalidate the thread
reference and does not (see [Open questions](#open-questions)). It is the only
row where the machine can end up confidently wrong rather than merely
unprotected.

---

## What exists, by layer

**19 mechanisms gate motion in release-shipping code**: 9 firmware, 10 UI.

### Firmware, release builds (`fw/Core/Src/Ramps.c`)

| # | mechanism | cite |
|---|---|---|
| 1 | Take-up Z/slip confirmation gate | `:944-1067` |
| 2 | Take-up confirm-window abort — never overwrites a real verdict with OK | `:1086-1124` |
| 3 | Take-up timeout backstop (5 s); recovery only via enable 1→0 | `:1126-1137` |
| 4 | Jog-mode (`servoMode==2`) take-up refusal | `:1273-1307` |
| 5 | Calibration request refusal (enabled / wrong mode / bad config) | `:644-676` |
| 6 | Calibration abort on condition change mid-run | `:681-691` |
| 7 | **Enable falling-edge teardown** — cancels pending take-up and all commanded motion so nothing survives the edge as banked debt | `:792-832` |
| 8 | `ELS_REQUIRE_QUIESCENCE` AND-gate | `:907-1035` |
| 9 | Hysteresis re-latch guard | `:1159-1178` |

!!! danger "Guard 8 is dormant in every build ever shipped"
    `ELS_REQUIRE_QUIESCENCE` defaults to 0 (`Ramps.c:67-68`) and the flag has
    never shipped on. Its protection — ANDing a "carriage genuinely stopped"
    test into the take-up gate — **does not exist on any machine in the
    field**. If a safety argument leans on it, that argument is fictional
    today.

Guard 7 is what makes disengage-while-armed physically safe. Two diagnostic
probes (`els_diag_disengage_latch.h`, `els_diag_mode_watch.h`) add a second
net during bring-up and are compiled out of release builds entirely; they must
never be counted as release protection.

### UI, release builds

Ten refusals: disengage-while-armed (`ui_controller.py:1181-1214`), no-Z-axis
and summed-Z engage refusals (`:1231-1267`), FSM double-tap guards, the
calibration CRC/fabricated-read guard (`els_cal.py:258-339`), calibration
protocol-version and config refusals, and the three thread-resync refusals
(`els_resync.py`), which fire when the wizard opens, before the operator moves
the carriage or closes the half nut.

### Reported but not gating

The divergence watchdog, the take-up outcome torn-snapshot guard
(`ui_controller.py:546-635`), and two display guards that fail in opposite
directions so the screen cannot lie: the phase offset **holds** its last value
on a read failure (`:677-694`) because 0 would read as "no offset being cut",
and the thread-ref-latched lamp **hides** (`:740-786`) rather than show a
stale latch. The ADV button will not hide the advanced ELS bar while a stop
job is engaged, because that bar is the only place the UI shows a job is
armed.

### Not a guard, despite appearances

`stepPulseRuntCount` (`Ramps.c:769-779`) is a pure counter, read only for
logging into a capture file; nothing in `fw/` or `ui/` refuses, alarms or
latches on it.

---

## Open questions

!!! danger "A latched thread reference survives sync being switched off"
    **This is a defect in the code**, following from the drive-enable note
    above.

    Dropping sync de-energizes the drive, so the leadscrew can be turned by
    hand. There is no leadscrew feedback — the firmware knows only commanded
    steps — so anything that moves while the drive is off is invisible to it,
    and a thread reference latched before that point no longer describes the
    machine.

    Firmware clears `referenceLatched` **only on the `elsStop.enable` 0→1
    edge** (`Ramps.c:781-783`, and `Ramps.h:191` says so). `stop_sync()`
    clears `syncEnable` on every scale and touches nothing else. So sync off
    and back on **without an engage cycle** carries the old reference across,
    and the UI keeps showing `REF LATCHED`.

    Pressing Sync Enable mid-cut, pressing it again, and resuming without
    disengaging takes this path.

    A latched thread reference must be cleared when sync is disabled. It
    currently is not.

Two more, both cheap to close:

- **Does a drive fault line exist in the hardware at all?** If not, that
  matrix row is not a software gap and should be struck.
- **Is spindle-encoder loss distinguishable from a stopped spindle at the
  drivetrain?** If not, no software detector can be written, and the answer
  belongs in the encoder-integrity work (index channel plus per-rev checksum).
