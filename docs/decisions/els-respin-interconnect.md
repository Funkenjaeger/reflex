# Respin punch list -- STM32 interconnect

**Sources.** Firmware (`fw/reflex.ioc`, `Ramps.h`, `Ramps.c`) = authoritative.
Upstream schematic V1.2 = `[S]`, **older than the actual board -- verify**.
`[S]` facts here were resolved by matching net labels to pin positions in the
PDF, then cross-checked: `ENC1B` = U5 Y2 = pin 18 -> PA9 agrees with Evan's own
schematic reading.

**Target part:** STM32F413RG, LQFP64.

---

## 1. Target pinout

`ENC1` moves to TIM8 so PA9 frees for the mask ROM. STEP stays on PA0 -- that is
why TIM8 and not TIM5. `ENC2B` moves off PB3 so SWO comes back.

### Encoders -- all eight channels

| Net | Today | Target | Timer |
|-----|-------|--------|-------|
| `ENC1A` | PA8 | **PC6** | TIM8_CH1 *(verify pin, §5.1)* |
| `ENC1B` | PA9 | **PC7** | TIM8_CH2 *(verify pin, §5.1)* |
| `ENC2A` | PA5 | PA5 | TIM2_CH1 |
| `ENC2B` | **PB3** | **PA1** | TIM2_CH2 -- moved to free SWO |
| `ENC3A` | PA6 | PA6 | TIM3_CH1 |
| `ENC3B` | PA7 | PA7 | TIM3_CH2 |
| `ENC4A` | PB6 | PB6 | TIM4_CH1 |
| `ENC4B` | PB7 | PB7 | TIM4_CH2 |

All eight arrive via U5 (74VHC9151FT), 8 of 9 channels used, An -> Yn:
Y1=19 `ENC1A`, Y2=18 `ENC1B`, Y3=17 `ENC2A`, Y4=16 `ENC2B`, Y5=15 `ENC3A`,
Y6=14 `ENC3B`, Y7=13 `ENC4A`, Y8=12 `ENC4B`. Y9 spare.

### Everything else

| Pin | Signal | From today |
|-----|--------|-----------|
| PA0 | `MTR_STEP` | keep |
| PA2 | free | keep (NC today) |
| PA3 | `SPARE_2` | keep |
| PA4 | `SPARE_3` | keep |
| PA8 | **`SPARE_1`** or debug pad | **CHANGED** -- was `ENC1A` |
| **PA9** | **RESERVED, leave unconnected** | **CHANGED** -- was `ENC1B`. §3.1 |
| PA10 | `RXD` USART1_RX | keep -- already the ROM's RX pin |
| PA13 | `TMS/SWDIO` | keep |
| PA14 | `TCLK/SWCLK` | keep |
| PA15 | `TXD` USART1_TX | keep |
| PB0 | `SPARE_4` | keep |
| **PB2** | **BOOT1, pull down** | **NEW** -- NC today |
| **PB3** | **`TDO/SWO` only** | **CHANGED** -- was `ENC2B` |
| PB12 | `USR_LED` | keep |
| PB14 | `MTR_DIR` | keep |
| PB15 | `MTR_ENA` | keep |
| **BOOT0** | **pull-down + force-high pad** | **NEW** -- NC today, confirmed §5.2 |
| PH0/PH1 | OSC_IN/OSC_OUT, 8 MHz | keep |

`SPARE_1` moves PA1 -> PA8. Free on the F411 today and still free after this:
PA2, PB1, PB4, PB5, PB8, PB9, PB10, PB13, PC13-15, VBAT.

**`.ioc` warning:** it lists 16 pins, the firmware drives 19. STEP/DIR/ENA are
set in code (`Ramps.h:31-38`), not CubeMX. Do not generate a pinout from it.

---

## 2. Connect

1. **BOOT0** -- 10k pull-down + a pad beside a 3V3 pad to force it high. No
   internal pull on F4 (AN4488 5.2).
