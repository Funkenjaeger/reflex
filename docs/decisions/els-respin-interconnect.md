# Board respin: STM32 interconnect punch list

**Status:** working reference, 2026-09-04.

**Two sources, and they disagree in scope:**

1. **Firmware** -- `fw/reflex.ioc`, `fw/Core/Inc/Ramps.h`, `fw/Core/Src/Ramps.c`,
   read 2026-09-04. Authoritative for what the software drives.
2. **Upstream schematic V1.2** (Stefano Bertelli, 2023-04-21), the PDF in
   `bartei/rotary-controller-pcb`. **Evan's actual board is NEWER than this**, so
   every schematic-derived line below is provisional and flagged `[V1.2]`.
   Anything marked that way needs confirming against the real board before it
   drives a decision.

The upstream repo is a **dead hard fork** for code purposes -- this is a
hardware reference only, not a suggestion to pull anything.

---

## 0. READ THIS FIRST: the `.ioc` is not the pinout

`fw/reflex.ioc` assigns **16 pins**. The firmware drives **19**. `STEP`, `DIR`
and `ENA` are configured in application code by `configureOutputPin()`
(`Ramps.c:262`) from `#define`s in `Ramps.h:31-38`. CubeMX has never known about
them.

**Regenerating a pinout from the `.ioc` silently drops the entire motor
interface.** Reconcile against `Ramps.h` before trusting any generated artifact.

| Pin | Signal | Source | Net `[V1.2]` |
|-----|--------|--------|--------------|
| PA0 | **STEP** | `Ramps.h` | `MTR_STEP` |
| PA1 | SPARE_1 | both | `SPARE_1` |
| PA5 | S_TIM2_CH1_ETR | `.ioc` | an `ENCnA` |
| PA6 | S_TIM3_CH1 | `.ioc` | an `ENCnA` |
| PA7 | S_TIM3_CH2 | `.ioc` | an `ENCnB` |
| PA8 | S_TIM1_CH1 | `.ioc` | **`ENC1A`** |
| PA9 | S_TIM1_CH2 | `.ioc` | **`ENC1B`** -- from U5 pin 18 (Y2) |
| PA10 | USART1_RX | `.ioc` | `RXD` |
| PA13 | SWDIO | `.ioc` | `TMS/SWDIO` -> J3 |
| PA14 | SWCLK | `.ioc` | `TCLK/SWCLK` -> J3 |
| PA15 | USART1_TX | `.ioc` | `TXD` |
| PB3 | S_TIM2_CH2 | `.ioc` | an `ENCnB` -- **and `TDO/SWO` on J3.** See §2. |
| PB6 | S_TIM4_CH1 | `.ioc` | an `ENCnA` |
| PB7 | S_TIM4_CH2 | `.ioc` | an `ENCnB` |
| PB12 | USR_LED | both | `USR_LED` |
| PB14 | **DIR** | `Ramps.h` | `MTR_DIR` |
| PB15 | **ENA** | `Ramps.h` | `MTR_ENA` (`ENA_DELAY_MS` 500) |
| PH0/PH1 | OSC_IN/OSC_OUT | `.ioc` | 8 MHz HSE, U7 ECS-80-12-33 |

TIM1 = ENC1 (spindle). TIM2/3/4 = the other three scales. TIM9 is an internal
clock source, TIM11 the HAL timebase, USART1 is Modbus.

**Which of ENC2/3/4 sits on which timer is NOT established here** -- the text
layer gives the net names but not the pin mapping. Confirm before relying on it.

---

## 1. ALREADY THERE -- do not "add" these, they exist `[V1.2]`

I initially wrote "add an SWD header" as a must-do. That was wrong.

* **J3, a 10-pin 1.27 mm debug header** (`10129381-910001BLF`), carrying
  `RESET`, `TMS/SWDIO`, `TCLK/SWCLK`, `TDO/SWO`, 3V3, 5V and two GNDs. This is
  the standard Cortex Debug pinout and it is the recovery path. **Keep it.**
* **Six test points, TP1-TP6**, on the motor/spare nets.
* **VCAP_1 with C12 10u**; `VDDA`/`VSSA` tied with filtering (C15 1u, C8/C9/C17
  100n); crystal load caps C18/C19 20pF.
* **A reset network** -- D4 plus R8 1K2 on `RESET`.
* **`SPARE_1` through `SPARE_4`** already exist as nets, all the way out to
  connectors. Only `SPARE_1` (PA1) is claimed in firmware.

---

## 2. MUST FIX -- conflicts the current design carries

### SWO and PB3 are the same pin

J3 exposes `TDO/SWO`. On STM32F4 that is **PB3**. Firmware configures PB3 as
`S_TIM2_CH2` -- an encoder input, pulled up, fed from the U5 buffer.

So the debug connector's SWO pin is wired into an encoder net. Two consequences:
**SWO tracing is unavailable** on this design, and a probe that ever drives that
line would fight a buffer output. On a machine whose only other output is a
touchscreen, losing printf-style tracing is a real cost.

