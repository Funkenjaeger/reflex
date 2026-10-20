Measure the Stop Overshoot
==========================

This measures how far the carriage coasts after the ELS stop fires. You
cut air passes to the stop with the ELS bar, as usual. The controller
measures each stop. The **Stop overshoot correction** is held off while the
measurement runs, so each stop shows the full coast.

There are two measurements:

- **Detect** tells you if this machine needs the correction. One pass.
- **Sweep** measures the overshoot table. 12 passes, about 5 minutes.

## Before you start

1. **Set Feed mode.** The measurement refuses to start in thread mode.
2. **Turn on the stop** and set a Stop Z with room to approach it.
3. **Move the tool clear** of the workpiece and the chuck. All passes are
   in air.

Press **Detect** or **Sweep**. Both windows close. Cut each pass to the
stop. After each stop a strip on the ELS bar shows the result and the next
step, and stays until the next pass starts. Between passes, press
**Retract**, then **Cut**. Open **Overshoot table → Measure** again to see
the progress, or to stop measuring.

The **Measure** window has a QR code for the step-by-step guide on the
docs site.

## Detect

Cut one pass at a medium feed, at least 3 mm/s of Z travel. The result is
one of:

- **Yes**: the carriage coasted more than 2 counts. Run the sweep.
- **No**: the carriage coasted 2 counts or less, at that feed. At that
  feed and slower, the correction is not needed. Faster feeds coast more.
- **Unknown**: the feed was too slow to tell, or the stop was not usable.

If the coast is close to the limit, the strip asks for a second pass. If
the two passes disagree, the result is unknown.

## Sweep

Cut 3 passes at each of 4 carriage speeds: 12 passes. Use speeds from
your slowest turning feed to faster than your fastest. These are test
speeds, not cutting settings. **The fastest speed is the top of the
table.** Above it, the correction holds its top value and a notice tells
you so. The guide has example settings.

Do the speeds in any order. Each pass is counted at the speed it ran at.
The strip names each speed by its feed and spindle speed, for example
**0.020 @ 340 rpm**, with its count. You can leave a speed and finish it
later at the same setting. A pass at a fifth speed is not counted. During
the sweep, touch the strip to see each speed and its count, when no other
strip is above it.

The sweep **refuses** to make a table if:

- the 3 passes at one speed coast more than 3 counts apart. The coast is
  not repeatable, and the correction needs a repeatable coast. Examine the
  drive and the drivetrain.
- the average speeds of two groups are within 10 %. Use 4 speeds more
  than 10 % apart.

When the sweep is complete, open **Overshoot table → Measure** and press
**Save**. The table then is this machine's. Save does not turn the
correction on. Turn it on, then verify it at a shoulder (see **Stop
overshoot correction**).
