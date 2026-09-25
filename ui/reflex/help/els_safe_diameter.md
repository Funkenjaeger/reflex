Safe Diameter
=============

Adds a committed diameter to **stop + retract** mode, so the controller
can tell whether the tool is clear of the work before it feeds the
carriage back.

## Why it matters

Pressing **Retract** returns the carriage to Start Z under power. If
the tool is still in the groove it gets dragged back along the thread.

Backing off in X is always your hand — there is one servo and it drives
the leadscrew, so nothing comes out of the cut on its own.

Wizard mode already guards this: it knows the diameter you set as step
3, and it disables Retract until X is clear of it. Stop + retract had
no committed diameter to compare against, so nothing caught the
mistake.

## Behavior

- **ON (default):** a **Safe ø** field appears in the advanced bar.
  Set it to a diameter the tool is safely clear of. Retract is then
  refused, with a message, until X is past it.
- **OFF:** the field is hidden and stop + retract behaves as it always
  has — Retract is never gated on X.

## Until you set a value

Enabling this does **not** by itself gate anything. The check stays
inactive until a diameter is actually committed, and Retract behaves
exactly as before in the meantime.

That is deliberate. An earlier attempt made the gate live without a
committed value and it blocked *every* threading retract on a machine
whose X readout sits below its power-on zero — a greyed Retract button
with no way to satisfy it. A value you set is what makes the gate
trustworthy, so the value comes first.

## Notes

- Same field as **Major ø** in wizard mode, and the same stored value.
  It is named differently here because its job is the clearance gate
  rather than the thread's dimension.
- Stop-only mode never shows it: there is no Retract in that mode.
- The **Inner Thread** setting decides which direction counts as
  clear — outward for OD work, inward for ID.
