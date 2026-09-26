# The UNCOMMISSIONED strip

An amber strip across the top of the home screen: a warning icon, the word
**UNCOMMISSIONED**, the line *defaults, not this machine — settings are not
saved*, and at the right-hand end, *Tap for options*.

It means this SD card holds no measured settings for this lathe. The numbers
on screen are the app's built-in defaults: close enough to look plausible, not
close enough to cut with. **Do not cut with the machine in this state.**

## Why it appears

At start-up Reflex looks in its settings directory (`/var/lib/reflex-config`
on a standard install). It counts the machine as commissioned only when both
of these hold:

- `Els-0.yaml` is there and is not empty, and
- there are at least **15** settings files (`*.yaml`) in the directory.

A commissioned lathe has about 17. If either check fails, the app runs on its
defaults and **refuses to save any setting**, so defaults can never be recorded
as this machine's calibration. Anything you change in this state is forgotten
when the app closes.

You will see it on a fresh card, a new install, or a settings directory that
was lost or only partly copied. The check runs once, at start-up. A
commissioned machine never shows the strip.

## Tap for options

Tapping the strip opens **This machine is not commissioned**. It says the card
has no settings for this lathe yet, and offers four buttons:

| Button | What it does |
|---|---|
| **New machine — set it up** | For a lathe being set up for the first time. Switches saving on and opens Setup. |
| **Restore from USB stick** | Opens **Setup → Backup** and starts **Import from USB**. |
| **Restore from GitHub gist** | Opens **Setup → Backup** and starts **Restore from gist**, signing in to GitHub first if this card has not been signed in. |
| **Not now — keep defaults, save nothing** | Closes the dialog and changes nothing. |

**Restore from GitHub gist** is not shown on a build without GitHub sign-in
configured.

### New machine: set it up

Saving switches on at once and stays on for this card, through restarts, and
the strip goes away. Then work through [Setup](../setup/index.md) and enter
this lathe's real, measured values.

!!! warning "Measure before you type"
    From the moment you tap it, everything you save is recorded as this
    machine's baseline, and nothing later can tell a measured number from a
    guess.

It leaves a file named `commissioning-dismissed` in the settings directory.
Deleting it over SSH brings the warning back and stops the saving.

### Restore a backup

1. Tap **Restore from USB stick** or **Restore from GitHub gist**. The Backup
   screen opens and the restore starts. Check the confirmation, then **Apply**.
   See [Backing up your settings](backing-up.md).
2. The status line counts down from 10 and **the app restarts by itself**. It
   comes back on the restored settings, and the strip does not come back.

With no stick plugged in, the Backup screen says so; plug one in and tap
**Import from USB** there.

If the app cannot restart by itself, the status line says so: restart the
machine. If you tap the strip before that restart, the dialog says the
settings were restored and offers only **OK**. That is deliberate. The app is
still running on its defaults, and switching saving on at that moment would
write them over what you just restored. The restart is the fix.

### Restore over SSH

A technician can also copy a settings directory onto the card over SSH, into
`/var/lib/reflex-config`, and restart. That is not offered in the dialog: it
needs a shell, not a touchscreen.

!!! note "There is no other way to silence it"
    No swipe, no Setup toggle. An accidental tap on the strip only opens the
    dialog, and saving switches on only from a second, deliberate tap on
    **New machine — set it up**.
