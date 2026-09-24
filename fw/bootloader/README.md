# reflex ELS Modbus field bootloader

Flash the application over the RS-485 Modbus link the UI already holds, so the
ST-Link is needed only for virgin boards, option bytes, and disaster recovery.
The register contract, flash geometry and image format are in
`../Core/Inc/els_identity.h` and recorded in
`../../decisions/els-modbus-register-map.md` (Implemented section).

**Status: hardware-verified on the machine 2026-09-07** (merge `fe9e8dc`). The
bootloader boots, validates and jumps; the identity window reads from both
stages; field flashing moved 44,868 bytes over RS-485 in 12.9 s with no
programmer and no power cycle; and the anti-brick swap-back restored the backup
image after three watchdog strikes. What that run did *not* cover is listed
under "Not proven on hardware" in `../todo.md`.

## What it is

* Bare-metal, no HAL, no FreeRTOS, 16 MHz HSI, no interrupts. Sector 0 of the
  STM32F411CE (16 KB budget; the link fails if it does not fit).
* Its own Modbus RTU slave on the same UART pins, baud and slave address as
  the app (USART1, PA10 RX / PA15 TX, 115200 8N1, address 17). No DE handling:
  the RS-485 driver enable is derived from TXD in hardware on this board.
* Receive is DMA2 stream 2 channel 4 in circular mode into a 1 KB ring, with
  frame boundaries taken from the polled (never interrupting) USART IDLE flag
  and the frame length from the change in the stream's remaining count. This
  is not an optimization: the byte-at-a-time poll it replaced lost 14% of
  incoming frames on the board, because a byte that arrives while the main
  loop is inside a blocking send -- or inside a flash write, which stalls
  instruction fetch on this single-bank part -- was simply never read. The
  DMA fills SRAM through both, and the IDLE flag latches, so a frame that
  completed during a multi-second erase is still there when the loop returns.
* Serves the identity window at 2048 (`idStage` = 1), the control window at
  2304, and the read-only receiver counters `blDiag` at 2420 (below). The app
  serves the identity window too (`idStage` = 2) and answers exception 2 at
  2304 and 2420.

```
sector 0  0x08000000  16 KB   bootloader (WRP once commissioned)
sector 1  0x08004000  16 KB   copy-state journal
sector 5  0x08020000 128 KB   RUN     -- the one address the app is linked at
sector 6  0x08040000 128 KB   STAGING -- the host writes here
sector 7  0x08060000 128 KB   BACKUP  -- previous RUN image, for the swap-back
```

## Building

```bash
# the bootloader (its own CMake project)
cmake -S bootloader -B bootloader/build -DCMAKE_BUILD_TYPE=MinSizeRel
cmake --build bootloader/build            # -> bootloader/build/reflex-bl.{elf,bin,hex}

# the app for the RUN slot, with the image header
cmake -S . -B build-slot -DCMAKE_BUILD_TYPE=Release -DREFLEX_APP_BASE=0x08020000
cmake --build build-slot                  # -> build-slot/reflex-fw.bin (header patched)
```

Since 2026-09-07 `.github/workflows/release.yml` runs both of those and
publishes the results, so a commissioning job does not have to build them:
`reflex-bl-<V>.bin` / `reflex-bl-<V>.elf` are this bootloader (step 4 below
programs the ELF), and `reflex-app-<V>.bin` is the slotted application for
step 5 and for any `modbus-flash.py` run. Those two are the whole release
payload — and they are different LAYOUTS, not different builds of the same
thing, which is the distinction the rest of this file is about.

