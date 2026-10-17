# Diagnostic probes

A probe is measurement code compiled into the firmware only on request. It
writes to the 64-register *diagnostic scratchpad* at the tail of `elsStop_t`,
which every build reserves so its offset never moves. In a release build the
block reads zero.

```bash
./scripts/build.sh --diag=takeup-settle-v3     # build with one probe
./scripts/flash.sh --diag=takeup-settle-v3     # build it and flash it (LEGACY board only)
./scripts/build.sh --diag                      # lists what is available
```

### On a board with the bootloader

`flash.sh` writes the legacy layout, so it refuses on a board carrying the field
bootloader. Build the probe for the RUN slot and flash it over the wire, with the
UI stopped:

```bash
cmake -S . -B build-slot-diag-takeup-settle-v3 -DCMAKE_BUILD_TYPE=Release \
    -DREFLEX_APP_BASE=0x08020000 \
    -DCMAKE_C_FLAGS=-DELS_DIAG_PROBE=ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V3
cmake --build build-slot-diag-takeup-settle-v3
python3 scripts/modbus-flash.py build-slot-diag-takeup-settle-v3/reflex-fw.bin \
    --port /dev/serial0 --record-variant diagnostic
```

`flash.sh`'s refusal prints these commands for your probe; the macro is its
`ELS_DIAG_SCHEMA_*` name in `Core/Inc/Ramps.h`. Use `build-slot/` to return to
release. The manifest records `variant: diagnostic, probe: unknown`, since
`modbus-flash.py` cannot see which probe an image carries.

Probes never go on `dev` or `main`.

## One probe at a time

Every probe writes the same 64 registers, so two together would produce
a capture that looks well-formed and means nothing. Selection is one macro
holding one value, `-DELS_DIAG_PROBE=ELS_DIAG_SCHEMA_<NAME>`, and the guards in
`Core/Inc/Ramps.h` reject the near misses at compile time:

| You pass | Result |
|---|---|
| nothing | release build, scratchpad reads zero |
| a registered probe | that probe, and only that probe |
| `ELS_DIAG_SCRATCH` by hand | **compile error**: it is derived, never passed |
| a misspelled macro name | **compile error**; it would otherwise evaluate to `0` and build an image with no probe |
| a retired probe | **compile error**, naming its replacement |
| an unregistered id | **compile error** |

## Knowing what is running

`elsStop.diagSchema` names the probe compiled in; `0` means none.
`protocolVersion` does not move when a probe changes, so **a reader must check
`diagSchema` and refuse any id it does not recognize.** `scripts/flash.sh`
records the probe in `~/firmware/flashed.json`; after an over-the-wire flash
(`probe: unknown`), the schema reflex-ui logs at connect is the authority.
Schema ids are **append only**: never renumbered, never reissued.

## Code layout

| File | Role |
|---|---|
| `Core/Inc/els_diag.h` | dispatch: the selected probe's header, or no-op entry points. Always included, from the foot of `Ramps.h` |
| `Core/Inc/els_diag_<name>.h` | one probe: its state machine, constants and trace geometry |
| `Core/Inc/Ramps.h` | the scratchpad registers and schema ids (register contract), plus `elsDiagCtx_t` |
| `Core/Src/Ramps.c` | call sites only: no probe logic, and no `#ifdef` |

Every probe implements these entry points, which keeps `Ramps.c`
probe-agnostic:

| Entry point | Called at |
|---|---|
| `elsDiagInit(ctx, stop)` | `RampsStart`. Publish `diagSchema` + geometry, clear the block |
| `elsDiagArm(ctx, stop)` | take-up initiation, before any pulses |
| `elsDiagCaptureStart(ctx)` | first tick at which commanded motion is complete |
| `elsDiagCapturing(ctx)` | cheap predicate, see below |
| `elsDiagTick(ctx, stop, dZ, dServo)` | once per ISR tick while capturing |
| `elsDiagServoGate(ctx, stop, mode)` | `servoEnableTask`, at the re-assert decision; `true` suppresses it (schema 3+) |
| `elsDiagTaskTick(ctx, shared, cal)` | once per `servoEnableTask` iteration (~100 ms), after the re-assert (schema 4+) |
| `elsDiagTickTail(ctx, shared)` | every ISR tick, unconditionally, after the tick's work and before `executionCycles` is taken (schema 8+) |

