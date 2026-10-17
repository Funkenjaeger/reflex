Sync ratio
==========

The sync ratio sets how many units the servo moves for each unit of
movement on the reference axis. It applies when synchronized movement
(sync mode) is active.

    servo_movement = reference_movement × (Numerator / Denominator)

## Unit Normalization

The ratio uses the **display units that the user selected**
(millimeters or inches):

- **In MM mode:** the ratio applies per millimeter of reference
  axis movement. The servo moves in its own configured units
  (usually degrees for a rotary table).
- **In Inch mode:** the ratio applies per inch of reference axis
  movement.

The DRO does the unit conversion automatically. The physical
behavior stays the same in each display mode.

## Examples

### Rotary Table (1:100 ratio)
- Numerator = 1, Denominator = 100
- For each 1 mm of travel on the reference axis, the servo
  turns 1/100 = 0.01°
- 100 mm of travel → 1° of rotation
- Use this for very fine angular positioning from linear travel

### Direct 1:1
- Numerator = 1, Denominator = 1
- The servo moves 1 unit for each 1 unit of reference movement
- Example: 1 mm of reference travel → 1° of rotation

### Threading (lathe)
- Numerator = 1, Denominator = 25
- For each mm of spindle-driven travel, the carriage moves
  1/25 = 0.04 mm. This makes a 0.04 mm pitch thread
- Set Numerator and Denominator to get the necessary thread pitch

### Dividing head with 90:1 worm gear
- Numerator = 1, Denominator = 90
- The worm gear decreases motion by 90:1
- 90 units of input → 1° of table rotation

## Notes

- Both values must be positive integers
- Two synced axes can enable sync at the same time only if they
  do not share the same physical scale input
- The sync ratio applies on top of the scale ratio and the axis
  transform
- A change between MM and Inch display modes does not change the
  physical behavior. The DRO adjusts the ratio automatically
