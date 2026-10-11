Safe Diameter
=============

Adds a committed diameter to **stop + retract** mode. With it, the
controller can tell whether the tool is clear of the work before it feeds
the carriage back.

## Why it matters

When you press **Retract**, the carriage goes back to Start Z under
power. If the tool is still in the groove, it is dragged back along the
thread.

You always back off in X by hand. There is one servo, and it drives the
leadscrew. Thus nothing moves the tool out of the cut automatically.

Wizard mode already has this guard. It knows the diameter you set in
step 3, and it disables Retract until X is clear of it. Stop + retract
had no committed diameter to compare against, so nothing caught the
mistake.

## Behavior

- **ON (default):** a **Safe ø** field appears in the advanced bar.
  Set it to a diameter that the tool is safely clear of. Retract is then
  refused, with a message, until X is past it.
- **OFF:** the field is hidden, and stop + retract behaves as it always
  has. Retract is never gated on X.

## Until you set a value

Turning this on does **not** gate anything by itself. The check stays
inactive until a diameter is actually committed. Until then, Retract
behaves exactly as before.

This is intentional. An earlier version made the gate live without a
committed value. It blocked *every* threading retract on a machine whose
X readout sits below its power-on zero. The result was a gray Retract
button with no way to satisfy it. A value that you set is what makes the
gate trustworthy, so the value comes first.

## Notes

- Same field as **Major ø** in wizard mode, and the same stored value.
  Here it has a different name because its job is the clearance gate,
  not the thread's dimension.
- Stop-only mode never shows it, because that mode has no Retract.
- The **Inner Thread** setting decides which direction counts as clear:
  outward for OD work, inward for ID.
