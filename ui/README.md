# Reflex UI (`ui/`)

The **operator half** of [Reflex](../README.md): a Kivy touchscreen DRO/ELS
app that runs on a Raspberry Pi at the machine (or any desktop for
development) and drives the STM32 controller in [`../fw/`](../fw/) as an
RS-485 Modbus RTU master. Workflow, configuration, validation, and display
live here; everything real-time lives in the firmware.

Screenshots and a demo video are in the [top-level README](../README.md).

---

## 🚀 UI features

* Responsive touch-capable UI built with **Kivy**
* **Configurable axes** — add/remove axes, assign hardware scale inputs, apply
  transforms (identity, scaling, weighted sum, angle cos/sin)
* ELS operator flows: threading wizard, electronic stop and retract targets,
  feeds/threads table
* Customizable display: fonts, colors, digit formats (metric/imperial/angle)
* **Contextual help** — info button on every setting field with documentation
  and examples
* Works on Raspberry Pi 3/4/5, Windows, macOS, and Linux

---

## 🎯 Software requirements

* Python 3.10+
* [`uv`](https://docs.astral.sh/uv/) package manager

---

## ⚙️ Installation & Setup

### 1. Clone the repository

```bash
git clone https://github.com/Funkenjaeger/reflex.git
cd reflex/ui
```

### 2. Install `uv`

Linux/macOS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

For Windows, see the [uv installation docs](https://docs.astral.sh/uv/getting-started/installation/).

### 3. Install dependencies

```bash
uv sync
```

### 4. Run the app

```bash
uv run python -m reflex.main
```

### 5. Run tests

```bash
uv run pytest
```

(The default suite includes the cross-half register-map contract test against
`../fw/Core/Inc/Ramps.h`; the emulator-backed system suite runs with
`uv run pytest -m system tests/system`.)

---

## 💻 Platform-specific Notes

### Windows/macOS/Linux

* Python >= 3.10
* Virtual environment managed automatically by `uv`
* Ensure your RS-485 adapter is accessible (check serial port permissions on Linux/macOS)
* On headless Linux (e.g. WSL), you may need environment variables and flags: `DISPLAY=:0 SDL_AUDIODRIVER=dummy KIVY_INPUT=mouse uv run python -m reflex.main --size=1024x600`

### Raspberry Pi & OSPI

* Install an SD card image from the [OSPI project](https://github.com/bartei/ospi)
* OSPI ships with RCP pre-installed in `/root/rotary-controller-python/`. Reflex UI **must** be manually installed to replace it:

  ```bash
  # Stop the existing RCP service
  sudo systemctl stop rotary-controller

  # Clone the reflex monorepo
  cd /root
  git clone https://github.com/Funkenjaeger/reflex.git
  cd reflex/ui
  uv sync

  # Update the systemd service unit to point to the new path and module
  ```

* View logs:

  ```bash
  journalctl -u reflex
  journalctl -xeu reflex
  tail -n +1 /var/log/kivy*
  ```

---

## 📂 Project Structure

```
reflex/
├── main.py                    # Entry point (asyncio + Kivy event loop)
├── app.py                     # MainApp class
├── feeds.py                   # Feed/thread pitch configurations
├── help/                      # Contextual help documents (markdown)
├── fonts/                     # Font files
├── pictures/                  # Image assets
├── sounds/                    # Audio assets (beep, snap, stop)
├── components/                # UI layer
│   ├── manager.py             # ScreenManager (navigation)
│   ├── appsettings.py         # ConfigParser setup
│   ├── home/                  # Home screen (coordbar, servobar, elsbar, jogbar, statusbar, mode layouts)
│   ├── screens/               # Full-screen views (home, setup, scale, servo, formats, axes, ELS, etc.)
│   ├── widgets/               # Reusable form widgets (number_item, boolean_item, dropdown_item, etc.)
│   ├── popups/                # Modal dialogs (keypad, help, feeds table, mode, etc.)
│   ├── toolbars/              # Toolbar buttons (led_button, toolbar_button, etc.)
│   ├── plot/                  # Plot/visualization (scene, popups, overlays)
│   └── setup/                 # Setup panels (logs, profiling)
├── dispatchers/               # Event dispatchers and state management
│   ├── saving_dispatcher.py   # Auto-persisting properties to YAML
│   ├── formats.py             # Display format settings
│   ├── circle_pattern.py      # Circle pattern calculator
│   ├── line_pattern.py        # Line pattern calculator
│   ├── rect_pattern.py        # Rectangle pattern calculator
│   ├── axis.py                # Axis configuration
│   ├── axis_transform.py      # Axis transform settings
│   ├── els.py                 # ELS configuration
│   ├── input.py               # Input configuration
│   ├── servo.py               # Servo configuration
│   └── board.py               # Board/device event dispatcher
├── fsms/                      # State machines
│   ├── els_fsm.py             # ELS state machine
│   ├── els_stop_hal.py        # ELS stop hardware abstraction
│   ├── els_mode_watch.py      # Firmware-mode census/divergence sampler
│   ├── fsm_event_bus.py       # Event bus for FSM communication
│   ├── ui_controller.py       # UI controller (mediates UI and FSM)
│   └── ui_fsm.py              # UI state machine
└── utils/                     # Hardware communication layer
    ├── communication.py       # ConnectionManager (Modbus RTU)
    ├── base_device.py         # C typedef parser and register I/O
    ├── devices.py             # Device type definitions
    ├── ctype_calc.py          # C-type arithmetic helpers
    ├── kv_loader.py           # KV file loading utility
    └── platform.py            # Platform detection utilities
```

---

## 🛠️ Troubleshooting

* **Serial issues**: Verify RS-485 wiring, correct serial port, and permissions
* **Service failures (Pi)**: Check `journalctl` logs and Kivy log files under `/var/log/`
* **Display issues**: Adjust font size and display format in the Formats setup screen

---

## 📚 Internal docs

* **FSM architecture pattern:** [`kivy-fsm-design-pattern.md`](kivy-fsm-design-pattern.md)
* **ELS shoulder-stop orchestration:** [`ELS_STOP.md`](ELS_STOP.md)
* **Repo structure ADR:** [`docs/decisions/repo-structure-monorepo.md`](docs/decisions/repo-structure-monorepo.md)

---

## 📄 License

MIT — see `LICENSE`.
