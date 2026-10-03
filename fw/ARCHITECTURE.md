# Firmware Architecture

STM32F411 (Cortex-M4 @ 100 MHz) firmware for a CNC rotary table controller. Reads up to 4 encoder inputs and drives a stepper motor with programmable sync ratios, controlled remotely over Modbus RTU (e.g. from a Raspberry Pi).

---

## Layer Stack

```
Modbus Master (Raspberry Pi)
        ↓ USART1 @ 115200 baud
FreeRTOS Tasks (application)
        ↓
SynchroRefreshTimerIsr (TIM9 ISR — bare-metal, high priority)
        ↓
STM32 HAL + CMSIS
        ↓
STM32F411 hardware
```

---

## Modules

### `Core/Src/Ramps.c` — Core Motion Control

- **`SynchroRefreshTimerIsr()`** — high-priority TIM9 ISR running every 20 µs (50 kHz; the rate and every tick constant derived from it are declared in `Core/Inc/els_isr_rate.h`). Reads all 4 encoder counters, computes deltas with fractional error tracking, applies sync ratios, and generates step/direction pulses on PA0/PB14. Uses the Cortex-M4 DWT cycle counter to measure its own execution time.
- **`updateIndexingPosition()`** — trapezoidal ramp (accel/cruise/decel) for indexed moves.
- **`updateJogPosition()`** — continuous speed control for jogging.
- Three FreeRTOS tasks: `userLedTask`, `updateSpeedTask`, `servoEnableTask`.

### `Core/Src/Modbus.c` — Communications

Full Modbus RTU slave implementation (address 17). The entire `rampsSharedData_t` struct is memory-mapped directly to Modbus holding registers — no translation layer. A master can read/write servo state (mode, target steps, speed, sync ratios) directly via FC3/FC6/FC16.

### `Core/Src/Scales.c` — Encoder Initialization

Configures TIM1–TIM4 in encoder mode (TI12, both channels). TIM2 is 32-bit for higher resolution; the rest are 16-bit.

### `Core/Src/tim.c`, `usart.c`, `gpio.c` — HAL Peripherals

STM32CubeMX-generated peripheral initialization code.

---

## Hardware Peripherals

| Peripheral | Role |
|---|---|
| TIM1–TIM4 | Encoder inputs (4 scales) |
| TIM9 | Motion control ISR timebase |
| TIM11 | FreeRTOS systick |
| USART1 | Modbus RTU (PA10 RX, PA15 TX) |
| PA0 | STEP pulse output |
| PB14 | DIR output |
| PB15 | ENA output (active low) |
| PA3 (SPARE_2) | Mirrors every STEP pulse in every build: set in `Core/Src/Ramps.c` on each step, cleared at the start of the next timer entry in the same file |
| PA4 (SPARE_3) | The only free spare pin; debug/scope output |
| PB12 | User LED |

**Clock:** HSE → PLL → 100 MHz SYSCLK, hardware FPU enabled.

---

## Concurrency Model

Hybrid bare-metal + FreeRTOS:

- The **TIM9 ISR** handles all timing-critical motion control outside the RTOS scheduler.
- **FreeRTOS tasks** handle everything millisecond-tolerant: Modbus parsing, speed updates, motor enable/disable, and LED status.
- `rampsSharedData_t` is the shared state between the ISR and tasks. The ISR reads servo commands from it; Modbus tasks write to it.

### FreeRTOS Tasks

| Task | Priority | Period | Purpose |
|---|---|---|---|
| `TaskModbusSlave` | Normal | Event-driven | Modbus protocol handler |
| `updateSpeedTask` | Low | 50 ms | Servo speed calculations |
| `servoEnableTask` | Low | 100 ms | Motor enable/disable |
| `userLedTask` | Low | 50 ms | Status LED + Modbus activity indicator |
| `defaultTask` | Normal | 1000 ms | Baseline task |

---

## Servo Modes

Controlled via Modbus register `servoMode`:

| Mode | Behavior |
|---|---|
| 0 | Disabled — motor idle |
| 1 | Indexing — move `stepsToGo` steps with trapezoidal accel/decel ramp |
| 2 | Jogging — continuous motion at `jogSpeed` |

### Encoder Synchronization

Encoder input is scaled by a programmable ratio before being added to `desiredSteps`:

```
desiredSteps += encoderDelta × (syncRatioNum / syncRatioDen)
```

Fractional remainders are tracked per-axis to prevent accumulated positioning error.

---

## ELS Shoulder Stop

The ELS stop ends a synchronized cut at a set Z position and keeps thread phase, so every pass re-enters the same groove however the carriage returned to the start. The reasoning is at `elsStop_t` in `Core/Inc/Ramps.h`, `Core/Inc/els_phase.h` and `Core/Inc/els_backlash_cal.h`.

