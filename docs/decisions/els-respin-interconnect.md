# Respin punch list -- STM32 interconnect

**Sources.** Firmware (`fw/reflex.ioc`, `Ramps.h`, `Ramps.c`) = authoritative.
Upstream schematic V1.2 = `[S]`, **older than the actual board -- verify**.

**Target part:** STM32F413RG, LQFP64.

---

## 1. Target pinout

`ENC1` moves to TIM8 so PA9 frees up. STEP stays on PA0 -- that is why TIM8 and
not TIM5.

| Pin | Signal | From today |
|-----|--------|-----------|
| PC6 | S_TIM8_CH1 `ENC1A` | **NEW** -- was PA8. *Verify pin exists, §5.1* |
| PC7 | S_TIM8_CH2 `ENC1B` | **NEW** -- was PA9. *Verify pin exists, §5.1* |
| PA0 | STEP | keep |
| PA1 | S_TIM2_CH2 `ENCnB` | **CHANGED** -- was SPARE_1. Takes over from PB3. |
| PA5 | S_TIM2_CH1_ETR | keep |
| PA6 | S_TIM3_CH1 | keep |
| PA7 | S_TIM3_CH2 | keep |
| PA8 | free -- new SPARE_1 or debug pad | **CHANGED** -- was `ENC1A` |
| **PA9** | **RESERVED, leave unconnected** | **CHANGED** -- was `ENC1B`. See §3.1 |
| PA10 | USART1_RX | keep |
| PA13 | SWDIO | keep |
| PA14 | SWCLK | keep |
| PA15 | USART1_TX | keep |
| PB2 | BOOT1, pulled down | **NEW** -- unconnected today |
| PB3 | TDO/SWO only | **CHANGED** -- was a scale input |
| PB6 | S_TIM4_CH1 | keep |
| PB7 | S_TIM4_CH2 | keep |
| PB12 | USR_LED | keep |
| PB14 | DIR | keep |
| PB15 | ENA | keep |
| BOOT0 | pull-down + force-high pad | **NEW** -- NC today |
| PH0/PH1 | OSC_IN/OSC_OUT, 8 MHz | keep |

Scales end on TIM8/TIM2/TIM3/TIM4. TIM1 and TIM5 spare.

**`.ioc` warning:** it lists 16 pins, the firmware drives 19. STEP/DIR/ENA are
set in code (`Ramps.h:31-38`), not CubeMX. Do not generate a pinout from it.

---

## 2. Connect

1. **BOOT0** -- 10k pull-down, plus a pad beside a 3V3 pad to force it high. No
   internal pull exists on F4 (AN4488 5.2). *Check §5.2 first.*
2. **PB2 / BOOT1** -- 10k pull-down. Floating today, so a BOOT0-high attempt
   lands in system memory or SRAM at random.
3. **J3 debug header** `[S]` -- keep. RESET, SWDIO, SWCLK, 3V3, GND.
4. **NRST** -- to J3, with its cap. Needed for connect-under-reset.
5. **VCAP** -- per the F413RG datasheet. Do not carry the F411 values across.
6. **VDDA / VSSA** -- filtered, even with no ADC.

---

## 3. Do not connect

1. **Nothing on PA9.** Mask ROM hard-codes USART1_TX there and drives it high at
   startup; an encoder channel on it means two push-pull outputs on one net.
   *Note: unplugging the encoder does NOT isolate PA9 -- U5's output still
   drives it.*
2. **No MCU pin to RS-485 DE.** DE is derived from TXD in hardware (Q1/R6/R7).
   Firmware confirms: `EN_Port = NULL`.
3. **PA11 / PA12** -- NC today. Route only if you want a USB DFU tier.

---

## 4. Debug pads

Pads or oversized vias that take a soldered wire, not probe-tip targets.
Distribute several GNDs among them.

| Priority | Net | Why |
|----------|-----|-----|
| 1 | `ENC1A`/`ENC1B` at the MCU | glitch width still unmeasured after weeks of VFD-noise work |
| 1 | U5 **inputs** for ENC1 | separates noise on the cable from noise after the buffer |
| 1 | STEP/DIR/ENA **at the MCU pin** | existing TPs are downstream of U2; keep both to measure U2 |
| 1 | BOOT0 + adjacent 3V3 | makes forcing it a jumper, not rework |
| 2 | USART1 TX/RX before U1 | separates "MCU didn't send" from "bus ate it" |
| 2 | Other 3 scale pairs at the MCU | same as ENC1, less often |
| 3 | RS-485 A/B | other half of the pair above |
| 3 | Spare GPIO direct to pad | scope trigger. `SPARE_n` won't do -- they're ULN2003 outputs |
| 3 | SWO (PB3) | once §1 frees it |
| 3 | 3V3 / 5V rails | supply noise, next to a VFD |

**Current board has TP1-TP6 all on motor/spare outputs and none on an encoder
input** -- instrumented what it generates, not what it receives.

---

## 5. Verify before layout

1. **PC6/PC7 bonded out on F413RG LQFP64**, and TIM8_CH1/CH2 land there.
   Everything in §1 depends on this.
2. **`R9` 100k net** `[S]` -- if it is a BOOT0 pull-down, §2.1 is already done.
   Could not resolve it from the PDF.
3. **U5 supply, 3V3 or 5V** `[S]` -- sets contention severity and whether 5V
   tolerance is load-bearing.
4. **Which `ENCn` is on which timer** -- net names known, mapping not.
5. **PB2, VBAT, PC13-PC15** -- tied to anything today?

---

## 6. Open questions, not blockers

* **U2 = ULN2003** `[S]` carries STEP/DIR/ENA and all four spares --
  open-collector, inverting, slow turn-off. Fine for ENA, unmeasured for a step
  train. No max step rate or edge has ever been scoped. First suspect if step
  rate ceilings out.
* **Differential encoders** -- connectors need no change; J6-J9 are DE-9s with
  spare pins. Only U5 changes, to line receivers. Shield bonding already took
  belt-off counts -51,525 -> 0, so this is by-construction insurance, not a
  measured need.
* **Keep** the 120R RS-485 bias (R3/R4/R5). Stiff on purpose -- the driver is
  off between bits, so bias alone holds the line. It is the baud ceiling.
