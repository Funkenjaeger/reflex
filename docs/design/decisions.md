# Design decisions

The decisions behind the contracts between the parts of Reflex, each with the
alternatives that were rejected.

- [One repository for firmware and UI](#one-repository-for-firmware-and-ui)
- [One register map for bootloader and application](#one-register-map-for-bootloader-and-application)
- [Stop overshoot correction](#stop-overshoot-correction)
- [Coupling-ratio check and gearbox position](#coupling-ratio-check-and-gearbox-position)
- [The gearbox is a table the owner sets](#the-gearbox-is-a-table-the-owner-sets)
- [PortOwner is the hardware seam](#portowner-is-the-hardware-seam)

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
  resolution, and its leadscrew. That machine's gearbox position does not
  matter: the overshoot was measured in all three of its positions at matched
  carriage speed and one table covers them, so the correction needs no
  knowledge of the gearing.
- There is no calibration procedure an ordinary user can run. The table was
  built by hand from recorded passes. Generalizing it is future work: a way for
  anyone to characterize their own machine, and to decide whether it needs the
  correction at all, from the controller alone.
- Until then, leave it off on any other machine, or check it as described in
  [the guide](../guide/stop-overshoot-correction.md).

## Coupling-ratio check and gearbox position

### Context

The servo is open loop: the controller knows what it told the leadscrew to do,
and only the Z scale knows what the carriage did. Two faults make those
disagree, and nothing caught either one. The half nut can open mid-cut, after
which the controller goes on reporting a cut while the carriage stands still.
And the development lathe has a three-position gearbox between servo and
leadscrew (B twice as fast as A, C half as fast) that the controller could not
see, so a moved lever silently doubled or halved every feed and thread pitch.

### Decision

- One measurement for both: the ratio of Z travel to commanded steps over a
  pass, which is 1 when coupled as expected, 0 with the half nut open, and 2 or
  0.5 with the lever moved.
- Every mismatch aborts the pass. A ratio that settles on another known gear
  position gives a yellow notice, and the next Cut or Retract adopts that
  position. A ratio that matches no position gives a red notice, adopts
  nothing, and fails again on every retry.
- The gear can be read about half a second after the take-up: a coarse check
  that the ratio is within 15 % of some position, then a tight check that it
  settles within 5 %.
- Step-valued settings stay stored in the reference position, A. The engaged
  position scales the servo's distance per step, so feed, pitch, retract and
  backlash follow it.
- The minimum distance from the stop at which Cut is allowed is sized for the
  fastest position, whatever position is set.
- The check ships logging only, behind a setting that is off by default.

### Why

- **A legitimate lever move is far more likely than an invalid ratio,** and the
  positions are a factor of two apart, so a short, coarse reading is enough to
  name the position. Only "slightly wrong" needs more of the pass.
- **Abort, then adopt on the next press,** so the operator always sees the
  change and agrees to it, and a pass is never finished at a ratio nobody chose.
- **The start distance covers the fastest position** because the take-up and,
  when threading, the phase correction run before the check has a reading, and
  the stop cannot cancel motion already committed. The same review found the
  distance sized for a correction of one leadscrew pitch, when the correction
  can be one thread pitch; it now uses the larger.

### Consequences and future work

- The gear positions and their factors were hard-coded for one lathe. They
  are now a table the owner sets, and the default is no gearbox: see
  [The gearbox is a table the owner sets](#the-gearbox-is-a-table-the-owner-sets).
- The thresholds come from replaying recorded passes in air, in all three
  positions, plus two real half-nut openings. No recording has a cutting load
  or a retract under the check, which is why it ships logging only.
- Backlash calibration only runs in the reference position.
- In thread mode the phase correction runs before the check can read, so the
  first pass after a lever move cuts briefly at the wrong pitch before the
  check stops it.

## The gearbox is a table the owner sets

### Context

The coupling-ratio check and the gearbox position above were built for one
lathe, with its three positions and their factors in the code. Most lathes
have no gearbox between servo and leadscrew, and those that do name and gear
their positions differently. With the table in the code, every installation
inherited one lathe's lever: a position named A on the main screen, a
cut-start margin sized for a x2 position that did not exist, and a refusal
that blamed the gearbox on a lathe with none.

### Decision

- **No gearbox is the default.** Nothing on screen names a position, the
  margin is not inflated, calibration is unrestricted, and the coupling check
  expects the plain leadscrew ratio. A mismatch still stops the pass when the
  check acts, with wording that does not mention gears, and nothing is ever
  adopted.
- **A gearbox is a table of 2 to 4 positions,** entered in Advanced ELS Settings
  as text, for example `A=1, B=2, C=1/2` or `Lo=1, Hi=3/2`. Names are the
  owner's, 1 to 3 characters, unique. The first position is the reference:
  factor 1, every step-valued setting stored in it, calibration only there.
- **Factors are typed,** as decimals or fractions relative to the reference,
  between 1/8 and 8, and any two at least 1.2x apart.
- **The table is persisted beside the position,** in the servo settings, so
  the settings snapshot and the commissioning ledger capture it. A stored
  position not in the table reads as the reference; with no gearbox it is
  ignored. An existing install gets no migration: its table is entered by
  hand.
- **A fast or slow hint comes from the factor,** above or below the
  reference, never from the name.

### Why

- **Text is the simplest editor that fits the existing settings rows,** and
  validation can say exactly what is wrong with an entry.
- **1.2x apart** keeps the 5 % bands the check settles in from overlapping,
  with margin for the 1 % scatter of a whole pass, so two positions cannot
  both claim a reading.
- **1/8 to 8** bounds the cut-start margin, which is sized for the fastest
  position, and keeps the decoupled floor (half the slowest factor, at most
  0.25) well above zero, the reading of an open half nut.

### Consequences and future work

- The check's thresholds were set on one lathe's positions, which were 2x
  apart. Tables with positions closer than that, down to the 1.2x limit, have
  not been run on a machine.
- A table that does not parse, from a hand-edited settings file, runs as no
  gearbox and is logged.
- Changing the table is refused during a pass, like changing the position.
- Factors are typed. Measuring one with a guided pass is future work.

## PortOwner is the hardware seam

### Context

The ELS stop state machines reached the firmware through a class named
`ElsStopHal`, which read as the hardware abstraction layer. It was not: it
covered one register block, and the servo, axis and input dispatchers wrote
their registers without passing through it. The name promised an invariant
point that did not exist.

### Decision

`PortOwner` (`ui/reflex/utils/port_owner.py`) is the hardware seam. Servo, axis
and input writes stay as they are and are not moved behind typed facades. The
class was renamed `ElsStopRegisters`, the ELS stop register facade, after the
register-block device class `ElsStop`. No behavior changed.

### Why

- Every exchange already funnels through `PortOwner`, and the wire-trace test
  pins that, so the invariants have one home.
- Rejected: per-device typed facades. They would add a layer per register block
  with nothing to enforce that `PortOwner` does not already enforce.
- Rejected: keeping the name and documenting it. The name was the defect.

### Consequences

Dispatchers other than the ELS stop code write through `PortOwner` directly, and
a new invariant belongs in `PortOwner`, not in a facade.
