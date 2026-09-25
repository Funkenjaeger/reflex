# Updating Reflex

A Reflex release is one version covering both halves: the firmware on the
controller and the UI on the touchscreen. The Update screen installs both
together, from the touchscreen. You do not need SSH, and you do not need to
work out whether the firmware changed.

What happens underneath, and the command-line route, are in
[Installing on a Pi → Updating later](../setup/installing.md#updating-later).

## Before you start

- **The Pi needs a network connection** that reaches GitHub.
- **Stop the spindle.** If an ELS job is engaged, the screen offers to
  disengage it for you, but it cannot do that while the spindle turns.
- **Finish the thread you are cutting.** The update reboots the controller,
  which ends the ELS job and the thread reference with it.

## Steps

1. **Open Setup → Update** (Setup is the gear in the sidebar). The screen is
   titled **Software Update**, and it fetches the list of releases when it
   opens.

2. **Look at the top two rows.** **Currently installed release** is the
   version running now. **Controller firmware** should read
   `protocol vN, matched`.

3. **Pick a version** from **Available releases**. The list holds final
   releases only, unless **Offer pre-releases (experimental)** is on. The
   screen remembers that choice. **Refresh available releases** fetches the
   list again.

4. **Tap Install Selected Release.** A release candidate asks first, in a
   **Pre-release** dialog: **Install Anyway** or **Cancel**. If an ELS job is
   engaged you are asked about that next. See [ELS job engaged](#els-job-engaged).

5. **Leave it alone until it finishes.** The button reads **Updating...**, a
   warning under it says not to power off, and the **Update Status** box scrolls
   into view and logs each step. The firmware half takes about fifteen seconds.
   During it the DRO stops and **Controller firmware** reads
   `link released for update`. That is the update holding the serial link, not
   a fault.

6. **The UI restarts itself** on the new version. If the status box instead
   ends with *"Update applied, but the UI did not restart. Tap Exit Application
   ..."*, tap **Exit Application**. The UI comes back on the new version by
   itself.

Drag the status box with a finger to scroll it. It is worth reading before
you leave the screen.

!!! note "Older releases are not offered"
    Releases from before 1.2.0-rc.1 carry no firmware image the updater can
    install, so they are left out of the list. Going back that far takes the
    command-line route.

## ELS job engaged

The controller will not reboot into its bootloader while an ELS job is
engaged, because a reboot would drop the servo mid-job. So when a job is
engaged, **Install Selected Release** opens an **ELS job engaged** dialog that
ends:

> Disengage ELS and install v1.2.0?

- **Disengage and Install** disengages the job the same way the ADV bar's
  **Disengage** button does, waits for the controller to confirm the job is
  released, and then starts the update.
- **Cancel** leaves everything as it was.

If the job cannot be disengaged right now, the dialog says why and offers only
**OK**:

> The spindle is turning. Stop the spindle.

or, with a cycle running, *"Stop the cycle before disengaging."* Do that, then
tap **Install Selected Release** again.

## If an update fails

**It puts things back.** The updater checks that it can finish before it
touches anything. If a step fails after that, it restores what it changed and
confirms it with the controller before saying so. In the rare case where it
cannot, the status box says the machine is MISMATCHED, or that its state is
UNKNOWN, and the recovery is from a terminal.

The first lines of the status box say what happened. The common ones:

> The firmware transfer FAILED and nothing changed: the controller is
> confirmed running its previous firmware ...

Nothing to recover. Try again later. If it keeps failing, use the
command-line route to see why.

> The update did not start: the controller REFUSED to reboot into its
> bootloader because an ELS job is engaged. ...

Disengage and install again. **Install Selected Release** offers to disengage
for you.

> REFUSED: the v... firmware speaks register protocol version ..., so the two
> halves of that release do not match each other. ... Do not retry this release.

The previous firmware is back and the UI was never changed. That release is
broken. Do not install it.

### When it says to turn the machine off

If a failed transfer leaves the controller waiting in its bootloader, or not
answering at all, the message starts with:

> WHAT TO DO NOW, no terminal needed: turn the machine OFF, wait 10 seconds,
> and turn it back ON.

Do exactly that. At power-on the bootloader starts the previous firmware by
itself. Then check that the DRO reads normally: the position displays show and
follow the machine.

This recovery was tested on the development lathe on 2026-09-24, twice: once
with the controller left waiting in its bootloader, and once with the
bootloader gone silent part-way through a transfer. Both came back on their own
firmware with the UI reconnected.

If the DRO still does not read normally, the controller needs recovering from
a terminal. See
[Updating later → From the command line](../setup/installing.md#from-the-command-line).

!!! danger "Do not power off while it is flashing"
    Turning the machine off is the recovery **only** when the status box tells
    you to. While an update is running, the warning on screen means what it
    says.
