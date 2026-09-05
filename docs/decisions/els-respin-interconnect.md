# Respin punch list -- STM32F413RGT6 interconnect

**Status:** working reference, 2026-09-04. Verified pin/AF assignments per
RM0430/AN2606/DS11581/AN4488 (see §5); nothing here has been fabricated or
measured on real hardware.

Every net that touches the MCU, cross-referenced against the datasheet and
AN2606 so the PA9 class of mistake cannot repeat.

**Documents used.** `DS11581 Rev 7` (STM32F413xG/H datasheet) -- LQFP64 pinout
p.44, alternate-function Table 12 pp.66-72. `AN2606 Rev 70` -- section 40,
**Table 87, STM32F413xx/423xx configuration in system memory boot mode**,
pp.196-201. Firmware: `fw/reflex.ioc`, `Ramps.h`, `Ramps.c`. Upstream schematic
V1.2 `[S]`, older than the actual board.

**Package:** LQFP64 -- 48 GPIO, 4x VDD, 4x VSS, VBAT, NRST, VDDA/VREF+,
VSSA/VREF-, VCAP_1, BOOT0, PH0/PH1.

---

## 1. THE CHECK: pins the mask ROM takes over

**This is the PA9 failure class, enumerated.** When BOOT0 is high the ROM
configures ALL of these before any application code runs. It does this
regardless of board wiring.

Severity is what matters:

* **OUTPUT (push-pull)** -- the ROM *drives* the net. Anything else driving it
  is a fight. **This is what PA9 is today. Never put a driven signal here.**
* **Open-drain** -- the ROM only ever sinks. Tolerable, but the pin floats when
  released, so anything downstream needs a defined level.
* **Input** -- the ROM only listens, with a weak pull. Harmless to a driver.

| Pin | ROM function | Direction | On LQFP64 | Verdict for us |
|-----|--------------|-----------|-----------|----------------|
| **PA9** | USART1_TX | **OUTPUT** | yes | **used deliberately -- see §2** |
| **PA6** | SPI1_MISO | **OUTPUT** | yes | **KEEP CLEAR of driven signals** |
| **PB10** | USART3_TX | **OUTPUT** | yes | keep clear |
| **PB13** | CAN2_TX | **OUTPUT** | yes | keep clear -- and never put MTR_STEP here |
| **PC11** | SPI3_MISO | **OUTPUT** | yes | keep clear |
| PA11 / PA12 | USB DM / DP | bidir | yes | leave NC unless you want DFU |
| PA10 | USART1_RX | input | yes | used deliberately, §2 |
| PB11 | USART3_RX | input | **no** | n/a |
| PB5 | CAN2_RX | input, pull-up | yes | acceptable |
| PA4 | SPI1_NSS | input, pull-down | yes | acceptable |
| PA5 | SPI1_SCK | input, pull-down | yes | acceptable |
| PA7 | SPI1_MOSI | input, pull-down | yes | acceptable |
| PA15 | SPI3_NSS | input, pull-down | yes | acceptable |
| PC10 | SPI3_SCK | input | yes | acceptable |
| PC12 | SPI3_MOSI | input | yes | acceptable |
| PA8 | I2C3_SCL | open-drain | yes | acceptable + needs a defined level |
| PB4 | I2C3_SDA | open-drain | yes | acceptable |
| PB6 | I2C1_SCL | open-drain | yes | acceptable |
| PB7 | I2C1_SDA | open-drain | yes | acceptable |
| PB14 | I2C4_SDA | open-drain | yes | acceptable |
| PB15 | I2C4_SCL | open-drain | yes | acceptable |
| PD5 / PD6 | USART2 | -- | **no** | n/a |
| PF0 / PF1 | I2C2 | -- | **no** | n/a |
| PE11-14 | SPI4 | -- | **no** | n/a |

**Pins with NO ROM function on LQFP64** -- the safest place for anything an
external buffer drives:

`PA0 PA1 PA2 PA3 PB0 PB1 PB2 PB3 PB8 PB9 PB12 PC0 PC1 PC2 PC3 PC4 PC5 PC6 PC7
PC8 PC9 PC13 PC14 PC15 PD2 PA13 PA14`

---

## 2. Target pinout -- verified against Table 12

AF numbers are from the datasheet, not from memory.

### Encoders -- all eight channels

| Net | Today | **Target** | Timer / AF | ROM function on target pin |
|-----|-------|-----------|------------|---------------------------|
| `ENC1A` | PA8 | **PC6** | TIM8_CH1, **AF3** | **none** |
| `ENC1B` | PA9 | **PC7** | TIM8_CH2, **AF3** | **none** |
| `ENC2A` | PA5 | **PA0** | TIM5_CH1, **AF2** | **none** |
| `ENC2B` | PB3 | **PA1** | TIM5_CH2, **AF2** | **none** |
| `ENC3A` | PA6 | **PB4** | TIM3_CH1, **AF2** | I2C3_SDA (open-drain) |
| `ENC3B` | PA7 | **PB5** | TIM3_CH2, **AF2** | CAN2_RX (input) |
| `ENC4A` | PB6 | PB6 | TIM4_CH1, **AF2** | I2C1_SCL (open-drain) |
| `ENC4B` | PB7 | PB7 | TIM4_CH2, **AF2** | I2C1_SDA (open-drain) |

