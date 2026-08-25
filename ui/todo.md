# Reflex UI Project Review - Findings and Recommended Actions

---

## Polish backlog — found during 2026-08-08 hardware testing

**DO NOT ACTION THESE YET.** Evan's explicit call: these are usability gripes
found while commissioning the backlash calibration on elspi, and they wait until
functional testing of the ELS integration branch is complete. Fixing polish
mid-test churns the thing being tested.

### 1. Calibration mode prerequisites are discovered by refusal, not up front

The wizard lets you press Start and only then refuses, because the firmware
checks the preconditions (`servoMode == 1`, `elsStop.enable == 0`) when it
consumes `calCommand`. The operator learns the requirement by being rejected.

**This is not only annoying — the remedy it demands can be destructive.**
Telling an operator to disengage the ELS stop is not free: firmware clears
`referenceLatched` on the next `enable` 0→1 edge (`Core/Src/Ramps.c`, the
enable-rising-edge block), so disengaging and re-engaging **discards a saved
thread phase reference**. An operator part-way through a threading job who
follows the error message loses their groove alignment. The message currently
presents that as a trivial precondition.

What it should do instead:
- Check the prerequisites **before** offering Start, and say what needs
  changing rather than refusing after the fact.
- Offer to put the machine into the right mode, with sanity checks first —
  spindle stopped at minimum.
- **A cancel that is genuinely safe**, and an explicit warning when
  `referenceLatched` is set, because proceeding costs the phase reference.
- Improve the refusal text. Evan rated the current messages "roughly 50%
  helpful": `"Servo is not in sync/index mode."` states a fact without telling
  the operator what to do about it. Source: `ELS_CAL_MESSAGES` in
  `reflex/utils/devices.py`.

Files: `reflex/components/home/els_backlash_cal_popup.py` (prompt flow),
`reflex/fsms/els_cal.py` (`start()` preconditions),
`reflex/utils/devices.py` (`ELS_CAL_MESSAGES`).

### 2. The drift warning fires on ANY difference, so it cries wolf

`BacklashCalibration.drift_steps` / `_drift_text()` in
`reflex/components/home/els_backlash_cal_popup.py` append *"A large change is
worth investigating"* whenever drift is non-zero. On elspi the run-to-run drift
was **1–3 steps** on a ~385-step measurement (well under 1%), and the warning
fired every time — so it reads as a fault report on a machine that is in fact
repeating beautifully.

Gate the sentence on the difference actually being large. Needs a threshold with
a rationale rather than a guessed constant: a sensible basis is the same
quantization the measurement carries (~5 steps at a 2-count motion threshold),
or a percentage of the measured value, whichever is greater. Below that, report
the drift as a plain number with no editorial.

Note the same anti-pattern to check for elsewhere in this feature: a warning
whose trigger condition is broader than the thing it warns about trains the
operator to ignore it.

---

## Hard Fork Cleanup

### Workflow Fixes
- **Done:** Pinned `ad-m/github-push-action` to `v0.7.0` in reflex-fw workflow
- **Done:** Updated `softprops/action-gh-release` to `v2` in reflex-fw workflow
- **Done:** Fixed `github.ref` to `github.ref_name` in reflex-fw workflow
- **Done:** Fixed `BRANCH` comparison from `refs/heads/main` to `main` in reflex-fw workflow
- **Done:** Fixed git user email to use `github-actions[bot]` in reflex-fw workflow
- **Done:** Updated `PaulHatch/semantic-version` from `v5.0.3` to `v6.0.2` in reflex-fw workflow
- **Issue:** `PaulHatch/semantic-version` v6 has breaking changes to default version patterns (now follow Conventional Commits), but reflex-fw explicitly sets `major_pattern`/`minor_pattern` so should be unaffected

---

## Deployment

## Deployment

### OSPI Service Unit Migration
- **Issue:** OSPI ships with a systemd service unit pointing to `/root/rotary-controller-python` and `rcp.main`. Reflex UI lives in a different path and uses `reflex.main`.
- **Action:** Document the exact changes needed to the systemd service unit (ExecStart, WorkingDirectory, etc.) and test the full migration path on a Pi.

### Document Systemd Service Unit Changes
- **Issue:** The README includes a bash snippet for replacing RCP with reflex-ui on OSPI but doesn't specify the exact systemd service unit changes.
- **Action:** Once deployment is tested on a Pi, document the exact changes (ExecStart, WorkingDirectory, etc.) and update the README with complete instructions.

### Reconcile Systemd Service Name in README
- **Issue:** The README's `journalctl` commands reference a service named `reflex`, but the original OSPI service is `rotary-controller`. The correct service name depends on what the migrated service unit is named.
- **Action:** Reconcile the service name as part of updating the Pi migration instructions.

---

## Architecture

### 5. Circular Import Dependencies
- **Issue:** Components still use `from reflex.app import MainApp` inside `__init__` methods to avoid circular imports. This is a known/accepted pattern documented in CLAUDE.md.
- **Action:** Consider dependency injection instead of `get_running_app()` as the codebase evolves.

