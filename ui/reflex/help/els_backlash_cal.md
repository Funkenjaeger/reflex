Backlash Calibration
====================

Measures leadscrew backlash automatically, so you do not have to work it
out by hand. Equally important, it makes the controller **refuse to start
a cut when it cannot confirm the take-up actually happened**.

Run it once when you commission the machine. Run it again if you change
anything in the drivetrain.

## Before You Start

The calibration moves the carriage back and forth. The travel is small
(less than a millimeter), but it is real motion. Thus:

1. **Close the half nut.** The measurement is not possible without it.
   Proof that the half nut is closed is half the purpose of the
   calibration.
2. **Move the tool clear** of the workpiece and the chuck.
3. **Stop the spindle.**

The controller refuses to run if the ELS stop is engaged. During the run,
it pauses spindle sync. Thus only the calibration moves the leadscrew.

## What It Does

Backlash is the play that the leadscrew turns through before the carriage
starts to move. Thus the calibration:

1. Drives the carriage until the Z scale moves. This seats the nut
   against one face.
2. Reverses, and counts the servo steps until the Z scale moves again.
   **That count is the backlash.**
3. Repeats for a total of three reversals.
4. Seats the nut again in the cutting direction. The play is then loaded
   on the same side that a threading pass starts from.

The three measurements must agree. If they do not agree, the result is
**rejected** (see below).

## Why the Take-up Is Larger Than the Measurement

After the result is accepted, the take-up is commanded at the measured
value **plus a margin** (20%, with a small minimum). This is intentional.
Do not make the margin smaller.

The controller cannot see the carriage move until it has moved far enough
to register on the Z scale. Thus every measurement reads slightly high,
and every measurement has some uncertainty. More importantly, a take-up of
exactly the backlash causes *no carriage motion at all*. That result looks
the same as an open half nut. The margin makes the motion observable, and
observable motion is the proof that the drivetrain is actually connected.

## When It Refuses

**"Carriage did not move. Close the half nut."**
The controller drove well past any plausible backlash, and the Z scale
did not respond. Usually the half nut is open. Other possible causes are
a slipping coupling, a disabled servo, or a Z scale that does not read.

**"Measurements do not agree (...). Result not accepted."**
The three reversals did not give consistent numbers. Do not retry until
it passes by chance. Do not increase the tolerance. A machine that does
not repeat is also a machine whose thread phase will not repeat. The same
fault would silently corrupt every other ELS operation. Examine the
drivetrain for a loose leadscrew coupling, a worn or slipping half nut, or
a Z scale mounting problem.

**"ELS stop is engaged. Disengage it to calibrate."**
A threading job is armed. Before you disengage, know what it costs:
**when you engage again, a new job starts. This clears the thread
reference and any phase offset you have entered.** If you are part-way
through a thread, complete it before you calibrate.

## During Cutting

After calibration, the controller confirms the take-up on **every pass**.
If the carriage does not respond, the pass does not start and you see:

> Carriage did not move. Close the half nut.

The calibration exists to prevent this failure. Before calibration
existed, the controller had no way to know about it. It continued and cut
the next pass in the wrong place. If you see this message, examine the
half nut before anything else.

**Clearing a stuck take-up costs you the thread reference.** A take-up
that never reaches its target holds the controller's motion gate shut.
Only disengaging and re-engaging the ELS stop releases it, and
pressing Cut again will not. That re-engage starts a new job, so the
thread reference and any phase offset are lost with it. There is no
other way. When a reference is actually at stake, the warning on screen
says so.

## Sizing the sweep limit

The sweep limit must be larger than your actual backlash, with room to
spare. Every leg of the calibration runs up to it. A leg that reaches it
without seeing the carriage move reports *"Carriage did not move. Close
the half nut."* Thus a limit set close to the real backlash turns normal
drift into a false half nut alarm.

A generous limit costs nothing. A leg stops the moment the scale moves,
so the limit only bounds the failure case. Aim for approximately **2–3×
your measured backlash**.

Measured on this machine (2026-08-08): **~0.76 mm** of real backlash,
against a 0.8 mm dial-indicator measurement. That is several times more
than on a typical well-tuned lathe. Do not size the limit from general
advice. Size it from what the calibration actually reports.

## Notes

- The stored take-up shows in **Backlash take-up (mm)** in Advanced ELS
  Settings. You can still enter a value by hand. Calibration only fills
  it in for you.
- When you run it again, it compares the result with the previous
  measurement. It tells you if the value has changed noticeably.
  Investigate a large change before you accept it.
- Internally, the measurement is in servo steps. The settings field shows
  millimeters.
