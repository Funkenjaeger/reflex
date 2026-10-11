Weight Numerator / Denominator
===============================

The weight ratio is a multiplier. A Scaling or Weighted Sum transform
applies it to a scale input.

    weighted_value = scale_input × (Weight Numerator / Weight Denominator)

## When Used

- **Scaling transform:** One weight ratio applies to the primary
  scale input.
- **Weighted Sum transform:** Each of the two scale inputs has its
  own independent weight ratio.

## Examples

| Scenario                 | Num | Den | Effect              |
|--------------------------|-----|-----|---------------------|
| Pass-through (no change) | 1   | 1   | 1:1 ratio           |
| Halve the reading        | 1   | 2   | 0.5× multiplier     |
| Gear ratio 1:72          | 1   | 72  | Divide by 72        |

## Notes

- Both values must be positive integers
- The system applies the weight after the scale ratio conversion
- Press "Apply Transform" to save changes
