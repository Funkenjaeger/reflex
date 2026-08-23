# ELS Shoulder Stop — Python orchestration

The position-based shoulder-stop and phase-preserving re-sync logic live in firmware. See [`reflex-fw/ARCHITECTURE.md` → ELS Shoulder Stop](https://github.com/Funkenjaeger/reflex-fw/blob/main/ARCHITECTURE.md) for the conceptual model: the cut/trigger/resume phases, the latched reference pair, and the modular-correction re-sync.

This document covers the **Python side** — what the GUI does to drive the firmware through a threading job.

## What Python is responsible for

The firmware owns the *algorithm*. Python owns the *workflow*: collecting setup parameters from the operator, pushing them to the right registers at the right times, walking the user through wizard steps, and clearing `elsStop.active` at the precise moment the next pass should begin.

A threading session looks like this from Python's perspective:

1. **Configure.** The operator works through the wizard (set stop Z, retract Z, start diameter, stop diameter, confirm half-nut). The wizard pushes geometry to the firmware — thread pitch in leadscrew steps, Z scale counts per pitch, scale index, backlash takeup magnitude — and arms the stop block by writing `enable = 1`.
2. **Cut.** Python writes `active = 0` (the only place this happens) to release the firmware's sync gate. The firmware drives the carriage to `stopPosition`, sets `active = 1` automatically, and latches its reference on the first trigger of the job.
3. **Retract.** Python commands the leadscrew indexer to move the carriage to the retract Z. Sync stays gated (`active = 1` throughout) so the retract motion doesn't corrupt the reference. The half-nut state is invisible to firmware and to Python — the operator may open it and reposition by hand at any point in the retract/wait period.
4. **Next pass.** Operator hits "Cut". Python writes `active = 0` again. This is the trigger for the firmware's re-sync state machine (backlash takeup, then phase correction, then sync resumes). The carriage advances toward `stopPosition`, the next trigger fires, and the cycle repeats.

The job ends when the operator disengages (Python writes `enable = 0`, clearing the firmware's `referenceLatched` so the next engage starts a fresh reference).

## Why the wizard flow matters

The firmware's re-sync correction depends on three configured quantities being mutually consistent: the sync ratio (encoder counts per leadscrew step), thread-pitch-in-steps, and Z-counts-per-pitch. Any one of them wrong by more than half a pitch will alias the correction onto a different thread groove. The wizard is the contract that keeps them in sync — it derives all three from the operator-entered thread spec and pushes them as a unit on `on_enter_cutting`. If the operator changes thread geometry mid-job (e.g., via a settings popup), the FSM must re-arm before the change takes effect; bypassing this is one of the easier ways to produce a "different groove every pass" symptom.

## Where the code lives

| Concern | File |
|---|---|
| FSM states (`disabled`, `stopped`, `cutting`, `retracting`, `alarm`) and their `on_enter_*` register writes | [`reflex/fsms/els_fsm.py`](reflex/fsms/els_fsm.py) |
| Hardware-abstracted register access (`set_active`, `set_enable`, `set_stop_position`, etc.) | [`reflex/fsms/els_stop_hal.py`](reflex/fsms/els_stop_hal.py) |
| Wizard state machine (configuration sequence → cycle loop) | [`reflex/fsms/ui_fsm.py`](reflex/fsms/ui_fsm.py) |
| User-facing controller that wires the wizard, the ELS FSM, and the UI together | [`reflex/fsms/ui_controller.py`](reflex/fsms/ui_controller.py) |
| Thread-geometry computation and unit conversions | [`reflex/dispatchers/els.py`](reflex/dispatchers/els.py) |
| Advanced settings (backlash, hysteresis, direction modes) | [`reflex/components/home/els_advbar.py`](reflex/components/home/els_advbar.py), [`els_settings_popup.py`](reflex/components/home/els_settings_popup.py) |

The layered architecture these files implement (UI → Controller → FSM → HAL → firmware registers) is documented separately in [`kivy-fsm-design-pattern.md`](kivy-fsm-design-pattern.md); the ELS stop is a faithful instance of that pattern.

## Backlash calibration (Python side)

The firmware measures; Python decides what to do with the measurement.

The wizard (`reflex/components/home/els_backlash_cal_popup.py`, opened from the
ELS settings popup) walks the operator through the safety preconditions, sets
`calCommand`, and then **edge-detects `calSeq`** — never `calCommand`, which the
firmware clears the instant the ISR consumes it, long before the run finishes.
Polling the command would report success immediately and read a stale result.

The run controller (`reflex/fsms/els_cal.py`) owns the policy the firmware
deliberately does not:

- **Consistency.** The three measurements must agree within
  `els_cal_max_spread_steps`. A wide spread is refused, and there is no "use it
  anyway" — a drivetrain that doesn't repeat is the finding, and the same fault
  would quietly corrupt every other ELS operation.
- **Margin.** The stored take-up is `measured + max(20%, floor)`. The floor
  matters because at a small lash a flat percentage collapses into the
  measurement's own quantization uncertainty and stops being margin at all.

Two properties elsewhere depend on this and are easy to break:

1. `els_backlash_steps` holds the **commanded** take-up, and
   `els_cal_last_measured_steps` holds the **raw measurement**. Only the command
   is written to the firmware. `ElsStopFsm._safety_margin_display` budgets
   against `els_backlash_steps`, so storing the raw measurement there would
   under-budget the cut-start safety margin by exactly the margin.
2. Calibration policy lives as module-level functions, not methods on
   `ElsDispatcher` — that class needs a running `MainApp`, so logic on it can
   only be tested by mirroring it in a stub, and mirrored rules drift.

## Take-up failures are now operator-visible

The firmware refuses to start a pass whose backlash take-up it could not
confirm. `takeupResult` / `takeupSeq` carry the outcome, and the UI renders the
physical check rather than the register value: *"Carriage not moving — is the
half-nut engaged?"* Recovery is disengaging and re-engaging the ELS stop.

This replaces the "no software interlock" caveat below for the take-up case
specifically. Pressing Cut with the half-nut open no longer produces a silent
wrong-phase pass; it produces a refusal that names the likely cause.

## Picking up an existing thread (manual reference latch)

Normally the job's thread reference auto-latches at the first stop trigger.
The "Pick up existing thread" wizard (ELS settings → Sync) instead latches it
at an operator-verified point on an existing thread: coarse jog in the cutting
direction only (loads the lash on the correct side), then hand-rotate the
spindle and work the cross-slide to seat the tool in the groove — Z held and
watched the whole time — then Confirm, which fires the firmware's atomic
`latchCommand`. The run controller (`reflex/fsms/els_resync.py`) enforces the
procedure's safety properties: a 1–3 count Z-hold tolerance whose violation is
recoverable only by a hand re-seat that must return the reading almost exactly
(a miss is surfaced as a Z-chain custody fault, never widened away), a spindle
stillness dwell gating Confirm, and a readback cross-check that the firmware
latched the Z this screen was watching. `latchSeq` is edge-detected exactly
like `calSeq` — never poll `latchCommand`. Operator doc:
[`reflex/help/els_thread_resync.md`](reflex/help/els_thread_resync.md);
emulator proof: `tests/system/test_els_thread_resync.py`.

## Multi-start threads (thread-phase offset)

Cutting a 2- or 3-start thread without re-indexing the workpiece: cut every start from the same reference, moving the controller's idea of thread phase by pitch/N between them. The firmware side is `elsStop.phaseOffsetSteps` (see `fw/ARCHITECTURE.md` → *Thread-phase offset*); Python owns three things it deliberately does not.

**Unit conversion.** The operator enters a distance in display units; the register wants leadscrew steps. `ElsFsm._leadscrew_steps_per_display_unit()` composes `servo.ratioNum/Den` (mm per step) with `formats.factor` (display units per mm), both exact `Fraction`s, and rounds **once** at the end.

**Accumulation.** The firmware holds one absolute total and replaces it on every apply, so the running total is built here — read `phaseOffsetSteps`, add the entry, write the sum back. It is read from the firmware and never from a UI-side copy, because the firmware clears the total on the `enable` 0→1 edge and a local copy would happily carry a half-pitch shift into the next job.

**The refusals**, all in `ElsFsm.apply_phase_offset()`, each returning its own `PHASE_OFFSET_*` code so the UI can state a reason rather than fail silently:

- **At one pitch — refused, not clamped.** One pitch of offset is a no-op and 1.5 pitches is indistinguishable from 0.5. Clamping would hand back a different thread start than the one asked for, in metal, before anything looks wrong.
- **Negative entries — refused.** Entry is advance-only. A negative offset does not step the phase back by `|offset|`; the forward bias turns it into a forward jog of `pitch − |offset|` (`els_phase.h`, T5), so a symmetric ± control would misrepresent what it does.
- **Turning, or a zero pitch** — there is no thread phase to shift, and the firmware is sent `threadPitchSteps = 0`.
- **Outside a job** — the firmware consumes the command *without* acking when `enable == 0`, and an absent ack is indistinguishable from a dropped frame. Refusing here is what turns that silence into a sentence on screen.

Proof: `tests/fsms/test_els_phase_offset.py` (25 cases, nine mutations applied and killed) and `fw/emulator/test/els_phase_offset_command_test.cpp` (eight killed).

**Not yet verified on hardware,** including the frame caveat in the firmware doc — on a `cuttingDir == −1` machine a given entry selects the complementary start.

## Protocol version

`Board._check_protocol_version()` reads `elsStop.protocolVersion` on each new
connection and compares it against `ELS_PROTOCOL_VERSION` in `devices.py`. The
whole shared struct is memory-mapped onto Modbus registers with no translation
layer, so a firmware whose `elsStop_t` differs reinterprets every register past
the point of divergence — which presents as plausible-looking garbage and gets
diagnosed as a hardware fault. The check is non-fatal: it flags and logs, since
the UI stays useful for everything that doesn't touch the moved registers, and
refusing to start would make reflashing harder than the fault warrants.

## Operator-visible expectations

- Engage the stop block **before** enabling sync — sync without a stop will free-run the leadscrew with the spindle.
- The cut will always stop at exactly the configured `stopPosition`. The phase correction never moves the stop; it only adjusts where the cut starts.
- Between passes, the operator may freely jog the carriage, open the half-nut, manually slide the carriage, and re-engage. As long as the half-nut is engaged when "Cut" is pressed, the firmware will absorb any residue.
- "Cut" with the half-nut still open is operator error and will produce a wrong-phase pass. There is no software interlock for this.
