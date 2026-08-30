# Backlash calibration

Step 3 of [setup](index.md), and the one that makes the take-up confirmation
mean anything. Run it once when commissioning, and again after any change to the
drive train.

!!! danger "This moves the carriage under power"
    Travel is small — under a millimetre — but it is real motion under servo
    control. Move the tool clear before starting.

## Before you start

1. **Engage the half nut.** The measurement is impossible without it, and
   proving the half nut is engaged is half the point.
2. **Move the tool clear** of the workpiece and the chuck.
3. **Stop the spindle.**

The controller refuses to run while the ELS stop is engaged, and it pauses
spindle sync for the duration, so the leadscrew is moved by the calibration and
by nothing else.

!!! warning "Disengaging to calibrate costs you the thread reference"
    If a threading job is live the calibration refuses, and the remedy —
    disengage, then re-engage afterwards — **starts a new job**. That clears the
    thread reference and any phase offset with it.

    Finish the thread first if you still need them.

## What it does

1. Drives the carriage until the Z scale moves, seating the nut.
2. Reverses and measures the play before the carriage moves again.
3. Repeats, three times in total.

The three measurements must agree within a spread limit or the result is
refused. What is stored is the raw measured mean — the play plus the detection
distance — kept separate from the take-up the controller actually commands, and
that separation is what lets a later run notice a change.

## If it says the measurements disagree

!!! danger "Do not retry until it happens to pass, and do not widen the limit"
    A wide spread means **the measurement is not reproducible**, and that is the
    finding, not an obstacle in front of it.

    A machine that does not repeat is a machine whose thread phase will not
    repeat either, and the same fault would quietly corrupt every other ELS
    operation — feeding included.

Look for a loose leadscrew coupling, a worn or slipping half nut, or a Z scale
mounting problem.

## Drift between runs

A completed calibration reports how far it landed from the last stored one.

**A small difference is normal.** The measurement carries a detection-distance
bias and real quantisation, so a few steps of change between runs says nothing.
Only a change larger than the spread the machine is held to *within* a single
run is called out as worth investigating — a warning that fired every time would
be one you learned to skip.

## Check the take-up before you trust it

Cut nothing, but run the confirmation both ways:

1. Engage, close the half nut, press **Cut**. The pass should start.
2. Open the half nut and press **Cut** again. It should refuse:

> Cut aborted — is the half-nut engaged?

If it refuses with the half nut closed, or **starts with it open**, stop and
find out why before threading anything. That check is the machine's core safety
property, and a check that cannot fail is not protecting you.

---

Setup complete. Next: [Feeding to a shoulder](../guide/feeding-to-a-shoulder.md)
is the simplest thing to try first.
