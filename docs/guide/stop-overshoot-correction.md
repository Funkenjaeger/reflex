# Stop overshoot correction

!!! warning "Experimental, and off by default"
    The correction has been accepted on metal on one machine, the development
    lathe. The measurement that makes a table for any other machine is new and
    has not yet been run on hardware. Check every table at a shoulder, as
    described below, before you trust it.

## What it corrects

When the carriage reaches **Stop Z**, the firmware stops sending steps on time.
The carriage does not stop on time. The servo drive has latency: it keeps
driving the leadscrew for a moment after the last step, so the carriage
overshoots the stop. The faster the feed, the further it overshoots.

On the development lathe that is nothing when hand-cranked, 7 counts (35 µm)
at .040 in/rev and about 340 rpm, and a little over 0.2 mm at the fastest feed
measured. It is the same every time at a given speed, which is what makes it
correctable. When you are turning to a shoulder, all of it goes into the
shoulder.

This is a limitation of the drive, not of the stop. The correction works
around it.

## What it does

With the correction on, the UI watches how fast Z is approaching and asks the
firmware to fire the stop **early**, by the overshoot measured for that speed
plus a small margin. The carriage comes to rest just **short** of Stop Z instead
of just past it. Your Stop Z itself never changes.

The overshoot at each speed comes from **this machine's overshoot table**. A
machine has none until it is measured, and with no table the correction cannot
turn on.

## Where to find it

Tap the **gear** on the advanced ELS bar, under the mode selector, to open
**Advanced ELS Settings**, or open it from **Setup → ELS → Advanced ELS
Settings**. The settings are at the bottom, under **Advanced**:

| Setting | What it does |
|---|---|
| **Stop overshoot correction** | On or off. **Default: off.** It cannot turn on without a table. |
| **Overshoot margin (Z counts)** | Extra counts of early stop on top of the measured overshoot. **Default: 1**, the value it was sized for. Larger values stop further short. 0 aims at the worst overshoot measured, with nothing to spare. |
| **Overshoot table → Measure** | Measures the table, or tells you whether this machine needs one. |

## Measuring the table

The measurement is a set of air passes that you cut to the stop with the ELS
bar, as usual, in **Feed** mode. The correction is held off while it runs, so
every stop shows the whole coast. After each stop the status strip shows the
result and the next step.

- **Detect** is one pass at a medium feed. It answers *yes* (the carriage
  coasted more than 2 counts), *no* (2 counts or less, at that feed), or
  *unknown*. It never reports a zero overshoot with confidence: a slow feed or a
  stop with steps still queued gives *unknown*.
- **Sweep** is 3 passes at each of 4 feeds, about five minutes. Start with the
  slowest feed you use and end with the fastest: the fastest becomes the top of
  the table. The sweep refuses to make a table when the 3 passes at one feed
  coast more than 3 counts apart, because the correction relies on the coast
  repeating.

When the sweep is complete, open **Measure** again and press **Save**. Saving
does not turn the correction on.

The table keeps, for each feed, the **worst** coast of its 3 passes, and
the correction adds the margin to that. Between standstill and the slowest feed
it is a straight line, which over-estimates the coast there: the safe side.

## What the table is bound to

A table is correct only for the setup it was measured with, and it records
that setup:

- the Z scale resolution;
- the leadscrew: **Lead Screw Pitch** and **Lead Screw Pitch Steps**;
- the **Gearbox table**.

Change any of them and the table is **stale**: the correction goes to off with
a notice naming the change, and the table is kept, so putting the setting back
makes it usable again. The check runs all the time, so after a restart the table
applies only if its setup still matches.

The gearbox **position** is not part of it: on the development lathe the
overshoot was measured in all three positions and one table covers them. On
another lathe, measure again after a gearbox change, or check it at a shoulder.

Some changes cannot be seen from the settings: a different servo drive, the
drive's own settings, or a different controller board. Measure the table again
after any of them. The **Maximum Speed** and **Acceleration** settings in
**Setup → Servo** do not enter into it: by the time the overshoot happens, the
controller has already stopped sending steps.

## After each corrected stop

The status strip shows where the carriage landed, in counts and in display
units:

> Stop landed 2 counts (0.010 mm) short.

The two should agree with your DRO. If they do not, check the Z scale settings.

**A corrected stop that lands more than 1 count past Stop Z turns the
correction off**, with a notice. Past means further in the direction the
carriage was traveling. One count is allowed because the trigger and the
settled reading are each a whole count. This catches what no setting reveals:
a drive retuned, a worn drivetrain, a board that behaves differently.

## Checking it on your lathe

Do this at each feed you intend to use, because the overshoot grows with speed.

1. **Air first.** Keep the tool clear of the work. Set a Stop Z, engage, and
   [feed to it](feeding-to-a-shoulder.md) with the correction **on**. Read
   where it landed in the status strip and on the Z DRO. It should settle
   **at Stop Z or a hair short** of it, never past. In real cuts to a shoulder
   on the development lathe, 9 stops out of 9 landed 1 to 4 counts (5 to 20 µm)
   short.
2. **Then cut a real shoulder** and check it.

## Faster than it was measured

The table stops at the fastest feed of its sweep: on the development lathe about
3,500 counts per second of Z travel, roughly 40 in/min on a 5 µm scale. Faster
than that, the correction holds at its largest value and says so once per pass:

> Z rate is above the calibrated range (3529 counts/s). Correction is held at its top value.

The carriage can then overshoot further than the correction allows for, so
check those stops by eye.

## Upgrading from 1.2.2-rc.2 or earlier

Until then the table was the development lathe's, built into the software. A
machine that had the correction **on** keeps that table as its own setting on the
first start, if its Z scale is 200 counts/mm and its leadscrew is 8 TPI at 1600
steps per revolution, and a notice says so once. On any other machine the
correction goes to off with a notice: measure the table.
