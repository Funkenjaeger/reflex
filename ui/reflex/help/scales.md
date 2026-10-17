Scale Ratio Configuration
=========================

Each scale input reads raw encoder counts. This setting changes the counts
into millimeters for the display:

    position_mm = raw_counts × (Numerator / Denominator)

## Two ways to enter it

**Scale entry** sets the form that you type. Both forms write the same
setting. Use the form that matches the number you have.

- **Resolution**: microns per count, the number printed on the scale
  ("1um", "5um"). Use this form for a usual linear scale.
- **Ratio**: the numerator and denominator above, in millimeters over
  counts. Use this form when the number you have *is* a ratio, for
  example a drive ratio through gearing.

The screen opens on a form that can show the stored value. Sometimes
it opens on Ratio for a scale that you expected as a resolution. Then
the stored value has no exact decimal form. A change to Resolution
would change the setting.

A switch between the two forms loses nothing. The value is stored as
an exact fraction in both forms.

## Common Scale Configurations

| Scale Type       | Resolution | Numerator | Denominator |
|------------------|------------|-----------|-------------|
| 1 µm linear      | 0.001 mm   | 1         | 1000        |
| 5 µm linear      | 0.005 mm   | 1         | 200         |
| 0.5 µm linear    | 0.0005 mm  | 1         | 2000        |
| 0.001" encoder   | 0.0254 mm  | 127       | 5000        |

## Examples

**1 µm glass scale:**
Each tick = 0.001 mm. Set Numerator=1, Denominator=1000.

**5 µm glass scale:**
Each tick = 0.005 mm. Set Numerator=1, Denominator=200.

**0.001" (1 mil) scale:**
Each tick = 0.0254 mm = 127/5000. Set Numerator=127, Denominator=5000.

## Tips

- Both ratio values must be positive integers
- If the DRO reads exactly half or double the expected value,
  the ratio is probably wrong by a factor of 2
- Examine the scale datasheet for the exact resolution per count
- Do not trust the sticker until a measurement confirms it. On this
  machine the X scale was set at 2.5 µm, but the sticker said 1 µm.
  For months the DRO read 2.5× the true travel. A dial indicator
  over a known move finds the correct value in ten minutes.
- Direction belongs to **Reverse**, not to the ratio. Never use a
  negative scale value.
