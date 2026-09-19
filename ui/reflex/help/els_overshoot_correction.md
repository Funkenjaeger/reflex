Stop Coast Correction
=====================

The ELS stop tells the leadscrew to stop when the Z scale reaches your
**Stop Z**. The carriage does not stop dead: it coasts on a little, and
the faster it was feeding, the further it coasts. On this machine that is
nothing when you crank by hand, about 0.05 mm at a slow feed and a little
over 0.2 mm at the fastest feed measured.

With this setting **ON**, the controller fires the stop *early* by the
measured coast for the speed the carriage is approaching at, so the
carriage comes to rest just **short** of Stop Z instead of just past it.
Your Stop Z itself never changes.

**Default: OFF.** Leave it off until you have checked it on your machine.

## Before you rely on it

**Verify at a shoulder.** Cut a test shoulder at the feed you intend to
use, with the correction on, and check where the carriage actually stops
against the DRO. It should stop at or a hair short of Stop Z, never past.

The correction is a table measured on **this** machine with its current
setup: the Z scale's resolution and the servo's maximum speed and
acceleration. If any of those change, the table no longer describes the
machine. Turn the correction off until it has been re-measured.

## Coast margin

Extra Z counts of early stop added on top of the measured coast, so the
carriage lands a little short rather than exactly on the worst case seen.
**1** is the setting it was sized for. Larger values stop further short;
0 aims at the worst measured coast with nothing to spare.

## Faster than measured

If the carriage approaches faster than anything in the table, the
correction holds at its largest measured value and a notice says so once
per pass. The carriage may then coast further than the correction allows
for, so check that stop by eye.
