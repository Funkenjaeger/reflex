Thread Phase Offset (Multi-Start Threading)
===========================================

Cuts a multi-start thread without touching the workpiece. A 2-start
thread is two identical helices half a pitch apart; a 3-start is three,
a third of a pitch apart. Rather than re-index the part in the chuck
between them — and inherit whatever error that indexing carries — you
cut one start, tell the controller to advance the thread phase by one
pitch divided by the number of starts, and cut the next from the same
datum.

The offset is a distance, entered the same way every other distance on
this machine is, and it accumulates: on a 3-start you enter a third of
the pitch, cut, enter it again, cut. The screen shows the running total
both as a distance and as a fraction of a pitch, because those answer
different questions — the distance is the number you typed and can check,
and the fraction is the one that says *this is start 2 of 3*.

## Applying an Offset Does Not Move Anything

Nothing happens on the machine when you press Apply. The offset changes
the correction the controller works out the next time the tool re-enters
the thread, so it is safe to apply at any point in a job, mid-pass
included. The effect shows up on the next resume.

That also means the ordinary threading discipline still applies. Before
cutting metal on a new start, back the tool clear in X and run an air
pass — the same check the pick-up-existing-thread wizard asks for, for
the same reason. Software cannot tell you the phase landed where you
pictured it; the air pass can.

## Entry Only Advances

There is no minus sign, and that is deliberate rather than a
simplification. A negative offset does not step the phase backwards by
the amount you type. The controller biases phase corrections forward, so
a negative entry becomes a *forward* move of one pitch minus your number
— a real motion, on a real thread, that does not match the label on the
button that produced it.

Going the other way is entering the rest of the pitch instead: on a
3-start, a third forward twice puts you where two thirds back would have.
And because entries accumulate, an offset entered a little wrong
self-corrects — enter a third again and you are on the start you wanted.

**Clear** returns the total to zero, which puts you back on the start the
job began on. Clear is not an undo of the last entry; it is an entry of
zero, and the controller treats it exactly like any other.

## What It Refuses, and Why

**At or past one pitch.** A total of exactly one pitch is the start you
began on, and one and a half pitches cannot be told apart from a half —
so the controller refuses instead of quietly wrapping. A wrapped offset
would hand you a different start than the one you asked for, cut in metal
before anything looked wrong. Clear the total and build it again.

**No job engaged.** The offset belongs to a threading job, and the
controller wipes it the moment a job starts. One entered outside a job
would evaporate at the next engage without a word, so it is refused up
front instead.

**Not threading, or no pitch set.** A phase offset shifts where a thread
starts. Turning has no thread phase to shift.

**No acknowledgement.** Every entry is confirmed by the controller before
this screen calls it applied. If the confirmation does not arrive, the
offset did *not* go in — usually because the job disengaged between the
button press and the write. The total on screen is still the truth; check
the ELS stop is engaged and try again.

## Notes

- The total lives in the controller and is cleared when a job is engaged.
  Disengage and re-engage and you are back on the first start, whatever
  this screen last showed you.
- Which start you cut first does not matter. Every start of an N-start
  thread is a legitimate start; they are the same helix, offset.
- On a machine whose carriage travels the other way, a given entry
  selects the complementary start — two thirds of a pitch where you
  pictured one third. It is not a wrong cut, and entering the fraction
  again walks you onto the start you had in mind.
- Precision beyond a couple of display digits is not required here. Phase
  error of that size cleans up over the first cutting passes, exactly as
  it does when picking up an existing thread.
