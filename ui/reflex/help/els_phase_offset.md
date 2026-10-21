Thread Phase Offset (Widening a Groove)
=======================================

Cuts a thread groove **wider than the tool that cuts it**. Cut the
groove with the cutter you have. Then enter a small offset to shift the
thread phase, and cut again. The tool re-enters the same groove a
fraction of a millimeter further along and cuts material off its side.
Enter a larger offset and cut again, until the groove has the width you
want. The workpiece is never
re-indexed, and the thread datum is never set again.

The offset is a distance. You enter it the same way as every other
distance on this machine. **The entry is the whole offset from the
original groove.** Apply *sets* the offset. It does not add to the offset
that is already there. The box opens with the current offset, so Apply
without an edit changes nothing. To widen the groove by a further 0.1 mm
when the offset is 0.3 mm, enter 0.4 mm.

**How big an offset to enter is your decision, not the machine's.** It
depends on the width of the cutter in the toolpost, and this controller
does not know that width. For that reason, the screen has no preset
amounts.

Every offset goes in the same direction. You open one side of the groove
and continue until it is wide enough. Thus the offset on screen **is the
widening**. It is not a controller number that stands in
for the widening. It is the distance that the groove has actually grown
past the width of your cutter, and you can measure it on the part. That
is the main number on the screen.

Under it, in smaller text, is the same offset as a fraction of one pitch,
written `0.333 x pitch`. That is not a width. It is the limit gauge.
Offsets alias at one pitch, and this number shows how much of that budget
is used. On a widening job, it should stay small. If the value increases
toward 1.000, the entries are not what you think they are.

## Applying an Offset Does Not Move Anything

Nothing moves on the machine when you press Apply. The offset changes the
correction that the controller calculates the next time the tool
re-enters the thread. Thus you can safely apply it at any point in a job,
mid-pass included. The effect occurs on the next resume.

The usual threading discipline thus still applies. Before you cut metal at
a new offset, move the tool clear in X. Then run an air pass. The Pick up
existing thread wizard asks for the same check, for the same reason.
Software cannot tell you that the phase landed where you pictured it. The
air pass can.

## The Entry Only Goes Forward

There is no minus sign because the work does not have one. Widening goes
in one direction, away from the groove you cut first: enter an offset,
cut, enter a larger offset, cut. You open one side of the groove. You do not work outward
from a centerline. An unsigned distance is exactly the control this job
needs.

The keypad has a sign key, so you can type a minus. The controller
refuses it. That refusal catches a slip. It does not block half of a
feature, and it is important that it catches the slip. A negative offset
does not move the phase backward by the amount you type. The controller
biases phase corrections forward. Thus a negative entry becomes a
*forward* move of one pitch minus your number. That is a real cut, in the
same groove (a whole pitch is one full turn of the same helix). It
removes material from the side you were not opening.

**Clear** sets the offset to zero. This puts the tool back on the groove
the job began on. Clear does not undo the last entry. It is an entry of
zero, and the controller treats it exactly like any other entry.

## What It Refuses, and Why

**At or past one pitch.** An offset of exactly one pitch puts the tool back
where it started, one turn along. One and a half pitches cannot be told
apart from a half. Thus the controller refuses the entry. It does not
wrap the entry silently. A wrapped offset puts the cut in a different
place from where you asked, in metal, before anything looks wrong. Enter
a smaller offset.

**No job engaged.** The offset belongs to a threading job, and the
controller erases it when a job starts. An offset entered outside a job
would be lost at the next engage, with no message. Thus the controller
refuses it at entry.

**Not threading, or no pitch set.** A phase offset shifts where the tool
re-enters the thread. Turning has no thread phase to shift.

**No acknowledgement.** The controller confirms every entry before this
screen shows it as applied. If the confirmation does not arrive, the
offset did *not* go in. The usual cause is that the job disengaged
between the button press and the write. The offset on screen is still
the last one that went in. Make sure the ELS stop is engaged. Then try again.

## Notes

- The controller keeps the offset, and it clears the offset when a job is
  engaged. If you disengage and engage again, the groove is back at its
  original width and position. This is true whatever this screen last
  showed you.
- Widening goes in one direction. Decide which side of the groove you will
  open before the first pass. Where you cut the first groove sets the
  side, and the offset cannot change it afterward.
- On a machine whose carriage travels the other way, an entry opens the
  **opposite flank** from the one you pictured. The groove still becomes
  wider by the amount you entered, but it grows the other way from the
  original cut. A larger entry does not correct that. It widens the groove
  further on the same wrong side. On the first offset of a job, run an
  air pass to confirm the direction.
- Precision beyond a couple of display digits is not necessary here. A
  phase error of that size cleans up in the first cutting passes. This is
  exactly the same as when you pick up an existing thread.
