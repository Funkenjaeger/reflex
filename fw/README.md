# Reflex firmware (`fw/`)

The **real-time half** of [Reflex](../README.md): STM32F411 firmware providing
encoder capture, step generation, and all motion control the UI must never be
trusted with — spindle-synchronized feed, the electronic stop, retract, jog
profiles, and thread-phase re-sync all execute here, in a 100 kHz ISR with
FreeRTOS tasks alongside. The UI talks to it as a Modbus RTU master over
RS-485; the register contract is defined in `Core/Inc/Ramps.h` and mirrored by
`../ui/reflex/utils/devices.py`, guarded by `protocolVersion`.

---

## ⚙️ Structure

* **STM32CubeMX** hardware configuration (`.ioc` included)
* Modular firmware with FreeRTOS support
* Programmed over SWD with an ST-Link V2
* Optimized for high-speed encoder + stepper/servo motor control
* Native FW+lathe **emulator** for hardware-free testing (below)

---

## 🛠️ Build & Flash

Build and flash on **the machine with the ST-Link plugged into it**. For this
project that is the Pi that also runs the UI, which is perfectly capable of
compiling the firmware — and doing both in one place means the binary on the
target cannot be a different revision from the checkout in front of you.
`git rev-parse HEAD` there *is* what is flashed.

### Requirements

```bash
sudo apt install gcc-arm-none-eabi cmake build-essential openocd
```

Plus an ST-Link v2 on USB. The `openocd` package installs udev rules granting
the `plugdev` group access, so flashing needs no `sudo`.

### Build and flash

```bash
./scripts/flash.sh
```

That builds, flashes over SWD, and records what it did.

> **Power-cycle the controller after flashing.** A reset alone does not reliably
> start the new firmware on this board. openocd's `Verified OK` confirms the
> flash *contents*, not what the core is *executing* — so programming and
> verification both report success while the machine keeps running the previous
> firmware, silently and with no error anywhere. Confirm from the UI log
> that `Firmware register protocol version N (expected N)` matches what you
> flashed before believing it took.

```bash
./scripts/flash.sh --diag=NAME   # with a diagnostic probe compiled in
./scripts/flash.sh --dry-run     # everything except the write
./scripts/build.sh               # build only, no flashing
./scripts/build.sh --diag        # lists the available probes
```

**It rebuilds every time by default.** `--no-build` opts out. A stale binary is
the easiest mistake to make and the hardest to notice; rebuilding costs seconds.

**`--host NAME` builds here and flashes there** over SSH, for the case where the
probe host genuinely cannot build. It adds a copy and a checksum — a transfer
that can silently truncate is worth verifying before it is written to the
controller of a machine with moving parts. Prefer the local path: it makes that
whole failure mode, and the version ambiguity that comes with it, not exist.

**Release and diagnostic builds live in separate directories.** `build/` is the
release firmware; each `--diag=NAME` build gets its own directory, so the flag
can never depend on what the last `cmake` invocation happened to say. A
diagnostic build compiles in **one** measurement probe and must **never** reach
`dev-staging`, `dev` or `main`. At runtime the `elsStop.diagSchema` register says
which probe is running (`0` = none), and the UI logs it at connect.

Which probes exist, what they measure, how to add or retire one, and why only one
can be compiled in at a time: **[DIAG.md](DIAG.md)**. `./scripts/build.sh --diag`
with no name lists them.

**Every flash is recorded** in `~/firmware/flashed.json` on the probe host: UTC
timestamp, variant, git revision, whether the tree was dirty, and the ELF's MD5.
Working out what firmware was on this lathe once took an afternoon of forensics
across build-artifact timestamps; this makes it a lookup.

### Underneath

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j$(nproc)
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
        -c 'transport select swd' -c 'program build/reflex-fw.elf verify reset exit'
```

OpenOCD rather than `st-flash`: it takes the ELF directly (load addresses come
from the headers, so there is no `--format`/base-address to get wrong), it is
markedly more tolerant of ST-Link **clones**, and it is the same tool that would
drive a GPIO-bitbanged probe if the boards are ever respun without a dongle.

> Bitbanging SWD from a Raspberry Pi's GPIO used to be documented here via
> `raspberry.cfg`. It has been removed: that config uses OpenOCD's
> `bcm2835gpio` driver, which memory-maps the GPIO block on the SoC — and the
> Pi 5 moved GPIO onto the RP1 southbridge, so the driver has nothing to map and
> cannot work there at all. It was never used in practice. `raspberrypi5.cfg`
> holds an untested `linuxgpiod` equivalent for whenever the boards get respun;
> read its header before trusting it.

---

## 🖥️ Lathe Emulator

A native Linux emulator is included for hardware-free firmware testing. It compiles the real firmware sources (`Ramps.c`, `Modbus.c`, `Scales.c`, `UARTCallback.c`) against a HAL/FreeRTOS shim layer and simulates lathe physics — spindle with inertia, leadscrew, carriage with half-nut engagement, and cross-slide. The emulator exposes Modbus RTU via PTY pair and TCP socket so the unmodified Python GUI can connect as if talking to real hardware.

A two-pane ANSI terminal dashboard with sparklines provides live visualization, with keyboard controls for spindle RPM, manual axis movement, half-nut engagement, and more. All parameters are configurable via TOML file.

The emulator also hosts the firmware test suite (`emulator/test/`), which drives
the real ISR directly — run it with `ctest` from `emulator/build`.

### Emulator Build & Run

```bash
cd emulator
cmake -B build
cmake --build build
./build/lathe-emulator config/lathe.toml
```

---

## 🔧 Hardware Configuration

* `.ioc` file for use with STM32CubeMX included
* Pin assignments for encoder, buttons, LEDs, SWD, etc. reviewed and tested
* Memory layout defined by `STM32F411CEUX_FLASH.ld` and `STM32F411CEUX_RAM.ld`

Board design and system-level hardware: see the [top-level README](../README.md).

### ⚠️ Recovery is SWD only — the ROM bootloader is not a path on this board

**Use ST-Link/SWD. Do not plan a recovery procedure around BOOT0.**

The STM32 system (mask ROM) bootloader is unreachable on this hardware, and it
should stay that way until a respin changes the pinout:

* **BOOT0 is not connected.** There is no jumper, pad or test point for it, and
  the STM32F4 has no internal pull on BOOT0 — AN4488 §5.2 states an external
  connection is *required*. A floating BOOT0 is not a supported way to select
  the boot source.
* **The package is UFQFPN48** — leadless, 0.5 mm pitch, pads tucked under the
  package edge. There is nothing to clip a wire to.
* **Even if BOOT0 were driven high, PA9 is the conflict.** The mask ROM puts
  USART1 on PA9/PA10 and drives PA9 as TX. Here PA9 is `ENC1B`
  (`reflex.ioc`: `PA9.Signal=S_TIM1_CH2`), fed from the 74VHC9151FT buffer's
  output. That would put two push-pull outputs on one net.

**Unplugging the encoder does not make that safe** — it only changes what
reaches the buffer's *input*. The buffer keeps driving PA9 for as long as the
board is powered. Isolating PA9 means lifting the buffer's output pin or cutting
the trace, which is not a field procedure. Whether the contention would actually
damage either driver has not been measured; it is unsupported either way.

**For a respin:** BOOT0 only becomes useful if ENC1 moves off PA9 first — e.g.
ENC1 onto TIM5 (PA0/PA1), which frees PA9/PA10 for the ROM's USART1 pair. Wiring
BOOT0 to a jumper *without* that reshuffle builds the conflict above, not a
recovery path.

---

## 📄 License

MIT — see `LICENSE`.
