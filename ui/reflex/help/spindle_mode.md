Spindle Mode
============

When this setting is on, the system treats this axis as a spindle (a
rotational speed source), not as a positional axis.

## Behavior

- **OFF (default):** The axis shows position values (mm, inches, or
  degrees). It supports sync and goto operations.
- **ON:** The axis shows rotational speed (RPM), not position. Use
  this for spindle encoders on lathes or milling machines.

## When to Enable

- The axis is connected to a spindle encoder
- You want to show RPM on the home screen
- You use ELS (Electronic Lead Screw) mode and need a spindle
  reference signal

## Notes

- You can still set a spindle-mode axis as the ELS spindle source in
  the ELS Setup screen
- Usually only one axis needs spindle mode on
- When spindle mode is on, the display uses the RPM format settings
  from the Formats screen