**`elsDiagCapturing` must stay trivial, and the ISR must call it before computing
`elsDiagTick`'s arguments.** Without the guard every tick pays for `dZ` and
`dServo`, and the ISR grows by 128 bytes. A hook moved from its instant in the
tick measures something else, so placement belongs to `Ramps.c`, not the probe.
In a release build the entry points compile to nothing; `.bss` grows 8 bytes for `elsDiagCtx_t`, which
sits last in `rampsHandler_t` so no field above it moves.

`elsDiagTickTail` has no capture guard, so it must be an empty inline in every
build that does not use it. Adding it left the release image byte-identical
apart from the build-identity stamp.

## Registry

### `takeup-settle-v2` (schema 2, retired)

Measured how long the carriage keeps moving after the ELS take-up, to set
`ELS_SLIP_SETTLE_TICKS`. Capture starts when the take-up completes and ends at
the servo's next pulse (`ELS_DIAG_END_PULSE`) or when the buckets run out
(`ELS_DIAG_END_WINDOW`), which is a floor, not a result.

| Field | Meaning |
|---|---|
| `diagSeq` | increments once per completed capture; edge-detect it (there is no "in progress" register) |
| `diagTrace[50]` | per-bucket **signed** sum of Z counts, signed so encoder dither cancels and real motion does not |
| `diagBucketTicks` | ISR ticks per bucket, published so the host never assumes the ISR rate |
| `diagBucketCount` | populated bucket count |
| `diagSettleTicks` | ticks from capture start to the last tick that saw motion: **the measurement** |
| `diagNetCounts` | signed Z counts across the whole capture |
| `diagCaptureTicks` | how long the capture ran; distinct from `diagSettleTicks`, which is when Z last *moved* |
| `diagEndReason` | `ELS_DIAG_END_*` |

v2 could not measure a confirmed take-up: the phase-correction jog that follows
confirmation, `ELS_SETTLE_TICKS` (0.5 ms) after the last pulse, ends the capture
at 26 ticks at the current 50 kHz rate (51 was the 100 kHz-era value, measured
before the rate split), and with the gate held open the decel ramp's
residual steps end it at 134 ticks -- an empirical 100 kHz-era figure awaiting a
50 kHz re-capture. Its zeros say nothing about take-up confirmation.

### `disengage-latch` (schema 3, intervening)

Counts `servoEnableTask` re-asserting `servoMode = 1` while
`elsStop.enable == 0`, which would restart a spindle-synced feed after a
disengage with the stop disarmed. **This probe changes behavior: it refuses the
re-assert and records it**, because observing alone would mean letting a
carriage run away.

| Field | Meaning (differs from schema 2: check `diagSchema` first) |
|---|---|
| `diagSeq` | events caught. **The result.** 0 = never happened this run |
| `diagNetCounts` | same count in 32 bits, so a long run cannot wrap unnoticed |
| `diagCaptureTicks` | `elsStop.active` at the most recent event (expect 0) |
| `diagSettleTicks` | `servoMode` at the event: 0 = this would have STARTED a feed |
| `diagEndReason` | 1 once any event has been seen |
| `diagTrace[]` | unused |

Suppression depends only on `enable == 0`, so sync motion started by a non-ELS
path also stops being auto-enabled. This probe must never reach a release
branch.

### `mode-watch-v2` (schema 5, durable)

Publishes the firmware-derived machine mode (`els_machine_mode.h`,
`ELS_MMODE_*`) once per `servoEnableTask` tick, and keeps schema 3's
intervention and its non-ELS-sync caveat. `diagNetCounts` counts only
suppressions where `servoMode` was 0, which would have switched the feed
on. reflex-ui's watchdog checks its own mode model against this register.