### 6. Communication Layer Functions Should Be Methods
- **File:** `reflex/utils/communication.py`
- **Issue:** `read_float`, `write_float`, `read_long`, `write_long`, `read_unsigned`, `write_unsigned`, `read_signed`, `write_signed` are all module-level functions that take `ConnectionManager` as the first argument. They duplicate the same try/except/connected pattern.
- **Action:** Refactor as methods on `ConnectionManager`, and extract the shared try/except pattern into a decorator or helper.

### 7. Duplicated C Typedef Parsing Logic
- **File:** `reflex/utils/base_device.py`
- **Issue:** `register_type()` (classmethod) and `parse_addresses_from_definition()` (instance method) contain nearly identical C struct parsing logic.
- **Action:** Extract shared parsing into a single function that both methods call.

---

## Safety / ELS guards

### Warn/prompt when enabling power feed with no ELS stop armed
- **Context (verified 2026-07-09, emulator-backed investigation):** feed is gated entirely by
  `syncEnable` on a scale — firmware `Ramps.c:626-631` auto-sets `servoMode=1` whenever any
  `scales[i].syncEnable != 0` (and ELS not already stopped), and the servo then follows the
  spindle. `syncEnable` is only set by deliberate operator action (`ServoDispatcher.toggle_enable`
  servo-enable button, `AxisDispatcher.toggle_sync` power-feed toggle, or the ELS engage→cut
  flow). Confirmed empirically that merely connecting — raw or full UI, spindle running — does NOT
  set `syncEnable` or move the carriage (so there is **no** uncontrolled feed on connect).
- **The gap:** nothing requires an ELS stop (`elsStop.enable` + a valid `stopPosition` on the
  cutting side) to be armed before the operator enables power feed. So an operator can start a
  sync feed toward the chuck/headstock with no auto-stop — the only protections are travel limits
  and the operator's own attention. That's normal for a bare power feed, but risky on this machine.
- **Why a prompt is reasonable (Evan, 2026-07-09):** in **advanced ELS mode**, *every* submode
  includes the stop function — so if the operator is in advanced ELS mode, it's reasonable to
  infer they intend to have an ELS stop set. Enabling feed there without an armed stop is likely a
  mistake, not an intentional bare power feed.
- **Action:** when enabling sync/power feed (servo enable / sync toggle) in advanced ELS mode with
  no valid ELS stop armed, prompt/confirm (or at least surface a visible warning) before allowing
  the feed — rather than silently feeding. Decide the exact UX (block-until-confirmed vs.
  warn-and-allow) and whether it applies only in advanced ELS mode or more broadly. The
  emulator-backed system-test suite (`.hermes/plans/2026-07-09_emulator-backed-system-tests.md`)
  is a natural place to add a regression test for whatever guard lands.

