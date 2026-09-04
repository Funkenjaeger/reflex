# One Modbus register map for the bootloader and the app

**Status:** proposed, 2026-09-04. Drafted in a `/closeloops` session at Evan's
request, jointly for the two tasks that each specified half of it:
the Modbus field bootloader and the firmware build-identity register.

## Why one map, and what "one map" actually means

Two tasks independently specified registers on the same link. The bootloader
needs a boot-stage status register so a client can tell which program answered;
the build-identity task needs a readback saying which firmware build is
executing. Designed separately, those become two conventions that disagree.

But "one map" cannot mean "the bootloader implements the app's map." The app's
map is `rampsSharedData_t`, cast wholesale into uint16 holding registers
(`RampsModbusData.u16regsize = sizeof(shared)/sizeof(uint16_t)`). It is roughly
436 registers today and it grows by design -- the established convention is to
append at the tail of `elsStop_t`, which is the last member, so existing offsets
never move.

**That growth is exactly why identity cannot live at the tail.** The bootloader
is write-protected in sector 0 and is meant to outlive many app layout changes.
If the identity registers sat at the end of `elsStop_t`, their address would be
a function of `sizeof(elsStop_t)` -- so every time the app grew, the WRP'd
bootloader would be answering at the wrong offset, and it could not be updated
to follow without defeating the point of write-protecting it.

## Decision

**A small IDENTITY WINDOW at a fixed base address, outside `rampsSharedData_t`,
implemented identically by both programs.** It is the only thing both implement.
Everything else is stage-specific.

### Identity window -- `ELS_ID_BASE` = 2048 (0x0800)

Read-only, both stages, 8 registers. Well clear of the app struct's current
~436 registers with room for it to keep growing.

| Off | Name | Meaning |
|-----|------|---------|
| +0 | `idMagic` | `0x454C`. Proves the window is implemented rather than reading as incidental zeros. |
| +1 | `idStage` | **1 = bootloader, 2 = application.** The discriminator. |
| +2 | `idWindowVersion` | Layout version of THIS window. Starts at 1. Independent of `protocolVersion`. |
| +3 | `idBuildRevLo` | Git short rev, low 16 bits. |
| +4 | `idBuildRevHi` | Git short rev, high 16 bits. 7 hex chars = 28 bits, so the top 4 read zero. |
| +5 | `idBuildDirty` | 1 if built from a dirty tree. A rev from a dirty tree does not identify the source. |
| +6 | `idAppProtocolVersion` | The app's `protocolVersion`; reads 0 in the bootloader. |
| +7 | `idReserved` | Pad to 8. |

**A client reads this window FIRST, always, before deciding what else is safe to
read.** That resolves the chicken-and-egg the bootloader task creates by having
both stages answer on the same slave address.

### Bootloader control window -- `ELS_BL_BASE` = 2304 (0x0900)

Bootloader only. The app returns an illegal-address exception here, which is
itself a usable signal.

| Off | Name | Meaning |
|-----|------|---------|
| +0 | `blStatus` | idle / erasing / writing / verifying / bad-image / ready-to-jump |
| +1 | `blResult` | Outcome of the operation counted by `blSeq`. 0 = OK. |
| +2 | `blSeq` | Increments once per completed operation. Edge-detect this. |
| +3 | `blCommand` | Host writes erase/write/verify/jump/stay; **firmware clears on consume.** |
| +4..5 | `blTargetAddr` | 32-bit flash address for the next data-window write. |
| +6..7 | `blImageLen` | 32-bit image length. |
| +8..9 | `blImageCrc` | 32-bit CRC32. |
| +10 | `blSlot` | Slot this operation targets, if A/B is adopted. |
| +11 | `blActiveSlot` | Slot the bootloader would jump to now. |
| +12..15 | reserved | |
| +16..115 | `blData[100]` | The ~100-register data window written with FC 0x10. |

## Conventions this inherits rather than reinvents

**`blSeq` must sit at a LOWER address than anything it counts.** Modbus FC3 copies a block one register at a
time in ascending address order and an interrupt can land between any two, so
seq-first makes a torn read come out as (stale seq, new payload), which
edge-detection harmlessly re-reads. The inverted order is the 2026-08-22
`takeupSeq`/`takeupResult` bug on elspi. `Ramps.h` states this invariant for
`calSeq` and `diagSeq`; it applies here unchanged.

**`blCommand` is the `calCommand` hand-off, not a completion flag.** Firmware
clears it the instant it consumes it, long before the operation finishes. Poll
`blSeq`, never `blCommand`.

**CRC32 is the STM32 hardware unit's variant** -- poly `0x04C11DB7`, init
`0xFFFFFFFF`, no reflection, no final XOR. This is NOT zlib CRC32, and the
client must match it.

## Consequences

**`protocolVersion` does NOT bump for this, and that is the point.** It
documents the layout of `rampsSharedData_t`. The identity window lives outside
that struct and is decoded separately, so `sizeof(shared)` is unchanged, no
existing offset moves, and the UI mirror's contract test -- which pins firmware
and mirror register counts against each other -- sees nothing. The
build-identity task's requirement was explicitly "do not bump `protocolVersion`
for this: that register guards the register MAP, and conflating the two makes
every rebuild look like a layout change." Keeping the window out of the struct
satisfies that structurally rather than by promising to remember.

**A UI that predates the window is unaffected.** It never reads 2048+, so
nothing it does changes.

**The readback is a cross-check, not an identity of record.** `~/firmware/
flashed.json` on the probe host already stores full rev + md5 per flash; the
register supplies the machine's side of a comparison. A 7-hex-char short rev
alone would be weak identity, and reading it in isolation proves less than
pairing it against that manifest.

**This closes the 2026-08-16 failure mode**: programming and verification both
report success, the UI reconnects, and the board runs the PREVIOUS image with no
error anywhere. `protocolVersion` cannot catch that whenever the layout did not
change, which is most flashes. `idBuildRev` can.

## Not decided here

- **A/B slots vs single slot.** They fit -- `reflex-fw.bin` is 41,092 bytes and
  RM0383 Table 4 gives the F411CE three 128 KB sectors -- and `blSlot` /
  `blActiveSlot` are reserved for them, but adopting A/B is a separate call that
  also determines whether the boot-attempt-counter anti-brick logic is needed.
- **The exact `blStatus` / `blResult` enumerations.** They should follow the
  `ELS_CAL_*` / `ELS_TAKEUP_*` precedent: distinct codes for distinguishable
  failures, never a binary fault flag.
- **Whether `ELS_ID_BASE` = 2048 is far enough.** It is ~4.7x the current struct
  size. If `rampsSharedData_t` is ever expected to approach that, move the base
  before shipping, not after.

## Provenance

The app-side facts here were read out of `fw/Core/Inc/Ramps.h` and
`fw/Core/Src/Ramps.c` on 2026-09-04 (`protocolVersion = 7`, the tail-append
convention, the `calSeq` ordering invariant, the diagnostic scratchpad's
schema-guard pattern, the explicit-pad rule). The bootloader-side facts --
hardware-derived DE, no ROM tier, PA11/PA12 NC -- are Evan's, from the
schematics, 2026-09-03. **Nothing in this document has been built or measured.**
