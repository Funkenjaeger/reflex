# reflex ELS Modbus field bootloader

Flash the application over the RS-485 Modbus link the UI already holds, so the
ST-Link is needed only for virgin boards, option bytes, and disaster recovery.
The register contract, flash geometry and image format are in
`../Core/Inc/els_identity.h` and recorded in
`../../docs/design/decisions.md`.

Verified on hardware, including field flashing and the anti-brick swap-back.

## What it is

* Bare-metal (no HAL, FreeRTOS or interrupts) in sector 0 of the STM32F411CE;
  the link fails past 16 KB. Runs on the 8 MHz HSE crystal, falling back to
  the 16 MHz HSI, whose error stalls transfers, only if the crystal does not
  start (blDiag `clockHse` says which).
* A Modbus RTU slave on the app's UART, baud and address (USART1, PA10 RX /
  PA15 TX, 115200 8N1, address 17). The RS-485 driver enable comes from TXD in
  hardware.
* Receive is DMA2 stream 2 channel 4 into a 1 KB ring framed by the USART IDLE
  flag, because a polled receiver loses bytes that arrive during a blocking
  send or a flash write.
* Serves the identity window at 2048 (`idStage` = 1), the control window at
  2304 and the read-only `blDiag` at 2420. The app serves the identity window
  (`idStage` = 2) and answers exception 2 at 2304 and 2420.

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

Releases (`.github/workflows/release.yml`) publish `reflex-bl-<V>.bin` / `.elf`
(this bootloader) and `reflex-app-<V>.bin` (the slotted application).
`scripts/reflex_image.py` patches length and CRC32 into the `.bin` only; the
ELF keeps zero placeholders, so program the `.bin` (or its `.hex`), **never the
ELF**, into the RUN slot.

The default `cmake -S . -B build` and `scripts/flash.sh` build the legacy
no-bootloader image at `0x08000000`. `flash.sh` programs it over SWD: the
recovery tool for a board that will not answer over Modbus. The `reflex-fw-<V>.bin` on older release pages is that legacy layout,
which neither the bootloader nor `modbus-flash.py` will take.

## Updating over Modbus

On the host wired to the board's RS-485 link, with the UI stopped:

```bash
python3 scripts/modbus-flash.py --identity --port /dev/ttyUSB0
python3 scripts/modbus-flash.py build-slot/reflex-fw.bin --port /dev/ttyUSB0
```

The client refuses unless `idMagic` matches, has the app jump into the
bootloader (`bootCommand` = 1), erases STAGING, streams 200 bytes per FC16,
verifies, applies (RUN to BACKUP, STAGING to RUN, journaled in flash), jumps,
and polls until the app reports the image's build rev. `--dry-run` skips the
writes.

`--revert [--expect-rev REV]` copies BACKUP back into RUN (`blCommand` 7); the
in-app updater uses it to roll back firmware its gate refused. A second REVERT
answers `NO_BACKUP`.

## Anti-brick

* The bootloader arms the IWDG (~32 s) before every jump; the app refreshes it
  every 50 ms, so a hung app is reset.
* A counter in an RTC backup register counts jumps until the app clears it
  once Modbus is live. After three unconfirmed attempts the bootloader swaps
  BACKUP into RUN (`REVERTED`), or stays resident (`STRUCK_OUT`) if that
  strikes out too or there is nothing to revert to.
* **No VBAT on this board**: a power cycle zeroes the backup registers, so the
  copy-state marker lives in the flash journal (sector 1), and a power loss
  mid-copy is resumed or aborted on the next boot.

## Recovery

Steps refer to [Bootloader bring-up](../../docs/setup/bootloader-bring-up.md),
which also covers first programming, write protection and the `blDiag`
counters.

* Bootloader wedged, or garbage in a write-protected sector 0: clear WRP (step
  9b, `off`), power cycle, reprogram (step 4).
* An update the updater could not roll back: `modbus-flash.py --revert`, with
  the UI stopped.
* RUN invalid and BACKUP empty: the bootloader stays resident (`runValid=0`),
  and `modbus-flash.py <image>` works without SWD.
* Anything else: `scripts/flash.sh` over SWD, after clearing WRP, replaces the
  bootloader with the legacy layout.
