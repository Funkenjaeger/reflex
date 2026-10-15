Transform Type
==============

Sets how this axis changes the raw scale input(s) into its displayed
value. Each type uses a different formula.

## Available Types

### Identity
The axis shows the one scale input, with no change.

    axis_value = scale_input

Use this for a simple axis with one scale (the most usual case).

### Sum
The axis shows scale input 0 plus the Second Scale Input.

    axis_value = input_0 + input_1

The Second Scale Input is enabled only for this type. Use this for an axis
that adds two scales, for example two linear encoders.

## Notes

- A change to the transform type shows or hides the related
  parameter fields below it
- To save changes, press "Apply Transform" at the bottom of the
  screen