2. **PB2 / BOOT1** -- 10k pull-down. NC today, so a BOOT0-high attempt lands in
   system memory or SRAM at random.
3. **J3 debug header** `[S]` -- keep. RESET, SWDIO, SWCLK, SWO, 3V3, 5V, GND.
4. **NRST** -- to J3, with its cap.
5. **VCAP** -- per the F413RG datasheet. Do not carry the F411 values across.
6. **VDDA / VSSA** -- filtered, even with no ADC.

---

## 3. Do not connect

1. **Nothing on PA9.** Mask ROM hard-codes USART1_TX there and drives it high at
   startup; an encoder channel on it means two push-pull outputs on one net.
   *Unplugging the encoder does NOT isolate PA9 -- U5's output still drives it.*
2. **No MCU pin to RS-485 DE.** Derived from TXD in hardware (Q1/R6/R7).
   Firmware confirms: `EN_Port = NULL`.
3. **PA11 / PA12** -- NC today `[S]`. Route only if you want a USB DFU tier.

---

## 4. Debug pads

Pads or oversized vias that take a soldered wire, not probe-tip targets.
Distribute several GNDs among them.

| Pri | Net | Why |
|-----|-----|-----|
| 1 | `ENC1A`/`ENC1B` at the MCU | glitch width still unmeasured after weeks of VFD-noise work |
| 1 | U5 **inputs** for ENC1 (A1/A2) | separates noise on the cable from noise after the buffer |
| 1 | STEP/DIR/ENA **at the MCU pin** | existing TPs are downstream of U2; keep both to measure U2 |
| 1 | BOOT0 + adjacent 3V3 | makes forcing it a jumper, not rework |
| 2 | `TXD`/`RXD` before U1 | separates "MCU didn't send" from "bus ate it" |
| 2 | `ENC2A/B`, `ENC3A/B`, `ENC4A/B` at the MCU | same as ENC1, less often |
| 3 | RS-485 A/B | other half of the pair above |
| 3 | Spare GPIO direct to pad | scope trigger. `SPARE_n` won't do -- ULN2003 outputs |
| 3 | SWO (PB3) | once §1 frees it |
| 3 | 3V3 / 5V rails | supply noise, next to a VFD |

**Current board has TP1-TP6 all on motor/spare outputs and none on an encoder
input** -- instrumented what it generates, not what it receives.

---

## 5. Verify before layout

1. **PC6/PC7 bonded out on F413RG LQFP64**, and TIM8_CH1/CH2 land there. §1
   depends entirely on this.
2. ~~`R9` net~~ **RESOLVED**: BOOT0 (pin 44) carries **no net label** on V1.2
   while every connected pin on that edge has one, so BOOT0 is genuinely NC and
   R9 is not its pull-down. R9 sits between 3V3 and NRST -- a reset pull-up.
   *Still `[S]`; confirm on the real board.*
3. **U5 supply, 3V3 or 5V** `[S]` -- sets contention severity on PA9 and whether
   5V tolerance is load-bearing.
4. **PB1, PB4, PB5, PB8-PB10, PB13, PC13-15, VBAT, PA2** -- shown NC on V1.2;
   confirm none were claimed on the newer board.

---

## 6. Open questions, not blockers

* **U2 = ULN2003** `[S]` carries STEP/DIR/ENA and all four spares --
  open-collector, inverting, slow turn-off. Fine for ENA, unmeasured for a step
  train. No max step rate or edge has ever been scoped. First suspect if step
  rate ceilings out.
* **Differential encoders** -- connectors need no change; J6-J9 are DE-9s with
  spare pins. Only U5 changes, to line receivers (9 channels today, 8 used).
  Shield bonding already took belt-off counts -51,525 -> 0, so this is
  by-construction insurance, not a measured need.
* **Keep** the 120R RS-485 bias (R3/R4/R5). Stiff on purpose -- the driver is
  off between bits, so bias alone holds the line. It is the baud ceiling.