| Field | Meaning (differs from schemas 2 to 4: check `diagSchema` first) |
|---|---|
| `diagCaptureTicks` | **current derived mode** (`ELS_MMODE_*`) |
| `diagSettleTicks` | previous mode, the from-side of the last transition |
| `diagSeq` | mode-transition counter; bumped last, so an edge-detected read sees a consistent pair |
| `diagNetCounts` | **effective** latch suppressions (`servoMode` was 0), cumulative. **Expect 0; nonzero is always a finding** |
| `diagEndReason` | 1 once any counted suppression has been seen |
| `diagTrace[0]`, `[1]` | `servoMode` (0 by construction) and `active` at the most recent counted suppression |

Mode values are append-only, pinned in `els_machine_mode_test` and
mirrored by reflex-ui. `HELD` merges "armed idle" and "stop fired", which the
registers cannot tell apart.

### `takeup-settle-v3` (schema 6)

Same registers and measurement as v2. A pulse arriving while `takeupPending` is
set re-arms the capture, so `t=0` is the take-up's last pulse, and
`elsDiagExtraDwell` holds the gate's dwell open until the capture publishes, up
to `ELS_DIAG_SETTLE_HOLD_CEILING_TICKS` (80 ms). The emulator mutation suite
requires both. `elsDiagExtraDwell()` is 0 in every other build.

**`END_WINDOW` inverts its v2 meaning.** The gate is held for the whole
window, so `END_WINDOW` is the complete measurement and
`settle_ticks` is the answer. `END_PULSE` means something drove the servo during
the hold: treat that trace as cut short.

**The diagnostic build decides later than release.** The gate's first verdict
lands ~20 ms after the last pulse instead of 0.5 ms, so refusals arrive later
and a disturbance placed by gate state behaves differently.
**Never read "the diagnostic build confirmed" as "release would have
confirmed."**

**Result.** Each of the 16 captures out of 36 that moved delivered one Z count
(net −1), between 0.79 ms and 17.86 ms after the last pulse.
`ELS_SLIP_SETTLE_TICKS` is 20 ms, which clears the longest;
`emulator/test/els_slip_horizon_commission_test.cpp` encodes the observations.

### `stop-overshoot` (schema 7)

On air passes the carriage stops ~0.0022" (~56 µm) past the programmed stop,
repeatably, and cannot be pushed back by hand, which rules out servo
overshoot-and-recover. `phase_live.jsonl` samples at 0.97 Hz, too slow for the
trigger, so this probe runs in the ISR, from the rising edge of `elsStop.active`
until Z has been still for 2 ms.

| field | meaning |
|---|---|
| `diagNetCounts` | signed Z counts traveled **after** the trigger: the overshoot |
| `diagReserved[0..1]` | servo steps the firmware **emitted** after the trigger, int32: **the discriminator** |
| `diagSettleTicks` | last tick that saw motion |
| `diagTrace[]` | per-bucket signed dZ, 400 µs buckets: the shape |
| `diagEndReason` | 1 = SETTLED (complete), 2 = WINDOW (still moving, treat net as a floor) |

**End reasons invert v3's**: here 1 is the success case.

- **Steps emitted > 0**: the firmware commanded the overshoot.
- **Steps emitted == 0**: the carriage moved with no command, so the cause is
  downstream of the pulse train.

**Zero does not mean mechanical.** `dServo` counts what the firmware emitted, not
what the drive did, so a drive that fails to honor its commanded position when
the pulse train stops abruptly looks the same here as drivetrain coast. The drive's
following-error readout, or an abrupt jog stop with the ELS out of the picture,
separates the two.

`elsDiagCapturing()` returns true on every tick, because there is no
stop-trigger hook, so the `dZ`/`dServo` arithmetic always runs, against a
2000-cycle budget.

### `isr-gap` (schema 8, baseline)

How late the motion ISR starts. TIM9 runs at NVIC priority 5, equal to
`configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY`, so FreeRTOS critical sections
and same-priority interrupts (the Modbus UART among them) can hold a tick back.
`executionCyclesPeak` measures how long the ISR runs, not how late it starts.
This probe peak-holds the gap between ISR entries (`executionInterval`, DWT
cycles) over a window, so the late ticks of the current Modbus stack are on
record before that stack is replaced.

