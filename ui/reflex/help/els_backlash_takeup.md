Backlash Takeup
===============

Compensates for mechanical backlash in the leadscrew/servo coupling. When
motion reverses direction, the controller commands extra servo travel.
Thus the carriage starts to move immediately. It does not wait for the
nut to cross the play window.

Enter it as a magnitude in **millimeters** of leadscrew travel. The
controller converts it to servo steps internally, from the servo gearing.

## How It Works

The same magnitude applies in two places, once per direction reversal in
the cycle:

- **Before each cut**, the firmware commands an extra take-up in the
  cutting direction. The cut then starts with the gear train preloaded,
  so the carriage moves on the first synchronized step. The take-up
  direction comes automatically from
  `sign(syncRatioNum × threadPitchSteps × zCountsPerPitch)`.
- **At the start of each retract** (the first retract after a cut),
  the host adds the take-up in the retract direction before it commands
  the retract move. Without this, the carriage stops short of Start Z by
  the play-window distance on every retract. Later self-loop retract
  corrections in the same cycle do not apply the take-up again. The nut
  is already against the retract-side wall.

You set only the magnitude. The direction comes from the spindle sync
ratio and thread geometry (cuts), or from the sign of the retract delta
(retracts).

## Typical Values

- **0.00 mm** — no compensation. Use it on rigid leadscrew/servo setups
  (direct couplings, preloaded ballscrews).
- **0.05 – 0.20 mm** — typical for a well-tuned manual lathe with
  a couple thousandths of measured backlash.
- **> 0.5 mm** — only if the machine has obvious mechanical slack. If
  possible, tighten the drivetrain first.

## How to Measure

1. Move the carriage in one direction. Then reverse it, and measure the
   travel before the workpiece actually moves.
2. Use that value as a starting point.
3. Cut a test thread. Examine whether successive passes align (no
   "stairstep" between passes). Adjust as necessary.

## Notes

- You always enter the value in mm, whatever the display mode.
- A value of 0 turns off the take-up completely. The firmware skips the
  pre-cut take-up, AND the host skips the pre-retract take-up.
- The pre-cut take-up runs at the start of **every** pass. This is true
  in turning and in threading, on the first pass of a job, and on every
  resume (since 2026-08-21). In every case, the firmware confirms that
  the carriage actually moved before it releases the cut. If the half nut
  is open or partly closed, the firmware refuses the cut with "Carriage
  did not move. Close the half nut." It does not cut silently. The only
  difference in turning is that no thread-phase correction follows the
  take-up.
- The same magnitude controls the pre-cut and the pre-retract take-ups.
  If your machine has asymmetric backlash, set the value to the larger of
  the two. Accept slight over-travel on the smaller side.
