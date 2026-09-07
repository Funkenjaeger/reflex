Scale Ratio Configuration
=========================

Each scale input reads raw encoder counts. The setting converts these
counts into millimeters for display:

    position_mm = raw_counts × (Numerator / Denominator)

## Two ways to enter it

**Scale entry** chooses which form you type. Both write the same
setting, so use whichever matches the number you actually have.

- **Resolution** — microns per count, the number printed on the scale
  itself ("1um", "5um"). Use this for an ordinary linear scale.
- **Ratio** — the numerator and denominator above, in millimetres over
  counts. Use this when the number you were given *is* a ratio, such
  as a drive ratio through gearing.

The screen opens on whichever form can express what is already stored.
If it opens on Ratio for a scale you expected to see as a resolution,
that means the stored value cannot be written exactly as a decimal —
changing it to Resolution would alter the setting.

Nothing is lost by switching between them: the value is stored as an
exact fraction either way.

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
- If your DRO readings are exactly half or double the expected value,
  your ratio is likely off by a factor of 2
- Check your scale's datasheet for the exact resolution per count
- Trust the sticker only as far as a measurement confirms it. On this
  machine the X scale was provisioned at 2.5 µm against a sticker
  reading 1 µm, and the DRO read 2.5× the true travel for months. A
  dial indicator over a known move settles it in ten minutes.
- Direction belongs to **Reverse**, not to the ratio. A negative scale
  value is never the answer.