# One Modbus register map for the bootloader and the app

**Status:** proposed 2026-09-04; IMPLEMENTED 2026-09-06 on branch
`feat/modbus-bootloader` (see the Implemented section at the end, which is
written from `fw/Core/Inc/els_identity.h` and supersedes the draft tables
where they differ). Hardware-verified on the machine 2026-09-07
(reflex `fe9e8dc`).
Drafted in a `/closeloops` session at Evan's request, jointly for the two
tasks that each specified half of it: the Modbus field bootloader and the
firmware build-identity register.

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

## Implemented (2026-09-06)

The code is the record: `fw/Core/Inc/els_identity.h` is included by both
stages and by nothing else of each other's; the host client
`fw/scripts/modbus-flash.py` and `fw/scripts/reflex_image.py` mirror it. What
follows names the choices that were still open above, and the one place the
draft was corrected.

### Identity window -- as drafted

Implemented exactly as the table above, by both stages, read-only, at 2048.
`idBuildRevLo/Hi` and `idBuildDirty` come from a header `cmake/BuildRev.cmake`
generates on EVERY build (`git rev-parse --short=7`, dirty = tracked changes,
the way `scripts/flash.sh` computes it); a build with no git reads rev 0 and
dirty 1, never a clean-looking zero. `idAppProtocolVersion` is
`ELS_PROTOCOL_VERSION` (8) in the app and 0 in the bootloader. The app
registers it as an auxiliary window on the Modbus handler (`Modbus.h`
`windows[]`, resolved by `Core/Inc/modbus_window.h`); a range that is not
wholly inside `rampsSharedData_t` or one window is exception 2, which closes a
small pre-existing hole where a single-register write at exactly
`u16regsize` was accepted.

### Control window -- one correction and four assignments

**`blSeq` is at +1 and `blResult` at +2**, the reverse of the draft table.
The Conventions section is binding and the table contradicted it: with
`blResult` below `blSeq`, a torn FC3 read comes out as (new result, stale
seq) and edge detection cannot tell; with `blSeq` below, a new seq is only
ever seen beside a result that was already written. `modbus_window_test`
pins the offsets.

The draft's reserved +12..15 are assigned: `+12 blWriteLen` (host, bytes a
WRITE programs, 4..200, x4), `+13 blAttempts` (RO, the boot-attempt count as
read at this boot), `+14 blCopyState` (RO, the journal state), `+15
blRunValid` (RO, 1 if RUN passes header + CRC + vector sanity). `blSlot` (+10)
only ever accepts STAGING (1); `blActiveSlot` (+11) always reads RUN (0).
Read-only registers inside the writable window are republished on every
read and after every command, so a host FC16 that covers them (the per-chunk
write does) cannot leave a lie behind.

| Register | Values |
|---|---|
| `blCommand` | 1 ERASE, 2 WRITE, 3 VERIFY, 4 APPLY, 5 JUMP, 6 STAY. Cleared on consume. **Executed to completion BEFORE the reply is sent**, so the host is never mid-request while an erase stalls the flash interface; the arrival of the reply means the operation finished, and `blSeq`/`blResult` say how. |
| `blStatus` | 0 IDLE, 1 ERASING, 2 WRITING, 3 VERIFYING, 4 BAD_IMAGE, 5 STAGED, 6 APPLYING, 7 READY_TO_JUMP, 8 STRUCK_OUT |
| `blResult` | 0 OK; 1 BAD_COMMAND, 2 SLOT, 3 ADDR_RANGE, 4 WRITE_LEN, 5 FLASH_ERASE, 6 FLASH_PROG, 7 FLASH_VERIFY, 8 HDR_MAGIC, 9 HDR_VERSION, 10 HDR_LENGTH, 11 HDR_CRC, 12 HOST_LEN, 13 HOST_CRC, 14 NOT_STAGED, 15 NO_RUN_IMAGE, 16 VECTORS, 17 JOURNAL, 18 BACKUP_FAILED, 19 COPY_FAILED |
| `blCopyState` | 0 IDLE, 1 BACKUP, 2 COPY, 3 TRIAL, 4 REVERT, 5 REVERTED |

The per-chunk transfer is ONE FC16 from `blCommand` (+3) through the end of
`blData` (+115), 113 registers: command, target address, image length, image
CRC, slot, write length and 200 bytes of data in one frame, then one FC3 of the
head to edge-detect `blSeq`. Two transactions per 200 bytes.

### Slots and the image

Run slot sector 5, staging sector 6, and -- the choice left open above --
**the previous image is kept in a third slot, BACKUP = sector 7**, not in
SRAM. Holding it in SRAM across the copy fails the requirement that a power
loss mid-copy be recoverable: between erasing RUN and writing the old image
back somewhere, it would exist only in RAM. With three flash slots every step
of an apply is a flash-to-flash copy that can be replayed from the journal.
Sector 7 was otherwise unused; the cost is one more 128 KB erase per update.

