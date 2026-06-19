# Reflex UI

[![Discord](https://img.shields.io/discord/1386014070632878100?style=social)](https://discord.gg/EDtgj7Yayr) [![Shop at Provvedo](https://img.shields.io/badge/Shop-Provvedo-blue?logo=shopify&style=flat-square)](https://www.provvedo.com/shop)

A **Kivy-based Digital Read-Out (DRO) and single-axis controller UI** for rotary tables and similar devices, designed to run on Raspberry Pi or desktop environments (Windows, macOS, Linux). Interfaces via RS-485/Modbus RTU with a dedicated STM32-based control board.

🛒 **Purchase all boards from our shop:** [Provvedo Shop](https://www.provvedo.com/shop)

---

## 🚀 Features

* Responsive touch-capable UI built with **Kivy**
* Communicates over **RS-485 Modbus RTU** with an STM32 controller ([reflex-fw](https://github.com/Funkenjaeger/reflex-fw))
* **Configurable axes** — add/remove axes, assign hardware scale inputs, apply transforms (identity, scaling, weighted sum, angle cos/sin)
* **Electronic Lead Screw (ELS)** mode for synchronized threading and power feed on manual lathes
* **Sync mode** with configurable gear ratios for spindle-synchronized movement
* **Circle pattern calculator** for bolt hole patterns
* Customizable display: fonts, colors, digit formats (metric/imperial/angle)
* **Contextual help** — info button on every setting field with documentation and examples
* Works on Raspberry Pi 3/4/5, Windows, macOS, and Linux

---

## 🎯 Requirements

* **Hardware**

  * Rotary controller board (STM32 firmware from [reflex-fw](https://github.com/Funkenjaeger/reflex-fw))
  * RS-485 interface (e.g. via Power Hat)
  * Raspberry Pi 3/4/5 for Pi deployments

* **Software**

  * Python 3.10+
  * [`uv`](https://docs.astral.sh/uv/) package manager

---

## ⚙️ Installation & Setup

### 1. Clone the Repository

```bash
git clone https://github.com/Funkenjaeger/reflex-ui.git
cd reflex-ui
```

### 2. Install `uv`

Install `uv` (Linux/macOS):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

For Windows, see the [uv installation docs](https://docs.astral.sh/uv/getting-started/installation/).

### 3. Install Dependencies

```bash
uv sync
```

### 4. Run the App

```bash
uv run python -m reflex.main
```

### 5. Run Tests

```bash
uv run pytest
```

---

## 💻 Platform-specific Notes

### Windows/macOS/Linux

* Python >= 3.10
* Virtual environment managed automatically by `uv`
* Ensure your RS-485 adapter is accessible (check serial port permissions on Linux/macOS)

### Raspberry Pi & OSPI

* Install an SD card image from the [OSPI project](https://github.com/bartei/ospi)
* OSPI ships with RCP pre-installed in `/root/rotary-controller-python/`. Reflex UI **must** be manually installed to replace it:

  ```bash
  # Stop the existing RCP service
  sudo systemctl stop rotary-controller

  # Clone reflex-ui
  cd /root
  git clone https://github.com/Funkenjaeger/reflex-ui.git
  cd reflex-ui
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
├── components/                # UI layer
│   ├── home/                  # Home screen (coordbar, servobar, elsbar, statusbar)
│   ├── screens/               # Full-screen views (setup, scale, servo, formats, etc.)
│   ├── widgets/               # Reusable form widgets with help button support
│   ├── popups/                # Modal dialogs (keypad, help, feeds table, etc.)
│   ├── toolbars/              # Toolbar buttons
│   └── plot/                  # Plot/visualization
├── dispatchers/               # Event dispatchers and state management
│   ├── saving_dispatcher.py   # Auto-persisting properties to YAML
│   ├── formats.py             # Display format settings
│   ├── circle_pattern.py      # Circle pattern calculator
│   └── board.py               # Board/device event dispatcher
└── utils/                     # Hardware communication layer
    ├── communication.py       # ConnectionManager (Modbus RTU)
    ├── base_device.py         # C typedef parser and register I/O
    └── devices.py             # Device type definitions
```

---

## 🛠️ Troubleshooting

* **Serial issues**: Verify RS-485 wiring, correct serial port, and permissions
* **Service failures (Pi)**: Check `journalctl` logs and Kivy log files under `/var/log/`
* **Display issues**: Adjust font size and display format in the Formats setup screen

---

## 📚 References & Related Projects

* **Firmware & hardware:** [reflex-fw](https://github.com/Funkenjaeger/reflex-fw)
* **PCB design & BOM:** [rotary-controller-pcb](https://github.com/bartei/rotary-controller-pcb)
* **OSPI OS:** [ospi](https://github.com/bartei/ospi) — ships with RCP pre-installed; see deployment notes below for replacing it with Reflex UI

### Internal docs

* **FSM architecture pattern:** [`kivy-fsm-design-pattern.md`](kivy-fsm-design-pattern.md)
* **ELS shoulder-stop orchestration:** [`ELS_STOP.md`](ELS_STOP.md)

---

## 🤝 Contributing

Contributions are welcome! Please:

* Open issues for bugs or feature requests
* Submit pull requests or improvements
* Help with testing, documentation, porting new features

---

## 🏆 Support

Join our [Discord community](https://discord.gg/EDtgj7Yayr) for support, collaboration, and updates.

---

## 📄 License

Licensed under MIT. See `LICENSE` for full terms.