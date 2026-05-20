Backlash Takeup
===============

Compensates for mechanical backlash in the leadscrew/servo coupling
by commanding extra servo travel during a takeup move before the
actual cut, so the cut begins with the gear train pre-loaded in the
correct direction.

Entered as a magnitude in **millimeters** of leadscrew travel. The
controller converts this to servo steps internally using the servo
gearing.

## How It Works

After the firmware latches a stop, the next cut begins by commanding
an extra `backlash takeup` of travel in the takeup direction before
the regular synchronized motion resumes. This consumes any mechanical
slack so the actual cut starts at a repeatable spindle phase.

The takeup direction is derived automatically by the firmware from:

    sign(stopDirection × threadPitchSteps × zCountsPerPitch)

You only configure the magnitude — direction follows the cut and
stop polarity settings.

## Typical Values

- **0.00 mm** — no compensation. Use on rigid leadscrew/servo
  setups (direct couplings, preloaded ballscrews).
- **0.05 – 0.20 mm** — typical for a well-tuned manual lathe with
  a couple thousandths of measured backlash.
- **> 0.5 mm** — only if the machine has obvious mechanical slack;
  consider tightening the drive train first.

## How to Measure

1. Move the carriage in one direction, then reverse and measure the
   travel before the workpiece actually moves.
2. Use that value as a starting point.
3. Cut a test thread and check if successive passes align (no
   "stairstep" between passes). Adjust as needed.

## Notes

- Value is always entered in mm regardless of display mode.
- A value of 0 disables takeup completely — the firmware skips the
  takeup move at the start of each cut.
- Takeup magnitude is shared by all cuts in the current cycle.