> **RETIRED 2026-09-13: `reflex-fw-<V>.bin` / `.elf` are no longer published.**
>
> They were the legacy no-bootloader image at `0x08000000`, kept for one
> population — boards still running that layout — under an explicit end
> condition: they ship until no such board remains. **Known legacy boards as
> of 2026-09-12: none.** This line said elspi from 2026-09-08, and that was
> wrong: on 2026-09-12 a sector-0 dump over the real ST-Link classified elspi
> BOOTLOADER, `flash.sh` refused, and `--enter-bootloader` answered as
> bootloader `8b6f5c3`. It was never returned to the legacy layout. It now
> runs bootloader and app `2bf5539`. With the list empty the condition was
> met, and `release.yml` stopped building and shipping the asset.
>
> **`scripts/flash.sh` STAYS.** The note this replaces said the asset and the
> script had to be retired together, "the same layout wearing two hats"; that
> coupling was false. `flash.sh` configures and builds the legacy image
> locally and programs `firmware/reflex-fw-<variant>.elf` from that build — it
> never consumed the release asset, so nothing was taken away from it. It
> remains the SWD recovery tool: the way into a board that will not answer
> over Modbus, and the only thing that runs with nothing in sector 0.
>
> Releases tagged before 2026-09-13 still carry the old asset. If you are
> reading one of those release pages, `reflex-fw-<V>.bin` there is still the
> legacy `0x08000000` layout and still not something the bootloader or
> `modbus-flash.py` will take.

`build-slot/reflex-fw.bin` is the image: `scripts/reflex_image.py` patches its
length and CRC32 in post-build and re-validates it. The ELF still carries zero
placeholders -- program the `.bin` (or the `.hex` made from it), never the ELF,
into the RUN slot. The default `cmake -S . -B build` (and `scripts/flash.sh`)
is the legacy no-bootloader layout and is unchanged.

Native tests for the portable core live in `../emulator/test/bl_*_test.cpp`
and run with `ctest` from `../emulator/build`.

## Updating over Modbus

On the probe host (elspi), with the UI stopped:

```bash
python3 scripts/modbus-flash.py --identity --port /dev/ttyUSB0
python3 scripts/modbus-flash.py build-slot/reflex-fw.bin --port /dev/ttyUSB0
```

The client reads the identity window first and refuses on any `idMagic`
mismatch; asks the app to enter the bootloader (`bootCommand` = 1, a jump, not a
reset, since 2026-09-12); erases
STAGING; streams 200 bytes per FC16; verifies; applies (RUN is backed up to
BACKUP, STAGING copied to RUN, journaled in flash); jumps; then polls until the
app answers with the image's build rev. `--dry-run` does everything except the
writes; `--enter-bootloader` and `--boot-app` are the two halves on their own.

`--revert [--expect-rev REV]` puts back the image the last APPLY displaced:
into the bootloader, `blCommand` 7 (BACKUP copied into RUN, journaled), jump,
wait for the application at `REV`, and append a `"variant": "revert"` record
to the manifest. It is how the in-app updater rolls back firmware its gate
refused. One step only: afterwards BACKUP and RUN hold the same image and a
second REVERT answers `NO_BACKUP`.

## Anti-brick

* The bootloader arms the IWDG (~32 s) before every jump and the app refreshes
  it from a 50 ms task. A hung app is reset.
* A boot-attempt counter in an RTC backup register is incremented before every
  jump and cleared by the app once Modbus is live. Three unconfirmed attempts:
  swap BACKUP back into RUN and boot it (`REVERTED`); if that strikes out too,
  or there is nothing valid to revert to, stay resident (`STRUCK_OUT`).
* **No VBAT on this board**: a power cycle zeroes the backup registers, which
  grants a fresh three attempts. That is why the copy-state marker is NOT there
  but in the flash journal (sector 1): a power loss mid-copy is resumed or
  aborted on the next boot without ever jumping into a torn image.

## Receiver diagnostics (`blDiag`, 2420..2427, added 2026-09-23)

Eight read-only uint16 counters, so a bench session can see what the receiver
did during a transfer instead of inferring it from the host's timeouts. They
were added after the 2026-09-19 and 2026-09-23 transfers stalled part-way and
recovered on their own; root cause not established. Read them with one FC3
of 8 registers at 2420 (a read straddling 2419/2420 is exception 2: it is
its own window). Any write that touches them is exception 2. Each counter
saturates at 65535 and is zeroed only at boot (so reading them from the
bootloader after a transfer is fine; a reset or a JUMP loses them).

