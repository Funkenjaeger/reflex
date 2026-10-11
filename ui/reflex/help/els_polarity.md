ELS Direction Configuration
===========================

The firmware sets the direction convention (direction canonicalization)
through the per-scale `scaleDir` and per-servo `servoDir` registers. The
Python UI writes these values when it connects. It writes them again
whenever the matching "Reverse" toggle changes in settings.

## Scale Input Reverse

Each scale input has a **Reverse** toggle (Setup > Inputs). When it is on,
it writes `scaleDir = -1` to the firmware register of that scale. When it
is off, it writes `scaleDir = +1`. The firmware applies this direction to
the encoder delta at the source. Thus a positive encoder delta always
equals a positive axis direction, whatever the wiring.

## Servo Reverse

The servo has a **Reverse** toggle (Setup > Servo). When it is on, it
writes `servoDir = -1` to the firmware register of the servo. When it is
off, it writes `servoDir = +1`. The firmware applies this direction to the
DIR pin output at the destination. Thus a positive step command always
equals positive motor motion, whatever the wiring.

## Commissioning

Set each toggle so that:
1. Positive encoder movement shows as increasing position on the DRO.
2. A positive step command moves the servo in the expected physical direction.

After you set the toggles, you do not need to change them unless you
rewire something.
