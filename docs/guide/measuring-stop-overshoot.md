# Measuring the stop overshoot

This is the walkthrough for **Advanced ELS settings → Overshoot table → Measure**.
The Measure window shows a QR code that opens this page, so you can follow it on a
phone at the machine.

The measurement records how far the carriage coasts after the ELS stop fires. The
[stop overshoot correction](stop-overshoot-correction.md) uses the result to fire
the stop early by that amount. You cut ordinary air passes to the stop with the ELS
bar, and the controller measures each stop. While a measurement runs, the
correction is held off, so every stop shows the whole coast. Expect those stops to
land past Stop Z: that is what is being measured.

There are two measurements:

| | What it answers | Passes |
|---|---|---|
| **Detect** | Does this machine coast enough to need the correction? | 1, sometimes 2 |
| **Sweep** | How far it coasts at each speed: the overshoot table | 12, about 5 minutes |

## Before you start

1. **Feed mode.** The measurement will not start in thread mode.
2. **Stop + retract**, with the stop on. Set **Stop Z**, then **Start Z** at least
   an inch (25 mm) back from it, so each pass has room to reach full speed.
3. **Tool clear** of the work and the chuck. Every pass is in air.
4. **Engage** and turn on **Sync Enable**.

Open **Advanced ELS settings**, **Overshoot table**, **Measure**, and press
**Detect** or **Sweep**. Both windows close and the ELS bar is yours again.

## The cue strip

After each stop, a strip on the ELS bar says what happened and what to do next. It
stays until the next pass starts, so there is no need to wait for it or hurry to
read it. Between passes, press **Retract**, then **Cut**.

During a sweep the strip names each speed by the feed and the spindle speed of
its first pass, for example **0.020 @ 340 rpm**: the feed as the ELS bar shows it,
and the rpm rounded.

| The strip says | Do this |
|---|---|
| … **Proceed with the next pass.** | Retract, then Cut again at the same setting. |
| **0.020 @ 340 rpm: 2/3 · 3 speeds left** | The pass is counted at that speed. Cut the next pass at the same setting, or at a different one. |
| **New speed: 0.040 @ 700 rpm, 1/3 · 0.020 @ 340 needs 1 more** | The pass started a new speed, and another speed is not finished. You can finish either first. |
| **0.040 @ 700 rpm: 3/3 · 0.020 @ 340 needs 1 more** | That speed is finished. Go back to the setting the strip names. |
| **Pass not counted: 4 speeds already open** | The pass was at a fifth speed. Use one of the four settings already started. |
| **Pass not counted: 0.020 @ 340 rpm already has 3/3** | That speed is finished. Use a different setting. |
| **Pass not counted.** … | That stop could not be measured. Cut the pass again. |

Open **Measure** at any time to see where the measurement is, or to stop it.
During a sweep, you can also touch the strip to open it, when no other strip is
above it. It shows each speed started, with its feed, spindle speed and count.

## Detect

Cut one pass at a medium feed, at least 3 mm/s (about 7 in/min) of carriage
travel. On the development lathe, 0.040 in/rev at about 340 rpm is enough. The
strip gives the result:

- **Detection: yes, coast** N **counts. Run the sweep.** The machine coasts more
  than 2 counts. Run the sweep to measure its table.
- **Detection: coast** N **at** … The carriage coasted 2 counts or less at that
  feed. At that feed and slower the correction is not needed; faster feeds coast
  more.
- **Detection: unknown.** The feed was too slow to tell, the stop could not be
  measured, or two passes disagreed. Try again at a faster feed.

A coast close to the limit asks for a second pass first.

## Sweep

Twelve passes: three at each of four speeds. The speed is the carriage speed,
which is the feed multiplied by the spindle speed. Do the speeds in any order.
You can leave a speed before it has three passes and finish it later: set the
feed and the spindle speed that the strip or **Measure** names for it.

Use four speeds from your slowest turning feed to faster than your fastest.
**The fastest speed becomes the top of the table.** Above it, the correction
holds its top value and says so, but a faster stop coasts more than that. A
light feed at a high spindle speed can move the carriage faster than a heavy
feed at a low one. So the fastest test speed is faster than any carriage speed
you use to turn to a shoulder. These are test speeds, not cutting settings.

### A lathe with spindle speed control

The four settings on the development lathe:

| Feed | Spindle | Carriage speed |
|---|---|---|
| 0.010 in/rev | ~340 rpm | about 1.4 mm/s |
| 0.020 in/rev | ~340 rpm | about 2.9 mm/s |
| 0.040 in/rev | ~340 rpm | about 5.8 mm/s |
| 0.040 in/rev | ~700 rpm | about 11.9 mm/s |

The feed table stops at 0.040 in/rev, so the fastest speed uses a higher spindle
speed instead.

### A lathe without spindle speed control

On a lathe with belt or gear speeds only, use the feed table at the spindle
speeds you have, over the same range. Keep the four speeds more than 10 % apart:
the sweep counts a pass at a speed within 10 % of one already started as that
speed.

When the last pass is done, the **Measure** window opens by itself with the worst
coast at each speed, labeled with its feed and spindle speed:

- **Save** makes it this machine's overshoot table. Saving does not turn the
  correction on: turn it on in **Advanced ELS settings**, then check it at a
  shoulder.
- **Discard** keeps the table you had.

The sweep **refuses** to make a table when the three passes at one speed coast
more than 3 counts apart: the correction needs a coast that repeats, so examine the
drive and the drivetrain first. It also refuses, which is rare, when the average
speeds of two groups are within 10 %. Use speeds more than 10 % apart.

## After saving

With the correction on, a corrected stop that lands short, which is the normal
case, shows nothing on screen. **Measure** shows where the last corrected stop
landed, in counts and in display units. A stop that lands more than 1 count past
Stop Z turns the correction off with a notice; measure again before you turn it
back on.
