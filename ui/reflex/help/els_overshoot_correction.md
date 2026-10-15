Stop Overshoot Correction
=========================

The ELS stop tells the leadscrew to stop when the Z scale reaches your
**Stop Z**. The carriage does not stop immediately. The servo drive has
latency, so it continues to drive the leadscrew for a moment after the
stop fires. Thus the carriage overshoots. The faster the feed, the larger
the overshoot. On the development lathe the overshoot is zero when you
crank by hand. It is about 0.05 mm at a slow feed, and a little over
0.2 mm at the fastest feed measured.

With this setting **ON**, the controller fires the stop *early*, by the
measured overshoot for the approach speed of the carriage. The carriage
then comes to rest just **short** of Stop Z, not just past it. Your Stop Z
itself never changes.

**Default: OFF.** Leave it off until you have checked it on your machine.

## The overshoot table

The correction uses this machine's **overshoot table**: the overshoot
measured at each feed. Measure it with **Overshoot table → Measure**. The
table is a machine setting, so a settings backup carries it.

**Default: no table.** With no table, the correction cannot turn on. If you
set it to on, it goes back to off, and a notice tells you to measure the
table.

A machine that had the correction on before tables were stored, at the
development lathe's geometry, keeps the built-in table as its own. A notice
tells you once.

## When the table goes stale

The table is correct only for the setup it was measured with: the Z scale
resolution, the leadscrew (**Lead Screw Pitch** and **Lead Screw Pitch
Steps**) and the **Gearbox table**. If one of them changes, the table is
**stale**. The correction goes to off, and a notice names the change. The
table is kept: put the setting back and the table is correct again. Or
measure the table again.

This is checked all the time, also after a restart.

Some changes cannot be seen here: a different servo drive, different drive
settings, or a different controller board. After such a change, measure the
table again. (The Setup → Servo speed and acceleration settings do not
affect the table. The controller has stopped sending steps before the
overshoot happens.)

The gearbox lever position is not part of the table. On the development
lathe one table covers all three lever positions.

## After each corrected stop

A stop that lands short, the normal case, shows nothing on screen.
**Overshoot table → Measure** shows where the last corrected stop landed,
in Z counts and in display units, for example:

> Last corrected stop: landed 2 counts (0.010 mm) short.

If the counts and the distance do not agree with your DRO, examine the
Z scale settings.

**If a corrected stop lands more than 1 count past Stop Z, the correction
goes to off**, and the status strip says so. Past is further in the
direction the carriage was traveling. Measure the table again before you
turn the correction back on.

## Before you rely on it

**Verify at a shoulder.** Turn the correction on. Cut a test shoulder at
the feed you intend to use. Examine on the DRO where the carriage actually
stops. It should stop at Stop Z or slightly short of it, never past it.

## Overshoot margin

Extra Z counts of early stop, added on top of the measured overshoot.
With this margin, the carriage lands a little short of the worst case
seen, not exactly on it. **1** is the setting it was sized for. Larger
values stop further short. 0 aims at the worst measured overshoot with
nothing to spare.

## Faster than measured

The carriage can approach faster than any speed in the table. Then the
correction holds at its largest measured value, and a notice tells you so
once per pass. The carriage can then overshoot further than the correction
allows for. Examine that stop by eye.
