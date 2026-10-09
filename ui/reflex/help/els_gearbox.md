Gearbox Position and the Coupling Check
======================================

The lever between the servo and the leadscrew has three positions:

- **A** — the position the leadscrew settings were set up in.
- **B (fast)** — the leadscrew turns twice as fast as in A.
- **C (slow)** — the leadscrew turns half as fast as in A.

**Gearbox position** tells the controller which one is engaged. Feeds,
thread pitches, the backlash take-up and retract moves are all worked out
for that position. If the lever is somewhere else, every one of them is
wrong by a factor of two.

Backlash calibration only runs with the gearbox position set to **A**.

## The coupling check

During every pass the controller compares how far the carriage actually
moved (on the Z scale) with how far the servo was told to move it. That
catches two faults:

- **The half nut opened mid-cut.** The carriage stops moving. The pass is
  aborted and the controller goes to stopped.
- **The lever is in a different position.** The carriage moves at twice or
  half the expected rate.

If the check is confident which position the lever is in, the pass is
aborted with a **yellow** message naming it. Your next **Cut** or
**Retract** takes that as your go-ahead: the controller switches to that
position and carries on with the right feed and pitch.

If the rate matches **no** position, something is wrong with the
drivetrain. The pass is aborted with a **red** message and nothing is
changed. It will keep refusing until the cause is fixed.

The check needs about half a second of the pass after the take-up to be
sure. A pass shorter than that is not checked.

## Shadow mode

**Default: OFF (log only).** With **Coupling check acts** off, the check
still runs on every pass and writes what it would have done to the log and
the flight recorder, but it never aborts a pass and never changes the
gearbox position. Turn it on once those records show it agreeing with what
you actually did at the machine.

## The start-of-cut distance

**Cut** stays unavailable until the carriage is far enough back from the
stop. That distance is sized for the **fastest** gearbox position, whatever
position is set: the backlash take-up and, when threading, the phase
correction run before the coupling check can read anything, and the stop
cannot cancel them once they have started. So the minimum start distance is
about twice what it would be for position A alone.
