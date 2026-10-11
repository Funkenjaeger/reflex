Software Update
===============

Install a release from the machine. A Reflex release is **one version covering
both halves**: the firmware on the controller and this UI. An install replaces
both halves or neither.

## Fields

### Currently Installed Release
The version that runs now. Read-only.

### Controller Firmware
What the controller reports: its register protocol version, and whether that
version matches this UI. During an update it reads *link released for update*.
This means the update stopped the DRO on purpose. Nothing is broken.

### Refresh Available Releases
Gets the release list from GitHub. An internet connection is necessary.
Releases published before the field bootloader existed have no firmware image.
They cannot supply both halves, so the list does not offer them.

### Offer Pre-releases (experimental)
When this is on, release candidates appear in the list with the final releases.

- **OFF (default):** final releases only.
- **ON:** adds release candidates. They are built the same way and carry both
  halves. They have less testing, so the screen asks you to confirm.

There is no longer a "dev" entry. It followed a branch, and no firmware image
is built for a branch. So it could only update half the machine.

### Offer Integration Builds (bench only)
This row appears only on a machine set up with an integration build source.
When this is on, integration builds (`vX.Y.Z-rc.N.dev.M`, a build on the way
to release candidate N) appear in the list. They are built the same way and
carry both halves. They have no testing beyond the bench, so the screen asks
you to confirm.

Before each update, the settings are saved to the commissioning snapshots. An
older release does not know the settings that a newer release added. It sets
them to their defaults the first time it saves. Use the snapshot to recover one.

### Available Releases
Select the version to install.

### Install Selected Release
Runs the full update. At the end, the service restarts itself.

If an ELS job is engaged, the screen asks you first. The update reboots the
controller, which ends the job, so the job must be disengaged. **Disengage and
Install** disengages it. The update starts when the controller confirms that
the job is released. If the spindle turns or a cycle runs, the screen tells
you. Stop it, then press Install Selected Release again.

## What happens when you press Install

1. **Checks that it can finish, and changes nothing.** It checks for a clean
   checkout, `uv` present, the tag fetched, and the firmware image downloaded
   and validated. If one is missing, the update stops here.
2. **Flashes the controller.** This takes about thirteen seconds, over the same
   RS-485 link this UI usually uses. The flasher needs that port, so the DRO
   stops during the flash. **Do not turn off the machine power while it is
   flashing.**
3. **Asks the new firmware for its register protocol version.** If the version
   is not what the new UI expects, the update **stops there**. The update puts
   the previous firmware back and confirms it runs. It does not install the UI
   half. You cannot bypass this step. If the UI and firmware disagree about the
   register layout, each register after the first difference reads as a value
   that looks correct but is wrong.
4. **Installs the UI half** and restarts.

## Notes

- The controller needs the Modbus field bootloader for step 2. A board flashed
  only over SWD does not have it. The update tells you this and stops.
- If the update refuses, nothing is left half-done. The status area names what
  stopped it. It also tells you when it put the previous firmware back. If that
  restore fails, the status area says the controller stays on the new firmware
  with the old UI. It also tells you what to do.
- After a failed transfer, the controller can be unreachable. The status area
  then tells you to turn the machine off, wait 10 seconds and turn it on. This
  was tested on the lathe: the controller comes back on its previous firmware.
- The status area is the log. Read it before you leave the screen.
- Updates from the command line are still supported. See the Installing page.
