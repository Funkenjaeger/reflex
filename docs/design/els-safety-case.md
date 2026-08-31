# ELS safety case

What protects the operator, in which machine state, against which failure —
and, just as importantly, where nothing does.

This page exists because the guards were accreting one incident at a time with
no stated policy, so safety calls stalled for weeks at a time. It is an
enumeration with citations plus the decisions that enumeration forces. It is
**not** a certification, and nothing here has been through fault injection.

!!! info "Provenance"
    Enumerated by reading the source at `bd10c92`, then updated for the four
    commits of 2026-08-31 (`7f2191d`, `eedc4da`, `a0068d2`, `308b920`). Every
    row cites a file and line. Where a fact could not be confirmed by reading
    code it is marked **UNVERIFIED** rather than asserted — see
    [Open questions](#open-questions), which is the most important section on
    this page.

---

## The decision this page had to make

The task that produced this document set a behavioural acceptance bar: decide
the servo-mode divergence watchdog's escalation, or admit the page is theatre.

**Decided 2026-08-31 — the watchdog escalates to a NOTICE, not to alarm.**

| rung | what it does | motion risk | status |
|---|---|---|---|
| log-only | writes a line to the journal | none, and no benefit either — at the lathe there is a touchscreen and no terminal, so this reached nobody | superseded |
| **notice** | **amber line on the top status bar via `els_uic.notify`** | **none — touches no motion path** | **ADOPTED** |
| alarm | `on_enter_alarm` → `set_enable(False)`, running the enable falling-edge teardown | a false positive stops the feed with the tool in the groove and the spindle turning | **NOT TAKEN** |

The reasoning is asymmetric and that asymmetry is the whole argument. A false
positive on the notice rung costs one amber line. A false positive on the
alarm rung is itself a hazard event — the detector would cause the class of
incident it exists to detect. Until the disengage path is demonstrated safe
rather than argued safe, the alarm rung stays closed.

Landed in `a0068d2`. Watchdog at `ui/reflex/dispatchers/servo.py:232`.

---

## Machine states

From `ui/reflex/fsms/els_fsm.py:20-24` — five, mirrored into the UI FSM as
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

That table is mostly GAP, and that is the finding. It is not evidence the
machine is dangerous — it is evidence that **what protects the operator today
is the take-up confirmation gate and the operator's own hands**, and that the
protection is concentrated almost entirely at one moment (the start of a pass)
in one state (`cutting`).

### Required behaviour, per failure class

**Spindle-encoder loss.** Should stop the feed. Today nothing detects it, in
any state. A dead encoder during `cutting` presents as zero sync deltas, which
is indistinguishable in the searched code from "the spindle stopped turning" —
a condition `toggle_engage` deliberately treats as *safe*
(`ui_controller.py:1181-1214`). Whether that ambiguity is hazardous depends on
drivetrain behaviour not established here. Grep for
`encoder.*loss|encoderFault|servoFault` across `fw/Core` and `ui/reflex`
returns **zero hits**.

**Z-scale loss.** Should stop the feed. The take-up gate (`Ramps.c:944-1067`)
and its confirm-window abort (`:1086-1124`) and 5 s timeout backstop
(`:1126-1137`) do exactly this — but only at take-up, at the start of a pass.
There is no continuous Z-liveness check *through* a cut.

**Modbus loss.** Should stop the feed. `els_fsm.py:271-276` escalates an
unacknowledged stop-write to `alarm`, and it is the only mechanism found that
handles a Modbus-shaped failure. It is scoped to `on_enter_cutting`. A link
drop while `retracting` — a powered move — is not covered. Note also that a
protocol-version mismatch at connect (`board.py:249-297`) is deliberately
**non-fatal**: it warns and permits engage.

**Drive fault.** Should stop the feed. No register, ISR check or UI path
referencing a driver fault line was found. **This may not be a software gap at
all** — the design writes step/dir directly from STM32 pins, so a fault line
may not exist in the hardware. Confirm before treating this row as work.

**UI death.** Should stop the feed — a live cut with no supervisor is the
worst cell in the table. Nothing in firmware times out a feed when the UI
stops polling. Guard #25 above is UI-*initiated* (it fires on a failed write
ack) and therefore cannot fire when the UI is the thing that died. The
firmware's take-up gates run independently of UI liveness, but they only run
at take-up.

