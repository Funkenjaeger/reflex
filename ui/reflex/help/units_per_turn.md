Units Per Turn
==============

The number of display units for one full revolution of the output
axis (after gearing). The default is 360, for degrees.

## Common Values

| Application           | Units per Turn | Meaning               |
|-----------------------|----------------|-----------------------|
| Rotary table (degrees)| 360            | 360° per revolution   |
| Rotary table (grads)  | 400            | 400 grads per rev     |
| Dividing (divisions)  | N              | N equal divisions     |

## How It Works

This value tells the DRO how to show one full rotation of the
output shaft. With the gearing ratio, it sets the relation between
motor steps and the displayed position.

## Examples

**Standard rotary table:**
Set to 360. The DRO shows the position in degrees (0–360).

**100-division indexing:**
Set to 100. The DRO shows the position as division numbers (0–100).
Each unit is 3.6° of physical rotation.

## Notes

- Must be a positive integer
- You cannot change this setting when ELS Mode is on
- A change to this value changes how positions are displayed. It
  does not change the physical motion
