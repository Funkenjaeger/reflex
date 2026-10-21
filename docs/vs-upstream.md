# What Reflex changes

Reflex is a deliberate **hard fork** of
[rotary-controller-python](https://github.com/bartei/rotary-controller-python)
(RCP, the Kivy UI) and
[rotary-controller-f4](https://github.com/bartei/rotary-controller-f4)
(RCF4, the STM32F411 firmware), with no upstream tracking and no intent to
merge back. The originals are a **rotary-table controller with a DRO
attached**; Reflex is a **manual-lathe controller**.

---

## 1. The domain changed, so the motion model changed

Upstream's synchronized axis is a rotary table indexing against a spindle.
Reflex points the same primitive at a leadscrew for power feed, threading, and
an optional **automatic electronic stop** at a shoulder, in
[three stop modes](guide/operator-modes.md) that share one take-up
confirmation and one thread datum.

## 2. Thread phase is re-derived after every pass

Upstream holds sync continuously. Reflex has to *stop*, which decouples sync,
so **thread phase is re-derived from the Z scale after every pass**. The half
nut is free between passes, and Reflex can
[pick up an existing thread](guide/picking-up-a-thread.md) and
[widen a groove past the tool that cuts it](guide/widening-a-groove.md).

## 3. The controller verifies instead of trusting

Before every pass the firmware drives the leadscrew through its backlash and
**confirms on the Z scale that the carriage moved**, or the pass does not
start. Backlash is [measured on the machine](setup/backlash-calibration.md),
and [When it refuses](guide/when-it-refuses.md) is generated from the app's
message tables. The [ELS safety case](design/els-safety-case.md) lists what
protects the operator in which state, against which failure, **including where
nothing does**.

## 4. The motion ISR rate is derived

Upstream runs the motion ISR at 100 kHz. The constraint that binds the rate is
**pulse shape**, not step rate: the STEP pulse is one tick wide and the period
is `servoCycles` ticks, so `servoCycles` must be at least 2, a hard floor of
20 kHz against the machine's provisioned 10,000 steps/s. Reflex runs at
**50 kHz** (`servoCycles = 5`, headroom to 25,000 steps/s), declared once in
`fw/Core/Inc/els_isr_rate.h` with every timing constant derived from it.

| | before | after |
|---|---|---|
| Sustained ISR load | ~40% | ~20% |
| Per-tick CPU budget | 1000 cycles | 2000 cycles |
| Measured cut-start peak (888 cycles) | 89% of budget | 44% |

At 50 kHz the added stop overshoot is 0.1–0.2 µm against a 5 µm Z encoder
count, below the resolution of the sensor that detects the stop.

## 5. The FW/UI register contract is enforced

Firmware and UI deploy as separate artifacts, so the running pair can lag the
source. `protocolVersion` and `diagSchema` handshake registers guard the seam,
the register map is **generated from schemas** (`registers/*.yaml`) with its
layout checked by the compiler, and a **contract test compares the UI's
register definitions against the firmware header on every test run**.

## 6. It can be developed and tested without the lathe

A native emulator **compiles the real firmware C sources** against simulated
lathe physics, and full-stack system tests drive the **actual UI against the
compiled firmware**. Two pages of the user guide are generated from the code
they describe, and CI fails the build when they drift.

## 7. Field serviceability

A **Modbus bootloader in sector 0** flashes the application over the RS-485
link with no programmer and no power cycle, and swaps the backup image back
after three watchdog strikes; the ST-Link is needed only for virgin boards,
option bytes and disaster recovery. A fixed **identity window** tells a client
which program and which build is answering.
([decision](design/decisions.md#one-register-map-for-bootloader-and-application))

## 8. One repository, one version

`fw/` and `ui/` share one repository with **full history preserved on both
sides**, and release together on one version number, which makes the contract
test in §5 possible.
([decision](design/decisions.md#one-repository-for-firmware-and-ui))

---

## What is not claimed

- **The hardware is still upstream's.** Reflex runs on the
  `rotary-controller-pcb` design and uses the same Kivy stack and the same
  RS-485 UART. The reference machine boots the
  [elspi image](https://github.com/Funkenjaeger/elspi) rather than upstream's
  OSPI, and the UI runs as an unprivileged service user (DRM master by first
  open, a polkit grant for NetworkManager) instead of root. The control-board
  respin is the first thing that needs new hardware.
- **One servo, on the leadscrew.** Backing off in X is your hand, in every
  mode.
- **No multi-start threading**, and it cannot exist before the respin — it
  needs the mod-lead fix. The thread phase offset is explicitly not a
  substitute: its correction folds within one pitch and biases into the cutting
  direction, which is exactly wrong for indexing a second start.
- **The safety case is an enumeration, not a certification.** Nothing in it has
  been through fault injection, and claims that could not be confirmed by
  reading the code are marked UNVERIFIED rather than asserted.
