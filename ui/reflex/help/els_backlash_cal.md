Backlash Calibration
====================

Measures leadscrew backlash automatically instead of you working it
out by hand, and — just as importantly — makes the controller **refuse
to start a cut when it cannot confirm the take-up actually happened**.

Run it once when commissioning the machine, and again if you change
anything in the drive train.

## Before You Start

The calibration moves the carriage back and forth. Travel is small
(under a millimetre) but it is real motion, so:

1. **Engage the half-nut.** The measurement is impossible without it —
   and proving the half-nut is engaged is half the point.
2. **Move the tool clear** of the workpiece and the chuck.
3. **Stop the spindle.**

The controller refuses to run if the ELS stop is engaged, and it pauses
spindle sync for the duration, so the leadscrew is moved by the
calibration and nothing else.

## What It Does

Backlash is the play the leadscrew turns through before the carriage
moves at all. So the calibration:

1. Drives the carriage until the Z scale moves, seating the nut
   against one face.
2. Reverses, and counts the servo steps before the Z scale moves
   again. **That count is the backlash.**
3. Repeats for a total of three reversals.
4. Re-seats in the cutting direction, so the machine is left with the
   play loaded on the same side a threading pass starts from.

The three measurements must agree. If they don't, the result is
**rejected** — see below.

## Why the Take-up Is Larger Than the Measurement

Once accepted, the take-up is commanded at the measured value **plus a
margin** (20%, with a small minimum). This is deliberate and it should
not be trimmed down.

The controller cannot see the carriage move until it has moved far
enough to register on the Z scale, so every measurement reads slightly
high, and any measurement carries some uncertainty. More importantly, a
take-up sized exactly at the backlash produces *no carriage motion at
all* — which is indistinguishable from an open half-nut. The margin is
what makes the motion observable, and observable motion is the proof
that the drive train is actually connected.

## When It Refuses

**"Carriage did not move — is the half-nut engaged?"**
The controller drove well past any plausible backlash and the Z scale
never responded. Usually the half-nut is open. It can also mean a
slipping coupling, a disabled servo, or a Z scale that isn't reading.

**"Measurements disagree."**
The three reversals didn't produce consistent numbers. Do not retry
until it happens to pass, and do not raise the tolerance — a machine
that doesn't repeat is a machine whose thread phase won't repeat
either, and the same fault would quietly corrupt every other ELS
operation. Look for a loose leadscrew coupling, a worn or slipping
half-nut, or a Z scale mounting problem.

**"Disengage the ELS stop before calibrating."**
A threading job is armed. Disengage first.

## During Cutting

Once calibrated, the controller confirms the take-up on **every pass**.
If the carriage doesn't respond, the pass does not start and you'll see:

> Carriage not moving — is the half-nut engaged?

This is the failure the calibration exists to prevent. Previously the
controller had no way to know, so it would proceed and cut the next
pass in the wrong place. If you see this message, check the half-nut
before anything else. To clear a stuck take-up, disengage and re-engage
the ELS stop.

## Sizing the sweep limit

The sweep limit must exceed your actual backlash with room to spare. Every leg
of the calibration runs up to it, and a leg that reaches it without seeing the
carriage move reports *"carriage did not move"* — so a limit set close to the
real lash turns normal drift into a false half-nut alarm.

Being generous costs nothing: a leg ends the moment the scale moves, so the
limit only bounds the failure case. Aim for roughly **2–3× your measured
backlash**.

Measured on this machine (2026-08-08): **~0.76 mm** of real lash, against a
0.8 mm dial-indicator measurement. That is several times larger than a typical
well-tuned lathe, so do not size the limit from general advice — size it from
what the calibration actually reports.

## Notes

- The stored take-up appears in **Backlash takeup (mm)** in ELS
  settings. You can still enter a value by hand; calibration just
  fills it in for you.
- Re-running compares against the previous measurement and tells you
  if it has changed noticeably. A large change is worth investigating
  rather than accepting.
- The measurement is in servo steps internally; the settings field
  shows millimetres.
