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

| The strip says | Do this |
|---|---|
| … **Proceed with the next pass.** | Retract, then Cut again at the same setting. |
| … **Proceed to the next faster setting.** | Retract, choose the next faster feed (or raise the spindle speed once the feed is at its maximum), then Cut. |
| **Speed changed.** … **Repeat it at the same setting.** | The pass ran at a different speed from the others in its group. Go back to the group's setting and cut it again. |
| **Pass** N **not counted.** … | That stop could not be measured. Cut the pass again. |

Open **Measure** at any time to see where the measurement is, or to stop it.

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

Twelve passes: three at each of four settings, from the slowest you use to the
fastest. **The fastest setting becomes the top of the table.** Above it, the
correction holds its top value and says so.

The four settings on the development lathe, as an example:

| Group | Feed | Spindle |
|---|---|---|
| 1 | 0.010 in/rev | ~340 rpm |
| 2 | 0.020 in/rev | ~340 rpm |
| 3 | 0.040 in/rev | ~340 rpm |
| 4 | 0.040 in/rev | ~700 rpm |

The feed table stops at 0.040 in/rev, so the last group raises the spindle speed
instead. That is what "the next faster setting" means.

When the last pass is done, the **Measure** window opens by itself with the worst
coast at each setting:

- **Save** makes it this machine's overshoot table. Saving does not turn the
  correction on: turn it on in **Advanced ELS settings**, then check it at a
  shoulder.
- **Discard** keeps the table you had.

The sweep **refuses** to make a table when the three passes at one setting coast
more than 3 counts apart: the correction needs a coast that repeats, so examine the
drive and the drivetrain first. It also refuses when two settings give the same
speed.

## After saving

With the correction on, a corrected stop that lands short, which is the normal
case, shows nothing on screen. **Measure** shows where the last corrected stop
landed, in counts and in display units. A stop that lands more than 1 count past
Stop Z turns the correction off with a notice; measure again before you turn it
back on.