The window opens at boot (the boot tick is skipped: its interval is not a gap)
and again on every host reset. The hook is `elsDiagTickTail`, every tick.

| Field | Meaning (check `diagSchema` first) |
|---|---|
| `diagSettleTicks` | **worst gap**, CPU cycles |
| `diagNetCounts` | ticks measured in the window (uint32; wraps after ~23.8 h at 50 kHz) |
| `diagCaptureTicks` | longest ISR run in the window, cycles, saturating at 65535. Window-scoped, unlike `executionCyclesPeak` |
| `diagEndReason` | **reset request**, written by the host: `1` starts a fresh window; the ISR writes `0` back as the acknowledgement |
| `diagBucketTicks` / `diagBucketCount` | 0 / 24: no time buckets; 24 `diagTrace` words in use |
| `diagTrace[0..1]` | shortest gap, uint32 |
| `diagTrace[2..9]` | late-tick counts, four uint32s: gaps strictly above +5%, +25%, +50% and 2x nominal, cumulative |
| `diagTrace[10..13]` | those four thresholds, cycles |
| `diagTrace[14]` | nominal tick, cycles (2000 at 50 kHz) |
| `diagTrace[15..16]` | window tick of the worst gap, uint32 |
| `diagTrace[17]` | cycles the tick **before** the worst gap ran |
| `diagTrace[18..22]` | at the worst gap: `machineMode`, `servoMode`, `enable`, `active`, `takeupPending` |
| `diagTrace[23]` | window number: 0 at boot, +1 per reset |
| `diagSeq` | bumps when the worst gap grows, on a reset, and once a minute |

A uint32 is two words, low first. The word map is `ELS_DIAG_GAP_W_*` in
`Core/Inc/els_diag_isr_gap.h`, mirrored by `scripts/isr-gap.py` and reflex-ui;
`scripts/lib/isr-gap-test.py` and `ui/tests/test_registry_contract.py` fail if
either drifts.

**Reading it.** A tick held back by D cycles shows as one gap of nominal + D and
then one of nominal - D, because the timer keeps its own time and only the
entry moves. A gap of 2x nominal or more means an update was lost: TIM9's
pending flag holds one update, not two. The duration of the tick before the
worst gap separates the two causes: near or above nominal, the ISR overran its
own budget; normal, something outside the ISR held the entry back. The mode
fields tell idle from a cut start (`takeupPending`, `active`, `machineMode`).

**Torn reads.** The ISR writes the block while the host reads it, so a 32-bit
counter can be read across its carry. `isr-gap.py` reads twice and requires
the worst-gap fields and `diagSeq` to agree.

**The UI records it.** reflex-ui recognizes schema 8 and, on each `diagSeq`
edge, appends the block to the diagnostic capture file with an `isr_gap`
object holding the decoded window and a UTC timestamp. Each new worst gap is
therefore on record with the time it happened, and the distribution is at most
a minute old when the session ends, even if nobody stops the UI to read it.
The cost is one 64-register read (about 12 ms at 115200 baud) per edge. The UI
never writes the reset request.

**Cost.** The hook runs before `executionCycles` is taken, so its cost is
inside `executionCycles` and `executionCyclesPeak` on the machine. Statically,
the common path (no reset pending, gap within +5%, no new extreme, no
heartbeat) is 30 instructions: 9 loads, 13 single-cycle ALU and store
instructions, 2 untaken branches and 6 taken ones. That is about 45 cycles
with warm prefetch and about 75 with every taken branch refilling from flash,
2 to 4% of the 2000-cycle budget. A late tick adds about 30 cycles, a new worst
gap about 20, and the reset tick clears the block once, about 300.
`scripts/check-isr-cost.sh` finds no double-precision, 64-bit divide or new
libm calls. The machine's own number is `fastData.cycles` under this probe
against release on an idle machine.

