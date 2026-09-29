# Stop overshoot correction

## Context

The ELS stop halts the carriage by ceasing step output when the Z scale crosses
the stop position. On the one machine this has been measured on, a lathe driven
by a CL86T closed-loop stepper drive, the carriage carries on for about 11 to
12 ms after the last step. The overshoot grows with feed rate, from nothing when
hand-cranked to about 0.2 mm at the fastest feed measured, and it repeats from
pass to pass.

That is a sample of one. Other drives, and the same drive with other settings,
will behave differently, and on some machines no correction is needed at all.

## Decision

Fire the stop early by the overshoot predicted for the current feed rate.

- The firmware takes a separate `stopOffset` register, in encoder counts, and
  triggers at the stop position minus that offset. The operator's target is
  never rewritten.
- The UI computes the offset from the approach rate using a measured table
  (rate against the worst overshoot seen), interpolated and rounded up, plus a
  small margin. Above the table's top rate it holds the top value; it never
  extrapolates.
- The correction is off by default.

## Why

- **It repeats, so it can be cancelled** without modelling what causes it.
- **The drive offered no setting that removes the delay.**
- **A table, not a formula,** because the measurements do not cleanly fit a
  model.
- **The offset never undercuts the prediction.** Stopping short leaves material
  that can still be removed; running past a shoulder scraps the part. The
  offset is clamped to 1 mm, which bounds what a bad value can do.

## Consequences and future work

- The table describes one machine: its drive and drive settings, its scale
  resolution, and its motor-to-carriage gearing. Whether gearbox position
  matters has not been measured yet.
- There is no calibration procedure an ordinary user can run. The table was
  built by hand from recorded passes. Generalizing it is future work: a way for
  anyone to characterize their own machine, and to decide whether it needs the
  correction at all, from the controller alone.
- Until then, leave it off on any other machine, or check it as described in
  `docs/guide/stop-overshoot-correction.md`.