**Respin: pick one.** Either free PB3 (easy on LQFP64 -- move that scale to
TIM8) and get SWO back, or drop SWO from the header so it stops looking
available.

### The test points are on the wrong nets

TP1-TP6 sit on the **motor and spare outputs** -- signals the firmware
originates and already knows the state of. There is **not one test point on an
encoder input.**

That is exactly backwards relative to where the time went. The VFD-noise
investigation ran for weeks and **still has an open question a scope pad would
have closed in minutes**: nobody captured the *width* of the injected glitches,
which is what decides whether input filtering would have helped. A
counts-per-second figure cannot answer it. The board instrumented the signals it
generates and left the signals it receives unreachable.

### BOOT0 -- and an open question `[V1.2]`

Evan's reading of his board is that **BOOT0 is NC**, and the STM32F4 has **no
internal pull** on it (AN4488 5.2: an external connection is *required*), so the
boot source is currently selected by leakage.

**But V1.2 carries an `R9 100k` on the MCU page whose net I could not determine
from the PDF text layer.** If R9 is a BOOT0 pull-down, BOOT0 is defined and this
item is already handled. **Confirm R9's net** -- it changes the answer.

**Respin regardless:** BOOT0 gets a deliberate pull-down *and* an accessible way
to force it high (pad beside a 3V3 pad, jumper, or solder bridge).

### BOOT1 = PB2 must be pulled low

The half that is easy to miss. On F4 the boot source decodes from BOOT0 **and**
BOOT1, and BOOT1 is PB2. BOOT0=1 with BOOT1=0 enters system memory; BOOT0=1 with
BOOT1=1 enters embedded SRAM. PB2 appears in neither the `.ioc` nor the code, so
it is presumably floating -- which makes any BOOT0-high attempt land in either
mode at random. **Pull PB2 down.**

---

## 3. MUST NOT CONNECT

**PA9 must not carry an encoder channel** if the ROM tier is ever wanted. The
mask ROM hard-codes USART1 on PA9/PA10, cannot remap, and configures those pins
at startup regardless of board wiring. A UART idles MARK, so BOOT0-high drives
PA9 high into `ENC1B` -- which U5 is driving. Two push-pull outputs, one net.

**The mitigation that does not work**, recorded so it is not re-invented:
*disconnecting the encoder does not isolate PA9.* That changes what reaches U5's
**input**; U5's output keeps driving PA9 while the board is powered. See
`fw/README.md`.

**Do not route an MCU pin for RS-485 DE.** DE is derived from TXD in discrete
hardware: TXD drives U1's D directly and Q1's base through R7 520R; Q1 (BC847C,
emitter grounded) inverts against R6 2K2 and drives RE and DE tied together.
Confirmed in firmware -- `RampsModbusData.EN_Port = NULL`. A DE pin would be
dead copper that invites firmware to drive nothing.

**PA11/PA12 are NC** -- no USB, no DFU tier. That is a decision to re-make, not
a fixed constraint.

---

## 4. MUST MOVE, if the ROM recovery tier is wanted

Freeing PA9 is not a one-pin change. The 48-pin plan was **ENC1 -> TIM5
(PA0/PA1)** -- and **PA0 is STEP**, PA1 is SPARE_1. That plan silently required
relocating the motor step output.

**Do not port it. Redo it for LQFP64**, where TIM8's channels exist (explicitly
absent on 48-pin parts: *"48 pins packages: TIM8:CH1, CH2, CH3, and CH4 pins not
available"*). On F4, TIM8_CH1/CH2 are PC6/PC7 -- **verify against the F413RG
LQFP64 pinout.**

* **ENC1 -> TIM8**
* **STEP stays on PA0** -- no motor-code change, which removes the only part of
  the old plan that touched the ISR emission site and pulse-width timing
* **PA8/PA9/PA10 freed**, giving the mask ROM its USART1 pair
* TIM1 becomes a spare advanced timer

That is the concrete case for the 64-pin part: a three-way shuffle plus a
firmware change collapses to a pin move.

---

## 5. Worth reconsidering, not yet decided

### MTR_STEP runs through a ULN2003 `[V1.2]`

`MTR_STEP`, `MTR_DIR`, `MTR_ENA` and all four spares pass through **U2, a
ULN2003ADR Darlington array**, to J4/J5. That makes every one of them
**open-collector, inverting, and slow** -- a Darlington's turn-off in particular
depends entirely on the external pull-up.

For an enable line that is fine. **For a step train it is a bandwidth question
nobody here has measured.** If step rate ever becomes a ceiling, or edges look
soft at the drive, U2 is the first suspect. Flagged as a question: no maximum
step rate has been established and no edge has been scoped.

It also means **the spares are not clean logic outputs** -- a spare intended as
a scope trigger or timing marker wants a direct MCU pad, not a Darlington
output.

### The encoder connectors already have room for differential

