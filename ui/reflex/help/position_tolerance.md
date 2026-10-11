Position Tolerance (mm)
=======================

The acceptable position error, in millimeters. When the current
position is within this distance of a target, the DRO treats the axis
as "at position."

## How It Works

This applies during a servo goto operation. When the actual position
is within the tolerance of the commanded target, the position
indicator changes color (or shows a confirmation).

## Typical Values

| Application           | Tolerance   |
|-----------------------|-------------|
| General machining     | 0.01 mm     |
| Precision work        | 0.005 mm    |
| Rough positioning     | 0.1 mm      |
| Rotary indexing       | 0.05 mm     |

## Notes

- The value is always in millimeters, whatever the display unit
- The field accepts decimal values
- If the tolerance is too tight, encoder noise can prevent the
  indicator from ever showing "at position"
- If the tolerance is too loose, you lose the benefit of position
  verification