| Reg | Name | Counts |
|-----|------|--------|
| 2420 | `framesTaken` | IDLE-delimited runs handed to the Modbus layer |
| 2421 | `crcErrors` | of those, dropped for a bad Modbus CRC |
| 2422 | `badFrames` | of those, dropped silently for anything else: runt, over 256 bytes, wrong slave address, wrong length for its function code. Frames answered with an exception are NOT counted. |
| 2423 | `overflowDrops` | runs longer than a frame (a stall spanning two frames), dropped by the ring |
| 2424 | `errOre` | USART overrun flags cleared by the receiver (with the DMA healthy this should stay 0) |
| 2425 | `errFe` | USART framing-error flags, likewise |
| 2426 | `errNe` | USART noise flags, likewise |
| 2427 | `dmaRestarts` | receive DMA stream found dead and re-armed after boot |

How to read them: `framesTaken` should track the host's request count. If it
runs well ahead, with `badFrames` rising about one per request, the bootloader
is hearing something besides the host — for example its own replies echoed
by the transceiver (an unverified hypothesis these counters can confirm or
rule out). The flag counters are a floor, not a census: a flag the DMA's own
DR read clears first goes uncounted (see `blHwUartPoll` in `src/bl_hw.c`).
Only the `framesTaken`/`crcErrors`/`badFrames`/`overflowDrops` arithmetic is
covered by native tests; the flag and restart counts exist only on the chip.

**Error-branch fix, same date, UNVERIFIED ON HARDWARE until the bench
session.** `blHwUartPoll`'s ORE/FE/NE branch used to read `USART1->DR`
unconditionally to clear the flags, which can steal the byte the DMA was about
to fetch — the exact trap the IDLE branch already guarded against (Open Loops
6aae713c, a code-read suspect for the stalls). It now does what the IDLE
branch does: re-read SR, and while RXNE is up leave the flags latched and
return. The one exception is a dead DMA stream (disabled, or an error flag
set): nothing will ever fetch DR then, so waiting on RXNE would leave the
bootloader deaf for good; it reads DR and re-arms the stream as before.

## Bring-up procedure (elspi; the parent session runs these over SSH, Evan power-cycles)

Prerequisites: `openocd`, `gcc-arm-none-eabi`, `cmake`, `python3-serial` on
elspi; a scratch clone of this branch outside the live checkout
(`/home/default/bl-build`), the ST-Link plugged in, and the UI stopped for
every step that touches the serial port.

1. **Build both images in the scratch clone** (commands above). Record
   `git rev-parse --short=7 HEAD`; the identity window will report it.
   Note the bootloader size printed by the link (`FLASH: ... 16 KB`).
2. **Confirm the ROM-less recovery path before writing anything**: the only
   way back is SWD, so `openocd -f interface/stlink.cfg -f target/stm32f4x.cfg
   -c "init; reset halt; flash info 0; flash info 0; shutdown"` must
   work and show every sector unprotected. If sector 0 is already protected
   (a previous attempt), clear it first: step 9b.
3. **Read what is there now** (UI stopped):
   `python3 scripts/modbus-flash.py --identity --port /dev/ttyUSB0`. Against
   today's firmware this exits nonzero with "no identity window" -- expected,
   the legacy app has none. Keep the output.
4. **Erase the journal and both spare slots so the state is known**, then
   program the bootloader:
   ```
   openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
     -c "init; reset halt; flash erase_sector 0 1 1; flash erase_sector 0 6 7; shutdown"
   openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
     -c "program bootloader/build/reflex-bl.elf verify exit"
   ```
5. **Program the app into the RUN slot from the patched .bin** (not the ELF):
   ```
   openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
     -c "program build-slot/reflex-fw.bin 0x08020000 verify reset exit"
   ```
   Sectors 1-4 are now: journal erased (= IDLE), 2-4 unused; 5 = app; 6, 7
   erased. The bootloader boots, finds RUN valid, counts attempt 1, jumps.
