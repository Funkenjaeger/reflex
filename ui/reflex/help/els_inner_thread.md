Inner / Outer Thread
====================

Tells the controller whether the workpiece is being threaded on the
outside (OD) or the inside (ID). Affects the direction in which the
cross slide must be backed off for the controller to consider the
tool "clear" of the cut.

## Behavior

- **OFF (OD work):** The cross slide is considered clear when the
  X position is greater than the start diameter (tool retracted
  outward).
- **ON (ID work):** The cross slide is considered clear when the
  X position is less than the start diameter (tool retracted
  inward).

This matters during threading: the controller will not begin a
Z-axis retract while the tool is still at depth, because doing so
would drag the cutter along the threads. The Inner Thread flag tells
the controller which direction "out of the cut" is.

## When to Set

- Set **OFF** for ordinary external threads (most lathe threading).
- Set **ON** for internal threads, bored threads, or any cut where
  the cross slide retracts toward the spindle centerline rather
  than away from it.

## Notes

- The flag only matters when the bar is in wizard mode and threading
  is selected. Stop-only and stop+retract modes ignore it.
- The diameter values you enter (Major ø, Minor ø) are interpreted
  the same way regardless of this setting — they're the X-axis
  positions at the major and minor thread diameters.
