# Design decisions

The decisions behind the contracts between the parts of Reflex, each with the
alternatives that were rejected.

- [One repository for firmware and UI](#one-repository-for-firmware-and-ui)
- [One register map for bootloader and application](#one-register-map-for-bootloader-and-application)
- [Stop overshoot correction](#stop-overshoot-correction)

## One repository for firmware and UI

### Context

Reflex is STM32 firmware and a Raspberry Pi touchscreen UI that talk over a
Modbus register map. They began as two repositories, but every interface change
needed a matching commit in each, and nothing kept the pair in step except
convention.

### Decision

One repository with `fw/` and `ui/`, released together under one version. CI
is filtered by path. Both histories were kept, and the old repositories are
archived.

### Why

- A version number names a firmware and UI pair that were tested together.
- A register-map change lands, and reverts, as one commit. One schema now
  generates both the C struct and the Python decoder.
- Rejected: separate versions per half (it undoes the point), a git submodule
  (daily friction for little gain at this size), and staying split (nothing
  stops a half-reverted pair).

### Consequences

Every release carries both halves even when only one changed, and releases are
cut deliberately with an explicit version.

## One register map for bootloader and application

### Context

The bootloader and the ELS application answer on the same Modbus address, so a
host has to know which one it is talking to before it does anything else.

### Decision

Both expose an **identity window at a fixed address (2048), outside the
application's register struct**: stage (bootloader or application), build
revision, and the application's `protocolVersion`. The bootloader adds its own
control window at 2304; the application does not answer there. The contract is
one append-only header, `fw/Core/Inc/els_identity.h`.

### Why

The address is fixed because the bootloader is flashed once and must outlive
every change to the application's layout. It sits outside the struct so that
`protocolVersion` keeps meaning "the register layout changed" and not "the
firmware was rebuilt".

## Stop overshoot correction

### Context

The ELS stop halts the carriage by ceasing step output when the Z scale crosses
the stop position. On the one machine this has been measured on, a lathe driven
by a CL86T closed-loop stepper drive, the carriage carries on for about 11 to
12 ms after the last step. The overshoot grows with feed rate, from nothing when
hand-cranked to about 0.2 mm at the fastest feed measured, and it repeats from
pass to pass.

That is a sample of one. Other drives, and the same drive with other settings,
will behave differently, and on some machines no correction is needed at all.

### Decision

Fire the stop early by the overshoot predicted for the current feed rate.

- The firmware takes a separate `stopOffset` register, in encoder counts, and
  triggers at the stop position minus that offset. The operator's target is
  never rewritten.
- The UI computes the offset from the approach rate using a measured table
  (rate against the worst overshoot seen), interpolated and rounded up, plus a
  small margin. Above the table's top rate it holds the top value; it never
  extrapolates.
- The correction is off by default.

### Why

- **It repeats, so it can be canceled** without modeling what causes it.
- **The drive offered no setting that removes the delay.**
- **A table, not a formula,** because the measurements do not cleanly fit a
  model.
- **The offset never undercuts the prediction.** Stopping short leaves material
  that can still be removed; running past a shoulder scraps the part. The
  offset is clamped to 1 mm, which bounds what a bad value can do.

### Consequences and future work

- The table describes one machine: its drive and drive settings, its scale
  resolution, and its motor-to-carriage gearing. Whether gearbox position
  matters has not been measured yet.
- There is no calibration procedure an ordinary user can run. The table was
  built by hand from recorded passes. Generalizing it is future work: a way for
  anyone to characterize their own machine, and to decide whether it needs the
  correction at all, from the controller alone.
- Until then, leave it off on any other machine, or check it as described in
  [the guide](../guide/stop-overshoot-correction.md).