**No encoder channel lands on a ROM output.** ENC1 and ENC2 land on pins with no
ROM function at all. TIM4 has only PB6/PB7 on LQFP64, so ENC4 has no
alternative -- open-drain is the best available and it is safe.

**Bonus:** TIM5 is a **32-bit** timer, so ENC2 gets a 32-bit position counter
instead of 16-bit. TIM1 and TIM2 are left entirely free.

### Motor, comms, debug

| Net | Today | **Target** | AF | ROM function | Note |
|-----|-------|-----------|----|--------------|------|
| `MTR_STEP` | PA0 | **PA8** | GPIO (TIM1_CH1 avail.) | I2C3_SCL (OD) | **needs a pull-down**, §3.4. TIM1 free for future hardware step generation. |
| `MTR_DIR` | PB14 | **PC4** | GPIO | none | |
| `MTR_ENA` | PB15 | **PC5** | GPIO | none | **needs a pull-down**, §3.4 |
| `USR_LED` | PB12 | PB12 | GPIO | none | |
| `TXD` | PA15 | **PA9** | USART1_TX, **AF7** | USART1_TX | **deliberate** -- this is what lets the ROM bootloader talk on the RS-485 bus |
| `RXD` | PA10 | PA10 | USART1_RX, **AF7** | USART1_RX | already correct |
| `TMS/SWDIO` | PA13 | PA13 | **AF0** | none | |
| `TCLK/SWCLK` | PA14 | PA14 | **AF0** | none | |
| `TDO/SWO` | (dead) | **PB3** | JTDO-SWO, **AF0** | none | freed -- tracing works again |
| `SPARE_1..4` | PA1/PA3/PA4/PB0 | **PA2, PA3, PB0, PB1** | GPIO | none | |
| BOOT0 | NC | **pull-down + jumper** | -- | -- | §3.1 |
| BOOT1 | NC (PB2) | **PB2 pull-down** | -- | none | §3.2 |

### Power and analog -- all of it

| Net | Pins | Note |
|-----|------|------|
| VDD | **4 pins** | 100n each, close |
| VSS | **4 pins** | |
| VDDA/VREF+ | 1 | filtered even with no ADC |
| VSSA/VREF- | 1 | |
| VCAP_1 | 1 | **verify value against DS11581** -- do not carry F411 values across |
| VBAT | 1 | tie to VDD if no RTC battery |
| NRST | 1 | cap + to the debug header |
| PH0/PH1 | 2 | 8 MHz HSE |

---

## 3. Do / do not

1. **BOOT0** -- 10k pull-down + a pad beside 3V3 to force high. No internal
   pull on F4 (AN4488 5.2). Confirmed NC on V1.2 `[S]`: pin 44 carries no net
   label while every connected pin on that edge does.
2. **PB2 / BOOT1** -- 10k pull-down. NC today, so a BOOT0-high attempt would
   land in system memory or SRAM at random.
3. **Do not connect PA6, PB10, PB13, PC11** to anything an external device
   drives. They are ROM push-pull outputs.
4. **`MTR_STEP` and `MTR_ENA` get hard pull-downs.** Both sit downstream of a
   ULN2003 whose input is high-Z. During a bootloader session the ROM releases
   or repurposes those pins, so without a pull-down the drive's enable and step
   lines float. **A floating enable on a lathe is not acceptable** -- the
   resistor is what makes "MCU not running" mean "drive disabled".
5. **No MCU pin to RS-485 DE** -- derived from TXD in hardware (Q1/R6/R7).
   Firmware confirms `EN_Port = NULL`.
6. **PA11/PA12 NC** unless you want the USB DFU tier.

---

## 4. Debuggability and test provisioning

### 4.1 Debug interfaces

* **Keep J3**, the 10-pin Cortex debug header `[S]`. With SWO now real, all six
  useful signals are live: SWDIO, SWCLK, **SWO**, NRST, 3V3, GND.
* **BOOT0 jumper + adjacent 3V3 pad** -- forcing the ROM becomes a jumper, not
  rework. This is the single thing whose absence blocked a hypothesis for weeks.
* **Serial bootloader now works over the existing RS-485 bus**, because TXD/RXD
  are on the ROM's own PA9/PA10. No extra connector needed for recovery.

### 4.2 Test pads -- signals, at the MCU

Pads or oversized vias that take a soldered wire, not probe-tip targets.
Several GNDs distributed among them, not one.

| Pri | Net | Buys you |
|-----|-----|----------|
| 1 | `ENC1A`/`ENC1B` at the MCU (PC6/PC7) | the glitch-width measurement still missing after weeks of VFD-noise work |
| 1 | U5 **inputs** A1/A2 for ENC1 | separates noise on the cable from noise injected after the buffer |
| 1 | `MTR_STEP` at PA8, **upstream of U2** | existing TPs are downstream; keep both and you can measure U2 itself |
| 1 | BOOT0 | see above |
| 2 | `ENC2/3/4` A+B at the MCU | same as ENC1, less often |
| 2 | `TXD`/`RXD` at PA9/PA10, before U1 | separates "MCU never sent" from "bus ate it" -- constant during bootloader bring-up |
| 2 | RS-485 A/B | the other half of that pair |
| 3 | `MTR_DIR`, `MTR_ENA` at the MCU | |
| 3 | 3V3 / 5V rails | supply noise, next to a VFD |
| 3 | NRST | |

