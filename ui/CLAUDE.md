# CLAUDE.md - Project Standards for RCP (Rotary Controller Python)

## Project Overview

RCP is a Kivy-based DRO (Digital Read-Out) and single-axis controller UI for rotary tables.
It communicates with embedded hardware (STM32) over RS-485/Modbus RTU using `minimalmodbus`.
Target platforms: Raspberry Pi (primary), Linux, Windows, macOS.

## Build and Run

```bash
# Install dependencies
uv sync

# Run the application
uv run python -m rcp.main

# Run tests
uv run pytest

# Build package
uv build
```

## Project Structure

```
rcp/
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
  - **Exception:** Properties that mirror embedded C firmware variable names (from the rotary-controller-f4 project) must keep their original naming (e.g., `syncRatioNum`, `maxSpeed`, `servoMode`, `scaledPosition`). This ensures naming parity between the Python UI and the STM32 firmware for easier cross-referencing.
  - For properties/variables that are local to the Python project and do not correspond to firmware names, prefer snake_case.
- **Imports:** Group in order: stdlib, third-party, local. Use absolute imports (`from rcp.utils.communication import ...`)
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
TODO: Extract this to a shared utility function.

### Component Pattern

Every UI component follows this structure:
1. Module-level: imports, logger setup, KV file loading
2. Class definition extends Kivy widget + optionally `SavingDispatcher` for persistence
3. `__init__` gets `MainApp` reference, calls `super().__init__()`, sets up bindings
4. `_skip_save` list to exclude transient properties from persistence
5. `_force_save` list to include non-standard property types in persistence

### Dispatchers

- `SavingDispatcher` auto-persists Kivy properties to YAML files in `~/.config/rotary-controller-python/`
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
- Settings path: `~/.config/rotary-controller-python/`

## Git and Releases

- **Branch strategy:** `main` for releases, `dev` for pre-releases, feature branches for work
- **Commit messages:** Follow conventional commits (`fix:`, `feat:`, `chore:`, etc.)
- **Versioning:** Automated via `python-semantic-release` from commit messages
- **CI/CD:** GitHub Actions workflow on push to `main`/`dev` triggers semantic release

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
- Test files go in `tests/` at project root, mirroring the `rcp/` structure
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
    from rcp.app import MainApp
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

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current
