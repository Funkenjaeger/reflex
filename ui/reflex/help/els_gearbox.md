Gearbox Table, Gearbox Position and the Coupling Check
=====================================================

Some lathes have a gearbox or a lever between the servo and the leadscrew,
so the same motor step moves the carriage a different distance in each
position. Many do not. The controller cannot see a lever: tell it here.

## Gearbox table

**Default: empty, meaning no gearbox.** The servo drives the leadscrew
directly, no position is shown anywhere, and every feed and pitch uses the
servo settings as they are.

With a gearbox, enter one entry per position, the **reference position
first**, as `name=factor`, separated by commas:

    A=1, B=2, C=1/2

- **2 to 4 positions.**
- **Names** are your own, 1 to 3 characters with no spaces, commas or
  `=`: `A`, `Lo`, `Hi`, `1`, `2`. Each name once.
- The **first position is the reference.** Its factor is 1 (you may leave
  `=1` off). Set up the leadscrew pitch and steps per revolution for this
  position: every step-valued setting is stored as measured in it.
- Every other **factor** is how far the carriage moves per motor step,
  relative to the reference: `2` twice as far, `1/2` or `0.5` half as far,
  `3/2` one and a half times. Between 1/8 and 8, and any two factors at
  least 1.2 times apart.

An entry that breaks a rule is refused with the reason on the status bar,
and the table in effect stays as it was. The table cannot change during a
pass.

## Gearbox position

Only shown with a gearbox table. Tells the controller which position is
engaged. Feeds, thread pitches, the backlash take-up and retract moves are
all worked out for that position. If the lever is somewhere else, every one
of them is wrong by the ratio of the two factors.

The main screen shows the position in effect as **Gear** and its name.

Backlash calibration only runs in the reference position.

## The coupling check

During every pass the controller compares how far the carriage actually
moved (on the Z scale) with how far the servo was told to move it. That
catches two faults:

- **The half nut opened mid-cut.** The carriage stops moving. The pass is
  aborted and the controller goes to stopped.
- **The carriage moves at the wrong rate.** With a gearbox, usually because
  the lever is in another position.

With a gearbox, if the check is confident the rate matches another position
in the table, the pass is aborted with a **yellow** message naming it
(**fast** or **slow** from its factor). Your next **Cut** or **Retract**
takes that as your go-ahead: the controller switches to that position and
carries on with the right feed and pitch.

If the rate matches **no** position, or, with no gearbox, is not the
leadscrew's rate, something is wrong with the drivetrain. The pass is
aborted with a **red** message and nothing is changed. It will keep
refusing until the cause is fixed.

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
stop. With a gearbox, that distance is sized for the **fastest** position,
whatever position is set: the backlash take-up and, when threading, the
phase correction run before the coupling check can read anything, and the
stop cannot cancel them once they have started. With a table of A=1, B=2,
C=1/2 and A set, the minimum start distance is about twice what it would be
for A alone.
