Gearbox Table, Gearbox Position and the Coupling Check
=====================================================

Some lathes have a gearbox or a lever between the servo and the leadscrew.
On those lathes, the same motor step moves the carriage a different
distance in each position. Many lathes do not. The controller cannot see a
lever, so you tell it here.

## Gearbox table

**Default: empty, meaning no gearbox.** The servo drives the leadscrew
directly. No position shows anywhere. Every feed and pitch uses the servo
settings as they are.

With a gearbox, enter one entry for each position, as `name=factor`,
separated by commas. Put the **reference position first**:

    A=1, B=2, C=1/2

That is the gearbox of the development lathe. This page uses it as the
example throughout.

- **2 to 4 positions.**
- **Names** are your own: 1 to 3 characters, with no spaces, commas or
  `=`. Examples: `A`, `Lo`, `Hi`, `1`, `2`. Use each name only once.
- The **first position is the reference.** Its factor is 1 (you can leave
  `=1` off). Set the leadscrew pitch and steps per revolution for this
  position. Every step-valued setting is stored as measured in this
  position.
- Every other **factor** is how far the carriage moves per motor step,
  relative to the reference: `2` is twice as far, `1/2` or `0.5` is half
  as far, `3/2` is one and a half times as far. Each factor must be
  between 1/8 and 8. Any two factors must be at least 1.2 times apart.

If an entry breaks a rule, the controller refuses it and shows the reason
on the status bar. The table in effect stays as it was. The table cannot
change during a pass.

## Measure gearbox factor

This measures one position's factor, so you do not have to trust the
number on the gear plate. Put the lever in the position, press
**Measure**, choose the position and press **Start**. Then cut 3 passes
in air to the stop with the ELS bar, in Feed mode. The factor is read over
each whole pass. When the passes agree, the window opens with the factor
for **Save** or **Discard**. Save changes only that position in the
table. The reference position is not measured: its factor is 1.

The step-by-step guide is on the docs site:
funkenjaeger.github.io/reflex/guide/measuring-gearbox-factor

## Gearbox position

This shows only when there is a gearbox table. It tells the controller
which position is engaged. Feeds, thread pitches, the backlash take-up and
retract moves are all calculated for that position. If the lever is in a
different position, every one of them is wrong by the ratio of the two
factors.

The main screen shows the position in effect as **Gear** and its name.

Backlash calibration runs only in the reference position.

## The coupling check

During every pass, the controller compares two distances: how far the
carriage actually moved (on the Z scale), and how far the servo was told
to move it. This finds two faults:

- **The half nut opened mid-cut.** The carriage stops moving. The
  controller stops the pass and goes to stopped.
- **The carriage moves at the wrong rate.** With a gearbox, the usual
  cause is a lever in a different position.

With a gearbox, the check can be confident that the rate matches a
different position in the table. Then the controller stops the pass and
shows a **yellow** message that names that position. Your next **Cut** or
**Retract** is your go-ahead. The controller changes to that position and
continues with the correct feed and pitch.

If the rate matches **no** position, something is wrong with the
drivetrain. With no gearbox, the same is true when the rate is not the
leadscrew rate. The controller stops the pass with a **red** message and
changes nothing. It continues to refuse until you correct the cause.

The check needs about half a second of the pass after the take-up to be
sure. It does not check a pass shorter than that.

## Shadow mode

**Default: OFF (log only).** When **Coupling check acts (off = log only)**
is off, the check still runs on every pass. It writes what it would have
done to the log and the flight recorder. But it never stops a pass and
never changes the gearbox position. Turn it on when those records show
that it agrees with what you actually did at the machine.

## The start-of-cut distance

**Cut** stays unavailable until the carriage is far enough back from the
stop. With a gearbox, that distance is sized for the **fastest** position,
whatever position is set. The reason: the backlash take-up and, when
threading, the phase correction run before the coupling check can read
anything. The stop cannot cancel them after they start. With a table of
A=1, B=2, C=1/2 and A set, the minimum start distance is about twice the
distance for A alone.
