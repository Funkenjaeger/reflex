# reflex ELS Modbus field bootloader

Flash the application over the RS-485 Modbus link the UI already holds, so the
ST-Link is needed only for virgin boards, option bytes, and disaster recovery.
The register contract, flash geometry and image format are in
`../Core/Inc/els_identity.h` and recorded in
`../../decisions/els-modbus-register-map.md` (Implemented section).

**Status: hardware-verified on the machine 2026-09-07** (merge `8184c3a`). The
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
* Serves the identity window at 2048 (`idStage` = 1) and the control window at
  2304. The app serves the identity window too (`idStage` = 2) and answers
  exception 2 at 2304.

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
programs the ELF), `reflex-app-<V>.bin` is the slotted application for step 5
and for any `modbus-flash.py` run, and `reflex-fw-<V>.bin` is the legacy
`0x08000000` image that `scripts/flash.sh` writes — which is a different
layout, not a different build of the same thing.

> **`reflex-fw-<V>.bin` is deprecated, and it has an end condition.**
>
> It exists for one population: boards still running the legacy no-bootloader
> layout, which is every board built before 2026-09-07 and any board a
> `--force-legacy` run has taken back there. It is not what a new board gets —
> `scripts/provision.sh` programs `reflex-bl` + `reflex-app` — and nothing
> built from it can be updated over the wire.
>
> **It ships until no board on the legacy layout remains, and then it stops.**
> That is the whole condition; there is no other reason to keep building it.
> Retiring it is four edits in `release.yml` — the legacy `cmake -S . -B build`
> in "Cross-build the release firmware", the two `cp fw/build/reflex-fw.*`
> lines in "Collect the artifacts", and the legacy half of "Check each firmware
> asset is the layout its name claims" — plus retiring `scripts/flash.sh`, since
> the asset and the script are the same layout wearing two hats.
>
> **Known legacy boards as of 2026-09-08: elspi.** It ran this bootloader on
> 2026-09-07 (the hardware verification above) and is back on the legacy layout
> today, which is exactly the loss the sector-0 guard in `scripts/flash.sh` now
> exists to prevent. Keep this list current; when it empties, the asset goes.

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
mismatch; asks the app to reboot into the bootloader (`bootCommand` = 1); erases
STAGING; streams 200 bytes per FC16; verifies; applies (RUN is backed up to
BACKUP, STAGING copied to RUN, journaled in flash); jumps; then polls until the
app answers with the image's build rev. `--dry-run` does everything except the
writes; `--enter-bootloader` and `--boot-app` are the two halves on their own.

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
* App in RUN invalid and nothing in BACKUP: the bootloader stays resident
  (`runValid=0`); `modbus-flash.py <image>` from the bootloader works with no
  SWD at all -- that is the point.
* Everything else: `scripts/flash.sh` still programs the legacy layout at
  0x08000000 over SWD (after clearing WRP), overwriting the bootloader; the
  stale images in sectors 5-7 are harmless to it.
