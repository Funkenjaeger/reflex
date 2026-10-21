# Measuring the gearbox factor

This is the walkthrough for **Advanced ELS settings → Measure gearbox factor →
Measure**.

A lathe with a gearbox or a lever between the servo and the leadscrew has a
**Gearbox table** in Advanced ELS settings, for example `A=1, B=2, C=1/2`. Each
factor is how far the carriage moves per motor step in that position, relative
to the first position, the reference. See
[A gearbox between servo and leadscrew](../setup/servo-and-leadscrew.md#a-gearbox-between-servo-and-leadscrew).
This measurement gives the machine's factor instead of the gear plate's.

You put the lever in one position and cut three ordinary air passes to the stop
with the ELS bar. The controller compares the Z scale travel with the commanded
motor steps over each whole pass. When the three passes agree, it proposes a
factor for that position.

The reference position is not measured: its factor is 1.

## Before you start

1. Enter the **Gearbox table** with the factors from the gear plate. The passes
   use it, so it must be approximately correct.
2. Put the lever in the position to measure. The measurement does not
   change **Gearbox position**.
3. **Feed mode.** The window asks for Feed mode. The feed and the spindle speed
   do not change the factor; a medium feed keeps each pass short.
4. **Stop + retract**, with the stop on. Set **Stop Z**, then **Start Z** at
   least an inch (25 mm) back from it. Each pass must move the carriage at least
   1000 Z scale counts after its first 0.3 s: that is 5 mm on a scale of 200
   counts per mm.
5. **Tool clear** of the work and the chuck. Every pass is in air.
6. **Engage** and turn on **Sync Enable**.

## The steps

1. Open **Advanced ELS settings** and press **Measure** on the **Measure gearbox
   factor** row. The **Measure gearbox factor** window opens.
2. Press **Position** until it shows the position the lever is in. It offers
   every position in the table except the reference.
3. Press **Start**. Both windows close and the ELS bar is yours again.
4. Cut a pass to the stop. Press **Retract**, then **Cut** for the next pass.
   After each stop, the strip on the ELS bar shows the result. It stays until
   the next pass starts.
5. After the third counted pass, the window opens by itself with the result.

From **Start** until the last retract is done, the controller drives in the
measured position: the take-up, the retract and the sync ratio use its factor
from the table. After that, it drives in the **Gearbox position** again. The
coupling check does not act while the measurement runs.

To see the progress, or to stop, open **Measure** again. The window is
**Measuring gear** B, with **Stop measuring** and **Close**. **Close** keeps
the measurement running.

## The cue strip

The examples are for position B of the table `A=1, B=2, C=1/2`.

| The strip says | What it means |
|---|---|
| **Gear B: 3 passes in air. Lever in B.** | The measurement started. Cut the first pass. |
| **Pass 1/3: r 2.001. Proceed with the next pass.** | The pass is counted, and it read 2.001. Retract, then Cut again. |
| **Pass too short to measure. Proceed with the next pass.** | The pass did not move far enough after the take-up. Move Start Z farther back, then cut the pass again. |
| **Pass not counted. Proceed with the next pass.** | The pass could not be measured, or the half nut opened. Cut the pass again. |
| **Gear B: factor 2. Save or discard.** | The three passes agree. The window opens with the result. |
| **Gear B refused: readings spread 0.85%.** | The passes did not agree. See [Refusals](#refusals). |
| **Gear B refused: factor 9 is out of range.** | See [Refusals](#refusals). |
| **Gear B refused: too close to A.** | See [Refusals](#refusals). |

## The result

The window **Gear B measured** shows the three readings, their spread and the
factor, for example:

> The passes read 2.001, 1.998, 2.003, a spread of 0.25%. Position B has
> factor 2. Save writes it to the gearbox table; the other positions are
> unchanged.

The factor is the mean of the three passes. When it is within 0.3 % of a
simple fraction (a denominator of 12 or less), the factor is that fraction, for
example 2 or 3/2. Otherwise it is a decimal with three places, for example
1.987.

- **Save** writes the factor into the **Gearbox table** for that position only.
  The status bar says **Gearbox factor saved.**
- **Discard** keeps the table as it was.

After **Save**, if the table has more positions after this one, the strip says
**Proceed to the next faster setting.** To measure the next position, move the
lever, open **Measure** again and choose that position.

## Refusals

### When you press Start

The reason shows at the top of the window.

| The window says | Do this |
|---|---|
| **Pass is running. Wait for the stop.** | Let the pass reach the stop, then press **Start**. |
| **An overshoot measurement is running. Stop it first.** | Open **Overshoot table → Measure** and stop it, or finish it. |
| **Set the Z scale and the leadscrew first.** | Complete [setup](../setup/index.md). The measurement needs the Z scale resolution and the leadscrew. |
| **No gearbox table.** | There is nothing to measure. Enter a **Gearbox table** first if the lathe has a gearbox. |

### At the end of the passes

The window **Gearbox measurement** opens with the readings. No factor is
proposed and the table does not change. Press **Close**.

| The window says | Do this |
|---|---|
| The passes read … **(at most 0.60% allowed). The coupling is not repeatable, so no factor was proposed.** | The three passes spread more than 0.6 % of their mean. Examine the drivetrain: a slipping coupling, a half nut that skips, or a lever between positions. Then measure again. |
| The passes read … **A factor must be between 1/8 and 8.** | The measured factor is outside the range the table accepts. Make sure the lever is fully in a position and the half nut is closed. |
| The passes read …, a factor of … **B and A (factor 1) must be at least 1.2x apart. Check the lever is in B.** | The lever is probably in the other position. Move it to the position you chose and measure again. |

### When you press Save

The reason shows at the top of the window and on the status bar.

| The window says | Do this |
|---|---|
| **Gearbox table changed during the measurement. Run it again.** | The table was edited before **Save**. Measure again. |
| **Factor not saved:** … | The new table was refused. The rest of the line says why. |
| **Gearbox table not changed. See the status bar.** | The table could not be applied, for example during a pass. Read the status bar, then press **Save** again. |

## After saving

Check **Gearbox position**: it must
name the position the lever is in. Move the lever back, or set **Gearbox
position** to the measured position.

If the lathe has an overshoot table for the
[stop overshoot correction](stop-overshoot-correction.md), saving makes it stale: the correction goes to off with a notice. Measure the
overshoot again with the new gearbox table.
