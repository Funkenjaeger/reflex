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

Tapping the strip opens **This machine is not commissioned**, which gives two
ways out. **Close** shuts the dialog and changes nothing.

### 1. Restore a backup, if you have one

1. Restore it from **Setup → Backup**, with **Import from USB** or
   **Restore from gist**. See [Backing up your settings](backing-up.md).
   Restoring the settings directory over SSH works too.
2. **Restart the machine.** The restored settings are found on the next boot,
   and the strip does not come back.

If you tap the strip between the restore and the restart, the dialog says a
configuration was written after the app started, and offers only **OK**. That
is deliberate. The app is still running on its defaults, and switching saving
on at that moment would write them over what you just restored. The restart is
the fix.

### 2. Dismiss and commission by hand

If there is no backup, tap **Dismiss**. Saving switches on at once and stays on
for this card, through restarts, and the strip goes away. Then work through
[Setup](../setup/index.md) and enter this lathe's real, measured values.

!!! warning "Measure before you type"
    From the moment you dismiss, everything you save is recorded as this
    machine's baseline, and nothing later can tell a measured number from a
    guess.

Dismissing leaves a file named `commissioning-dismissed` in the settings
directory. Deleting it over SSH brings the warning back and stops the saving.

!!! note "There is no other way to silence it"
    No swipe, no Setup toggle. An accidental tap on the strip only opens the
    explanation, and dismissal is a second, deliberate tap on **Dismiss**.
