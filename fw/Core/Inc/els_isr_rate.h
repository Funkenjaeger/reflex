/*
 * THE ISR TICK RATE, DECLARED ONCE, AND THE CONVERSION EVERY TICK CONSTANT
 * SHOULD HAVE BEEN WRITTEN THROUGH.
 *
 * Every ELS timing constant in this firmware is a raw tick count, and a tick
 * count only means something once you know the tick rate. That coupling was
 * implicit until 2026-08-28, and it is the recorded hazard on the take-up gate
 * task: "a CubeMX regen silently HALVES the ISR rate, doubling every tick
 * constant in wall-clock terms. Note the asymmetry -- servoCycles is derived at
 * runtime from the live timer registers so it self-corrects,
 * ELS_SLIP_SETTLE_TICKS is a raw count so it does not."
 *
 * Now they are derived, so the rate and the constants cannot drift apart.
 *
 * ---- WHY THE RATE CHANGED, 100 kHz -> 50 kHz --------------------------------
 * The 100 kHz rate was never derived. It traces to 357837ee (pre-fork upstream)
 * bundled into an unrelated change and justified in the commit message as
 * "speed up interrupt timer to 100K, works fine".
 *
 * What the machine needs, from its own provisioning: maxSpeed = 10000 steps/s,
 * and servoCycles = floor(clock_freq / maxSpeed). The binding constraint is the
 * PULSE SHAPE, not the step rate -- STEP is set at the emission site and
 * cleared at the NEXT tick's entry, so pulse width is exactly one tick and step
 * period is servoCycles ticks. servoCycles must be >= 2 or the pin never goes
 * low and there is no pulse train at all. Hard floor: 2 x maxSpeed = 20 kHz.
 *
 * 50 kHz gives servoCycles = 5 -- a 20 us pulse in a 100 us period -- and
 * supports maxSpeed up to 25000 steps/s before this needs revisiting, which is
 * 2.5x the machine's current provisioning.
 *
 * What it buys, on both axes at once: sustained ISR load ~40% -> ~20%, and the
 * per-tick budget 1000 -> 2000 CPU cycles, so the measured cut-start peak of
 * 888 goes from 89% of budget to 44%. Larger than the STM32G474 respin upgrade
 * under consideration (40% -> 24%), and free.
 *
 * Stop detection was checked, not assumed: at 50 kHz the added stop overshoot
 * is 0.1-0.2 um against a 5 um Z encoder count -- below the resolution of the
 * sensor that detects the stop. See the homelab-wiki journal, 2026-08-28
 * (evening), for the full derivation.
 *
 * ---- THE EMULATOR DELIBERATELY DECLARES A DIFFERENT RATE --------------------
 * READ THIS BEFORE "FIXING" IT. The emulator's ISR thread runs at 10 kHz
 * (emulator/config/lathe.toml, isr_rate_hz), not at the hardware rate. That
 * predates this header by a long way and is not something this change
 * introduced.
 *
 * The consequence is that a tick constant has always meant a DIFFERENT WALL
 * CLOCK DURATION in the emulator than on the machine: ELS_SLIP_SETTLE_TICKS =
 * 700 is 7 ms on hardware and was 70 ms in the emulator. Every emulator test
 * that exercises settle behaviour has been exercising the 10x horizon.
 *
 * So the emulator build pins ELS_ISR_TICK_HZ to the OLD hardware rate
 * (emulator/CMakeLists.txt), which reproduces exactly the tick values those
 * tests were written against. That is preserving existing behaviour, not
 * endorsing it: it keeps this commit a rate change rather than a rate change
 * plus a silent re-tuning of every emulator timing test.
 *
 * Making the emulator faithful -- declaring 10000 here so the constants come
 * out as the same DURATIONS in both places -- is the right end state and is a
 * separate decision with its own test fallout. Do it deliberately, not as a
 * side effect of something else.
 */
#ifndef ELS_ISR_RATE_H
#define ELS_ISR_RATE_H

#include <stdint.h>

/* TIM9's interrupt rate. MUST match the timer configuration in tim.c
 * (MX_TIM9_Init) and reflex.ioc: rate = 100 MHz / (Prescaler+1) / (Period+1).
 * At Prescaler 100-1 and Period 20-1 that is 100e6 / 100 / 20 = 50 kHz.
 *
 * There is a runtime cross-check for this in updateSpeedTask, which computes
 * clock_freq from the LIVE timer registers -- so if a CubeMX regen moves the
 * timer without moving this define, servoCycles is computed from the real rate
 * while these constants are computed from the stale one. That divergence is
 * the thing to look for if timings go strange after a regen. */
#ifndef ELS_ISR_TICK_HZ
#define ELS_ISR_TICK_HZ 50000
#endif

/* Ticks per millisecond. Integer by construction at every rate this firmware
 * will plausibly run (50 kHz -> 50, 100 kHz -> 100, 10 kHz -> 10). */
#define ELS_TICKS_PER_MS  (ELS_ISR_TICK_HZ / 1000)

/* Durations -> ticks. Write timing constants through these, never as a bare
 * count, so the wall-clock intent survives a rate change.
 *
 * ELS_US_TO_TICKS is integer arithmetic on purpose: at 50 kHz one tick is
 * 20 us, so sub-20us durations round DOWN to zero. Every current caller is
 * comfortably above that (the smallest is 400 us), but a new sub-tick constant
 * would silently become 0 -- check the arithmetic when adding one. */
#define ELS_MS_TO_TICKS(ms)  ((int32_t)((ms) * ELS_TICKS_PER_MS))
#define ELS_US_TO_TICKS(us)  ((int32_t)(((us) * ELS_TICKS_PER_MS) / 1000))

/* The per-tick CPU budget these constants share the core with: one tick at the
 * 100 MHz core clock. This is what executionCyclesPeak is measured against --
 * a peak at or above it means the ISR overran its own tick and the Modbus task
 * got nothing, which is what the 2026-08-23 comms loss looked like from the
 * firmware side. Derived rather than written down so it tracks the rate. */
#define ELS_ISR_CYCLE_BUDGET (100000000 / ELS_ISR_TICK_HZ)

#endif /* ELS_ISR_RATE_H */
