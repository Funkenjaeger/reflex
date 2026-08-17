ELS Direction Configuration
===========================

Direction canonicalization is handled by firmware via per-scale `scaleDir`
and per-servo `servoDir` registers. The Python UI writes these values on
connect and whenever the corresponding "Reverse" toggle changes in settings.

## Scale Input Reverse

Each scale input has a **Reverse** toggle (Settings → Scale Inputs). When
enabled, it writes `scaleDir = -1` to that scale's firmware register; when
disabled, `scaleDir = +1`. The firmware applies this direction to the
encoder delta at the source — positive encoder delta always equals positive
axis direction regardless of wiring.

## Servo Reverse

The servo has a **Reverse** toggle (Settings → Servo). When enabled, it
writes `servoDir = -1` to the servo's firmware register; when disabled,
`servoDir = +1`. The firmware applies this direction to the DIR pin output
at the destination — positive step command always equals positive motor
motion regardless of wiring.

## Commissioning

Set each toggle so that:
1. Positive encoder movement shows as increasing position on the DRO.
2. A positive step command moves the servo in the expected physical direction.

Once set, these should not need to change unless you re-wire something.
