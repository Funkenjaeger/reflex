ELS Direction Polarity
======================

Three independent toggles that capture the wiring relationships
between the operator's "forward" direction, the spindle encoder,
the Z-axis scale, and the servo. Each toggle controls one specific
piece of motion; set them once at first commissioning, then leave
them alone.

You need three flags (not one) because three different physical
relationships need to be captured independently:

## Invert Sync Direction

Controls the **carriage direction during a synchronized cut**.

- Flips the sign of the spindle's sync ratio numerator.
- Captures: operator's "forward" spindle rotation × servo step
  polarity × leadscrew lead direction.
- Toggle this if pressing Engage and turning the spindle forward
  drives the carriage the **wrong way**.

## Invert Stop Direction

Controls **which side of the stop position triggers the auto-stop**
and the **backlash takeup direction**.

- Flips the sign of `elsStop.stopDirection` written to the firmware.
- Captures: operator's "forward" carriage direction × Z-scale
  wiring sign.
- Toggle this if the ELS stop never trips (firmware looks for the
  carriage to approach from the wrong side), or if the backlash
  takeup move runs in the wrong direction at the start of a cut.

## Invert Retract Direction

Controls **which way the servo moves during a Retract command**
(non-synchronized move between cuts).

- Flips the sign that converts Z-scale count deltas to leadscrew
  step deltas during retract.
- Captures: servo step polarity × Z-scale wiring sign (independent
  of spindle direction).
- Toggle this if pressing Retract drives the carriage away from
  Start Z instead of toward it.

## Why Three Separate Flags

A typical lathe wiring fault won't show up in all three motions —
it might break only the cut, only the stop trigger, or only the
retract. Bundling them into one "invert everything" toggle would
mean you'd have to flip wiring elsewhere to compensate for the
side-effects. Keep them separate so you can fix exactly what's
wrong without breaking the rest.

## Commissioning Order

1. With all three flags off, engage ELS and turn the spindle slowly
   in your "forward" direction. **Carriage should travel away from
   the chuck.** If not, toggle *Invert Sync Direction*.
2. Set a stop Z some distance ahead of the carriage. Run a slow
   cut and verify the ELS stop trips at the right position. If the
   carriage runs through the stop without tripping, toggle *Invert
   Stop Direction*.
3. After the stop fires, command a Retract. The carriage should
   move back toward Start Z. If it runs the other way, toggle
   *Invert Retract Direction*.

Once all three motions go the right way, the flags should not need
to change again unless you re-wire something.
