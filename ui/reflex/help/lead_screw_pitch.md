Lead Screw Pitch
================

These settings describe the physical lead screw on your lathe.
The ELS uses them to calculate the correct servo speed for each
thread pitch or feed rate.

## Lead Screw Pitch (in MM or IN)

The linear distance the carriage moves for each revolution of the
lead screw. Set the value and the unit (mm or inch) to match your
machine.

### Common Values

| Machine Type          | Pitch     | Unit  |
|-----------------------|-----------|-------|
| Metric lathe (2mm)    | 2.0       | MM    |
| Metric lathe (3mm)    | 3.0       | MM    |
| Imperial lathe (8 TPI)| 0.125     | Inch  |
| Imperial lathe (12 TPI)| 0.0833  | Inch  |

**TPI to pitch:** pitch_inches = 1 / TPI
- 8 TPI → 1/8 = 0.125"
- 12 TPI → 1/12 ≈ 0.0833"

## Pitch in Inch

Sets the unit of the lead screw pitch value: inches or millimeters.
Turn it on if the lead screw is specified in TPI or inches. Turn it
off for a metric lead screw.

## Lead Screw Pitch Steps

The number of stepper motor steps for one full revolution of the
lead screw. This value depends on the motor step angle and the
microstepping configuration.

### Examples

| Motor         | Microstepping | Steps/Rev |
|---------------|---------------|-----------|
| 1.8° stepper  | Full step     | 200       |
| 1.8° stepper  | 1/4 step      | 800       |
| 1.8° stepper  | 1/8 step      | 1600      |
| 1.8° stepper  | 1/16 step     | 3200      |
| 0.9° stepper  | Full step     | 400       |

If a gear or belt reduction is between the motor and the lead
screw, multiply the steps by the gear ratio.

## Notes

- Set all three parameters correctly for accurate threading
- You can edit these fields only when ELS Mode is on
- Incorrect values cause incorrect thread pitches
