ELS Axis Roles
==============

Sets which configured axes act as the spindle, the saddle (Z) and the
cross slide (X) for Electronic Lead Screw operation.

## Axis Roles

### Spindle Axis
The rotational reference input. This axis must connect to a spindle
encoder, and it should have Spindle Mode turned on. The ELS system reads
the spindle speed and position from this axis to synchronize the servo
feeds.

### Saddle Axis (Z)
The longitudinal axis, usually the carriage/saddle on a lathe. When ELS
is active, this axis follows the spindle at the selected thread pitch or
feed rate, for longitudinal cuts and threading.

### Cross Slide Axis (X)
The transverse axis, usually the cross slide on a lathe. When ELS is
active, this axis can be synchronized for facing operations or taper
turning.

## Configuration

- Select axis names from the dropdowns (or "None" to leave a role
  unassigned).
- Assign each role to a different axis.
- Z and X cannot operate in ELS mode until the spindle axis is
  configured.

## Notes

- The dropdowns list axes by the names configured in the Axes setup.
- If no axes are configured yet, set them up in the Axes screen first.
- You can leave X or Z as "None" if your setup needs only one
  synchronized axis.