**Bench procedure.** On the probe host, in `fw/` of a checkout carrying the
probe:

```bash
# 1. Build for the RUN slot.
cmake -S . -B build-slot-diag-isr-gap -DCMAKE_BUILD_TYPE=Release \
    -DREFLEX_APP_BASE=0x08020000 \
    -DCMAKE_C_FLAGS=-DELS_DIAG_PROBE=ELS_DIAG_SCHEMA_ISR_GAP
cmake --build build-slot-diag-isr-gap

# 2. Flash over the wire, UI stopped. Note the rev it replaces (the identity
#    window, printed first) for step 6.
sudo systemctl stop reflex-ui.service
python3 scripts/modbus-flash.py --identity --port /dev/serial0
python3 scripts/modbus-flash.py build-slot-diag-isr-gap/reflex-fw.bin \
    --port /dev/serial0 --record-variant diagnostic

# 3. Open a known window, still with the UI stopped.
python3 scripts/isr-gap.py --port /dev/serial0 --reset

# 4. Start the UI and run the session to be measured.
sudo systemctl start reflex-ui.service

# 5. Stop the UI and read the window.
sudo systemctl stop reflex-ui.service
python3 scripts/isr-gap.py --port /dev/serial0
python3 scripts/isr-gap.py --port /dev/serial0 --json > isr-gap-$(date +%F-%H%M).json

# 6. Return to release: put back the image step 2 displaced, and check that
#    the board comes back on the rev noted there.
python3 scripts/modbus-flash.py --revert --expect-rev <rev from step 2> \
    --port /dev/serial0
python3 scripts/isr-gap.py --port /dev/serial0   # must refuse: diagSchema is 0
sudo systemctl start reflex-ui.service
```

Repeat steps 3 to 5 for each window to be measured separately (idle, jog,
threading passes); a reset is the boundary. `--revert` goes back one step
only; if it refuses, flash the release RUN-slot image instead
(`build-slot/reflex-fw.bin` built from the release commit, or the release's
own `reflex-fw.bin`).

### Retired ids

| Schema | Probe | Superseded by |
|---|---|---|
| 1 | `takeup-settle` | `takeup-settle-v2`, which ends at the servo's next pulse |
| 2 | `takeup-settle-v2` | `takeup-settle-v3` |
| 4 | `mode-watch` | `mode-watch-v2`, which counts only effective suppressions |

Data recorded under a retired id keeps its original meaning.

## Adding a probe

1. **Register the schema id** in `Core/Inc/Ramps.h` after the highest id.
   `scripts/lib/diag.sh` parses these defines, so the probe becomes selectable
   as `--diag=<lowercase-hyphenated-name>`.
2. **Add an arm to the `#error` chain** in `Ramps.h`, or the build refuses.
3. **Write the probe** in `Core/Inc/els_diag_<name>.h` and add an arm to the
   dispatch `#if` in `els_diag.h`. Nothing goes in `Ramps.c`.
4. **Add a test target** in `emulator/CMakeLists.txt` mirroring
   `els_diag_scratch_takeup_settle_v3_test`, and an assertion arm in
   `emulator/test/els_diag_scratch_test.cpp`, whose `#else #error` fails a probe
   with no assertions. Pin the **literal** wire value, not `ELS_DIAG_PROBE`.
5. **Mirror it in the UI**: the schema constant in `ui/reflex/utils/devices.py`;
   `KNOWN_SCHEMAS` in `ui/reflex/fsms/els_diag.py`, without which the UI logs
   `firmware reports diagSchema=N, which this UI does not know how to interpret; refusing to guess`
   and records nothing; and `SCHEMAS_WITH_END_REASON` in the same file, if the
   probe publishes `diagEndReason`. `ui/tests/test_registry_contract.py` checks
   the ids against the firmware.
6. **Document it here**: one-off or durable, and its result.

## Retiring a probe

Mark the schema line `/* RETIRED -- see <replacement> */`. The scripts drop it
from the selectable list, and the `#error` chain should name the replacement.
The id stays forever. The code may stay while the guards keep it out of a
release build.
