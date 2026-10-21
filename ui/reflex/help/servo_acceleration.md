Acceleration (Steps/s²)
=======================

The rate at which the motor increases to maximum speed, in steps per
second squared.

## How It Works

The servo uses a trapezoidal motion profile:
1. Accelerate at this rate until it gets to max speed
2. Move at max speed
3. Decelerate at this rate to stop at the target

## Guidelines

- Higher values = faster response but more stress on the
  mechanical system
- Lower values = smoother motion but slower moves
- Start with a low value. Increase it until the motion is fast
  and smooth

## Typical Ranges

| Setup                 | Typical Acceleration  |
|-----------------------|-----------------------|
| Light rotary table    | 500–2000 steps/s²     |
| Heavy rotary table    | 200–1000 steps/s²     |
| Lathe carriage        | 1000–5000 steps/s²    |

## Notes

- Value must be a positive integer
- Acceleration that is too high with heavy loads causes missed steps
- Acceleration that is too low makes the system slow to respond
- The servo uses the same acceleration value to increase and to
  decrease speed
