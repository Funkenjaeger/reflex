Inner / Outer Thread
====================

Tells the controller whether you are threading the outside (OD) or the
inside (ID) of the workpiece. It sets the direction in which you must back
off the cross slide before the controller considers the tool "clear" of
the cut.

## Behavior

- **OFF (OD work):** The cross slide counts as clear when the X position
  is greater than the start diameter (tool retracted outward).
- **ON (ID work):** The cross slide counts as clear when the X position
  is less than the start diameter (tool retracted inward).

This matters during threading. The controller does not start a Z-axis
retract while the tool is still at depth, because the retract would drag
the cutter along the threads. The Inner Thread setting tells the
controller which direction is "out of the cut".

## When to Set

- Set **OFF** for ordinary external threads (most lathe threading).
- Set **ON** for internal threads, bored threads, or any cut where
  the cross slide retracts toward the spindle centerline, not away
  from it.

## Notes

- This setting matters wherever you have committed a diameter and
  threading is selected. That means wizard mode, and stop + retract when
  **Safe diameter** is on. Stop-only ignores it, because stop-only has no
  Retract to gate.
- The diameter values you enter (Major ø, Minor ø) mean the same thing
  whatever this setting is. They are the X-axis positions at the major
  and minor thread diameters.
