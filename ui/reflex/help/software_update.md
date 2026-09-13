Software Update
===============

Install a release from the machine. A Reflex release is **one version covering
both halves** — the firmware on the controller and this UI — so installing one
replaces both, or it replaces neither.

## Fields

### Currently Installed Release
The version running now. Read-only.

### Controller Firmware
What the controller reports: its register protocol version, and whether that
matches this UI. During an update it reads *link released for update*, which
means the DRO is stopped on purpose rather than because something broke.

### Refresh Available Releases
Fetches the release list from GitHub. Needs an internet connection. Releases
published before the field bootloader existed carry no firmware image and are
not offered — they cannot supply both halves.

### Offer Pre-releases (experimental)
When enabled, release candidates appear in the list alongside final releases.

- **OFF (default):** final releases only.
- **ON:** adds release candidates. They are built the same way and carry both
  halves; they are simply less tested, and you are asked to confirm.

There is no longer a "dev" entry. It tracked a branch, and no firmware image is
built for a branch — so it could only ever have updated half the machine.

### Available Releases
Pick the version to install.

### Install Selected Release
Runs the whole update. The service restarts itself at the end.

## What happens when you press Install

1. **Checks it can finish, changing nothing.** Clean checkout, `uv` present,
   the tag fetched, the firmware image downloaded and validated. Anything
   missing stops it here.
2. **Flashes the controller** — about thirteen seconds, over the same RS-485
   link this UI normally uses. The DRO stops for the duration because the
   flasher needs that port. **Do not power the machine off while it is
   flashing.**
3. **Asks the new firmware what it speaks.** If its register protocol version
   is not what the new UI expects, the update **stops there** and the UI half
   is not installed. There is no way past this: a UI and firmware that
   disagree about the register layout read every register after the point of
   divergence as plausible nonsense.
4. **Installs the UI half** and restarts.

## Notes

- The controller needs the Modbus field bootloader for step 2. A board flashed
  only over SWD does not have it, and the update will say so and stop.
- If the update refuses, nothing is left half-done — the status area names what
  stopped it. The exception is a refusal at step 3, which says so explicitly:
  the controller has the new firmware and this UI is still the old one.
- The status area is the log. It is worth reading before leaving the screen.
- Updating from the command line is still supported; see the Installing page.
