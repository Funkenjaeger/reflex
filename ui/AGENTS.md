# AGENTS.md — Reflex UI

## Branching and Hardware Verification — READ FIRST

**This UI drives a real lathe through reflex-fw. The only complete test is on
hardware, and Evan runs that, not on demand.** The emulator-backed system suite
is good and getting better, but it has repeatedly looked green while something
real was wrong — no servo dynamics, no Modbus timing, no metal. Emulator green
is evidence, never verification.

**Do NOT commit directly to `dev-staging`.** It is one step from a dev release
and everything on it is supposed to be hardware-verified.

- Work on a **feature branch**, or on **`integration`** when several changes are
  in flight and separate branches would just be overhead.
- `integration` / feature branch → `dev-staging` is merged **only after Evan has
  verified on hardware**. He does that merge, or explicitly asks for it.
- `dev-staging` → `dev` and `dev` → `main` are **Evan's alone**. Never do these.

**The one exception**, for changes that cannot affect machine behaviour and so
need no hardware run: documentation, help files, `todo.md`, and tests. Anything
that changes what gets written to a firmware register — HAL, FSM, dispatchers,
`devices.py` — is NOT clerical, however small it looks.

Register-map changes are never clerical on either side: `devices.py` and
reflex-fw's `Ramps.h` are one contract, and they must land together.

If unsure whether a change qualifies, it does not. Put it on a branch and ask.

**Never push without being asked.** `origin` fans out to BOTH
`github.com/Funkenjaeger/reflex-ui` and `dserver:/mnt/git/reflex-ui.git`, so any
push writes two remotes at once.

## Todo Tracking

When you encounter a task, follow-up item, or piece of work that should be tracked, add it to `todo.md` in the project root. This applies to:
- Deferred work discovered during development
- TODO comments that appear in code or documentation
- Bugs or improvements identified during debugging
- Deployment or configuration tasks that need documentation
- Any action item that won't be completed in the current session

Do NOT leave TODO comments in code, documentation, or bash snippets — always route them to `todo.md` instead.

## Project Overview

Reflex UI is a Kivy-based DRO (Digital Read-Out) and single-axis controller UI for rotary tables.
It communicates with embedded hardware (STM32) over RS-485/Modbus RTU using `minimalmodbus`.
Target platforms: Raspberry Pi (primary), Linux, Windows, macOS.

## Sibling Repos

This project is tightly coupled with **reflex-fw**, the STM32 firmware that runs on the controller board.

- **Interface:** RS-485 Modbus RTU — the UI reads/writes holding registers that map directly to the firmware's shared data struct
- **Version compatibility:** For released versions, matching major.minor implies UI↔FW compatibility. For dev branches, assume the latest commit on each repo's respective branch is compatible. Cross-repo changes affecting the Modbus register interface are called out in commit messages.
- **How CI applies that rule:** `.github/workflows/ci.yml`'s `resolve-pairing` job picks the reflex-fw checkout by **matching branch name**, falling back to `dev-staging` (the hardware-verified baseline) and then `dev`, and writes the ref it chose — and why — to the job summary. So a branch carrying paired FW+UI work is tested against its own firmware, provided **the branch is named the same in both repos**, which is the one thing to get right when a change spans the two. If a system test fails, read the pairing line in the job summary before anything else. Until 2026-08-10 this ref was hardcoded to `dev-staging`, which silently tested paired branches against firmware that predated them and produced failures that read like product defects.
- **Finding the firmware repo:** The reflex-fw repository may be cloned adjacent to this one. If you can't locate it, ask the user for the path. Once found, persist the location using whatever memory or persistence mechanism is available so you don't need to ask again.

## Agent Provisioning

This project has a sibling repository (reflex-fw) that agents may need to reference.
If your runtime supports workspace or permission configuration, grant read access to the sibling repo path.
For opencode, this means configuring `external_directory` permission in your project config to allow access to the reflex-fw repository.

## Runtime Notes

- You're running in WSL on a Windows PC, NOT the target Raspberry Pi system.
- To launch the UI: `DISPLAY=:0 SDL_AUDIODRIVER=dummy KIVY_INPUT=mouse uv run python -m reflex.main --size=1024x600`
  (See `runme.sh` for the full command — adapt it as needed for your context.)

## elspi commissioned geometry