A job runs from `enable = 1` to `enable = 0` and keeps one reference. When Z crosses `stopPosition` in `stopDirection`, the firmware sets `active = 1`, gating sync off; the job's first trigger latches the spindle and Z positions. Software clears `active` to start every pass, the first included. That edge runs the backlash take-up and its Z confirmation, turning included, then the re-sync if a reference and a pitch exist.

### Re-sync mechanism

The re-sync subtracts the actual advance since the latch (Z times the pitch geometry) from the ideal advance (spindle motion times the sync ratio), folds the result modulo the pitch to the shortest signed value, and queues it as an indexing move before sync resumes. The sync return then spans whole pitches, so the spindle phase at the next trigger matches the latched phase.

### Limits

A geometry mismatch over half a pitch (the correction folds within `pitch/2`) aliases into another groove. `threadPitchSteps / zCountsPerPitch` must equal the drivetrain's leadscrew steps per Z count, or phase drifts with cut distance. The take-up confirmation catches an open half-nut at Cut.

### Manual reference latch

`latchCommand` takes the same capture at an operator-chosen point (a tool seated in an existing groove, lash loaded by a cutting-direction jog) to pick up a re-chucked or foreign thread. The ISR sets `latchedSpindle`, `latchedZ` and `referenceLatched` and increments `latchSeq`. With `enable == 0` it does neither; the missing edge is the refusal.

### Thread-phase offset (widening a groove past the cutter)

The offset widens a groove without moving the tool. `elsStop.phaseOffsetSteps`, a total in leadscrew steps, is added to `phaseError` before the mod-pitch fold and takes effect at the next resume. The host writes `phaseOffsetPending`, then `phaseOffsetCommand` (that order carries 32 bits over 16-bit registers without a lock), and edge-detects `phaseOffsetSeq`; accumulation is the host's job. The total clears on the `enable` 0-to-1 edge, never between passes.

**Frame caveat.** The offset acts in the machine frame: with `cuttingDir` −1 an entry of `X` acts as `pitch − X` and widens the other flank, a wrong part with the tool still in the groove. This is unverified on hardware; `els_phase_offset_command_test` case 7 pins both polarities.

### Backlash calibration and take-up confirmation

A pulse count cannot show that the carriage moved (half-nut open, servo disabled, coupling slipped), so a configured take-up (`backlashSteps` nonzero) is confirmed against the Z scale. Requiring Z to move `backlashSteps` worth of counts is wrong in the unsafe direction: travel inside the lash moves only the nut, so it would refuse every correctly configured take-up. Instead the take-up commands the calibrated lash plus a margin, so a correct one must move the carriage by the detection distance plus that margin. Never trim it toward the minimum.

Calibration (`calCommand`, run in the ISR) seats against a flank, counts servo steps until Z moves across three reversals, and re-seats in the cutting direction; the host judges consistency and writes `backlashSteps`. Sync is gated for the run, since `scales[].syncEnable` ignores `elsStop.enable`. Each leg arms on the first pulse in the new direction, since the decelerating ramp overshoots and its return travel is lash.

The gate fails closed at the start of a pass, tool clear of the work: sync stays gated (`takeupPending`, `ELS_TAKEUP_ERR_UNCONFIRMED`), and after 250 ms (`ELS_TAKEUP_CONFIRM_WINDOW_TICKS`) the pass aborts to `active = 1` with the reference intact. A take-up that never reaches its target reports `ELS_TAKEUP_ERR_TIMEOUT` and holds until `elsStop.enable` returns to 0. A `calMotionThreshCounts` of 0 also fails closed: a permissive default would leave every uncommissioned machine open loop.

### Modbus interface

The register list is `registers/els_stop.yaml` at the repository root. Command registers clear the instant the ISR consumes them; edge-detect the `*Seq` counters instead of polling. `machineMode` is a register, republished every ~100 ms in every build, since only one diagnostic probe runs at a time. `protocolVersion` is `protocol_version` in that file: bump it and regenerate whenever `rampsSharedData_t` changes shape, since reflex-ui checks it at connect.

---

## Build System

- Toolchain: `arm-none-eabi-gcc`
- Build system: CMake 3.30+
- Optimization: `-Ofast` (Release), `-Og` (Debug)
- FPU: `-mfloat-abi=hard -mfpu=fpv4-sp-d16`
- Linker script: `STM32F411CEUX_FLASH.ld` (512 KB flash @ 0x8000000, 128 KB RAM @ 0x20000000)
- Outputs: `.elf`, `.hex`, `.bin`

### Flashing

```bash
# ST-Link v2, over SWD
st-flash --format ihex write reflex-fw.hex
```

`raspberry.cfg` bitbangs SWD from a Raspberry Pi's GPIO through OpenOCD's `bcm2835gpio` driver, which cannot work on a Pi 5; `raspberrypi5.cfg` is an untested replacement. See the README.
