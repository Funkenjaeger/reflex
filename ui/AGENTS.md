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

## The firmware half (`../fw`)

This app is tightly coupled with the STM32 firmware in `../fw` — the same repository since the 2026-08-17 monorepo weld.

- **Interface:** RS-485 Modbus RTU — the UI reads/writes holding registers that map directly to the firmware's shared data struct
- **Version compatibility:** a single commit now spans both halves, so a checkout is self-consistent by construction; cross-half changes affecting the Modbus register interface are still called out in commit messages. The old cross-repo branch-name pairing resolver is gone — CI and the system tests always build the in-repo firmware. (The DEPLOYED pair on the machine can still lag, which is what the `protocolVersion` check at connect is for.)

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
  uv run pytest -m system tests/system/
  ```
- **WSL/Linux only.** The emulator's Modbus link is a PTY (`/dev/pts/N`); native Windows can't open
  it, and the venv is a WSL venv. Run from a WSL shell.
- **Requires the fw/ emulator.** The in-repo `../fw` is the default; set `REFLEX_FW_DIR` only to point at a different checkout.
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

The same file also checks the **diagnostic schema registry**, which is a second
cross-repo contract in the same header — see below.

## Diagnostic probes — the UI half

**reflex-fw's `DIAG.md` is the reference.** It owns the probe registry, the
one-probe-at-a-time rule, and the procedure for adding or retiring one. That is
deliberately not duplicated here: a second copy of a registry is a registry that
drifts. This section covers only what lives in *this* repo.

A firmware **probe** writes a 64-register scratchpad reserved at the tail of
`elsStop_t`. `elsStop.diagSchema` names which probe is compiled in; `0` means
none, which is every release build.

`reflex/fsms/els_diag.py` (`ElsDiagRecorder`) is the reader. Read its module
docstring before touching it — the three properties it lists are load-bearing,
particularly that it is **inert against release firmware**: it interrogates
`diagSchema` once per connection and, finding `0` or an id it does not know,
issues no further reads at all.

**Adding a probe means touching three things here, not one.** Mirroring the id
alone is the mistake, and it fails at the lathe rather than in CI:

| Where | What |
|---|---|
| `reflex/utils/devices.py` | the `ELS_DIAG_SCHEMA_*` constant |
| `reflex/fsms/els_diag.py` — `KNOWN_SCHEMAS` | **the one that bites.** The recorder refuses any schema outside this set, logs *"which this UI does not recognise"*, and goes dormant. Firmware fine, flash fine, nothing recorded. |
| `reflex/fsms/els_diag.py` — `SCHEMAS_WITH_END_REASON` | only if the probe publishes `diagEndReason` |

`test_register_map_contract.py` now enforces the first two: every live firmware
probe must be in `KNOWN_SCHEMAS`, ids must agree by name *and* value, and the UI
must not recognise a schema the firmware never defined. Until 2026-08-16 nothing
did, and the two registries could disagree with CI green.

**Retired schemas stay in `KNOWN_SCHEMAS` on purpose.** The firmware refuses to
*build* a retired probe, but every recorded `.jsonl` line carries its own schema,
so captures taken under an older probe must stay readable. Retired ids are never
deleted and never reissued.

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
- **Commit messages:** Follow conventional commits (`fix:`, `feat:`, `chore:`, etc.).
  The release notes are generated from them, so the log is what describes a release.
- **Versioning:** ONE version for the whole monorepo — the repo-root `VERSION`
  file, mirrored into `ui/pyproject.toml`'s `version` by the release workflow.
  Both halves carry the same version even when only one changed: `v1.4.0` names
  a known-good FIRMWARE + UI PAIR, which is the point of the monorepo
  (`docs/decisions/repo-structure-monorepo.md`). Do not bump either by hand.

- **CI/CD — CORRECTED 2026-08-22 for the monorepo. The split-era rule that
  "on any given branch exactly one of the two exists" is no longer true, and
  reasoning from it will mislead you:**
  - `fw.yml` / `ui.yml` / `system.yml` — the test suites — run on **every**
    branch, path-filtered by subtree. That is how `dev-staging` and `dev` were
    verified green before the 2026-08-22 promotions, which the old model could
    not have done.
  - `release.yml` runs **only when dispatched by hand**, and only from `main`
    or `dev`. It refuses a pre-release version on `main` and a final version on
    `dev`, refuses a tag that already exists, and refuses to publish a firmware
    image carrying diagnostic probe symbols.

- **`[skip ci]` is still not for you.** It suppresses *every* workflow for that
  push, and since the test suites now run on every branch, the marker's only
  effect anywhere is to delete the test run. It no longer buys anything even on
  `main`/`dev`, because a release is never triggered by a push — it is
  dispatched. The single legitimate use is the release workflow's own version-bump
  commit, which it writes itself.

  As of 2026-08-11 seven commits on the `integration` line carried it, including
  the whole backlash-calibration wizard; none of that work was ever seen by CI.

- **DO NOT QUOTE THE MARKER IN A COMMIT MESSAGE — not even to explain it.**
  GitHub scans the entire pushed commit message, body included, and any
  occurrence of `[skip ci]` / `[ci skip]` / `[no ci]` suppresses every workflow
  for that push. Discussing the marker in prose is indistinguishable from using
  it. On 2026-08-22 the commit that RETIRED this habit quoted the marker in its
  own body while explaining the trap, and so became the one commit in the
  release-flow work that CI never ran — caught only because someone went
  looking for a green tick that was never going to appear. Write it as "the CI
  skip marker" in commit messages; quote it freely in files like this one,
  which are never scanned.

- **python-semantic-release was retired 2026-08-22.** Its config had accumulated
  v7 keys that PSR 10 silently ignores, so options read like live settings while
  doing nothing, and nobody could say from the file what a push would do.

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