Each encoder arrives on a **DE-9** (`L77TSEH09SOL2RM5`, J6-J9) carrying 5V, GND
and a single-ended A/B pair -- through 1k2 resistors R10-R17 into U5, a
**74VHC9151FT 9-channel Schmitt buffer** (8 of 9 channels used). `ENC1B` is U5
pin 18 = Y2.

Differential signalling remains the by-construction fix for VFD noise, and
**the connectors do not have to change** -- a DE-9 has spare pins for the
complements. What changes is U5 -> line receivers.

Note the standing hardware-sourcing preference: US/allied/FOSS provenance, not
Chinese. TI and Analog Devices both make suitable RS-422 receivers.

**Not established:** whether U5 runs on 3V3 or 5V. The text layer puts 5V near
its decoupling. It matters twice -- it sets how hard the PA9 contention would
be, and it decides whether those pins' 5V-tolerance is load-bearing.

---

## 6. KEEP -- deliberate choices that read as mistakes

* **R3/R4/R5 = 120R** on the RS-485 side, where 560R-1k is the usual bias. The
  driver is disabled during every mark bit (DE follows TXD combinationally, no
  RC hold), so between bits the bus is held at a differential 1 by **bias
  alone**. It has to be stiff. This bias, not the UART, is the ceiling if more
  baud is ever wanted.
* **Pull-ups on every encoder input pin** (`GPIO_PULLUP` on PA5-PA9, PB3, PB6,
  PB7).
* **The hardware DE circuit.** It deletes the entire DE-timing bug class.
* **SWD pads regardless of anything else**, per the bootloader task.

---

## 7. Debug pads: what deserves breakout

Ordered by what actually cost time on this project. The board's existing
instrumentation is on outputs; the gap is inputs.

### Tier 1 -- non-negotiable

* **Keep J3** (SWDIO, SWCLK, RESET, 3V3, GND). With no ROM tier and no DFU
  tier, SWD is the only way back from a bad flash. Include NRST -- *connect
  under reset* is what recovers a part whose application reconfigures the debug
  pins.
* **Several GNDs distributed near the probe points**, not one. A long ground
  lead turns a fast-edge measurement into a guess.

### Tier 2 -- the ones this year's blockers argue for

* **ENC1A / ENC1B at the MCU side of U5 (PA8, PA9).** The highest-value pair on
  the board, and the one measurement that is still missing. See §2.
* **The other three scale pairs** (PA5/PB3, PA6/PA7, PB6/PB7) -- same argument.
* **U5 inputs as well as outputs**, at least for ENC1. Probing both sides of the
  buffer is what separates "noise arriving on the cable" from "noise injected
  after the buffer", and that distinction is the whole differential-signalling
  question.
* **STEP (PA0), DIR (PB14), ENA (PB15) at the MCU pin** -- upstream of U2, not
  the existing TPs which sit downstream. Scoping the step train against the
  encoder input is the core ELS debugging loop: take-up, backlash, stop
  overshoot and the half-nut detector all live in that relationship. Keeping the
  downstream TPs too is what lets you measure U2's contribution.
* **BOOT0**, with an adjacent 3V3 pad, so forcing it is a jumper rather than
  rework. On the current board this was impossible -- which is precisely why the
  BOOT0 hypothesis went untested.
* **USART1 TX/RX at the MCU (PA15, PA10)**, *before* U1. Separates "the MCU
  never sent it" from "the bus ate it" -- the discrimination the field
  bootloader will need constantly during bring-up.

### Tier 3 -- cheap, each buys a specific answer

* **RS-485 A/B differential test points** -- the other half of the pair above.
* **A spare MCU GPIO straight to a pad**, bypassing U2, as a scope trigger /
  timing marker. Two of them let you bracket an interval rather than guess at
  one edge. The existing SPARE_n nets do *not* serve this -- they are Darlington
  outputs.
* **SWO (PB3), if §2 frees it.**
* **3V3 and 5V rail pads**, for supply-noise measurement on a board that lives
  next to a VFD.

### Physical form

The stated use is "probing **and/or wire tacking**", which argues for pads or
oversized vias that accept a soldered wire -- not bare probe-tip targets, and
not footprints that only take a spring pin. The lesson from this board is
direct: a UFQFPN48 with nothing to attach to made a hypothesis untestable and
left it open for weeks.

---

## 8. What this document cannot tell you

* **Everything V1.2 says may be stale.** Evan's board is newer.
* **`R9`'s net** -- possibly the BOOT0 pull-down, possibly not. Changes §2.
* **U5's supply rail**, 3V3 or 5V. See §5.
* **Which of ENC2/3/4 maps to which timer**, and the exact TP1-TP6 net mapping.
  The text layer gives names, not associations.
* **Whether PB2, VBAT and PC13-PC15 are tied to anything.**
* **The F413RG LQFP64 pinout specifics** -- TIM8 channel pins, VCAP count, which
  port C pins are bonded out. Verify from the datasheet before committing §4.
* **Any max step rate or edge measurement** for the U2 question in §5.