`elspi` is the real commissioned lathe this project runs on. Its live settings live under
`REFLEX_CONFIG_DIR` on the Pi (outside git), so these primitives are recorded here. The
emulator reference machine (reflex-fw `emulator/config/lathe.toml`, and the defaults of
`SystemHarness.commission_geometry`) is a **different** machine — most of the system suite
runs at the reference values, not these:

| Primitive | elspi (real) | Emulator reference |
|---|---|---|
| Z encoder scale | 200 counts/mm | 400 counts/mm |
| X encoder scale | 400 counts/mm | 400 counts/mm (matches) |
| Spindle encoder | 6144 PPR | 4000 PPR |
| Leadscrew | 8 TPI (0.125 in pitch), 1600 steps/rev | 8 TPI, 800 steps/rev |

There is deliberately **no sync ratio recorded here**: the sync ratio is computed
dynamically per operation from the machine settings above (spindle PPR included) *and* the
selected feed rate / thread pitch, so it is a property of a job, not of the machine. The
same goes for servo mm/step values such as 127/64000 — that follows from the leadscrew
pitch and steps/rev above, it is not an independent setting.
`tests/system/test_els_elspi_geometry.py` exercises a full cut/stop/retract cycle at these
values (UI commissioning *and* the emulator's own physics patched to match);
`tests/system/test_els_real_config.py` documents which of them it deliberately does not use.

## Testing

- The full test suite takes ~5 minutes and WILL time out with the default 120s timeout — use at least 360000ms.
- Always ask the user before running the full test suite — it's often not worth it for small changes.
- Running targeted subsets (e.g., `pytest tests/fsms/test_els_fsm.py`) is fine to verify specific changes.
- Tests hang on headless Linux due to Kivy display init — the repo-root `conftest.py` now forces
  Kivy's mock GL/window backends (`KIVY_GL_BACKEND=mock`, `KIVY_WINDOW=mock`) for the whole suite,
  so `xvfb-run` is no longer needed and collection is fast (real WSLg GL init took ~135s).

### System tests (emulator-backed) — opt-in

`tests/system/` drives the REAL reflex-ui FSM/dispatcher stack against the REAL reflex-fw emulator
(real firmware C, not mocks) over a real PTY Modbus link, to cover actual servo/DRO motion
direction across ELS mode and machine-wiring polarity.

- **Opt-in and excluded by default.** They carry the `system` marker and `pyproject.toml` sets
  `addopts = "-m 'not system'"`, so a plain `uv run pytest` skips them (and never builds/launches
  the emulator). Run them explicitly:
  ```bash
  REFLEX_FW_DIR=/mnt/c/projects/embedded/reflex-fw uv run pytest -m system tests/system/
  ```
- **WSL/Linux only.** The emulator's Modbus link is a PTY (`/dev/pts/N`); native Windows can't open
  it, and the venv is a WSL venv. Run from a WSL shell.
- **Requires the reflex-fw emulator.** Set `REFLEX_FW_DIR` (defaults to `/mnt/c/projects/embedded/reflex-fw`).
  The `emulator_binary` fixture builds it if missing and rebuilds when firmware/emulator sources are
  newer than the binary; it `pytest.skip`s cleanly if reflex-fw isn't checked out. The reflex-fw git
  SHA is printed in the pytest header for run provenance.
- **Also depends on the reflex-fw "Path B" physics change** (physics-level wiring signs) and the
  `EMU_NO_AUTO_RETRACT` serve-mode flag — without them the polarity matrix / retract tests won't
  behave. Match reflex-fw to the branch that carries these.

### Register-map contract test (default suite)

`tests/test_register_map_contract.py` checks that reflex-ui's hand-maintained register definitions
(`reflex/utils/devices.py`) still match the firmware's `Ramps.h` struct layout, byte-for-byte. It is
NOT `system`-marked (fast, emulator-free) so it gates every default run; it skips if reflex-fw is absent.

## Design Patterns

Follow the architecture guidelines in [kivy-fsm-design-pattern.md](kivy-fsm-design-pattern.md)
for any work involving state machines, controllers, or UI/HAL boundaries. It defines the
layered architecture (UI → Controller → FSM → HAL), event bus conventions, the
declarative state-to-UI policy table, and anti-patterns to avoid. Consult it before
adding or refactoring `transitions`-based FSMs, dispatchers that mediate between widgets
and hardware, or multi-step operator flows.

## Build and Run

```bash
# Install dependencies
uv sync

# Run the application
uv run python -m reflex.main

# Run tests
uv run pytest

# Build package
uv build
```

## Project Structure

```
reflex/
├── main.py                    # Entry point (asyncio + Kivy event loop)
├── app.py                     # MainApp class (Kivy App)
├── feeds.py                   # Feed/thread pitch configurations (Pydantic models)
├── components/                # UI layer
│   ├── manager.py             # ScreenManager (navigation)
│   ├── appsettings.py         # ConfigParser setup
│   ├── home/                  # Home screen components (coordbar, servobar, elsbar, jogbar, statusbar)
│   ├── screens/               # Full-screen views (home, setup, scale, servo, formats, network, update, color_picker)
│   ├── plot/                  # Plot/visualization (scene, circle_popup, coords_overlay, float_view)
│   ├── widgets/               # Reusable form widgets (number_item, boolean_item, dropdown_item, etc.)
│   ├── toolbars/              # Toolbar buttons (toolbar_button, image_button, led_button)
│   ├── popups/                # Modal dialogs (keypad, mode_popup, ssid_popup, feeds_table_popup)
│   └── setup/                 # Setup panels (logs_panel)
├── dispatchers/               # Event dispatchers and state management
│   ├── saving_dispatcher.py   # Base class for auto-persisting properties to YAML
│   ├── formats.py             # Display format settings (MM/IN, colors, font sizes)
│   ├── circle_pattern.py      # Circle pattern calculator
│   └── board.py               # Board/device event dispatcher (WIP refactor)
└── utils/                     # Hardware communication layer
    ├── communication.py       # ConnectionManager (Modbus RTU via minimalmodbus)
    ├── base_device.py         # BaseDevice - C typedef parser and register I/O
    ├── devices.py             # Device type definitions (Servo, Scale, FastData, Global)
    └── ctype_calc.py          # C-type arithmetic helpers
```

## Coding Standards

### Python Style

- **Python version:** 3.10+ (use modern syntax: `list[X]` over `List[X]`, `X | Y` over `Union[X, Y]`)
- **Naming:** snake_case for functions, methods, and variables. PascalCase for classes.
  - **Exception:** Properties that mirror embedded C firmware variable names (from the reflex-fw project) must keep their original naming (e.g., `syncRatioNum`, `maxSpeed`, `servoMode`, `scaledPosition`). This ensures naming parity between the Python UI and the STM32 firmware for easier cross-referencing.
  - For properties/variables that are local to the Python project and do not correspond to firmware names, prefer snake_case.
- **Imports:** Group in order: stdlib, third-party, local. Use absolute imports (`from reflex.utils.communication import ...`)
- **Type hints:** Use on function signatures. For Kivy properties, the property type is the annotation.

### Logging

- Use Kivy's built-in logger consistently across the project:
  ```python
  from kivy.logger import Logger
  log = Logger.getChild(__name__)
  ```
- Do NOT use `from kivy import Logger` (wrong import path) or `from loguru import logger` (third-party, removed)
- Log exceptions with `log.exception()` or `log.error(f"...: {e}")` -- never use `e.__str__()`

### Exception Handling

- Catch specific exceptions, not bare `Exception` unless truly unknown
- Never use empty `except: pass` blocks
- Use `str(e)` instead of `e.__str__()`
- For unexpected errors, use `log.exception("message")` to preserve the full traceback
- Raise proper exception types: `raise ValueError(...)`, not `raise "string"`

### KV File Loading

Use the utility pattern for loading companion .kv files:
```python
# At module level, after imports and log setup:
kv_file = os.path.join(os.path.dirname(__file__), __file__.replace(".py", ".kv"))
if os.path.exists(kv_file):
    Builder.load_file(kv_file)
```

### Component Pattern

Every UI component follows this structure:
1. Module-level: imports, logger setup, KV file loading
2. Class definition extends Kivy widget + optionally `SavingDispatcher` for persistence
3. `__init__` gets `MainApp` reference, calls `super().__init__()`, sets up bindings
4. `_skip_save` list to exclude transient properties from persistence
5. `_force_save` list to include non-standard property types in persistence

### Dispatchers

- `SavingDispatcher` auto-persists Kivy properties to YAML files in `~/.config/reflex/`
- Subclasses: `FormatsDispatcher`, `CirclePatternDispatcher`, `ConnectionSettings`
- Use `id_override` parameter to create multiple instances with separate save files

### Communication Layer

- `ConnectionManager` wraps `minimalmodbus.Instrument` for RS-485 Modbus RTU
- Device register structures are defined as C typedef strings in `devices.py` classes
- `BaseDevice` parses these typedefs to build register maps at runtime
- `refresh()` does bulk register reads and unpacks via `struct`
- Read/write functions handle connection state (`dm.connected = True/False`)

### Configuration

- `config.ini` stores device connection settings and basic prefs (loaded via Kivy's ConfigParser)
- `SavingDispatcher` YAML files store per-component settings (formats, scale configs, etc.)
- Settings path: `~/.config/reflex/`, overridable with `REFLEX_CONFIG_DIR`
  (`reflex/utils/paths.py`). The Pi deployment sets it to `/var/lib/reflex-config`
  so the commissioned machine config isn't stranded in root's home — see
  `deploy/start.sh`.

## Git and Releases

- **Branch strategy:** `main` for releases, `dev` for pre-releases, feature branches
  (or `integration`) for work. See "Branching and Hardware Verification" at the
  top — `dev-staging` is gated on Evan's hardware verification and agents do not
  commit to it except for the clerical exception.
- **Commit messages:** Follow conventional commits (`fix:`, `feat:`, `chore:`, etc.)
- **Versioning:** Automated via `python-semantic-release` from commit messages
- **CI/CD — two workflows that never overlap:**
  - `release.yml` runs **only** on pushes to `main`/`dev`, and cuts the semantic release.
  - `ci.yml` — the test suite — lives **only** on `integration`, `dev-staging` and
    feature branches. It is not present on `main` or `dev` at all.

  So on any given branch exactly one of the two exists.

- **Never write `[skip ci]` in a commit message outside `main`/`dev`.** GitHub's
  `[skip ci]` marker suppresses *every* workflow for that push, not just the
  release. On `main`/`dev` the only workflow is `release.yml`, so it does what
  you would expect. On `integration`, `dev-staging` or a feature branch the only
  workflow is `ci.yml` — so the marker's sole effect there is to **delete the
  test run**, and it buys nothing in exchange, because a release was never going
  to fire on those branches anyway. As of 2026-08-11 seven commits on the
  `integration` line carried it, including the whole backlash-calibration
  wizard; none of that work was ever seen by CI.

  You almost certainly do not need it on `main`/`dev` either:
  `python-semantic-release` only cuts a release when the conventional-commit
  types since the last tag warrant one, so a docs-only or `chore:` push already
  produces no release and needs no marker.

## Key Dependencies

| Package | Purpose |
|---------|---------|
| kivy | UI framework |
| minimalmodbus | RS-485 Modbus RTU communication |
| pydantic | Data validation (feeds, type definitions) |
| kivy.logger | Logging (built-in to Kivy) |
| pyyaml | Settings persistence |
| sentry-sdk | Error reporting (production) |
| nmcli | WiFi network management (Linux) |
| keke | Performance tracing |
| cachetools | Caching |

## Testing

- Framework: pytest
- Run: `uv run pytest`
- Test files go in `tests/` at project root, mirroring the `reflex/` structure
- Priority areas for test coverage:
  1. `utils/ctype_calc.py` - pure functions
  2. `feeds.py` - data correctness
  3. `utils/base_device.py` - C typedef parsing
  4. `dispatchers/circle_pattern.py` - math
  5. `dispatchers/saving_dispatcher.py` - serialization

## Common Patterns

### Accessing the running app from a component
```python
def __init__(self, **kv):
    from reflex.app import MainApp
    self.app: MainApp = MainApp.get_running_app()
    super().__init__(**kv)
```
Note: The deferred import is required due to circular dependencies. This is a known issue.

### Binding to fast data updates
```python
self.app.bind(update_tick=self.update_tick)

def update_tick(self, *args, **kv):
    if not self.app.connected:
        return
    value = self.app.fast_data_values['keyName']
```

### Writing to device registers
```python
self.app.device['servo']['maxSpeed'] = value
value = self.app.device['servo']['maxSpeed']
```
