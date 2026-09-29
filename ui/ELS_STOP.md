# ELS shoulder stop in the UI

The shoulder-stop and phase-preserving re-sync logic live in firmware. The model (the cut/trigger/resume phases, the latched reference pair, the modular-correction re-sync) is in [`fw/ARCHITECTURE.md`](../fw/ARCHITECTURE.md#els-shoulder-stop) (*ELS Shoulder Stop*).

## What Python is responsible for

The firmware owns the *algorithm*; Python owns the *workflow*: collecting setup parameters, writing them to the registers at the right times, and clearing `elsStop.active` when the next pass should begin. A threading session:

1. **Configure.** The wizard collects stop Z, retract Z, start and stop diameters and the half-nut confirmation, writes the geometry (thread pitch in leadscrew steps, Z scale counts per pitch, scale index, backlash takeup magnitude), and arms the stop block with `enable = 1`.
2. **Cut.** Python writes `active = 0` (the only place it does) to release the sync gate. The firmware drives the carriage to `stopPosition`, sets `active = 1`, and latches its reference on the job's first trigger.
3. **Retract.** The leadscrew indexer moves the carriage to the retract Z with sync gated (`active = 1`), so the reference is untouched. Neither firmware nor Python sees the half-nut; the operator may open it and reposition by hand.
4. **Next pass.** "Cut" writes `active = 0` again, which starts the firmware's re-sync: backlash takeup, phase correction, then sync. The next trigger fires at `stopPosition`.

Disengaging writes `enable = 0`, which clears `referenceLatched` so the next engage starts a fresh reference.

## Why the wizard flow matters

The re-sync correction needs the sync ratio (encoder counts per leadscrew step), thread-pitch-in-steps and Z-counts-per-pitch to agree; one wrong by more than half a pitch aliases the correction onto a different groove. The wizard derives all three from the thread spec and writes them as a unit on `on_enter_cutting`. A mid-job geometry change (e.g., from a settings popup) must re-arm the FSM before it takes effect.

## Where the code lives

| Concern | File |
|---|---|
| FSM states (`disabled`, `stopped`, `cutting`, `retracting`, `alarm`) and their `on_enter_*` register writes | [`reflex/fsms/els_fsm.py`](reflex/fsms/els_fsm.py) |
| Hardware-abstracted register access (`set_active`, `set_enable`, `set_stop_position`, etc.) | [`reflex/fsms/els_stop_hal.py`](reflex/fsms/els_stop_hal.py) |
| Wizard state machine (configuration sequence, then cycle loop) | [`reflex/fsms/ui_fsm.py`](reflex/fsms/ui_fsm.py) |
| User-facing controller that wires the wizard, the ELS FSM, and the UI together | [`reflex/fsms/ui_controller.py`](reflex/fsms/ui_controller.py) |
| Thread-geometry computation and unit conversions | [`reflex/dispatchers/els.py`](reflex/dispatchers/els.py) |
| Advanced settings (backlash, hysteresis, direction modes) | [`reflex/components/home/els_advbar.py`](reflex/components/home/els_advbar.py), [`els_settings_popup.py`](reflex/components/home/els_settings_popup.py) |

The layered architecture these files implement (UI → Controller → FSM → HAL → firmware registers) is in [`kivy-fsm-design-pattern.md`](kivy-fsm-design-pattern.md).

## Backlash calibration (Python side)

The firmware measures; Python decides. The wizard
(`reflex/components/home/els_backlash_cal_popup.py`) walks the
operator through the safety preconditions, sets `calCommand`, and
**edge-detects `calSeq`**. Never poll `calCommand`: the firmware clears it the
instant the ISR consumes it, long before the run finishes, so polling reads a
stale result as success.

The run controller (`reflex/fsms/els_cal.py`) owns the policy:

- **Consistency.** The three measurements must agree within
  `els_cal_max_spread_steps`, with no override: a drivetrain that does not
  repeat is the finding, and the same fault would corrupt every other ELS
  operation.
- **Margin.** The stored take-up is `measured + max(20%, floor)`. At a small
  lash a flat percentage falls inside the measurement's quantization
  uncertainty; the floor keeps a real margin.

Two invariants depend on this:

1. `els_backlash_steps` holds the **commanded** take-up and
   `els_cal_last_measured_steps` the **raw measurement**; only the command is
   written to the firmware. `ElsStopFsm._safety_margin_display` budgets against
   `els_backlash_steps`, so the raw measurement there would under-budget the
   cut-start safety margin by exactly the margin.
2. Calibration policy is module-level functions, not `ElsDispatcher` methods:
   that class needs a running `MainApp`, so logic on it can be tested only by
   mirroring it in a stub, and mirrored rules drift.

## Take-up refusals

The firmware refuses to start a pass whose backlash take-up it could not
confirm. `takeupResult` / `takeupSeq` carry the outcome, and the UI renders the
physical check rather than the register value: *"Carriage not moving — is the
half-nut engaged?"* Recovery is disengaging and re-engaging the ELS stop.

## Picking up an existing thread (manual reference latch)

The job's reference normally latches at the first stop trigger. The "Pick up
existing thread" wizard (ELS settings → Sync) instead latches it on an existing
thread: a coarse jog in the cutting direction only (loading the lash on the
correct side), then the operator hand-rotates the spindle and works the
cross-slide to seat the tool in the groove with Z held, and Confirm fires the
firmware's atomic `latchCommand`. The run controller
(`reflex/fsms/els_resync.py`) enforces:

- a 1–3 count Z-hold tolerance, recoverable only by a hand re-seat that
  returns the reading almost exactly (a miss is a Z-chain custody fault, never
  widened away);
- a spindle stillness dwell gating Confirm;
- a readback check that the firmware latched the Z this screen was watching.

`latchSeq` is edge-detected like `calSeq`; never poll `latchCommand`. Operator
doc: [`reflex/help/els_thread_resync.md`](reflex/help/els_thread_resync.md);
emulator test: `tests/system/test_els_thread_resync.py`.

## Widening a groove past the cutter (thread-phase offset)

The firmware side is `elsStop.phaseOffsetSteps` (`fw/ARCHITECTURE.md`, *Thread-phase offset*), which displaces thread phase by a distance. The operator-entered offset is the first of two sources named in `fw/Core/Inc/els_phase.h`; the second, the X-depth-derived compound infeed, will feed the same `Pending` path. Python owns three things.

**Unit conversion.** `ElsFsm._leadscrew_steps_per_display_unit()` turns the operator's display units into leadscrew steps: it composes `servo.ratioNum/Den` (mm per step) with `formats.factor` (display units per mm), both exact `Fraction`s, and rounds **once** at the end.

**Accumulation.** The firmware holds one absolute total and replaces it on every apply, so Python reads `phaseOffsetSteps`, adds the entry, and writes the sum back. It reads the firmware, never a UI-side copy: the firmware clears the total on the `enable` 0-to-1 edge, and a local copy would carry a shift into the next job. Both readouts lead with that total, the widening, with fraction-of-a-pitch beside it.

**Refusals**, all in `ElsFsm.apply_phase_offset()`, each with its own `PHASE_OFFSET_*` code so the UI can state a reason:

- **At one pitch: refused, not clamped.** This is the aliasing bound: one pitch of offset is a no-op and 1.5 pitches is indistinguishable from 0.5. Clamping would put the cut somewhere other than where the operator asked, in metal, before anything looks wrong.
- **Negative entries: refused.** Widening runs one way, so a negative entry is a slip. It is refused rather than absoluted because the math is asymmetric: the forward bias turns `−X` into a forward jog of `pitch − X` (`els_phase.h`, T5), a real cut in the same groove on the flank the operator was *not* opening.
- **Turning, or a zero pitch:** there is no thread phase to shift; the firmware is sent `threadPitchSteps = 0`.
- **Outside a job:** with `enable == 0` the firmware consumes the command *without* acking, and an absent ack is indistinguishable from a dropped frame.

Tests: `tests/fsms/test_els_phase_offset.py` and `fw/emulator/test/els_phase_offset_command_test.cpp`.

**Not yet verified on hardware,** including the firmware doc's frame caveat: on a `cuttingDir == −1` machine an entry opens the *opposite flank* from the one the operator pictured.

## Protocol version

`Board._check_protocol_version()` reads `elsStop.protocolVersion` on each new
connection and compares it with `ELS_PROTOCOL_VERSION` in `devices.py`. The
shared struct maps onto Modbus registers with no translation layer, so a
firmware whose `elsStop_t` differs reinterprets every register past the point
of divergence as plausible garbage that looks like a hardware fault.
The check flags and logs but does not refuse: the UI stays useful for
everything outside the moved registers, and refusing to start would make
reflashing harder.

## Operator-visible expectations

- Engage the stop block **before** enabling sync; sync without a stop free-runs the leadscrew with the spindle.
- The cut always stops at exactly the configured `stopPosition`; the phase correction adjusts only where the cut starts.
- Between passes the operator may jog, open the half-nut, slide the carriage by hand, and re-engage. If the half-nut is engaged at "Cut", the firmware absorbs any residue.
- "Cut" with the half-nut still open is refused by the take-up check when a take-up is configured. With `backlashSteps` at 0 there is no take-up to confirm, and the pass runs in the wrong phase.
