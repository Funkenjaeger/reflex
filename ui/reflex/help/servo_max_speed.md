Maximum speed (Steps/s)
=======================

The maximum speed that the stepper or servo motor can reach, in steps
per second.

## Guidelines

- Start with a low value and increase it in small steps
- A value above the motor's capability causes missed steps and
  position errors
- The actual speed depends on your motor, driver and power supply

## Typical Ranges

| Motor Type            | Typical Max Speed     |
|-----------------------|-----------------------|
| NEMA 17 stepper       | 1000–3000 steps/s     |
| NEMA 23 stepper       | 500–2000 steps/s      |
| NEMA 34 stepper       | 300–1500 steps/s      |
| Closed-loop servo     | 2000–10000 steps/s    |

These values are approximate. The actual values depend on the
microstepping settings, supply voltage and load.

## Notes

- Value must be a positive integer
- The motor accelerates up to this speed (see the Acceleration
  setting) and never goes above it
- If you hear grinding, or the motor stalls during fast moves,
  decrease this value
