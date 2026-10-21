# Bootloader bring-up

Commissioning a controller board for the Modbus field bootloader: program it
over SWD once, then prove each path before relying on it. What the bootloader
is, how to build it and how to update over Modbus are in
[`fw/bootloader/README.md`](https://github.com/Funkenjaeger/reflex/blob/main/fw/bootloader/README.md).

## Before you start

On the host wired to the board: `openocd`, `gcc-arm-none-eabi`, `cmake` and
`python3-serial`; a scratch clone of the repository outside any live checkout
(`~/bl-build`); the ST-Link plugged in; and the UI stopped for every step that
touches the serial port. Paths below are relative to `fw/` in the scratch
clone. The operator does the power cycles.

## Procedure

1. **Build both images in the scratch clone** (the commands under
    [Building](https://github.com/Funkenjaeger/reflex/blob/main/fw/bootloader/README.md#building)).
    Record `git rev-parse --short=7 HEAD`; the identity window will report it.
    Note the bootloader size printed by the link (`FLASH: ... 16 KB`).

2. **Confirm the ROM-less recovery path before writing anything**: the only
    way back is SWD, so `openocd -f interface/stlink.cfg -f target/stm32f4x.cfg
    -f scripts/lib/sector0-optcr.cfg` must work and print `OPTCR=0x0fffaacd`
    (nWRP bits 16..27 all 1 = no sector protected; RDP 0xAA = level 0). Not
    `flash info 0`: openocd can print nothing for it in `-c` batch mode, so it
    cannot show protection either way. If sector 0 is already protected
    (`0x0ffeaacd`), clear it first: step 9b.

3. **Read what is there now** (UI stopped):
    `python3 scripts/modbus-flash.py --identity --port /dev/ttyUSB0`. On a
    board still running the legacy app this exits nonzero with "no identity
    window"; that is expected. Keep the output.

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

6. **POWER CYCLE (operator).** A reset alone has not reliably started new
    firmware on this board (`fw/README.md`); a power cycle also exercises the
    real VBAT-less path (backup registers zeroed, attempt counter starts
    fresh).

7. **Verify the app came up through the bootloader**:

    * `modbus-flash.py --identity` -> `stage=application`, `rev=<step 1>`,
      `appProtocol=11` (`ELS_PROTOCOL_VERSION`, `fw/Core/Inc/Ramps.h`).
    * start the UI; its log must say
      `Firmware register protocol version 11 (expected 11)`; the DRO must read.
    * stop the UI again for the next steps.

8. **Verify the software path into the bootloader and back**:

    * `modbus-flash.py --enter-bootloader` -> `stage=bootloader`, and the
      bootloader line shows `status=IDLE copyState=IDLE attempts=0 runValid=1`.
    * `modbus-flash.py --boot-app` -> `VERDICT: OK -- application <rev>`.

9. **First over-the-wire update**: touch a comment in `Core/Src/main.c`
    (the tree becomes dirty, so the rev reads `<rev>-dirty` and is
    distinguishable), rebuild `build-slot`, then
    `modbus-flash.py build-slot/reflex-fw.bin`. Expect the one-line verdict
    with the dirty rev, then `--identity` agreeing. Expected duration
    ~10-20 s; the erase step alone may take up to 4 s with no reply.

    * **9a.** Repeat once more with the clean tree to leave the board on a
      clean rev.
    * **9b. Write-protect the bootloader sector** (RDP stays level 0):

        ```
        openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
          -c "init; reset halt; flash protect 0 0 0 on; shutdown"
        openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
          -f scripts/lib/sector0-optcr.cfg
        ```

        The read must print `OPTCR=0x0ffeaacd` (nWRP bit 16 = sector 0 is 0,
        active low; every other nWRP bit 1; RDP still 0xAA). Then **POWER
        CYCLE (operator)** so the option bytes reload, and read it again: same
        value. To clear it (needed before any SWD reflash of sector 0,
        including a return to the legacy layout via `scripts/flash.sh`):
        `flash protect 0 0 0 off`, the read must show `0x0fffaacd`, and a
        power cycle.

    * **9c. Prove REVERT** (needs a bootloader that supports `blCommand` 7).
      BACKUP now holds step 9's dirty rev, RUN the clean one:
      `modbus-flash.py --revert --expect-rev <step 9 dirty rev> --manifest
      /home/<user>/firmware/flashed.json` -> `VERDICT: OK -- reverted`, and
      `--identity` agrees. Then `--revert` again must refuse with
      `NO_BACKUP` and change nothing. Finish by repeating 9a, so the board
      ends on the clean rev with the dirty one in BACKUP. WRP from 9b covers
      sector 0 only, so it does not stand in the way.

10. **Record.** `modbus-flash.py` appends to `~/firmware/flashed.json` itself
    once the board reports the new revision. Check the last line names the
    rev you flashed. Run as root, pass
    `--manifest /home/<user>/firmware/flashed.json`: root's `~` is not where
    the record is read.

Commissioning does not exercise the anti-brick swap-back, which takes ~100 s
of watchdog strikes on a hung image. To test it, flash an image whose `main()`
spins after `HAL_Init()` with no task running: the bootloader must revert to
BACKUP after three ~33 s strikes, and `--identity` must then show the previous
rev.

## Receiver diagnostics

`blDiag`, registers 2420..2428, is nine read-only uint16 registers: eight
counters of what the receiver did during a transfer, then the `clockHse` flag.
Transfers have stalled part-way and recovered on their own; the cause is not
known. Read them with one FC3 of 9 registers at 2420 (a read straddling
2419/2420 is exception 2: it is its own window). Any write that touches them is
exception 2. Each counter saturates at 65535 and is zeroed only at boot, so
reading them from the bootloader after a transfer is fine; a reset or a JUMP
loses them.

| Reg | Name | Counts |
|-----|------|--------|
| 2420 | `framesTaken` | IDLE-delimited runs handed to the Modbus layer |
| 2421 | `crcErrors` | of those, dropped for a bad Modbus CRC |
| 2422 | `badFrames` | of those, dropped silently for anything else: runt, over 256 bytes, wrong slave address, wrong length for its function code. Frames answered with an exception are not counted. |
| 2423 | `overflowDrops` | runs longer than a frame (a stall spanning two frames), dropped by the ring |
| 2424 | `errOre` | USART overrun flags cleared by the receiver (with the DMA healthy this should stay 0) |
| 2425 | `errFe` | USART framing-error flags, likewise |
| 2426 | `errNe` | USART noise flags, likewise |
| 2427 | `dmaRestarts` | receive DMA stream found dead and re-armed after boot |
| 2428 | `clockHse` | not a counter: set once at boot, 1 = running on the 8 MHz crystal, 0 = fell back to the internal RC (HSI) |

`framesTaken` should track the host's request count. If it runs well ahead,
with `badFrames` rising about one per request, the bootloader is hearing
something besides the host. The flag counters are a floor: a flag the DMA's
own DR read clears first goes uncounted (see `blHwUartPoll` in
`bootloader/src/bl_hw.c`).