**The current board has TP1-TP6 all on motor/spare outputs and none on an
encoder input** -- it instrumented what it generates, not what it receives.
That is the gap to close.

### 4.3 Growth headers -- free pins, chosen for what they can become

After the assignments above, these are unused. Bring them out rather than
leaving them stranded under the part.

| Header | Pins | Becomes |
|--------|------|---------|
| **Analog** | PC0, PC1, PC2, PC3 | ADC1_IN10-13. Spindle current, motor temp, supply monitoring. |
| **SPI** | PA5, PA6, PA7 (+ PA4 or PA15 as NSS) | SPI1 (AF5). Display, SD card, external ADC. *Note PA6 is a ROM output -- anything here must tolerate being driven during a bootloader session.* |
| **I2C / CAN** | PB8, PB9 | I2C1 alt (AF4) **or** CAN1 (AF9). Sensors, or a second bus. Mutually exclusive. |
| **Timer / misc** | PC8, PC9 | TIM3_CH3/CH4, TIM8_CH3/CH4, SDIO_D0/D1. PC9 is also MCO_2 -- a clock output for probing. |
| **Spare GPIO** | PC12, PD2, PC13-15 | PC13-15 have limited drive; low-speed only. |

Also free: **PA15** (was TXD), **PB14/PB15** (were DIR/ENA), **PA4**.

**A spare GPIO direct to a pad, bypassing U2, is worth reserving explicitly as
a scope trigger.** The `SPARE_n` nets do not serve this -- they are ULN2003
outputs. Two such pins let you bracket an interval rather than guess at an edge.

---

## 5. Verified vs not

**Verified from the documents:**

* PC6/PC7/PC8/PC9 exist on LQFP64 (DS p.44 figure) and carry TIM8_CH1/CH2 at
  AF3 (Table 12).
* PA0/PA1 carry TIM5_CH1/CH2 at AF2 and have no ROM function.
* PB4/PB5 = TIM3_CH1/CH2 AF2; PB6/PB7 = TIM4_CH1/CH2 AF2.
* PA9/PA10 = USART1_TX/RX AF7; PB3 = JTDO-SWO AF0.
* The complete ROM pin list, from AN2606 Table 87.
* LQFP64 supply pin counts.

**TIM5 and TIM8 encoder mode -- CONFIRMED on the F413 itself**, from
`RM0430 Rev 9` (the F413/F423 reference manual), established by which chapter
each section sits in rather than by inference:

* **TIM8** -- *"17.3.16 Encoder interface mode"*, inside the chapter
  **"Advanced-control timers (TIM1&TIM8)"**. pp.497-499; the running header on
  p.499 reads exactly that.
* **TIM5** -- *"18.3.12 Encoder interface mode"*, inside the chapter
  **"General-purpose timers (TIM2 to TIM5)"**. pp.558-560, same test.
* **TIM9-TIM14 have NO encoder mode** -- the phrase appears nowhere in their
  chapter. Excluding them from the allocation was correct, not lucky.
* Growth, noted not relied on: **LPTIM1 has its own encoder mode**
  (RM0430 §21.4.14). A fifth quadrature input exists if one is ever wanted,
  with different capabilities from the TIMx interface -- read that section
  before counting on it.

**NOT verified -- do not treat as settled:**

1. **PB4 is NJTRST at reset (AF0), PB3 is JTDO.** Both default to JTAG until
   firmware selects SWD-only. PB3 already lives with this today; PB4 would join
   it. Transient at reset only, but confirm the CubeMX config disables JTAG.
2. **U5's supply rail, 3V3 or 5V** `[S]` -- sets whether 5V tolerance is
   load-bearing on the encoder pins.
3. **Everything marked `[S]`** -- the real board is newer than V1.2.
4. **VCAP_1 capacitor value** for this part.

---

## 6. Open questions, not blockers

* **U2 = ULN2003** `[S]` carries STEP/DIR/ENA and the spares -- open-collector,
  inverting, slow turn-off. Fine for ENA, unmeasured for a step train. No max
  step rate or edge has ever been scoped. First suspect if step rate ceilings
  out. Note the target puts STEP on PA8 = **TIM1_CH1**, so hardware step
  generation via TIM1 is available later without another respin.
* **Differential encoders** -- J6-J9 are DE-9s with spare pins, so connectors do
  not change; only U5 becomes line receivers. Shield bonding already took
  belt-off counts -51,525 -> 0, so this is insurance, not a measured need.
* **Keep** the 120R RS-485 bias (R3/R4/R5). Stiff on purpose -- the driver is
  off between bits, so bias alone holds the line. It is the baud ceiling.