6. **POWER CYCLE (Evan).** A reset alone has not reliably started new firmware
   on this board (`../README.md`); a power cycle also exercises the real
   VBAT-less path (backup registers zeroed, attempt counter starts fresh).
7. **Verify the app came up through the bootloader**:
   * `modbus-flash.py --identity` -> `stage=application`, `rev=<step 1>`,
     `appProtocol=8`.
   * start the UI; its log must say
     `Firmware register protocol version 8 (expected 8)`; the DRO must read.
   * stop the UI again for the next steps.
8. **Verify the software path into the bootloader and back** (this is the
   step elspi cannot do without it, so it is the one that matters):
   * `modbus-flash.py --enter-bootloader` -> `stage=bootloader`, and the
     bootloader line shows `status=IDLE copyState=IDLE attempts=0 runValid=1`.
   * `modbus-flash.py --boot-app` -> `VERDICT: OK -- application <rev>`.
9. **First over-the-wire update**: touch a comment in `Core/Src/main.c`
   (the tree becomes dirty, so the rev reads `<rev>-dirty` and is
   distinguishable), rebuild `build-slot`, then
   `modbus-flash.py build-slot/reflex-fw.bin`. Expect the one-line verdict with
   the dirty rev, then `--identity` agreeing. Expected duration ~10-20 s;
   the erase step alone may take up to 4 s with no reply.
   9a. Repeat once more with the clean tree to leave the board on a clean rev.
   9b. **Write-protect the bootloader sector** (RDP stays level 0):
       ```
       openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
         -c "init; reset halt; flash protect 0 0 0 on; flash info 0; shutdown"
       ```
       then **POWER CYCLE (Evan)** so the option bytes reload, and re-check
       `flash info 0` shows sector 0 protected. To clear it (needed
       before any SWD reflash of sector 0, including a return to the legacy
       layout via `scripts/flash.sh`): the same command with `off`, and a
       power cycle.
   9c. **Prove REVERT** (needs a bootloader built with `blCommand` 7, i.e. from
       2026-09-12 on). BACKUP now holds step 9's dirty rev, RUN the clean one:
       `modbus-flash.py --revert --expect-rev <step 9 dirty rev> --manifest
       /home/<user>/firmware/flashed.json` -> `VERDICT: OK -- reverted`, and
       `--identity` agrees. Then `--revert` again must refuse with
       `NO_BACKUP` and change nothing. Finish by repeating 9a, so the board
       ends on the clean rev with the dirty one in BACKUP. WRP from 9b covers
       sector 0 only, so it does not stand in the way.
10. **Record.** `modbus-flash.py` appends to `~/firmware/flashed.json` itself
    once the board reports the new revision (since 2026-09-11; before that this
    step was by hand). Check the last line names the rev you flashed. Run as
    root, pass `--manifest /home/<user>/firmware/flashed.json` — root's `~` is
    not where the record is read.

Anti-brick is deliberately NOT exercised in bring-up: proving the swap-back
means running a deliberately hung image on the lathe controller for ~100 s
of watchdog strikes. If it is ever wanted, the payload-free way is an image
whose `main()` spins after `HAL_Init()` with no task running; the bootloader
must revert to BACKUP after three ~33 s strikes and `--identity` must then show
the previous rev. Decide that separately.

## Recovery

* Bootloader wedged or WRP-protected garbage in sector 0: clear WRP (9b with
  `off`), power cycle, reprogram (step 4).
* Firmware an update installed is wrong for this UI (the updater's gate
  refused it and could not roll back on its own): `modbus-flash.py --revert`
  from the command line, with the UI stopped.
* App in RUN invalid and nothing in BACKUP: the bootloader stays resident
  (`runValid=0`); `modbus-flash.py <image>` from the bootloader works with no
  SWD at all -- that is the point.
* Everything else: `scripts/flash.sh` still programs the legacy layout at
  0x08000000 over SWD (after clearing WRP), overwriting the bootloader; the
  stale images in sectors 5-7 are harmless to it.
