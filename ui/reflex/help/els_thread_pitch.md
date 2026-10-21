ELS Thread Pitch
================

The thread pitch (or feed per revolution) is the distance the carriage
moves for one full spindle revolution. The controller writes the selected
pitch to the spindle sync ratio. The servo then follows the spindle at the
correct ratio during a cut.

Choose the pitch with the **FEED/THREAD** control on the ELS bar. It opens
the **Select feed** list. The list shows the tables for the current display
unit, MM or IN.

## Thread type

Thread type is the only control in Advanced ELS settings. It records the
thread profile family, for the operator's reference. It is for information
only. It does not affect motion, and nothing else reads it. The choices
follow the display unit:

- **MM:** ISO Metric, ACME
- **IN:** Unified, Whitworth, ACME

## Notes

- The ELS bar has the FEED/THREAD control, the pitch value, and the up and
  down steppers. All of them select from the Select feed tables.
- The advanced bar has no pitch or feed table control.
