Thread Phase Offset (Widening a Groove)
=======================================

Cuts a thread groove **wider than the tool that cuts it**. Cut the
groove with the cutter you have, tell the controller to advance the
thread phase by a small step-over, and cut again — the tool re-enters
the same groove a fraction of a millimeter further along and takes the
side off it. Repeat until the groove is as wide as you want. The
workpiece is never re-indexed and the thread datum is never
re-established.

The offset is a distance, entered the same way every other distance on
this machine is, and it accumulates. **How big a step to take is your
decision, not the machine's** — it depends on the width of the cutter in
the toolpost, which nothing in this controller knows. There are no
preset amounts on the screen for that reason.

Every step-over goes the same way — you open one side of the groove and
keep going until it is wide enough — so the running total on screen **is
the widening**. Not a controller number that stands in for it: the
distance the groove has actually grown past the width of your cutter,
which you can go and measure on the part. That is the headline.

Under it, smaller, is the same total as a share of one pitch, written
`0.333 x pitch`. That is not a width — it is the limit gauge. Offsets
alias at one pitch, and this is what says how much of that budget is
spent. On a widening job it should stay small; a value creeping up toward
1.000 means the entries are not what you think they are.

## Applying an Offset Does Not Move Anything

Nothing happens on the machine when you press Apply. The offset changes
the correction the controller works out the next time the tool re-enters
the thread, so it is safe to apply at any point in a job, mid-pass
included. The effect shows up on the next resume.

That also means the ordinary threading discipline still applies. Before
cutting metal at a new offset, back the tool clear in X and run an air
pass — the same check the pick-up-existing-thread wizard asks for, for
the same reason. Software cannot tell you the phase landed where you
pictured it; the air pass can.

## Entry Only Advances

There is no minus sign because the work does not have one. Widening runs
in a single direction away from the groove you cut first — step over,
cut, step over, cut — and you are opening one side of the groove, not
working outward from a centerline. An unsigned distance is exactly the
control the job calls for.

The keypad does have a sign key, so a minus can be typed, and the
controller refuses it. That refusal is catching a slip, not fencing off
half a feature — and it matters that it catches it. A negative offset does
not step the phase backwards by the amount you type. The controller biases
phase corrections forward, so a negative entry would become a *forward*
move of one pitch minus your number: a real cut, in the same groove (a
whole pitch is one full turn of the same helix), taking material off the
side you were not opening.

**Clear** returns the total to zero, which puts the tool back on the
groove the job began on. Clear is not an undo of the last entry; it is
an entry of zero, and the controller treats it exactly like any other.

## What It Refuses, and Why

**At or past one pitch.** A total of exactly one pitch puts the tool
back where it started, one turn along, and one and a half pitches cannot
be told apart from a half — so the controller refuses instead of quietly
wrapping. A wrapped offset would put the cut somewhere other than where
you asked, in metal, before anything looked wrong. Clear the total and
build it again.

**No job engaged.** The offset belongs to a threading job, and the
controller wipes it the moment a job starts. One entered outside a job
would evaporate at the next engage without a word, so it is refused up
front instead.

**Not threading, or no pitch set.** A phase offset shifts where the tool
re-enters the thread. Turning has no thread phase to shift.

**No acknowledgement.** Every entry is confirmed by the controller before
this screen calls it applied. If the confirmation does not arrive, the
offset did *not* go in — usually because the job disengaged between the
button press and the write. The total on screen is still the truth; check
the ELS stop is engaged and try again.

## Notes

- The total lives in the controller and is cleared when a job is engaged.
  Disengage and re-engage and the groove is back at its original width
  and position, whatever this screen last showed you.
- Widening is one-directional. Decide which side of the groove you are
  opening before the first pass — it is set by where you cut the first
  groove, and it is not something the offset can change afterwards.
- On a machine whose carriage travels the other way, a given entry opens
  up the **opposite flank** from the one you pictured. The groove still
  ends up wider by the amount you entered — it grows the other way from
  the original cut. Entering more does not correct that; it widens
  further on the same wrong side. Confirm the direction with an air pass
  on the first step-over of a job.
- Precision beyond a couple of display digits is not required here. Phase
  error of that size cleans up over the first cutting passes, exactly as
  it does when picking up an existing thread.
