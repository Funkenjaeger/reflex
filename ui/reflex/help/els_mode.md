ELS Mode
========

Electronic Lead Screw (ELS) mode synchronizes the servo motor to a
spindle encoder. This gives electronic threading and feeding on a manual
lathe.

## Behavior

- **OFF (default):** The servo operates in positioning mode. It moves to
  commanded positions (goto, jog, sync ratio).
- **ON:** The servo locks to the spindle encoder. It follows the spindle
  at a ratio set by the selected feed or thread pitch. The ELS
  configuration overrides the gearing ratios and units-per-turn settings.

## When to Enable

- You have a spindle encoder installed and configured
- You want to cut threads or use power feed synchronized to the
  spindle
- You have configured the lead screw pitch parameters (see below)

## Requirements

- A spindle axis must be configured and assigned in ELS Setup
- The lead screw pitch must be correctly set for your machine
- At least one saddle (Z) or cross slide (X) axis must be assigned

## Notes

- When ELS mode is on, the gearing ratio and units-per-turn fields on
  this screen are disabled. The ELS system controls them.
- Set the ELS axis assignments in the ELS Setup screen.
- Select the feed and thread from the home screen toolbar.
