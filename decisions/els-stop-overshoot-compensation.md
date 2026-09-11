# Decision doc: ELS stop overshoot — compensate it, and how a user calibrates their own machine

**Status:** **DECIDED 2026-09-03** for the compensation branch (the drive-tuning branch is parked, not
abandoned). **PROPOSED** for the user-facing calibration design in
[Making it usable by someone other than Evan](#making-it-usable-by-someone-other-than-evan) — none of
that is implemented, and the shipped default is and remains zero correction.
**Date:** 2026-09-03.
**Supersedes:** nothing. This is the first decision record on the subject.

---

## The problem

The ELS stop overshoots. When the stop fires, the carriage keeps travelling past the commanded
`stopPosition` by **50–130 µm (2.0–5.1 thou)**, scaling with feed rate.

The firmware is measured out of it. On the trigger, sync pauses and `desiredSteps` stops advancing;
the stop-overshoot probe recorded **0 servo steps emitted after the trigger in 12 of 14 captures**
(1 step in the other two). The carriage moves anyway, so the cause is **downstream of the STEP
pin** — in the drive, its command path, or the mechanics. A hand-cranked spindle at approximately
zero speed produced exactly zero overshoot on both the probe and a dial indicator, which is what
makes it a rate-dependent effect rather than lash.

## What is solid, and what turned out softer than previously claimed

Solid, from 31 SETTLED schema-7 captures:

- total overshoot 50–130 µm, scaling with feed rate;
- **the error is deterministic** — three passes at one speed gave −25, −25, −25 counts, zero
  spread;
- motion decays to zero over 11–12 ms.

Softer than the record claimed, corrected 2026-09-03. The task previously asserted the carriage
holds full feed rate for 4–6 ms and *then* ramps down at ~1.5 m/s², giving
`overshoot = v·Td + v²/2a` with both constants speed-independent. Re-reading the captures against
`els_diag_stop_overshoot.h` does **not** cleanly reproduce that shape: the stacked profile decays
gradually with no sharp knee, and the faster speed group is flat for only about 3 ms. The
functional form is **plausible, not established.**

Two traps were found in reading that data, and they are recorded here because both produce
confident wrong answers:

1. **`end_reason == 2` (END_WINDOW) records are not measurements.** The header is explicit: the
   trace ran out while Z was still moving, so `net_counts` is a **floor, not a result**. Exactly one
   such record exists, and including it produced a spurious 940 µm outlier — 37 thou, far outside
   the real range. Excluding it, the SETTLED set spans exactly the documented 50–130 µm.
2. **A bucket holds about one Z count at these feed rates.** Each bucket is 400 µs, so an individual
   trace is quantised to 0/1/2 and has no resolvable plateau. Per-trace shape detection returns
   whatever the threshold implies — two different thresholds each returned a confident 86–92% ramp
   share, both artifacts. **Traces must be stacked** before any shape claim is made.

## The decision: compensate, do not chase the drive

**Fire the stop early by the predicted overshoot.** The determinism is what licenses this: you do
not need to understand a delay to cancel it, you need it to repeat, and it does — zero spread across
repeats at a fixed speed.

The drive branch is parked. Its state, and the cable diagnosis it is waiting on, live in the
`Open Loops – Watch Items` task *"Talk to the CL86T over RS232 if a working cable ever turns up."*
Summarised: two USB-serial adapters failed, the second a known-good Gearmo FTDI, which took the
adapter off the suspect list and left a hand-terminated DB9 of unverified pinout as the leading
candidate.

**Do not buy a CL86T-V4.1 to resolve this.** Checked 2026-09-03: the V4.1 manual documents no
smoothing, filter, damping, jerk or S-curve parameter either — its single "filter" is an EMI line
filter on the power supply. MotionStudio ships no offline parameter definitions (294 files, none
mentioning CL86 or CL57), so the parameter list comes from the drive at connect time and cannot be
inspected before purchase. Buying to find out is a gamble with no way to de-risk it.

### Threading and turning are affected differently

Evan's observation, 2026-09-03, and it materially changes the priority. Under the transport-delay
reading, during the flat portion the drive is faithfully replaying step commands that were correctly
synced `Td` ago. `dZ/dθ` is therefore unchanged and **thread pitch is preserved** — the tool simply
cuts further than asked. Only the decaying tail is uncommanded motion against a still-turning
spindle, and only that region loses phase.

That degraded region is roughly **1–2 thou of a 2–5 thou overshoot** (±50%, because the flat/ramp
boundary is not sharp in the data). When threading into a runout, that lands in thread being
abandoned anyway.

**Turning to a shoulder is not rescued by this.** There, phase is irrelevant and the *entire*
overshoot is a straight position error into the shoulder. Turning may be the real motivating case.

---

## Making it usable by someone other than Evan

Everything below is **proposed**, not decided.

### The finding that shapes the whole design: no diagnostic build is needed

The diag probe exists to find the **cause** — the bucket trace, the step-emission discriminator. A
*correction* needs only **total overshoot versus speed**, and every input for that is an ordinary
release-build register:

| Register | Access | Provides |
| --- | --- | --- |
| `scales[i].position` | read-only, ISR-updated | absolute Z in encoder counts |
| `scales[i].speed` | read-only | Z speed in counts/s — measured, not inferred |
| `elsStop.stopPosition` | SW write | the commanded target, same units |

So `overshoot = Z position after settling − stopPosition`, and the host already writes
`stopPosition` via `ElsStopHal.set_stop_position()`. **Phase 1 is entirely host-side Python. No
custom firmware build, and plausibly no firmware change at all** — the correction is a pre-biased
`stopPosition` written at pass start.

For the record, the stop-overshoot probe is also purely observational, unlike the take-up probe:
`elsDiagExtraDwell` returns 0 ("this probe does not hold any gate open") and `elsDiagServoGate`
always returns false. It would be a safe candidate for shipping in release if a trace were ever
needed. It is not needed for this.

### A Z scale is a precondition, not a variable

An earlier draft of this design included a manual dial-indicator fallback for users without a Z
scale. That was wrong and is removed: **a user without a Z scale cannot use the ELS stop at all**,
so there is no such user to design for. Every user who can use the stop already owns exactly the
instrument needed to calibrate it — no additional hardware, no setup they have not already done.

### A) Detecting whether a given machine has the problem

One guided air pass at a moderate speed. Read `scaleSpeed` at the trigger, wait ~200 ms (well past
the 11–12 ms settle), read `scaleCurrent`, subtract `stopPosition`. Report the result.

**This check must be able to fail.** A working scale still yields garbage if the stop never fired,
the pass was too short to reach feed rate, or sync dropped mid-pass. Those must report **UNKNOWN,
not "no problem"** — a check structurally unable to observe the fault will hand every user a
confident clean bill. Gate on the scale having registered sane travel during the pass.

### B) Characterising it

Repeat across several speeds spanning the machine's usable range, with repeats at each speed.

**Spread is the gate on whether compensation applies at all.** Evan's machine gave zero spread. If a
user's repeats at one speed disagree beyond a threshold, their overshoot is not deterministic,
feedforward cannot help, and the wizard must **say so and refuse to fit** rather than average
noise into a plausible-looking constant.

### Store a table, not a formula

This is the main recommendation, and the reason is directly upstream in this document: the
`v·Td + v²/2a` model **is not cleanly supported by our own data**. Shipping it would put the weakest
part of the analysis into every user's machine.

A measured lookup table with interpolation assumes no mechanism, survives a drive that behaves
differently, and **refuses to extrapolate**. Clamp to the measured range; outside it, hold the
endpoint value or disable the correction and say why. A fitted curve may still be *shown* for the
user's confidence while the *table* is what gets applied.

### Gating and safety

- **Shipped default is zero correction.** Non-negotiable.
- **Gated behind an advanced menu, with a warning.**
- **Clamp the maximum correction** to a sane absolute value, so a bad calibration cannot fire the
  stop wildly early.
- **Apply a fraction — proposed default ~90% — so residual error is undershoot.** Material left at a
  shoulder is recoverable; material removed is scrap.
- **Bind the calibration to the config it was measured under.** A correction taken at different
  steps/mm or a different scale ratio is silently wrong. Store the relevant config identity and
  invalidate with a re-run prompt when it changes. This is the footgun most likely to catch a second
  user.

### A useful property: the loop closes in encoder counts

Overshoot is measured in counts, the correction is applied in counts, and `stopPosition` is already
in counts. A mis-provisioned `ratioNum`/`ratioDen` — the exact defect currently sitting on X, at
2.5 µm/count, matching neither Z nor its own sticker — would therefore make the **displayed** value
wrong while the physical correction stayed right.

That is the good failure mode: the user sees a nonsense number and investigates, rather than the
machine quietly cutting to a scaled-wrong target. To make it visible, **the wizard should show
overshoot in both counts and display units**, so a ratio problem surfaces as a disagreement rather
than hiding behind a single converted number.

## What this record does not decide

- Whether the correction is applied per-pass at pass start (host-side, using the measured spindle
  rate) or live at the trigger (firmware-side). Phase 1 assumes the former; a machine whose spindle
  speed drifts materially within a pass would need the latter.
- The speed count, repeat count, and spread threshold for the wizard. These need the widened dataset
  first — only two speed groups exist today (n=27 and n=4).
- Whether turning-to-a-shoulder, rather than threading, is the real motivating case. The phase
  analysis above suggests it is, and that would raise the feature's priority.
