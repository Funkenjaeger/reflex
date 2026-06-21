# Reflex UI Project Review - Findings and Recommended Actions

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

### Use BoxShadow for the highlight glow (upgrade-free)
- `kivy.graphics.BoxShadow` exists in Kivy 2.3.1 (added in 2.2.0) — a real soft
  drop-shadow/glow primitive. The current StyledButton highlight glow is faked with
  a stacked RoundedRectangle halo in `styled_button.kv`.
- **Action:** Replace the faked outer-glow rect with a `BoxShadow` instruction for a
  softer, more mockup-accurate cyan bloom on the highlighted (Cut) state. Less canvas
  code, better result. No Kivy upgrade required.

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
- **Big DRO readouts stay amber:** the large position numbers use `app.formats.display_color`
  (a user-saved preference, default amber). Not recolored to cyan — decide whether the facelift
  should override the saved display color or add a separate "facelift" theme toggle.
- **`servobar.kv` / `jogbar.kv` / inner `coordbar` Num/Den/feed buttons:** still raw `Button`s
  with grey backgrounds. Lower priority; convert to `StyledButton`/recessed fields once the
  layout proportions are confirmed against the mockup.
- **els_advbar `btn_start_stop` and elsbar gear/ADV/feed buttons:** left as raw Buttons
  (state-colored, disabled logic, image children) — higher layout risk. Restyle later.
- **Status bar `v1.3.0` pill:** currently a restyled `LedButton`; the mockup wants a plain
  bordered version pill (no LED dot). Minor; add a `no_led` flag to `LedButton` if desired.
- **Sidebar P0–P3 / mode buttons:** P-offset and mode are popup/keypad driven (not one-hot),
  so they remain `ToolbarButton`s rather than `TabSelector`s. Could be restyled to match the
  tab strip visually without changing interaction.

### Preview scripts (not for production)
- `previews/preview_widgets.py`, `previews/preview_probe.py`, `previews/preview_home_live.py`
  render widgets via `export_to_png` (needs `EventLoop.idle()` ticks + a double export, else
  only one tile renders). Consider deleting these or moving under `tests/` before release.