**Image header: leading, at +0x200 from the slot base**, 32 bytes (`magic`
"RFLX", `headerVersion` 1, `flags` bit 0 dirty, `imageLength`, `crc32`,
`buildRev`, 3 reserved). The vector table stays at the slot base (VTOR
alignment wants that; the F411 table is 0x198 bytes, and the APP linker
script ASSERTs it clears 0x200). The CRC covers `[0, imageLength)` with the
`crc32` field taken as zero, STM32 CRC-unit variant. `Core/Src/image_header.c`
compiles the header with zero length/CRC placeholders and
`scripts/reflex_image.py patch` fills them into `reflex-fw.bin` post-build and
re-validates the file; the ELF is never a valid image.

The app is linked for the slot by `-DREFLEX_APP_BASE=0x08020000` (linker script
`STM32F411CEUX_APP.ld`, VTOR set in `SystemInit`); the default build is the
legacy layout, untouched, and `scripts/flash.sh` is unchanged.

### Copy-state marker: a journal in flash sector 1

`fw/bootloader/core/bl_state.c`. Sixteen-byte records `{MAGIC, state,
~state, SEAL}` appended into an erased sector; the current state is the last
valid record before the first blank one; none means IDLE. The seal is the last
word programmed, so a record torn by power loss is a prefix without it -- a
test found that without the seal a record torn after `state` (0) with a blank
complement decoded as a valid IDLE. The sector is compacted only while no copy
is in flight and fewer than five records remain. Every transition is journaled
BEFORE the flash work it names: BACKUP (RUN -> BACKUP; BACKUP may be torn),
COPY (STAGING -> RUN; RUN may be torn), TRIAL, REVERT (BACKUP -> RUN; RUN may
be torn), REVERTED. On boot the bootloader finishes or aborts whatever the
journal says was in flight before deciding anything. `bl_core_test` interrupts
an apply after every one of its ~1000 flash operations and reboots on the
result: no interruption point yields a jump into an invalid RUN, and every one
converges to a valid image.

### Anti-brick: IWDG + attempt counter in backup registers

`RTC->BKP0R` = `(0xB007 << 16) | count`. The bootloader increments it before
every jump; the app writes `(0xB007, 0)` on the first Modbus frame it counts
(the `u16InCnt` edge in userLedTask), which is also the confirmation that
promotes TRIAL or REVERTED to IDLE on the next boot. Three unconfirmed
attempts: revert to BACKUP if it holds a valid image different from RUN, and
give the reverted image a fresh count; a strike-out after REVERTED, or with
nothing to revert to, is STRUCK_OUT -- resident, no ping-pong. **This board has
no VBAT, so a power cycle zeroes the registers**: an untagged counter reads as
0 and is NOT a confirmation. A power cycle therefore grants a fresh three
attempts; that is accepted, and it is exactly why the copy-state marker is in
flash and not here. The IWDG is armed by the bootloader (about 32 s,
LSI/256/4096) before every jump, is frozen while a debugger halts the core,
and is refreshed by the app from the 50 ms LED task; it cannot be stopped.

`RTC->BKP1R` = "STAY" (0x53544159) is the software path into the bootloader:
the app writes it and resets; the bootloader clears it and stays resident.

### App side (protocolVersion 7 -> 8)

`elsStop.bootCommand` / `bootSeq` appended at the tail of `elsStop_t` (bytes
464..467, registers 232/233), the calCommand hand-off: 1 = reboot into the
bootloader and stay, 2 = plain reboot; consumed in servoEnableTask, cleared on
consume, acked on `bootSeq`, REFUSED (cleared, no ack) while `enable != 0`.
Mirrored in `ui/reflex/utils/devices.py` (`ELS_PROTOCOL_VERSION` 8) and the
contract test (`KNOWN_ROOT_SIZE` 468). The `Ramps.c` changes are confined to:
the identity-window registration, the counter clear and IWDG kick in
userLedTask, and the command consume (`elsBootCommandTick`). The ISR is
untouched.

**Consequence to decide (not decided here):** `elsStop_t` was exactly 128
registers, two 64-register reads per UI tick; it is now 130.
`ui/tests/fsms/test_els_stop_snapshot.py` pins that boundary on purpose and
now fails twice, asking for the chunk size (`BaseDevice.MAX_REGISTERS_PER_READ`)
to be raised deliberately rather than paying a silent third request per tick.
That is a UI change outside the remit of this branch.

### Client

`scripts/modbus-flash.py`, Python 3 + pyserial, RTU hand-rolled (CRC-16, FC
3/6/16) rather than minimalmodbus so it needs no UI venv, owns its per-request
timeouts (10 s on ERASE, 15 s on APPLY) and sends the 113-register chunk
frame. Reads the identity window first, always; refuses on an `idMagic`
mismatch; sends `bootCommand` 1 if the app answers and waits for stage 1;
erase, stream, verify, apply, jump; polls until stage 2 with the rev of the
image; one verdict line. `--identity`, `--dry-run`, `--enter-bootloader`,
`--boot-app`.

### Not verified

Everything that needs the chip: the flash controller sequence (unlock, sector
erase with PSIZE x32, word program), the CRC unit against the software
implementation (the software one is checked against Python and the published
0xC704DD7B), USART1 at 16 MHz HSI and the 1.5 ms frame-gap timing on a real
RS-485 bus, the backup-register access sequence, the IWDG arming and its
freeze-under-debug, the jump hygiene, and the option-byte WRP procedure. The
bring-up procedure is in `fw/bootloader/README.md`.