### Audit for unexpected large feed moves from arbitrary control ordering (broader than connect)
- **Concern (Evan, 2026-07-09):** the connect case is clean, but that's only one entry point. The
  UI exposes *separate, independently pressable* controls — servo/**Sync Enable**, **advanced ELS
  enable/engage**, ELS submode, DIR, and stop-Z entry — with no enforced ordering. Risk likely
  hides in the state combinations reachable by pressing them in an unexpected order, especially the
  interaction between the standalone Sync-Enable path and the ELS-engage path (both ultimately set
  `syncEnable`, the firmware feed master switch). Goal: find any sequence that produces an
  **unexpectedly large** feed (drives into chuck/headstock), not just a wrong-direction one.
- **Specific hypotheses to check (not yet investigated):**
  1. **Stale/default `stop_z`.** `controller.stop_z` defaults to 0.0. Engage with the carriage far
     from 0 and no stop_z entered → ELS arms against a stopPosition far away → a large feed to
     "reach" the stop when cut is pressed. Confirm what stop_z is used if never set this cycle.
  2. **Sync-Enable vs. ELS-engage interaction.** Pressing servo/Sync-Enable (sets `servoMode`→
     `_sync_spindle_to_servo` sets spindle `syncEnable`) while ELS is engaged-and-armed
     (`active=1` holding) — does the arming hold survive, or does the sync write release/override it
     and start feeding? And vice-versa (engage while a manual sync feed is already running).
  3. **Manual axis power-feed coupling.** `AxisDispatcher.toggle_sync` on a non-spindle DRO axis:
     does the ELS `_sync_spindle_to_servo` coupling turn an intended small manual feed into a
     full ELS-rate spindle-synced feed?
  4. **Mid-engaged mode/direction change.** Flipping `els_forward`/DIR or ELS submode while engaged
     (`_on_modes_changed` pushes a new `stopDirection`) — can it move the stop to the far side of
     the current position so the next cut feeds a long way (or the wrong way) before stopping?
  5. **Backlash-takeup / thread re-sync magnitude.** `on_enter_cutting`/`push_thread_geometry` can
     command a takeup or phase-correction move; check whether a stale `els_backlash_steps`,
     `threadPitchSteps`, or `zCountsPerPitch` (e.g. left over from a prior threading job, or an
     unmapped axis) can make that move unexpectedly large.
  6. **Re-engagement after an auto-stop.** After ELS fires and the operator re-engages, verify the
     resume can't command a large move (wrong reference latch / stale stopPosition).
- **Method:** once the emulator-backed system suite can drive a cut (Task 7+), add an adversarial
  "button-ordering" test group that drives these sequences against the real FSM + emulator and
  asserts the total feed travel stays bounded (no move exceeds the intended cut span + margin).
  This is the natural regression harness for whatever guards result.
- **Relationship:** this is the broader version of the "enable feed with no stop armed" item above;
  that guard may cover some cases, but this audit should enumerate the full reachable state space
  first so we know what the guard(s) must cover.

### UI FSM can lock in "Cutting…" with Stop disabled (TOCTOU on is_ready_to_cut)
- **Found:** overnight review of the system-test work (2026-07-09).
- **Issue:** `ui_fsm.py:40` transitions `in_cycle.waiting_to_cut → in_cycle.cutting`
  UNCONDITIONALLY on the action button; `on_enter_in_cycle_cutting` (`ui_fsm.py:102-104`) then
  calls `ElsFsm.cut()`, whose `is_ready_to_cut` guard can REFUSE (e.g. Z drifted past the safety
  margin between the last `_apply_policy` tick and the click — a time-of-check/time-of-use gap).
  Result: UI FSM sits in `in_cycle.cutting` (`can_stop=False`, blank action button per
  `ui_controller.py:22`) while the domain FSM is still `stopped`. No `stop_active` ever fires, so
  there's no FSM path out except toggling Engage. No motion occurs (firmware `active=1` still
  holds), so it's a lockup, not a crash — but a lathe UI that says "Cutting…" while disabling Stop
  is bad. **Action:** gate the UI `waiting_to_cut → cutting` transition on `els_fsm.may_cut()` (or
  roll the UI FSM back to `waiting_to_cut` when `ElsFsm.cut()` is refused). Add a regression test.

### ELS safety-critical register writes are fire-and-forget (no read-back / abort)
- **Found:** overnight review (2026-07-09).
- **Issue:** the `reflex/utils/communication.py` write helpers swallow all exceptions (log only),
  and reads return 0 on failure. In `ElsFsm.on_enter_cutting` (`els_fsm.py:117-136`) the sequence
  writes `stopPosition`/`stopDirection`/`enable` then clears `active` to release the cut. If the
  `stopPosition` write fails on a transient Modbus timeout but the link recovers before
  `set_active(False)`, the cut resumes against the PREVIOUS pass's stop position — a wrong-shoulder
  cut. **Action:** consider read-back verification (or aborting the state transition / raising the
  ELS alarm) for the safety-critical `elsStop` writes (`stopPosition`, `stopDirection`, `enable`)
  before releasing `active`. Weigh against the added Modbus round-trips per cut.

### ~~Investigate: does changing servo maxSpeed at runtime corrupt the ELS sync ratio?~~ RESOLVED — NOT A BUG (2026-07-10)
- **Verdict:** phantom. There is NO maxSpeed→syncRatioDen coupling. Instrumenting
  `AxisDispatcher._set_sync_ratio` (it is not bound to `maxSpeed`, and `final_ratio =
  scale_ratio * user_sync / servo_ratio` has no maxSpeed term) showed that setting
  `board.servo.maxSpeed=100000` after connect does NOT change `scales[0].syncRatioDen`
  (stays 25) and does not even re-invoke `_set_sync_ratio`. The overnight "25→25000"
  observation was a confound in the original probe.
- **What the retract hang actually was:** (1) servo *polarity* — the retract is a direct
  servo indexing move (`servo.stepsToGo`), so its direction depends on `servoDir`; the
  `EMU_RPM=-30` band-aid only fixes the sync-mediated cut, so without `servo_reverse=true`
  the retract ran the wrong way; (2) servo *rate* — at the hermetic `maxSpeed=1000` default a
  cut-time step backlog flushes after the ELS stop (an emulator 10 kHz-ISR artifact). Both
  resolved by commissioning the harness servo like the real machine (`servo_reverse=true`,
  `maxSpeed=10000`). Task 9 now passes with no product change. See commit 3568921.

### Safety audit results (2026-07-10, emulator-driven, branch `fix/els-safety`)
Adversarial control-ordering probes against the real controller/FSM stack + emulator.
Findings (probes were temporary; regression tests land with each fix):

- **DOMINANT ROOT CAUSE — sync feed is decoupled from the ELS stop.** The
  servo/Sync-Enable feed runs free whenever `syncEnable=1` + spindle turning + nothing
  actively gating (no armed stop, or ELS disarmed). Reproduced two ways: (H2b) enabling
  Sync-Enable standalone with no ELS armed → carriage fed ~11,965 counts freely; (H6)
  engage+cut normally then **disengage ELS** while Sync-Enable stays on → the stop is
  removed but the feed continues (~9,700 counts in 6 s). This is the core of the
  "no-stop-armed feed guard" item — broadened: cover BOTH enable-without-stop AND
  disarm-while-feeding. **DECISION (Evan): confirm-to-override on enabling feed with no
  armed stop (advanced ELS mode); disengaging ELS also stops an active sync feed; basic
  bare power feed unaffected.**
- **CONFIRMED — never-set / stale `stop_z` (H1).** `stop_z_valid` was hard-coded True, so
  the 0.0 default was silently usable (engage+cut → feed to Z=0). NOTE: a feed-*distance*
  check was rejected (legit cuts can be multiple inches; the hazard is a WRONG stop_z, not a
  large one). **FIXED (f5a6913):** `stop_z_valid` now means "operator actually set a stop_z"
  — starts False, the action gate blocks the cut, the field shows "--". Invalidated on ELS
  Z-axis remap.
  - **FOLLOW-UP DONE (effa7f5, 7bd9297, 71a55db):** the DRO re-zero / units-switch corruption
    is now fixed structurally — `stop_z`/`retract_z` (and `start_dia`/`stop_dia`) are anchored
    to the raw leadscrew/X encoder captured at Set, with the scaled value a live-derived
    display mirror; the cut writes the frozen encoder. A re-zero / units switch just
    re-references the display (physical target unchanged), matching the DRO. Only never-set +
    axis remap invalidate. Optional per-machine notify (`ElsDispatcher.stop_z_reframe_notify`
    = silent/warn/confirm) flags an offset/coordinate re-reference (not units). UI (settings
    dropdown, notice strip) needs an on-device smoke test.
  - **KNOWN LIMITATION — SUM-transform ELS Z axis (PRE-EXISTING, Fable-flagged 2026-07-11).**
    `AxisDispatcher.position_to_encoder` / `scaled_from_encoder` only account for the axis's
    PRIMARY input. If the ELS Z axis is configured as a SUM transform (leadscrew scale +
    compound-slide scale), the stop is anchored/armed against the primary encoder only — a
    contribution from the second scale would put the physical stop in the wrong place. This is
    UNCHANGED by the encoder work (the old cut-time `position_to_encoder` conversion had the
    identical flaw), and the derived mirror at least stays self-consistent with what's armed.
    Normal single-input Z lathe setups (incl. elspi) are unaffected. FUTURE: refuse/warn when
    an ELS Z axis has a multi-input transform, or fold the full transform into the encoder
    conversion.
- **CONFIRMED — 'Cutting…' lockup (H3).** Deterministically reproduced: when the domain
  cut is refused, the UI parks in `in_cycle.cutting` (blank action, Stop disabled) with
  no exit but the Engage toggle. Fix: gate the UI `waiting_to_cut→cutting` transition on
  `may_cut()` / roll back on refusal.
- **SAFE (guards hold):** H2a (engage→sync doesn't feed — arming gates it); H4
  (mid-engage DIR flip → cut guard blocks).
- **DEFERRED to hardware verification (Evan's decision) — post-stop overshoot.** The
  carriage overshoots the stop because a servo step backlog flushes after the stop latches
  (pulse generation isn't gated by `elsStop.active`). Large at the emulator's 10 kHz ISR
  (~4,600 counts past an 8,000 feed); ~10× smaller expected on real 100 kHz hardware. NO
  firmware change this release — measure actual overshoot on the real lathe first; fix in
  reflex-fw only if hardware shows a real problem.
- **LOW PRIORITY — H5 backlash takeup.** The cut-start takeup is bounded by
  `els_backlash_steps` (config). Inconclusive in the emulator; add a config-range
  validation rather than treat as a control-flow bug.

**Fixes landed (branch `fix/els-safety`, all with emulator/unit regressions;
Fable-reviewed twice — review + verify):**
- H3 'Cutting…' lockup — `fedfc9b` (gate UI cut on fresh domain `may_cut`).
- Sync/stop guard — `02a03d8` (confirm-to-override on feed w/o armed stop;
  disengage stops the feed).
- elsStop write verification — `ddd078a` (verify stopPosition/scaleIndex + enable
  ACK before releasing; abort→alarm on failure).
- H1 stop_z validation ("--", must-set) — `f5a6913`.
- Review fixes — `baadad8` (CRITICAL: sync guard was on the wrong bar — routed
  elsbar Sync Enable through the guard; verify `enable` before release; alarm
  state exit + may_cut/toggle_engage guards + alarm-text surface).
- Alarm recovery completion — `9f054c9` (UI FSM leaves alarm on disengage;
  double-tap-engage guard).
- Encoder-anchored ELS targets — `effa7f5`, `7bd9297`, `71a55db` (stop_z/retract_z
  + diameters anchored to the physical encoder; re-reference notify silent/warn/
  confirm; closes the H1 DRO-rezero gap above).
- Deferred (Evan): post-stop overshoot — hardware-verify first, no firmware change.

**On-device smoke test (Kivy UI, not headless-testable — logic IS tested):**
- Sync-guard confirm popup (CustomPopup + cancel button).
- H1 "--" Stop-Z display + alarm-text instruction line.
- Re-reference notify UI: ELS-settings "Stop re-reference" dropdown +
  els_advbar notice strip (warn message / confirm Keep+Reset).

## Dead Code and Cleanup

### 8. Dead/Commented-Out Code
- `reflex/app.py:56-59` - `beep()` method is a no-op with commented-out implementation
- `reflex/components/toolbars/toolbar_button.py:12-22` - class body is `pass` followed by commented-out code
- `reflex/components/screens/color_picker_screen.py:15-19` - commented-out `__init__`
- `reflex/components/home/home_toolbar.py:20-21` - commented-out `popup_scene`
- `reflex/components/screens/home_screen.py` - `TraceOutput` opens file but `self.exit_stack` is never initialized (would raise `AttributeError` at runtime)
- **Action:** Remove dead code. Either restore `beep()` or remove it entirely. Fix or remove the `TraceOutput` code path.

---

---

## UI Facelift (ui-facelift branch)

### StyledButton rollout
- **Status:** Prototype complete. `StyledButton` (slate + cyan-glow highlight states)
  is applied to **Engage** and the **Cut/action** button in `els_advbar.kv` only.
- **Action:** Once the look is approved, roll `StyledButton` out to the remaining
  buttons to match the mockup: the left sidebar tabs (MM/IN, P0–P3, ABS/INC, ELS/DRO),
  the Z/⌀/square/DIR/arrow buttons, ADV, Sync Enable, FEED/THREAD, Zero buttons, etc.
  These live across multiple components (coordbar, servobar, jogbar, toolbars), not
  just els_advbar.
- **Note:** `is_highlighted` is currently hard-set `True` on the Cut button to demo the
  glow. Wire it to real state (e.g. action active / selected) when productionizing.

### Gradients in Kivy (investigation closed)
- Hermes blamed a missing gradient feature on the Kivy version. Not a version issue:
  `kivy.graphics.LinearGradient` does **not** exist in any released Kivy (confirmed
  absent in 2.3.1, the current stable). Upgrading Kivy will NOT add a gradient
  primitive. Real gradients require a gradient **texture** on a Rectangle/RoundedRectangle
  or a custom shader. The StyledButton "metal sheen" is faked with stacked shapes
  instead — no upgrade needed. If true gradients are wanted later, generate a 1xN
  gradient texture once and assign it to the shape's `texture`.

### Highlight glow: BoxShadow — REJECTED (decided 2026-06-21)
- Prototyped a `kivy.graphics.BoxShadow` soft cyan bloom for the highlighted (Cut)
  state vs. the existing faked stacked-RoundedRectangle halo. BoxShadow looked nicer
  but was deemed **too gratuitous for this industrial-style UI**.
- **Decision:** Keep the faked glow (reads as effectively no bloom — just the cyan
  border + text + teal fill). No real-glow primitive. Branch `facelift/glow-experiment`
  and its `glow_mode` API were discarded. Do NOT revisit unless the design direction
  changes.

### ELS FSM test suite is red at HEAD (pre-existing, unrelated to facelift)
- `tests/fsms/test_els_fsm.py` and `tests/fsms/test_ui_controller.py` fail/error
  heavily on a clean HEAD checkout (~26 failed, 23 passed, 18 errors), independent
  of any UI-facelift changes.
- **Root cause:** test mock drift after the hard fork / refactors. Example:
  `_safety_margin_display()` reads `self.servo.leadScrewPitch`, but the test servo
  fixture is a `types.SimpleNamespace` without that attribute →
  `AttributeError: 'types.SimpleNamespace' object has no attribute 'leadScrewPitch'`.
- **Action:** Update the FSM test fixtures/mocks to match the current
  ElsFsm/ElsUiController/servo interfaces and get the suite green again. Verify the
  engage-with-no-Z-axis guard (ui_controller.toggle_engage) and the lazy z_axis/x_axis
  properties (els_fsm) are covered by a regression test once the suite runs.

---

## UI Facelift (StyledButton rollout)

### Reusable widgets created (solid)
- `reflex/components/widgets/facelift_theme.py` — central dark/cyan palette module.
- `reflex/components/widgets/tab_selector.py/.kv` — one-hot `TabSelector`/`TabSegment` group.
- `reflex/components/widgets/icon_button.py/.kv` — square Font Awesome `IconButton` (slate / cyan-glow).
- `reflex/components/widgets/circular_button.py/.kv` — cyan-outline `CircularButton` (Zero keys).
- `reflex/components/widgets/recess_panel.py/.kv` — `RecessPanel` container + `RecessFrame` overlay class.
- Restyled `toolbars/led_button.kv` (LED dot + label, optional icon/two-line) and
  `toolbars/text_header_button.kv` (recessed inset value field).

### Deferred / needs human design judgment
- **ABS/INC blink lost:** the old TwoStateButton blinked on `app.board.blink and app.abs_mode`
  (a board-connection hint). The new `TabSelector` swap dropped this. Decide whether the
  blink hint matters; if so add a `blink`/pulse affordance to `TabSegment`.
- **FEED/THREAD toggle (elsbar):** the mockup shows a FEED/THREAD one-hot toggle, but in the
  live `ElsBar` the feed/thread mode is chosen via `FeedsTablePopup` (`mode_name`), not a
  two-segment toggle. Left as-is (different interaction model). Revisit if the design wants a
  literal toggle that filters the feeds table.
- **DIR arrows (elsbar):** still a `TwoStateButton` with FA arrows. Could become a horizontal
  2-segment `TabSelector`, but its `value`/`on_release` semantics are load-bearing — left alone
  to avoid regressions. Convert with care + live testing.
- **Big DRO readouts → cyan: DONE (2026-06-21).** `display_color` default switched amber→cyan
  (`#40e0ed`) in `dispatchers/formats.py`; local saved `FormatsDispatcher-0.yaml` updated too.
  Existing user configs keep their saved value until reset. (Optional later: a dedicated
  "facelift" theme toggle instead of overriding the saved preference.)
- **`servobar.kv` / `jogbar.kv` / inner `coordbar` Num/Den/feed buttons:** still raw `Button`s
  with grey backgrounds. Lower priority; convert to `StyledButton`/recessed fields once the
  layout proportions are confirmed against the mockup.
- **els_advbar `btn_start_stop` and elsbar gear/ADV/feed buttons:** left as raw Buttons
  (state-colored, disabled logic, image children) — higher layout risk. Restyle later.
- **Status bar `v1.3.0` pill:** currently a restyled `LedButton`; the mockup wants a plain
  bordered version pill (no LED dot). Minor; add a `no_led` flag to `LedButton` if desired.
- **Sidebar P0 / mode now multi-segment one-hots: DONE (2026-06-21).** P0–P3 (4 segments)
  and ELS/DRO (built from `allowed_modes()`, so correct per use case) are `TabSelector`
  groups like MM/IN; tap *anywhere* on a group cycles the one-hot selection (touch-friendly).
  Sidebar accent moved to the right (inner) edge; `TabSegment` edge made reactive (was a
  stale one-shot read of orientation). Interim `TabButton` widget removed.
- **Dropped the sidebar long-press popups (follow-up):** converting to pure one-hots removed
  the P-offset keypad (so offsets **4–99 are no longer selectable** from the sidebar — only
  P0–P3) and the mode popup. If keypad/extended-offset or popup access is still wanted, add a
  long-press affordance to `TabSegment`/`TabSelector`.

### Next batch — queued 2026-06-21 (rollout merged into ui-facelift @ d16c3dc)
Reviewed the merged result live; these are the remaining gaps vs the mockup, in
rough priority order:
- **Restyle the remaining grey buttons** (biggest cohesion win): `ADV`, the `DIR`
  arrows, the ELS `← →` nav arrows (left/right of the Thread field), and
  `btn_start_stop`. Visual-only dark-theme restyle — preserve `TwoStateButton`/`value`
  and `on_release` behavior; test live since several are load-bearing.
- **Theme polish:** set `Window.clearcolor` to the mockup's near-black blue-grey
  (~`#0a0e12`) instead of relying on Kivy's default pure black; add a recess/inset
  frame around the central lathe-graphic panel (currently a grey block).
- **`Z` / `⌀` axis-letter buttons:** the axis-name buttons (render as `?`/`R` boxes
  when disconnected) should become `StyledButton`s like the mockup's `Z` and `⌀` keys.
- **Wire highlights to real state:** `btn_action` (Cut) has `is_highlighted: True`
  hard-coded to demo the look — drive it from actual action-active/selected state.
  Audit other `is_highlighted`/selected bindings to ensure they reflect real state.

### Note: dark background is NOT a bug
The home content area reads as transparent `(0,0,0,0)` in `export_to_png` output; the
app sets no `Window.clearcolor`, so Kivy's default pure-black shows through at runtime
(matches the mockup). Do not "fix" a white background — it's a PNG-alpha/matte artifact
in light-themed image viewers. Composite previews over black before judging.

### Preview scripts (not for production)
- `previews/preview_widgets.py`, `previews/preview_probe.py`, `previews/preview_home_live.py`
  render widgets via `export_to_png` (needs `EventLoop.idle()` ticks + a double export, else
  only one tile renders). Consider deleting these or moving under `tests/` before release.

### Mockup fonts (Chakra Petch / Share Tech Mono / DSEG7) — DONE + follow-ups (2026-06-21)
- Bundled all three (SIL OFL 1.1) under `reflex/fonts/` with their OFL license files.
  UI labels → Chakra Petch (`THEME.FONT_BOLD`); status-bar telemetry/version → Share
  Tech Mono (`THEME.FONT_MONO`); DRO/RPM/Stop-Z numerals → DSEG7 Classic (`THEME.FONT_SEG`).
- **DSEG7 has no `+` or `/` glyph.** Consequences/decisions:
  - The leading `+` is stripped at the seven-seg display only (`...replace("+","")` on the
    value labels); positives show unsigned, negatives keep `-`. Format strings still carry
    `+` (the formats_screen digit-parser depends on the `{:+0.` prefix — don't remove it).
    To restore a literal `+` on readouts like the mockup, render the sign in a companion
    (non-DSEG7) font — a sign/digit/unit split in coordbar/dro_coordbar/els_mode/text_header.
  - **Speed value stays a normal font** (its string embeds the unit, e.g. `+0.000 M/min`,
    which DSEG7 would garble). Only pure-numeric fields use DSEG7.
  - **Rotary/angle mode not handled:** `formattedPosition` appends `°` in angle mode, which
    DSEG7 lacks → would garble for the rotary_table use case (only linear mm/in verified).
    Fix before shipping rotary.
- **DRO font picker (`formats.font_name`) no longer controls the big numerals** (now pinned
  to DSEG7). It still affects units/other displays. Decide whether to keep/repoint the picker.

## Switchable UI theme (dark cyan ↔ light brushed-aluminum/amber)

Implemented: reactive `ThemeProvider` (`app.theme`) fed by `reflex/components/widgets/palettes.py`
(DARK/LIGHT); selected via `app.formats.theme` (persisted) + a "UI Theme" dropdown in Format
Settings. All chrome colors flow through `app.theme.<token>`; no theme conditionals in KV.
Deferred / decisions to revisit:
- **DRO digits stay on `app.formats.display_color`** (operator color picker), NOT a theme token.
  They sit on dark `readout_bg` cells so cyan reads in both themes; in the light/amber theme the
  big DRO numerals therefore remain cyan by design. Panel numerals/units use `app.theme.readout`
  (cyan in dark, charcoal in light). Revisit if the DRO should go amber in the light theme.
- **`formats.color_on`/`color_off`** (amber on/off tints, used by two_state_button + ssid_popup)
  are still operator-config colors, not theme tokens. They happen to read OK on both themes; fold
  into tokens if fuller centralization is wanted.
- **Plot toolbar buttons** (bottom of Plot View) are stock dark-grey toolbar widgets, not themed.
- **LIGHT `accent` on the bare background** is ~2.88:1 (just under WCAG 3.0) for separators/axis
  lines; acceptable as decoration. Darken `accent` further only if it must clear 3.0 everywhere.

### Theme definitions moved to per-theme INI files

Themes are now defined in `reflex/themes/<name>.ini` (built-in: dark, light) and
`~/.config/reflex/themes/<name>.ini` (user-added; a file reusing a built-in name
overrides it). Each file is a stdlib-`configparser` INI with `[meta]` (label),
`[colors]` (rgba 0..1 tokens), `[paths]` (logo + fonts) and `[seeds]` (the
operator color recommendations applied on theme switch). `palettes.py` discovers
and loads them (filename = theme identity; missing tokens inherit the default
theme). The Format Settings "UI Theme" dropdown auto-lists discovered themes.
To add a theme: copy a built-in .ini into the user dir, edit, restart. (Themes
load once at startup — no hot-reload.)

## UI facelift — adversarial-review findings (pre-merge to main, 2026-06-22)

Surfaced by a second-pass adversarial review of the `switchable-themes` branch
(see `ui-facelift-review.html` at repo root). A1–A3 fixed; A4+ deferred pending
report review.

Real bugs — FIXED in 4a017b8 (2026-06-22):
- A1 **Plot stale-token repaint** — DONE. `plot/scene.py` now defers the repaint
  to the next frame via a Clock trigger so the full token sweep finishes first.
- A2 **X-axis None-deref crash** — DONE. `ui_controller.on_action_button_clicked`
  now None-guards both axes (warns + bails); regression test added
  (`test_on_action_button_clicked_without_x_axis_does_not_raise`).
- A3 **Partial-theme switch crash** — DONE. `_sync_theme` (app.py) skips None seeds.

Robustness / polish — FIXED (2026-06-22):
- A4 **icon-font token** — DONE. Routed the ~21 hardcoded Font Awesome paths to
  `app.theme.font_icon` across the ELS/servo/plot KV + IconButton/KeypadIconButton.
  NOTE: only the *icon* font was migrated. The other hardcoded fonts (Manrope/
  iosevka/ChakraPetch/ShareTech/DSEG literals) remain — a broader role-based pass
  (each site → font_bold/font_mono/font_seg by the glyphs it needs, verified to
  avoid tofu) is still open if wanted.
- A5 **dropdown `_options` leak** — DONE. `delete_all_dropdown_options()` now
  `clear()`s the list.
- A6 **malformed color salvage** — DONE. `_load_file` skips a bad color/seed line
  (logged) so the token backfills from default instead of dropping the theme.
- A7 **dead LABELS** — DONE (dropped). Removed the unused `[meta] label` parsing
  and `palettes.LABELS`; the picker keeps showing the file names.

Light-theme contrast tuning — FIXED (2026-06-22):
- A8 **text_disabled** — DONE. light.ini 0.5,0.52,0.54 → 0.42,0.44,0.46.
- A9 **version label** — DONE. statusbar.kv now uses `text` (not `text_dim`).
- A10 **blink header label** — DONE. text_header_button.kv now uses `accent_text`.

Open follow-ups (not bugs):
- A4-broad: role-based migration of the remaining non-icon hardcoded fonts.

Works-as-specified (note, not bugs):
- Theme switch re-seeds + persists operator color choices (display_color/color_on/
  color_off) — matches the requested behavior, but previewing the other theme
  clobbers a customization with no undo. Consider confirm-or-revert if it matters.
- Engage-refused-no-Z is log-only; consider an operator-visible toast/alarm line.

---

## Deployment automation

- **Script the Pi deployment.** The full manual procedure for deploying reflex-ui to a
  fresh OSPI install is documented in `deploy/DEPLOYMENT.md` (with `deploy/start.sh` and
  `deploy/reflex-ui.service` as the artifacts). Turn this into a one-shot deploy —
  preferably an **Ansible playbook** (idempotent: install uv, create `/reflex-ui`, sync
  deps, install unit, disable rcp + enable reflex-ui), or a bash script as a simpler
  fallback. Should parameterize host/branch and support the committed-push and rsync
  transfer modes.

---

## README auto-updating screenshots

- **Approach: committed images, not release assets.** README screenshots live in
  `docs/screenshots/` and are referenced by relative path. CI regenerates them and
  semantic-release commits them as part of the release commit (via its `assets` config
  in `pyproject.toml`). Chosen over release assets because (a) the repo is private and
  GitHub's Camo proxy can't fetch private release assets, so asset URLs wouldn't render,
  while relative-path images do; (b) per-branch bespoke (main vs dev) for free; (c) PSR
  already makes a commit each release, so this adds no extra commit; (d) still no loop
  (the release commit is pushed via `GITHUB_TOKEN`, which doesn't re-trigger workflows).
- **Update cadence = release cadence.** Screenshots only refresh when a version-bumping
  push cuts a release (that's when PSR commits the assets). Pushes that don't bump a
  version won't update them. Accept; revisit if per-push freshness is needed.
- **Headless GL fallback.** Capture runs under `xvfb-run` + Mesa software GL
  (`LIBGL_ALWAYS_SOFTWARE=1`). If CI rendering proves flaky, that env var is the first
  lever.
- **Capture script config isolation.** `scripts/capture_readme_screenshots.py` runs
  against an isolated temp `HOME`, so dispatcher YAML writes (theme, axes, ELS indices)
  never touch a developer's real `~/.config/reflex`; it configures the showcase
  (use_case=lathe, Z/X/S axes, ELS axis indices) deterministically at runtime. One
  residual: `app.use_case` persists to the repo-root `config.ini` (path is hardcoded
  relative to the module, not under HOME) — gitignored and harmless, but a local run
  will set `use_case=lathe` there.

---

## System-test suite: explicit feed selection

- **Context (2026-07-12, geometry commissioning):** `SystemHarness.commission_geometry()` now
  commissions the harness to the emulator reference machine (400 counts/mm scales, 4000 counts/rev
  spindle, exactly 127/32000 mm/step leadscrew), so system-test spans/tolerances are real mm and
  the firmware's count-domain registers are physically meaningful.
- **Gap:** the cut feed rate in the system tests still comes from the spindle axis's *default*
  `syncRatioNum/Den = 360/100`, reinterpreted as 3.6 mm/rev (1.8 mm/s at EMU_RPM=30). Production
  sets this via `ElsBar.update_feeds_ratio` from `feeds.table`; the harness has no ElsBar, so the
  tests implicitly depend on a rotary-axis default that happens to be a usable feed.
- **Done (2026-07-12):** `SystemHarness.set_feed(pitch_mm)` mirrors `update_feeds_ratio`
  (signed spindle-axis `syncRatioNum/Den` from `els_forward`); all ELS system tests now select
  16 TPI (`Fraction(254, 160)`, feeds.py Thread IN "16" = 1.5875 mm/rev, ~0.79 mm/s at
  EMU_RPM=30) explicitly.

---

## UI state codes — follow-ups

Shipped on `feat/ui-state-encoding`: `reflex/uistate/` records a
versioned code per visual state change, `scripts/replay_ui_state.py` re-renders
any code to a PNG, `scripts/storyboard.py` builds a contact sheet. See the
AGENTS.md section for the rules; these are the known gaps.

- **Schema v1 covers the home screen only.** Setup and config screens encode as
  a screen name and replay in their default state. Extending to them is a new
  schema id, not an edit to v1.
- **Modals are not reconstructed.** Open `ModalView`s are digested (so a replay
  of a frame with a popup open reports drift rather than lying), but nothing
  re-opens them. Needs a small registry of popup classes plus their visual
  properties: `Keypad`, `ModePopup`, `FeedsTablePopup`, `ElsSettingsPopup`,
  `BacklashCalPopup`, `HelpPopup`.
- **Mid-transition frames are not captured.** The recorder waits for the picture
  to stop changing before recording (`SETTLE_SECONDS`, and a stability probe
  over both values and the widget tree), so a 50 ms `FadeTransition` between
  screens is never a recorded frame. That is the right trade for a storyboard,
  but it means the log shows discrete screens, not the wipe between them.
- **Not yet run on the Pi.** The per-capture cost (a widget walk plus a zlib
  compress, on state changes only — never on `board.update_tick`) is measured
  only on a dev box. Worth confirming at the machine before trusting the
  "steady-state cost is negligible" claim, and `REFLEX_UISTATE=off` is the lever
  if it is not.
- **No retention policy beyond rotation.** 5 MB x 5 files under
  `uistate_dir()`. Fine for a card, but there is no "keep the frames around an
  alarm and discard the rest" pass, which is what an incident actually wants.

## Release flow: uv.lock version is not updated

`ui/uv.lock` recorded `reflex 1.0.0rc2` while `ui/pyproject.toml` said
`1.1.0rc1`, so any `uv run` rewrites the lock and dirties the tree. The release
workflow writes the repo-root `VERSION` and `pyproject.toml`'s `version` but
does not refresh the lock. Noticed 2026-08-25 while working on the UI state
codes branch, and deliberately not fixed there — it belongs with the release
workflow, not a feature branch.
