# Decision doc: ELS stop overshoot — compensate it, and how a user calibrates their own machine

**Status:** **DECIDED 2026-09-03** for the compensation branch (the drive-tuning branch is parked, not
abandoned). **PROPOSED** for the user-facing calibration design in
[Making it usable by someone other than Evan](#making-it-usable-by-someone-other-than-evan) — none of
that is implemented, and the shipped default is and remains zero correction.
**2026-09-18:** the MECHANISM is decided and implemented for reflex rc.4 (protocolVersion 11) --
see [2026-09-18: the mechanism, as built](#2026-09-18-the-mechanism-as-built). It ships OFF; the
per-machine calibration wizard is still proposed, not built.
**Date:** 2026-09-03; amended 2026-09-18.
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
- ~~**Apply a fraction — proposed default ~90% — so residual error is undershoot.**~~ **SUPERSEDED
  2026-09-18 — the sign was backwards.** The goal stands (material left at a shoulder is
  recoverable; material removed is scrap), but the correction fires the stop EARLY, so the carriage
  settles at `target − offset + overshoot`: applying 90% of the coast lands it **10% of the coast
  PAST** the target. Landing short needs `offset > overshoot`. See the 2026-09-18 section.
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

- ~~Whether the correction is applied per-pass at pass start (host-side, using the measured spindle
  rate) or live at the trigger (firmware-side).~~ **Decided 2026-09-18:** neither exactly -- the
  host computes it LIVE from the measured Z rate and writes it through a rate limiter; the firmware
  applies whatever value is in the register at the trigger. See below.
- The speed count, repeat count, and spread threshold for the wizard. These need the widened dataset
  first — only two speed groups exist today (n=27 and n=4).
- Whether turning-to-a-shoulder, rather than threading, is the real motivating case. The phase
  analysis above suggests it is, and that would raise the feature's priority.

---

## 2026-09-18: the mechanism, as built

Decided by Evan and implemented for reflex rc.4 (branch `feat/stop-offset`, protocolVersion 11).

**A separate `stopOffset` register, not a rewritten `stopPosition`.** `elsStop.stopOffset` (int16,
host-written) is a count of encoder counts to fire the stop EARLY. The ISR moves its threshold to
`stopPosition − sign(stopDirection) × clamp(stopOffset, 0, ELS_STOP_OFFSET_MAX)` and measures the
hysteresis re-arm clearance from the same effective threshold, so an offset of 0 is exactly the
protocol-10 stop. `stopPosition` stays the exact, overshoot-ignorant target the operator set. The
reason it is a second register: `stopPosition` is 32 bits, and `Modbus.c` `process_FC16` stores a
multi-register write one 16-bit half at a time, so an ISR that re-reads `stopPosition` every pass
could see a torn target between the two halves. A single 16-bit register is written and read
atomically. Negative values are treated as 0 (never late); `ELS_STOP_OFFSET_MAX` is 200 counts
(1 mm on elspi, ~5× the largest measured coast), so a bad write can move the stop at most 1 mm
early. The trigger snapshot gains `stopTriggerOffset`, the clamped value each trigger used, behind
`stopTriggerSeq`. Both registers went into former alignment pads, so nothing else moved
(`bootCommand` is still register 168).

**UI-live, through a write limiter.** The UI recomputes the offset every board tick from the live Z
rate (`fastData.scaleSpeed` of the stop's reference scale — the same register `stopTriggerZSpeed`
copies, so the table's x-axis and the live input are one quantity). Every register assignment is an
immediate Modbus exchange, and the tick is held at two exchanges (a third per tick is what halved it
to ~16 Hz before 2026-09-07), so a new value is written only when it has moved by ≥ 1 count AND
≥ 250 ms have passed since the last write: at most one extra exchange every ~8 ticks, none at a
steady rate. Turning the correction off, or disarming, writes 0 once. While the stop is latched
(`active`) the offset is held — the ISR cannot fire then, and the carriage retracts at rapid speed in
that state.

**Sizing: the envelope + 1 count.** The table is the per-rate MAXIMUM over same-speed sets from the
protocol-10 sessions of 09-12/13/14 (re-derived 2026-09-18): (0,0) (300,1) (596,3) (1187,9) (1640,12)
(1692,17) (2500,29) (2507,32) (3567,43), Z rate in counts/s, overshoot in counts at 5 µm/count.
`offset = ceil(interpolated envelope) + 1`. Between points it is linear; above 3567 counts/s it
**holds** 43 (+1) and posts one notice per pass, rather than extrapolating a coast curve past the
fastest pass anyone measured.

**The sign.** The carriage settles at `target − offset + overshoot`. It lands short only if
`offset > overshoot`, so the prediction is never scaled below 100% — the earlier "~90% applied"
proposal lands 10% of the coast past the target and is superseded. A unit test pins
`offset(rate) > predicted(rate)` at every table point and across a dense sweep; it was seen to fail
against both a 90% mutation and a 90%-plus-margin mutation.

**Default off, and bound to this machine.** Opt-in on the ELS settings screen under "Advanced", with
the warning *fires the stop early by the measured coast; verify at a shoulder before relying on it*.
The table lives in code for this release (`ui/reflex/fsms/els_overshoot.py`) and is bound to elspi's
current configuration — Z scale 5 µm/count, ServoBar maxSpeed 10000 / acceleration 20000; change
either and it must be re-measured. The per-machine calibration wizard above remains separate, later
work, and nothing here has yet been verified at the lathe.

## 2026-09-19: first bench result, the dithering, and the fix

**Bench (elspi, flight session `20260919T112827Z`).** Air passes at .040 in/rev, ~342 rpm, Stop Z
−6889, approach in −Z. The true Z rate from rpm × feed is ~1158 counts/s (342 × 0.040 × 5080 / 60);
the firmware's trigger snapshot read 1180–1200. Correction **off** (three passes): every stop
settled at −6896, **7 counts past** the target, all three. Correction **on** (three passes, margin
1): settled −6887, −6887, −6888 — **2, 2 and 1 counts short**. The sign and the sizing work.

**The dithering.** The recorder's `stopOffsetWritten` shows the UI wrote stopOffset 15–21 times per
corrected pass, walking 9 → 10 → 11 → 10 → 9 about every 270 ms through the whole steady part of
4–6.5 s, 22–37 mm passes. The live input, the single-tick `fastData.scaleSpeed`, wandered
~1040–1140 counts/s at a steady feed, and every wobble crossed a count boundary of the table; the
offset in effect at the trigger (`stopTriggerOffset` 10, 9, 9) was simply the last write. Every one
of those writes was an extra Modbus exchange, the thing the limiter exists to ration. A second,
quieter defect sat under it: the table's x-axis was the trigger **snapshot** rate, which reads high
against truth (1200 here vs ~1158; median +1.8%, up to +7% against the position stream over the
09-12..14 passes), while the live reading sat at or below truth — so the UI sized from a lower rate
than the table was built on, up to ~1 count of under-correction.

**The fix (Evan, 2026-09-19).**

- *One rate method on both sides.* The table is re-keyed on the **stream** rate — Z position delta
  over time, as the 2026-09-18 analysis already computed it for the same same-speed sets — with the
  overshoot column unchanged: (0,0) (280,1) (583,3) (1185,9) (1600,12) (1679,17) (2430,29) (2469,32)
  (3529,43). The corrector now computes its live rate the same way, from successive
  `fastData.scaleCurrent` positions of the stop's reference scale over a ~0.5 s window (no rate at
  all until the samples span 0.25 s; the window restarts across a > 0.3 s gap, on a scale change, and
  at Cut, so the rapid retract made while held at the shoulder is never averaged into the next
  approach). On the bench passes the position-derived rate's standard deviation is ~110 counts/s
  over a single tick and ~12 over the 0.5 s window. Hold-above-range is kept; the top point is now 3529.
- *Asymmetric hold.* The offset target is the largest offset wanted in the last 1 s: it **rises at
  once** (bigger offset = lands shorter = the safe side) and **falls only** after a full second of
  lower wanted values, and then only to the largest of them, never to a dip. Held-while-active, the
  zero on off/disarm (which also forgets the held value) and the ≥ 1 count / ≥ 250 ms limiter are
  unchanged and remain the outer bound. Time held at the shoulder does not count as time low, so a
  pass starts at its predecessor's offset.
- *Replay.* `ui/tests/fsms/test_els_overshoot_replay.py` replays the three corrected passes (Z
  position, `active` and host time only, 7.7 KB) through the corrector: at most 3 writes in the
  steady part, the offset in effect at the trigger ≥ the table's offset for 1158 counts/s (10), and
  never falling in the last second before the trigger. Against the previous corrector it fails
  (18 steady-part writes on the first pass; a fall 11 → 8 in its last second); with the fix the
  passes make 5, 0 and 0 writes from Cut to the stop, with 10 in effect at every trigger.
- *The on-screen warning is gone.* The one-line label under the toggle (quoted in the section
  above) was clipped and had no precedent in the menu; its caution lives in the setting's help
  topic, *Stop Coast Correction*, like every other setting's.

Not yet re-run at the lathe with the fix.
