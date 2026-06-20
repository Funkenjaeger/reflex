ELS Thread Pitch
================

Selects the thread pitch (or feed-per-revolution) that the carriage
follows for one full spindle revolution. The selected pitch is
written to the spindle's sync ratio so the servo tracks the spindle
at the right ratio during a cut.

## Metric Mode

Switches the pitch dropdown between metric and imperial tables.

- **ON** — pitches listed in millimeters (e.g. `1.00`, `1.50`,
  `2.00` mm/rev).
- **OFF** — pitches listed in threads-per-inch (TPI; e.g. `20`,
  `16`, `13` threads per inch).

Switching this setting reloads the Pitch dropdown and resets the
Thread Type to a value compatible with the new unit system.

## Pitch

Chooses one entry from the current table.

- **Metric:** the value is the linear travel of the carriage per
  spindle revolution (e.g. M10×1.5 → pitch `1.50`).
- **Imperial:** the value is threads per inch (e.g. 1/4-20 → pitch
  `20`).

The pitch you select drives the spindle sync ratio used during a
cut. To cut a thread not in the table, add an entry to the feeds
table in the source.

## Thread Type

Documents the thread profile family for operator reference. Currently
informational — it does not affect motion. Available values depend
on Metric Mode:

- **Metric:** ISO Metric, ACME
- **Imperial:** Unified, Whitworth, ACME

## Notes

- The pitch selection is shared with the visible ELS bar. Changing
  it on either bar updates the other.
- For power feeding (continuous feed, no thread), use the feed
  table on the visible ELS bar instead — the advanced bar only
  exposes thread pitches.
