# Installing on a Pi

Getting Reflex onto the Raspberry Pi at the machine, from a blank SD card to a
UI that boots on its own.

!!! warning "This is the manual path, and it is deliberately explicit"
    There is no installer yet. Every step below is a command you run, and the
    page exists so that you do not have to work any of it out for yourself. A
    scripted, idempotent provisioning path is planned; until it lands, this is
    the procedure — it is the same one the developer's own machine was built
    with, not an idealized version of it.

Budget an hour or so, most of which is the Pi compiling dependencies.

---

## What you need

| | |
|---|---|
| **Controller board** | STM32F411, compatible with the [rotary-controller-pcb](https://github.com/bartei/rotary-controller-pcb) design. Ready-made boards are sold by [Provvedo](https://www.provvedo.com) (no affiliation). |
| **Raspberry Pi** | 3, 4 or 5, with an RS-485 link to the board — a Power Hat is the usual way. |
| **Display** | 1024×600 touchscreen. That resolution is what the launch wrapper asks for and what the layouts are drawn against. |
| **ST-Link V2** | A USB SWD dongle, for flashing the controller. Clones work; OpenOCD is tolerant of them. |
| **A network connection to the Pi** | Everything here is done over SSH. You do not need a keyboard on the Pi. |

Encoders on the spindle and axes, and a step/dir servo or stepper on the
leadscrew, are the machine side of the job and are out of scope for this page.

---

## Step 1 — Start from the OSPI image

Write the SD card image from the [OSPI project](https://github.com/bartei/ospi)
and boot the Pi from it.

**Why start there rather than from plain Raspberry Pi OS.** OSPI is the image
built for the upstream project Reflex forked from, and it arrives with the
awkward parts already solved: the SDL2/EGL/DRM libraries Kivy renders through,
a display that comes up without an X server, and a UART freed from the serial
console so the RS-485 link can use it. Reflex uses the same Kivy and the same
serial port, so all of that carries over and you install no apt packages for
the UI at all.

What you should find on a freshly written image:

| | |
|---|---|
| **OS** | Raspbian 13 (trixie). 64-bit kernel, **32-bit (armhf) userland** — harmless, but it is why `uv` fetches an `armv7` build. |
| **Login user** | `default`, with `sudo` (it will prompt for a password). |
| **Python** | 3.13. Reflex needs ≥ 3.10. |
| **The app** | Upstream **RCP** in `/rotary-controller-python`, started at boot by `rcp.service`, which runs `/start.sh` **as root**. |
| **Display** | No X server, no `DISPLAY`. Kivy draws straight to KMS/DRM, which is also why the service runs as root. |
| **Serial** | `enable_uart=1`, no serial console in `cmdline.txt`, and `/dev/serial0` symlinked to the hardware UART. |

!!! note "Check rather than trust"
    These were read off a running machine on 2026-08-30. That machine began as
    an OSPI image and has been modified since, and the image itself moves.
    Treat the table as what to expect and verify, not a guarantee:

    ```bash
    cat /etc/os-release; dpkg --print-architecture; python3 --version
    systemctl is-enabled rcp.service; ls -l /dev/serial0
    ```

**RCP is left on disk.** Nothing below deletes it, and the last section of this
page puts it back in one command if you want to return to it.

---

## Step 2 — Reach the Pi over SSH

Everything from here on is run **on the Pi**, over SSH. You need three things
first, and none of them are Reflex-specific:

- **The Pi on your network.** Ethernet needs nothing. For WiFi, the easiest
  route is to set it when you write the card — Raspberry Pi Imager's advanced
  options will preseed the network, the hostname and SSH into the image.
- **SSH enabled.** If it is not, an empty file named `ssh` in the boot
  partition turns it on at the next boot; you can create that from the machine
  you wrote the card with.
- **The login.** The account is `default`. The **password is the OSPI image's,
  not something Reflex sets** — check the [OSPI
  project](https://github.com/bartei/ospi) for the image you wrote, and change
  it with `passwd` once you are in.

```bash
ssh default@raspberrypi.local
```

If mDNS does not resolve — it often will not from WSL — find the address from
your router or with `ping raspberrypi.local` on the host machine, and use the
IP instead.

---

## Step 3 — Put the source on the Pi

```bash
mkdir -p ~/projects
git clone https://github.com/Funkenjaeger/reflex.git ~/projects/reflex
```

This lands the monorepo — the UI in `ui/` and the controller firmware in `fw/`
— at `/home/default/projects/reflex`. **Use that path.** The systemd unit
shipped in the repo names it, so putting the checkout somewhere else means
editing the unit as well.

Cloning gives you the default branch, which is the current tested state. To pin
a specific release instead:

```bash
# monorepo releases; ui-* and fw-* tags are pre-weld archives
git tag -l 'v*'
git checkout v1.1.0
```

The tree is owned by `default`, not root, so you can pull and edit without
`sudo` later. The service runs as root, which can read it regardless.

---

## Step 4 — Build the Python environment

Reflex uses [uv](https://docs.astral.sh/uv/) to build a virtual environment
from the lockfile.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # lands in ~/.local/bin/uv
cd ~/projects/reflex/ui
~/.local/bin/uv sync
```

`pyproject.toml` lives in `ui/`, not at the repo root, which is why the `cd`
matters. This is the slow step — it is compiling wheels on a Pi.

It creates `~/projects/reflex/ui/.venv`, which the launch wrapper activates. No
`sudo`, and no apt packages: the system libraries Kivy needs came with the
image.

---

## Step 5 — Make Reflex the boot application

The repo ships both pieces — a launch wrapper and a systemd unit — so this is
installation, not authoring.

```bash
chmod +x ~/projects/reflex/ui/deploy/start.sh
sudo cp ~/projects/reflex/ui/deploy/reflex-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
# stop upstream RCP -- this leaves it installed, it does not remove it
sudo systemctl disable --now rcp.service
sudo systemctl enable --now reflex-ui.service
```

The UI should come up on the display within a few seconds. If it does not:

```bash
systemctl status reflex-ui.service --no-pager
journalctl -u reflex-ui.service -b --no-pager | tail -50
```

!!! danger "Do not run both"
    RCP and Reflex both drive the display and both claim the serial port.
    Disable one before enabling the other, exactly as above.

??? info "What the unit and wrapper actually do, if you need to change them"
    `deploy/start.sh` exports the `KCFG_*` variables that configure Kivy
    (1024×600, fullscreen, log directory), activates the venv, and runs
    `python -m reflex.main`. It works out its own location, so a checkout
    elsewhere still launches — but the unit's `ExecStart` still has to point
    at it.

    `deploy/reflex-ui.service` runs as **root** (required for KMS/DRM and for
    writing logs to `/var/log`) and restarts on exit, with a burst limit so a
    genuinely broken build stops instead of looping forever.

    The wrapper also sets `REFLEX_CONFIG_DIR=/var/lib/reflex-config`, which is
    why your machine settings do not end up in `/root` where you could not read
    or back them up.

---

## Step 6 — Flash the controller firmware

**The board must be running Reflex firmware, not upstream's.** The register
layout diverged; the UI checks it at connect and will tell you plainly if it is
wrong.

Plug the ST-Link into the Pi and the SWD header on the board. Build and flash
from the Pi — the same checkout you just cloned, so what is on the controller
cannot be a different revision from what is in front of you.

```bash
sudo apt install gcc-arm-none-eabi cmake build-essential openocd
cd ~/projects/reflex/fw && ./scripts/provision.sh
```

`openocd`'s packaging installs udev rules granting the `plugdev` group access,
so the flash itself needs no `sudo`.

**This is the only time you need the ST-Link.** `provision.sh` puts *two*
things on the board: the field bootloader in sector 0, and the application in
the RUN slot behind it. Once the bootloader is there, every later firmware
update goes over the RS-485 link the UI already uses — no programmer, no
power cycle, nothing to unplug at the machine:

```bash
python3 scripts/modbus-flash.py build-slot/reflex-fw.bin --port /dev/ttyUSB0
```

The layout, the anti-brick behavior and the optional step that write-protects
the bootloader once the board is confirmed working are all in
`fw/bootloader/README.md`.

!!! warning "`flash.sh` is not this step"
    The repo also has `scripts/flash.sh`. It writes the **legacy** layout —
    the application at `0x08000000`, with no bootloader at all — and it exists
    only for boards that are still on that layout. Run against a board
    provisioned as above it would overwrite the bootloader, so it now reads
    sector 0 first and refuses. Use `provision.sh` for a new board and
    `modbus-flash.py` thereafter.

!!! danger "Power-cycle the controller afterwards"
    A reset alone does not reliably start the new firmware on this board.
    OpenOCD's `Verified OK` confirms what was *written*, not what the processor
    is *executing* — so both the programming and the verification report
    success while the board keeps running the old firmware, with no error
    anywhere.

Confirm the board came up *through the bootloader*, with the UI stopped:

```bash
cd ~/projects/reflex/fw
python3 scripts/modbus-flash.py --identity --port /dev/ttyUSB0
```

You want `stage=application` and the revision you just built. `stage=bootloader`
means the bootloader is alive but did not accept the image in RUN — the same
output carries `blStatus` and `blRunValid`, which say why.

Then start the UI and watch its log as it connects:

```bash
journalctl -u reflex-ui.service -b --no-pager | grep -i "protocol version"
```

The two numbers in `Firmware register protocol version N (expected N)` must
match. A mismatch names itself — the UI says whether the firmware or the UI is
the older half — and it blocks calibration rather than letting you commission
against a register map it does not understand.

---

## Step 7 — Commission the machine

The software is installed; it does not yet know anything about *your* lathe —
which input is the spindle, what a scale count is worth, how a distance becomes
leadscrew steps.

That is [Setup](index.md), and it is done on the touchscreen rather than over
SSH. Do it in the order given there; the reasoning for the order is on that
page.

---

## Where everything lives

| | |
|---|---|
| Source checkout | `/home/default/projects/reflex` |
| Python environment | `/home/default/projects/reflex/ui/.venv` |
| Machine settings | `/var/lib/reflex-config` — YAML per axis and per component |
| systemd unit | `/etc/systemd/system/reflex-ui.service` |
| Launch wrapper | `<checkout>/ui/deploy/start.sh` |
| Application log | `journalctl -u reflex-ui.service` |
| Kivy log | `/var/log/kivy*` |
| Serial port | `/dev/serial0`, overridable as `serial_port` under `device` in the config |

`/var/lib/reflex-config` is the one directory that holds anything you cannot
regenerate. It is readable by the `default` account on purpose — so you can
diff it, back it up, and notice it drifting.

---

## Going back to RCP

Nothing above removed it.

```bash
sudo systemctl disable --now reflex-ui.service
sudo systemctl enable --now rcp.service
```

Reflex's settings stay in `/var/lib/reflex-config`, untouched, so switching
back again costs nothing. The controller firmware is the exception — RCP needs
upstream firmware, so a full revert means reflashing the board from an upstream
checkout.

---

## Updating later

```bash
cd ~/projects/reflex && git pull
cd ui && ~/.local/bin/uv sync   # only if dependencies changed
sudo systemctl restart reflex-ui.service
```

If the release also changed the firmware, push it over the wire — the ST-Link
was a step 6 thing only:

```bash
sudo systemctl stop reflex-ui.service        # it holds the serial port
cd ~/projects/reflex/fw
cmake -S . -B build-slot -DCMAKE_BUILD_TYPE=Release -DREFLEX_APP_BASE=0x08020000
cmake --build build-slot
python3 scripts/modbus-flash.py build-slot/reflex-fw.bin --port /dev/ttyUSB0
sudo systemctl start reflex-ui.service
```

No programmer and no power cycle: the bootloader stages the image, verifies it,
keeps the previous one as a backup and jumps. The protocol version check is what
tells you whether you needed to update at all — it is worth reading the log after
every update rather than only when something looks wrong.
