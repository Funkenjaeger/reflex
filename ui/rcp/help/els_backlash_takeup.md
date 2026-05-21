Backlash Takeup
===============

Compensates for mechanical backlash in the leadscrew/servo coupling
by commanding extra servo travel whenever motion reverses direction,
so the carriage actually starts moving immediately instead of waiting
for the nut to traverse the play window.

Entered as a magnitude in **millimeters** of leadscrew travel. The
controller converts this to servo steps internally using the servo
gearing.

## How It Works

The same magnitude is applied in two places, one tick per direction
reversal in the cycle:

- **Before each cut**, the firmware commands an extra takeup in the
  cutting direction. The cut then begins with the gear train pre-loaded
  so the carriage starts moving on the first synchronized step. The
  takeup direction is derived automatically from
  `sign(stopDirection × threadPitchSteps × zCountsPerPitch)`.
- **At the start of each retract** (the first retract after a cut),
  the host adds the takeup in the retract direction before commanding
  the retract move. Without this, the carriage stops short of Start Z
  by the play-window distance on every retract. Subsequent self-loop
  retract corrections within the same cycle don't reapply takeup —
  the nut is already on the retract-side wall.

You only configure the magnitude. Direction is derived from the
cut/stop polarity settings (cuts) or the sign of the retract delta
(retracts).

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
  pre-cut takeup AND the host skips the pre-retract takeup.
- The same magnitude governs both the pre-cut and pre-retract takeups.
  If your machine has asymmetric backlash, set the value to the larger
  of the two and accept slight over-travel on the smaller side.
