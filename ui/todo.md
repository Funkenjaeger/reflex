# RCP Project Review - Findings and Recommended Actions

---

## Community Feedback (2026-04-04 / 2026-04-05)

### Feature Requests

- [x] **Speed control for indexing mode** (Luke C) — Added `indexSpeed` property to ServoDispatcher with a "Speed" button in the servobar. Speed is persisted, applied before index/offset moves, and reverted to axis max on arrival. Keypad enforces min (5% of maxSpeed) and max (maxSpeed) limits.
- [x] **Keypad min/max validation** — Keypad now accepts optional `min_value`/`max_value` args. Limits are shown in the header and values outside the range are rejected. Extracted `build_title`, `parse_value`, `validate_value` as static methods with 37 tests.
- [x] **RST help rendering** — Converted all 27 help files from Markdown to RST. HelpPopup now uses Kivy's `RstDocument` for proper formatted rendering (headings, tables, lists, bold). `load_help()` auto-resolves `.rst` files with `.md` fallback.
- [x] **Pinch-to-zoom on plot** — Added two-finger pinch-to-zoom gesture in the plot view. Custom touch tracking in `FloatView` intercepts two-finger gestures and maps distance changes to the existing `zoom` property. Single-finger pan via ScrollView remains unchanged.
- [ ] **Tool radius offset** (colinb) — Add a tool radius compensation feature so users can account for current tool radius in DRO calculations. colinb says it's the thing he most often forgets to account for.
- [ ] **Units for speed indication** (Stefano) — Add ability to change the units for the speed display.
- [ ] **STM32 firmware flashing from Pi** (Stefano) — Implement the ability to program the STM32 board directly from the Raspberry Pi via the setup page, to simplify future firmware updates for users.
- [ ] **WIP user documentation** (colinb) — Users are asking for documentation/instructions. Consider creating a basic user guide.

### Bugs / Fixes

- [x] **Serial communication resilience** — ConnectionManager now tolerates up to 5 consecutive errors before marking the link as disconnected. Prevents transient checksum/physical-layer glitches from triggering full reconnection cycles that reset index position and servo state. Introduced `report_error()` method and per-error debug logging.
- [x] **Spindle encoder degrees-per-revolution bug** (Pawcu) — Removed the spurious `360 *` multiplier from the spindle-mode `scale_ratio` in `_set_sync_ratio()`. The scale ratio should convert encoder steps to revolutions (not degrees) since the user sync ratio already accounts for the 360° per turn. Matches Pawcu's fix in PR #46.

### PRs to Review

- [ ] **PR #46 — Assisted/automatic threading** (Pawcu) — Adds AT wizard flow, spindle sync, and threading support. Requires corresponding firmware PR bartei/rotary-controller-f4#12. Spindle encoder fix already applied independently. Needs review and evaluation for phased integration.

### Planned Work (Stefano, week of 2026-04-05)

- [x] Bug fixes for reported issues (serial resilience, spindle encoder fix)
- [x] Speed control for indexing mode
- [x] Help file rendering improvements (RST conversion)
- [x] Plot pinch-to-zoom gesture
- [ ] Speed indication units
- [ ] ELS mode improvements — needs to work perfectly, will require multiple firmware+UI iterations
- [ ] Review Pawcu's PR #46 — phased integration, not all at once
- [ ] STM32 flashing from Pi


## Architecture

### 5. Circular Import Dependencies
- **Issue:** Components still use `from rcp.app import MainApp` inside `__init__` methods to avoid circular imports. This is a known/accepted pattern documented in CLAUDE.md.
- **Action:** Consider dependency injection instead of `get_running_app()` as the codebase evolves.

### 6. Communication Layer Functions Should Be Methods
- **File:** `rcp/utils/communication.py`
- **Issue:** `read_float`, `write_float`, `read_long`, `write_long`, `read_unsigned`, `write_unsigned`, `read_signed`, `write_signed` are all module-level functions that take `ConnectionManager` as the first argument. They duplicate the same try/except/connected pattern.
- **Action:** Refactor as methods on `ConnectionManager`, and extract the shared try/except pattern into a decorator or helper.

### 7. Duplicated C Typedef Parsing Logic
- **File:** `rcp/utils/base_device.py`
- **Issue:** `register_type()` (classmethod) and `parse_addresses_from_definition()` (instance method) contain nearly identical C struct parsing logic.
- **Action:** Extract shared parsing into a single function that both methods call.

---

## Dead Code and Cleanup

### 8. Dead/Commented-Out Code
- `rcp/app.py:56-59` - `beep()` method is a no-op with commented-out implementation
- `rcp/components/toolbars/toolbar_button.py:12-22` - class body is `pass` followed by commented-out code
- `rcp/components/screens/color_picker_screen.py:15-19` - commented-out `__init__`
- `rcp/components/home/home_toolbar.py:20-21` - commented-out `popup_scene`
- `rcp/components/screens/home_screen.py` - `TraceOutput` opens file but `self.exit_stack` is never initialized (would raise `AttributeError` at runtime)
- **Action:** Remove dead code. Either restore `beep()` or remove it entirely. Fix or remove the `TraceOutput` code path.

---