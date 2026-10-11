Stop Overshoot Correction
=========================

The ELS stop tells the leadscrew to stop when the Z scale reaches your
**Stop Z**. The carriage does not stop immediately. The servo drive has
latency, so it continues to drive the leadscrew for a moment after the
stop fires. Thus the carriage overshoots. The faster the feed, the larger
the overshoot. On this machine the overshoot is zero when you crank by
hand. It is about 0.05 mm at a slow feed, and a little over 0.2 mm at the
fastest feed measured.

With this setting **ON**, the controller fires the stop *early*, by the
measured overshoot for the approach speed of the carriage. The carriage
then comes to rest just **short** of Stop Z, not just past it. Your Stop Z
itself never changes.

**Default: OFF.** Leave it off until you have checked it on your machine.

## Before you rely on it

**Verify at a shoulder.** Turn the correction on. Cut a test shoulder at
the feed you intend to use. Examine on the DRO where the carriage actually
stops. It should stop at Stop Z or slightly short of it, never past it.

The correction is a table measured on **this** machine with its current
setup: the Z scale resolution, the servo drive and its own settings, and
the leadscrew. If any of those change, the table no longer describes the
machine. Turn the correction off until the table is measured again. On the
development lathe, a move of the gearbox lever is not such a change. The
table was measured in all three lever positions and covers them. On a
different lathe, check the table again after a gearbox change. (The
Setup → Servo speed and acceleration settings do not affect it. The
controller has stopped sending steps before the overshoot happens.)

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
