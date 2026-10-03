# AGENTS.md — Reflex Firmware

## Branching and Hardware Verification — READ FIRST

**This project drives a real lathe. The only complete test is on hardware, and
the maintainer runs that, not on demand.** The emulator and the test suite have
repeatedly looked green while something real was wrong — no servo dynamics, no
Modbus timing, no metal. Emulator green is evidence, never verification.

**Do NOT commit directly to `dev`.** Release candidates are cut from it, and
everything on it is supposed to be hardware-verified.

- Work on a **feature branch**, or on **`integration`** when several changes are
  in flight and separate branches would just be overhead.
- A commit is hardware-verified when an integration build (`vX.Y.Z-alpha.N`) made
  from it has been installed and run on the machine. Merging `integration` into
  `dev` happens **only after that**; the maintainer does that merge, or
  explicitly asks for it.
- Merging `dev` into `main` is **the maintainer's alone**. Never do it.

**The one exception**, for changes that cannot affect machine behavior and so
need no hardware run: documentation, comments, tests, and
emulator-only code. Anything touching `Core/` is NOT clerical, however small it
looks or however well tested — `Core/Src/Ramps.c` is the ISR that moves the
machine.

If unsure whether a change qualifies, it does not. Put it on a branch and ask.

**Never push without being asked.** `origin` is the canonical remote. This
public repository receives only `dev`, `main` and release tags, each by a
deliberate push: never `integration`, a feature branch or an `-alpha` tag.

What has and has not been proven on metal is recorded where a claim belongs: in
the header or test that makes it. A constant nobody has measured says so in its
own comment; a feature the emulator covers but hardware does not says so in the
test that covers it. Do not create a separate ledger of verification points.

## Work tracking

**The work queue is not kept in this repo.** The maintainer keeps it outside,
so there is no in-repo file to append an item to. Raise a deferred item, a bug
found while debugging, or a hardware workaround that a future board revision
should remove by telling the maintainer in your reply.

Do NOT leave `TODO`/`FIXME` comments in code, documentation, or bash snippets.
If something is worth writing down in the source, write the fact itself — what
is unmeasured, what the constraint is, why the code is shaped this way — as a
normal comment that stands on its own, not as a pointer to a queue.

## What this firmware is

Reflex Firmware is the STM32F411 firmware for the Reflex lathe DRO and electronic leadscrew: encoder capture, step generation and motion control, talking to the host UI over RS-485/Modbus RTU.

## The UI half (`../ui`)

This firmware is tightly coupled with the Python/Kivy host application in `../ui`, in the same repository.

- **Interface:** RS-485 Modbus RTU — the entire `rampsSharedData_t` struct is memory-mapped to Modbus holding registers
- **Version compatibility:** a single commit spans both halves, so a checkout is self-consistent by construction; cross-half changes affecting the Modbus register interface are still called out in commit messages. (The DEPLOYED pair on the machine can still lag — the `protocolVersion` register guards that seam.)

## Building

### Firmware (STM32F411, arm-none-eabi toolchain)
```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```
Outputs: `build/reflex-fw.elf`, `.hex`, `.bin`.

### Emulator (native, x86_64)
```bash
cmake -S emulator -B emulator/build
cmake --build emulator/build
```
Outputs: `emulator/build/lathe-emulator`.

### Always build both after code changes
After any changes to `Core/Src/Ramps.c`, `Core/Inc/Ramps.h`, or other firmware sources, build **both** targets and fix any issues before proceeding. Ensure **all build artifacts stay in `build/` directories** — nothing should leak into the repo root or `emulator/` directory.

## Architecture

### Key files
- `Core/Src/Ramps.c` — core motion control (ISR, indexing, jog, ELS)
- `Core/Inc/Ramps.h` — shared data structs, Modbus-mapped
- `Core/Src/Modbus.c` — Modbus RTU slave (addr 17)
- `Core/Src/Scales.c` — encoder timer init

### Concurrency
- TIM9 ISR (`SynchroRefreshTimerIsr`) handles all motion control at 20 µs ticks (50 kHz; rate and derived tick constants in `Core/Inc/els_isr_rate.h`)
- FreeRTOS tasks handle Modbus, speed updates, motor enable
- `rampsSharedData_t` is the shared state, memory-mapped to Modbus registers

### Servo modes
- 0: disabled
- 1: indexing (trapezoidal ramp, sync-driven)
- 2: jogging (continuous speed)

### ELS (electronic leadscrew) threading
- `elsStop.enable = 1` starts a threading job
- Sync gates on/off via `elsStop.active` and `elsStop.takeupPending`
- Resume sequence (active 1→0): reset state → backlash takeup → phase correction → sync resume
- Phase correction folds to ±pitch/2, then constrained to cutting direction
- Sync un-gates before correction move completes
- `stepsToGo`/`currentSpeed` reset before takeup

### Modbus register layout
Adding fields to `rampsSharedData_t` shifts register offsets and breaks host compatibility.

### ISR tick order (critical for timing)
1. Reset STEP pin
2. Update execution interval
3. Handle ELS enable/active state transitions
4. Check takeup completion → phase correction
5. Read encoders, compute sync deltas, check ELS trigger
6. Run indexing/jog ramp
7. Generate step pulses
8. Update servoCyclesCounter
