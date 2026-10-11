ELS Thread Pitch
================

Selects the thread pitch (or feed per revolution): the distance the
carriage moves for one full spindle revolution. The controller writes the
selected pitch to the spindle sync ratio. The servo then follows the
spindle at the correct ratio during a cut.

## Metric Mode

Changes the pitch dropdown between the metric and imperial tables.

- **ON**: pitches show in millimeters (for example `1.00`, `1.50`,
  `2.00` mm/rev).
- **OFF**: pitches show in threads per inch (TPI; for example `20`,
  `16`, `13` threads per inch).

When you change this setting, the Pitch dropdown reloads. The Thread Type
resets to a value compatible with the new unit system.

## Pitch

Chooses one entry from the current table.

- **Metric:** the value is the linear travel of the carriage per
  spindle revolution (for example, M10×1.5 → pitch `1.50`).
- **Imperial:** the value is threads per inch (for example, 1/4-20 →
  pitch `20`).

The selected pitch sets the spindle sync ratio used during a cut. To cut
a thread that is not in the table, add an entry to the feeds table in the
source.

## Thread Type

Records the thread profile family, for the operator's reference. At
present it is for information only. It does not affect motion. The
available values depend on Metric Mode:

- **Metric:** ISO Metric, ACME
- **Imperial:** Unified, Whitworth, ACME

## Notes

- The visible ELS bar uses the same pitch selection. A change on either
  bar updates the other.
- For power feed (continuous feed, no thread), use the feed table on the
  visible ELS bar. The advanced bar shows only thread pitches.
