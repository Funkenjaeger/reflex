# What Reflex changes

Reflex is a deliberate **hard fork** of
[rotary-controller-python](https://github.com/bartei/rotary-controller-python)
(RCP, the Kivy UI) and
[rotary-controller-f4](https://github.com/bartei/rotary-controller-f4)
(RCF4, the STM32F411 firmware). There is no upstream tracking and no intent to
merge back; divergence is the point.

The originals are a **rotary-table controller with a DRO attached**. Reflex is
a **manual-lathe controller**. Almost everything below follows from that one
reframe — and the parts that do not follow from it are the parts about how the
project is built and checked.

!!! info "What this page is"
    An orientation for anyone who knows the upstream projects and wants to know
    what is actually different here, and for anyone deciding whether Reflex is
    worth running. It is a summary; the reasoning lives in the design notes and
    the ADRs it links to.

---

## 1. The domain changed, so the motion model changed

Upstream's synchronized axis is a rotary table indexing against a spindle.
Reflex points the same primitive at a leadscrew and builds the lathe job on top
of it: power feed, threading, and — the actual headline — an **automatic
electronic stop**. You feed or thread up to a shoulder and the controller stops
the carriage, hands off.

The stop is optional, and how much of the cycle you hand over is a choice, not
a difficulty level: [three stop modes](guide/operator-modes.md) over the same
take-up confirmation and the same thread datum.

## 2. Thread phase became a first-class, re-derivable quantity

Upstream holds sync continuously. Reflex has to *stop*, which decouples sync —
so rather than treat that as a limitation, **thread phase is re-derived from
the Z scale after every pass**. Three capabilities fall out that most ELS
projects do not have:

- The half nut is free between passes. You are no longer married to it for the
  whole job.
- **[Pick up an existing thread](guide/picking-up-a-thread.md)** — latch a
  reference on work this setup did not cut: a re-chucked part, a thread cut
  elsewhere, a damaged thread being chased.
- **[Widen a groove past the tool that cuts it](guide/widening-a-groove.md)** —
  step thread phase between passes so a narrow tool cuts a wide groove.

## 3. The controller verifies instead of trusting

The sharpest cultural difference from upstream, and the theme of 1.1.0.

Before every pass the firmware drives the leadscrew through its backlash and
**confirms on the Z scale that the carriage actually moved**. If it did not —
an open half nut, a slipping coupling, a dead scale, a servo that is not
enabled — the pass does not start. Backlash is *measured on the machine*
([calibration run](setup/backlash-calibration.md)), not configured as a guess.

Refusals are a designed surface rather than an error path:
[When it refuses](guide/when-it-refuses.md) is generated from the same message
tables the app draws from, and every message names the machine state before it
names the fault.

Behind all of it sits an explicit [ELS safety case](design/els-safety-case.md)
— what protects the operator in which state, against which failure, with a file
and line for each claim, **including where nothing does**. It also argues its
own escalation policy: the servo-divergence watchdog raises a notice and
deliberately does *not* alarm, because alarming drops sync and de-energizes the
drive, freeing the leadscrew mid-pass. A false positive on that rung would
cause the class of incident the detector exists to catch.

## 4. A real-time constant that was inherited, then derived

Upstream ran the motion ISR at 100 kHz. That rate was never derived: it traces
to a pre-fork commit, bundled into an unrelated change, justified in the commit
message as *"speed up interrupt timer to 100K, works fine"*.

Reflex worked out what actually binds it. The constraint is **pulse shape**,
not step rate — STEP is set at the emission site and cleared on the next tick's
entry, so the pulse is one tick wide and the period is `servoCycles` ticks;
`servoCycles` must be at least 2 or the pin never goes low and there is no
pulse train at all. Against the machine's provisioned 10,000 steps/s that is a
hard floor of 20 kHz.

**50 kHz** was adopted. It gives `servoCycles = 5` — a 20 µs pulse in a 100 µs
period — and headroom to 25,000 steps/s, 2.5× the machine's current
provisioning. What it bought, on both axes at once:

| | before | after |
|---|---|---|
| Sustained ISR load | ~40% | ~20% |
| Per-tick CPU budget | 1000 cycles | 2000 cycles |
| Measured cut-start peak (888 cycles) | 89% of budget | 44% |

That is a larger gain than the STM32G474 hardware respin under consideration
would have delivered (40% → 24%), and it cost nothing.

The consequence was checked rather than assumed: at 50 kHz the added stop
overshoot is 0.1–0.2 µm against a 5 µm Z encoder count — below the resolution
of the sensor that detects the stop.

The rate is now **declared once** (`fw/Core/Inc/els_isr_rate.h`) with every
timing constant derived from it, because the old coupling was implicit and a
CubeMX regeneration could silently halve the rate and double every tick
constant in wall-clock terms.

This section is the method in miniature: inherit a constant, find out what it
is for, derive it, check what changing it costs, and write down why.

## 5. The FW/UI seam is a contract, not a convention

Firmware and UI deploy as separate artifacts, and the board requires a physical
power cycle after a flash — so the running pair can always lag the source.
Reflex makes that seam explicit and enforced:

- `protocolVersion` and `diagSchema` handshake registers, permanent by design.
- The register map is **generated from one schema** (`registers/els_stop.yaml`)
  and its layout checked by the compiler, replacing hand-maintained parallel
  struct definitions on both sides of the wire.
- A **contract test compares the UI's register definitions against the firmware
  header on every test run** — which was not possible upstream, where the two
  halves lived in unrelated repositories.

## 6. It can be developed and tested without the lathe

A native emulator **compiles the real firmware C sources** against simulated
lathe physics: shim headers override the HAL and FreeRTOS, so the code under
test is the code that ships, not a reimplementation of it.

On top of that, full-stack system tests drive the **actual UI against the
compiled firmware** — thread re-sync, take-up attribution, re-zero, the
reversing matrix, real machine geometry. Features are developed desk-first and
confirmed in a short window at the machine, instead of being debugged at the
machine.

## 7. Field serviceability

A **Modbus bootloader in sector 0** flashes the application over the RS-485
link the UI already holds, so the ST-Link is needed only for virgin boards,
option bytes and disaster recovery. Verified on the machine 2026-09-07: 44,868
bytes in 12.9 s, no programmer and no power cycle, with an anti-brick swap-back
that restored the backup image after three watchdog strikes.

Alongside it, a fixed **identity window** — magic, stage, build revision, dirty
flag — at an address deliberately outside the growing application struct, so a
client can ask *which program is answering and which build it is*, and keeps
getting a correct answer as the application map grows.
([ADR](decisions/els-modbus-register-map.md))

## 8. Documentation is part of the product

A full user guide, from installing on a Pi through to picking up an existing
thread — and two of its pages are **generated from the code they describe**,
with CI failing the build when they drift. Design decisions are recorded as
dated ADRs carrying their reasoning and their rejected alternatives, rather
than surviving as folklore.

## 9. One repository, one version

`fw/` and `ui/` were welded into a monorepo with **full history preserved on
both sides**, path-rewritten so `git log -S` and `--follow` work across the
whole lineage. They release together, on one version number — which is what
makes the contract test in §5 possible at all.
([ADR](decisions/repo-structure-monorepo.md))

---

## What is not claimed

- **The hardware is still upstream's.** Reflex runs on the
  `rotary-controller-pcb` design, boots from upstream's OSPI image, uses the
  same Kivy stack and the same RS-485 UART, and the UI still runs as root for
  KMS/DRM. The control-board respin is the first thing that needs new hardware.
- **One servo, on the leadscrew.** Backing off in X is your hand, in every
  mode.
- **No multi-start threading**, and it cannot exist before the respin — it
  needs the mod-lead fix. The thread phase offset is explicitly not a
  substitute: its correction folds within one pitch and biases into the cutting
  direction, which is exactly wrong for indexing a second start.
- **The safety case is an enumeration, not a certification.** Nothing in it has
  been through fault injection, and claims that could not be confirmed by
  reading the code are marked UNVERIFIED rather than asserted.
