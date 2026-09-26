# Backing up your settings

Everything that fits Reflex to *your* lathe lives on the Pi's SD card: axis
setup, scale ratios, servo gearing, backlash, the ELS settings. SD cards die,
and a dead card takes every copy on it along with it. The Backup screen keeps a
copy somewhere else.

The card also keeps its own record. Every time one of these settings changes,
the change is logged on the card, so an old value leaves a history instead of
being silently overwritten. A backup is what gets that record off the card.

Open **Setup → Backup** (Setup is the gear in the sidebar). There are two
places to keep a copy, a **USB stick** and a **GitHub gist**. Both hold the same
single file, and restoring from either goes through the same confirmation.

!!! info "What is in the file"
    Every settings file in one YAML document, stamped with the machine's name,
    when it was taken, and the controller firmware revision. It never contains
    your GitHub sign-in.

## USB stick

### Export

1. Plug a USB stick into the Pi.
2. Tap **Export to USB**. The line under the buttons reads
   *"Exported `reflex-commissioning-<name>-<time>.yaml` to the USB stick."*

Each export is a new file named with the time, so exporting again never
overwrites an older backup. With no stick in, it says *"No USB stick found --
insert one and try again."*

### Import

1. Plug in the stick.
2. Tap **Import from USB**. It takes the **newest** backup on any stick that
   is plugged in.
3. The **Import commissioning bundle** dialog shows the backup's **Machine**,
   **Captured** time and **Firmware**. Check that it is the machine and the
   backup you meant.
4. **Apply** writes it. **Cancel** leaves everything alone. The status line
   then reads *"Imported N file(s) from …. Restarting in 10 s to load
   them."* and counts down.
5. **The app restarts by itself** and comes back on the restored settings.
   Leave the settings alone during the countdown.

!!! warning "If it says it could not restart by itself"
    The status line then reads *"Could not restart by itself: restart the
    machine to load them."* Restart the machine straight away, before you
    change anything. The running app still holds the settings it started
    with: change any setting first, and the whole group that setting belongs
    to is written back over what you just restored.

An import from a newer Reflex than the one running is refused, and nothing is
written. The status line says the bundle schema is newer than this app
understands. Update Reflex first, then import.

## GitHub gist

**Optional, and off by default.** It keeps a copy in a *secret* gist in your
own GitHub account, updated by itself whenever those settings change. It needs a
GitHub account and a network connection on the Pi. Reflex asks GitHub for
access to your gists and nothing else.

!!! note "Secret is not private"
    Anyone who has a secret gist's address can read it. The file holds your
    machine settings, and no password or token.

### Turn it on

1. Tap **Gist sync: OFF**. After *"Contacting GitHub..."* the screen shows an
   eight-character code in large type, with a QR code beside it.
2. On your phone, scan the QR code (or go to the address shown), sign in to
   GitHub, type the code, and approve.
3. Within a few seconds the button reads **Gist sync: ON**, and the first sync
   runs straight away: *"Synced to gist …"*.

If something goes wrong, the status line says what, and sync stays off:

- *"The code expired before it was entered. Turn the toggle on again for a fresh code."*
- *"Authorization was denied on GitHub. Gist sync stays off."*
- *"Could not reach GitHub. Gist sync stays off."*

### While it is on

Every time one of these settings changes, it syncs by itself, in the
background, to the **same** gist, so the gist's revision history on GitHub is
your machine's history. **Sync now** pushes a copy immediately.

A sync that fails is not retried in a loop. The status line says
*"Gist sync failed -- will retry at the next change."*, and the machine keeps
working either way.

### Sign-in expired

If GitHub stops accepting the Pi's sign-in, sync turns itself **off** and says
so:

> GitHub no longer accepts this machine's sign-in. Gist sync is now OFF: tap
> it to sign in again.

Tap **Gist sync: OFF** and sign in again as above. This has happened on the
development lathe, and signing in again fixed it.

### Turn it off

Tap **Gist sync: ON**. The Pi forgets its sign-in and reads
*"Gist sync off. Revoke the GitHub grant at https://github.com/settings/applications"*.
Turning sync off does not revoke GitHub's permission. Only you can do that, at
that address, which is also shown under the buttons. The gist itself stays in
your account.

### Restore from a gist

This is how you put a machine's settings onto a new card.

1. Tap **Restore from gist**. On a card that has not been signed in to GitHub
   yet, it shows the code and QR first, as under *Turn it on*; sign in on your
   phone and the restore carries on by itself. Signing in this way does **not**
   turn sync on and does not upload anything.
2. If there is only one backup in your account it goes straight to the
   confirmation. If there are several, pick one. Each row shows a machine id
   and when it was last updated, newest first. The confirmation shows when the
   backup was captured, so you can check before you apply.
3. The same **Import commissioning bundle** dialog as a USB import: check it,
   then **Apply**.
4. The app restarts by itself after a 10-second countdown, as for a USB import.

On a card showing UNCOMMISSIONED, **Sync now** and turning sync on upload
nothing. The status line says *"Nothing to back up yet: this machine is not
commissioned."* A card in that state holds only defaults, and a gist of them
would sit above the real backup in the restore list.

!!! tip "A card showing UNCOMMISSIONED"
    Tap the strip: its **Restore from USB stick** and **Restore from GitHub
    gist** buttons start these same restores. Restoring a backup and restarting
    clears it. See [The UNCOMMISSIONED strip](uncommissioned.md).
