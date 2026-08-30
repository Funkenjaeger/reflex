# Commissioning

First setup on a new machine, in the order the later steps depend on the earlier
ones. Every field named here has its own help on the machine — press the **?**
on the settings screen — and those are indexed under
[Reference](../reference/index.md).

!!! danger "Do this with the tool clear"
    The backlash calibration at the end moves the carriage under power. Nothing
    before it does, but do not run it with a tool at the work.

## 1 — Use case and axes

Set the **use case** to lathe; that is what exposes ELS mode at all. Other use
cases silently fall back to DRO.

Then name your axes and point them at scale inputs. The board carries four scale
inputs, and it creates one axis per input — so a machine using three of them
still carries a fourth axis named `?`. That is correct and harmless: the input
exists and may be wired later.

!!! note "An unnamed axis is not offered as a summed contributor"
    An axis that still has its default `?` name is treated as unprovisioned.
    You can assign an input to it — that is how it gets provisioned — but it
    will not be offered as the second contributor to another axis's **Sum**
    transform, because the ELS can only track one scale index and would
    otherwise display a sum the machine is not following.

Set the **ELS axis roles**: which axis is Z (the saddle), which is X (the cross
slide), and which is the spindle.

## 2 — Scales

For each input, set the resolution so the DRO reads true. Verify it against a
dial indicator over a decent travel rather than trusting the label on the scale:
label errors and a wrong ratio look identical on screen and only differ by a
factor you will notice at the part.

## 3 — Servo gearing

The leadscrew pitch, the servo's steps per revolution, and any belt ratio between
them. This is what converts a distance into leadscrew steps, and it is the
geometry the phase-offset feature refuses to work without.

Get this wrong and everything still *looks* right — the DRO reads from the
scales, which are independent — while every commanded move is off by the ratio.
Check it by commanding a known move and measuring.

## 4 — Backlash calibration

The one that moves the machine, and the one that makes the take-up confirmation
meaningful.

**Before you start:**

1. **Engage the half nut.** The measurement is impossible without it — and
   proving the half nut is engaged is half the point.
2. **Move the tool clear** of the workpiece and the chuck.
3. **Stop the spindle.**

The controller refuses to run while the ELS stop is engaged, and pauses spindle
sync for the duration, so the leadscrew is moved by the calibration and nothing
else.

!!! warning "Disengaging to calibrate costs you the thread reference"
    If a threading job is live the calibration refuses, and its remedy —
    disengage, then re-engage afterwards — starts a **new job**. That clears the
    thread reference and any phase offset. Finish the thread first if you still
    need them.

**What it does:** drives the carriage until the Z scale moves (seating the nut),
reverses and measures the play, three times. The three measurements must agree
within a spread limit, or the result is refused.

!!! danger "Do not retry until it happens to pass"
    A wide spread means the measurement is not reproducible, and that *is* the
    finding. Do not raise the tolerance to make it pass: a machine that does not
    repeat is a machine whose thread phase will not repeat either, and the same
    fault would quietly corrupt every other ELS operation. Look for a loose
    leadscrew coupling, a worn or slipping half nut, or a Z scale mounting
    problem.

**Drift between runs.** A completed calibration reports how far it landed from
the last stored one. A small difference is normal — the measurement carries a
detection-distance bias and real quantisation. Only a change larger than the
spread the machine is held to within a single run is called out as worth
investigating.

Re-run it if you change anything in the drive train.

## 5 — Check the take-up

Cut nothing, but engage, close the half nut and press **Cut** once. You should
see the pass start. Then open the half nut and press **Cut** again: it should
refuse with

> Cut aborted — is the half-nut engaged?

If it refuses with the half nut closed, or *starts* with it open, stop and find
out why before threading. That check is the machine's core safety property.
