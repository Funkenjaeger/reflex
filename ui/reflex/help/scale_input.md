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
- An "X" axis using Input 0 with the Identity transform
- A "Total" axis using Input 0 with the Sum transform, with Input 1 as
  its Second Scale Input

The **Second Scale Input** is enabled only when the Transform Type is Sum.

Thus you can get multiple logical axes from a single encoder.

## Sync Conflict

Two axes can share the same physical scale input. If they do, they
cannot both have sync mode on at the same time. If another axis that uses
the same physical input already has sync on, turning sync on is blocked
and a warning is written to the log. Turn sync off on the other axis
first.

## Notes

- The dropdown lists all configured scale inputs by index
- If no inputs are configured, set them up in the Inputs screen first
