# ELS Shoulder Stop — Python orchestration

The position-based shoulder-stop and phase-preserving re-sync logic live in firmware. See [`rotary-controller-f4/ARCHITECTURE.md` → ELS Shoulder Stop](https://github.com/bartei/rotary-controller-f4/blob/main/ARCHITECTURE.md) for the conceptual model: the cut/trigger/resume phases, the latched reference pair, and the modular-correction re-sync.

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
| FSM states (`disabled`, `stopped`, `cutting`, `retracting`, `alarm`) and their `on_enter_*` register writes | [`rcp/fsms/els_fsm.py`](rcp/fsms/els_fsm.py) |
| Hardware-abstracted register access (`set_active`, `set_enable`, `set_stop_position`, etc.) | [`rcp/fsms/els_stop_hal.py`](rcp/fsms/els_stop_hal.py) |
| Wizard state machine (configuration sequence → cycle loop) | [`rcp/fsms/ui_fsm.py`](rcp/fsms/ui_fsm.py) |
| User-facing controller that wires the wizard, the ELS FSM, and the UI together | [`rcp/fsms/ui_controller.py`](rcp/fsms/ui_controller.py) |
| Thread-geometry computation and unit conversions | [`rcp/dispatchers/els.py`](rcp/dispatchers/els.py) |
| Advanced settings (backlash, hysteresis, direction modes) | [`rcp/components/home/els_advbar.py`](rcp/components/home/els_advbar.py), [`els_settings_popup.py`](rcp/components/home/els_settings_popup.py) |

The layered architecture these files implement (UI → Controller → FSM → HAL → firmware registers) is documented separately in [`kivy-fsm-design-pattern.md`](kivy-fsm-design-pattern.md); the ELS stop is a faithful instance of that pattern.

## Operator-visible expectations

- Engage the stop block **before** enabling sync — sync without a stop will free-run the leadscrew with the spindle.
- The cut will always stop at exactly the configured `stopPosition`. The phase correction never moves the stop; it only adjusts where the cut starts.
- Between passes, the operator may freely jog the carriage, open the half-nut, manually slide the carriage, and re-engage. As long as the half-nut is engaged when "Cut" is pressed, the firmware will absorb any residue.
- "Cut" with the half-nut still open is operator error and will produce a wrong-phase pass. There is no software interlock for this.
