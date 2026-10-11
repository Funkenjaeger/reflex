Scale Input
===========

This setting selects the hardware scale input that supplies data to
this axis.

## How It Works

The DRO hardware has multiple scale input channels (Input 0, Input 1,
etc.). Each channel reads from a physical encoder connected to the
board. This dropdown connects one hardware input to this axis.

## Multiple Axes, Same Input

Multiple axes can read from the same physical input. For example:
- An "X" axis using Input 0 with identity transform
- A "Compound X" axis using Input 0 with an Angle Cos transform

Thus you can get multiple logical axes from a single encoder.

## Sync Conflict

Two axes can share the same physical scale input. If they do, they
cannot both have sync mode on at the same time. The system warns you
when it detects a conflict.

## Notes

- The dropdown lists all configured scale inputs by index
- If no inputs are configured, set them up in the Inputs screen first
