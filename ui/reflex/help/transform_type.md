Transform Type
==============

Sets how this axis changes the raw scale input(s) into its displayed
value. Each transform type uses a different formula.

## Available Types

### Identity
Sends the scale input through with no change.

    axis_value = scale_input

Use this for a simple axis with one scale (the most usual case).

### Scaling
Multiplies the scale input by a weight ratio.

    axis_value = scale_input × (Weight Num / Weight Den)

Use this to apply a gear ratio or unit conversion on top of the
scale ratio.

### Weighted Sum
Adds two scale inputs, each with its own weight.

    axis_value = (input_0 × W0_Num/W0_Den) + (input_1 × W1_Num/W1_Den)

Use this for compound axis calculations. Examples: the sum of two
linear encoders, or a measurement derived from two inputs.

### Angle Cos
Calculates the cosine projection of a scale input at a fixed angle.

    axis_value = scale_input × cos(angle)

Use this for an angled linear scale. Example: the X component of a
compound slide at a known angle.

### Angle Sin
Calculates the sine projection of a scale input at a fixed angle.

    axis_value = scale_input × sin(angle)

Use this for the Y component of an angled axis.

## Notes

- A change to the transform type shows or hides the related
  parameter fields below it
- To save changes, press "Apply Transform" at the bottom of the
  screen
