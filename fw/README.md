# Reflex firmware (`fw/`)

The **real-time half** of [Reflex](../README.md): STM32F411 firmware providing
encoder capture, step generation, and all motion control the UI must never be
trusted with — spindle-synchronized feed, the electronic stop, retract, jog
profiles, and thread-phase re-sync all execute here, in a 50 kHz ISR with
FreeRTOS tasks alongside. The UI talks to it as a Modbus RTU master over
RS-485; the register contract is defined in `Core/Inc/Ramps.h` and mirrored by
`../ui/reflex/utils/devices.py`, guarded by `protocolVersion`.

---

## Build and flash

Build and flash on **the machine with the ST-Link plugged into it**, for this
project the Pi that also runs the UI. `git rev-parse HEAD` there *is* what is
flashed.

### Requirements

```bash
sudo apt install gcc-arm-none-eabi cmake build-essential openocd
```

Plus an ST-Link v2 on USB. The `openocd` package installs udev rules granting
the `plugdev` group access, so flashing needs no `sudo`.

### Flashing

A **new board** gets provisioned once, over SWD:

```bash
./scripts/provision.sh
```

It builds both stages, programs the bootloader into sector 0 and the
application into the RUN slot, erases the journal and spare slots, and records
what it did.

After that the ST-Link is needed only for virgin boards, option bytes and
recovery. Updates go through the bootloader over the RS-485 link the UI
already holds:

```bash
python3 scripts/modbus-flash.py build-slot/reflex-fw.bin --port /dev/ttyUSB0
```

The slotted application is built with `-DREFLEX_APP_BASE=0x08020000`;
`--identity` reads back the running stage and git rev. Design and register map:
`bootloader/README.md` and `../decisions/els-modbus-register-map.md`.

`./scripts/flash.sh` is the **legacy** path for a board without the
bootloader: it writes the application at `0x08000000`. It writes only over a
legacy application it recognizes or an erased sector 0; the bootloader, or
anything it cannot identify, is a refusal (`--force-legacy` overrides, and
destroys whatever was there). The rules are in `scripts/lib/sector0.py`.

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

It rebuilds every time by default; `--no-build` opts out.

`--host NAME` builds here and flashes there over SSH, with a copy and a
checksum, for a probe host that cannot build. Prefer the local path.

Release and diagnostic builds live in separate directories: `build/` for
release, one per `--diag=NAME`. A diagnostic build compiles in **one**
measurement probe and must **never** reach `dev-staging`, `dev` or `main`; the
`elsStop.diagSchema` register says which probe is running (`0` = none). The
probes are documented in **[DIAG.md](DIAG.md)**.

Every flash is recorded in `~/firmware/flashed.json` on the probe host, one
JSON object per line: UTC timestamp, variant, git revision, whether the tree
was dirty, and an MD5 of what was written. `flash.sh`, `provision.sh` and
`modbus-flash.py` append to it, `modbus-flash.py` (and so the in-app updater)
only once the board reports the new revision running.

### Underneath

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j$(nproc)
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
        -c 'transport select swd' -c 'program build/reflex-fw.elf verify reset exit'
```

OpenOCD rather than `st-flash`: it takes the ELF directly, so there is no base
address to get wrong, and it is more tolerant of ST-Link **clones**.

> `raspberrypi5.cfg` holds an untested `linuxgpiod` config for bitbanging SWD
> from a Pi 5's GPIO; read its header before trusting it.

---

## Lathe emulator

A native Linux emulator compiles the real firmware sources against a
HAL/FreeRTOS shim and simulates the lathe (spindle, leadscrew, carriage with
half nut, cross-slide). It serves Modbus RTU on a PTY pair and a TCP socket, so
the unmodified UI connects as if to real hardware. It also hosts the firmware
test suite (`emulator/test/`), which drives the real ISR; run it with `ctest`
from `emulator/build`.

```bash
cd emulator
cmake -B build
cmake --build build
./build/lathe-emulator config/lathe.toml
```

---

## Hardware configuration

`reflex.ioc` is the STM32CubeMX configuration; the memory layout is in
`STM32F411CEUX_FLASH.ld` and `STM32F411CEUX_RAM.ld`. Board design and
system-level hardware: see the [top-level README](../README.md).

### Recovery is SWD only

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

## License

MIT — see `LICENSE`. The STM32 drivers and CMSIS under `Drivers/` and FreeRTOS
under `Middlewares/` keep their own licenses, in those directories.