---

## What exists, by layer

**19 mechanisms gate motion in release-shipping code** — 9 firmware, 10 UI.
The figure this page was commissioned to check was "~7", which appears to have
been a guess: no commit or document anywhere arrives at 7.

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
probes (`els_diag_disengage_latch.h`, `els_diag_mode_watch.h`) provide a
belt-and-braces net during bring-up and are compiled out of release builds
entirely — they must never be counted as release protection.

### UI, release builds

Ten refusals: disengage-while-armed (`ui_controller.py:1181-1214`), no-Z-axis
and summed-Z engage refusals (`:1231-1267`), FSM double-tap guards, the
calibration CRC/fabricated-read guard (`els_cal.py:258-339`), calibration
protocol-version and config refusals, and the three thread-resync refusals
(`els_resync.py`).

!!! note "Changed 2026-08-31"
    The three thread-resync refusals now fire when the wizard **opens**, not
    at the Begin button (`eedc4da`). They previously refused only after the
    operator had followed the jog instructions — moving the carriage by hand,
    closing the half nut, and hauling it back against the flank. The
    conditions are unchanged; the timing was the defect.

### Reported but not gating

The servo-mode divergence watchdog (now a notice, see above), the take-up
outcome torn-snapshot guard (`ui_controller.py:546-635`), and two display
integrity guards that fail in deliberately opposite directions: the phase
offset **holds** its last value on a read failure (`:677-694`) because 0 would
read as "no offset being cut", while the thread-ref-latched lamp **hides**
(`:740-786`) rather than show a stale latch. Neither gates motion; both exist
so the screen cannot lie.

A new UI-visibility guard landed the same day (`308b920`): the ADV button
refuses to hide the advanced ELS bar while a stop job is engaged, because that
bar is the only place armed-ness is visible anywhere in the UI.

### Not a guard, despite appearances

`stepPulseRuntCount` (`Ramps.c:769-779`) is a pure counter. Grepped across
`fw/` and `ui/`: no consumer takes any refuse, alarm or latch action on it. It
is read only for logging into a capture file. It lives in guard-adjacent ISR
code and its register comments describe it in guard-like language, which is
exactly why it is called out here.

---

## Open questions

!!! warning "The carriage-hold claim is UNVERIFIED, and it is load-bearing"
    Three places — the watchdog docstring, its test module, and the Open Loops
    task body — asserted that clearing enable *"releases the carriage hold"*,
    citing `Ramps.c:815/826`. Reading those lines: they cancel in-flight
    take-up and zero commanded motion (`stepsToGo`, `currentSpeed`,
    `desiredSteps`). Whether the carriage is then **held by the drive or
    free** is a fact about CL86T behaviour with no commanded motion that no
    source in this repository states, and the cited line numbers have drifted.

    This matters because it is the strongest form of the argument for keeping
    the alarm rung permanently closed. The decision above does not depend on
    it — the simpler argument (a false positive stops the feed mid-cut) is
    sufficient — but the claim should be settled at the machine rather than
    inherited, and it should stop being repeated until it is.

Two more, both cheap to close and neither closed here:

- **Does a drive fault line exist in the hardware at all?** If not, that
  matrix row is not a software gap and should be struck rather than carried.
- **Is spindle-encoder loss actually distinguishable from a stopped spindle
  at the drivetrain?** If it is not, no software detector can be written, and
  the answer belongs in the encoder-integrity work (index channel plus per-rev
  checksum) rather than here.

---

## How to use this page

When a safety call comes up, find the cell. If it is **✓**, the mechanism is
named and cited — go read it rather than re-deriving it. If it is **GAP**,
that is not a bug report; it is a statement that the protection was never
written, and the decision in front of you is whether it should be.

Two standing rules this page asks you to keep:

1. **Never count a diagnostic-build mechanism as release protection.** Two of
   them exist and both are compiled out.
2. **Never count guard 8.** It is present in the source and absent from every
   machine.
