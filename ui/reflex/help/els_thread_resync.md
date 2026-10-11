Pick Up an Existing Thread
==========================

This wizard syncs the controller to a thread that already exists: a part
that was re-chucked, a thread cut on another machine, or a damaged thread
that you chase. After the sync, every pass follows the existing groove.

Normally the controller anchors the thread phase automatically at the end
of the first cutting pass. That moment is not special. It is only a point
where the machine is sure that the carriage is on the correct side of the
leadscrew backlash. This wizard lets you set the same anchor by hand, at a
point that you have examined against the existing thread. After that,
everything works the same as normal threading.

## Before You Start

1. Set the thread (pitch, direction) exactly as it was cut. The phase
   anchor cannot correct a wrong pitch.
2. **Engage the ELS stop.** The reference belongs to a threading job, so
   a job must be armed first. It must also be a *fresh* job. If a
   reference already exists, disengage and re-engage.
3. Close the half nut. Keep the tool clear of the work in X.

## The Procedure

**1. Jog in the cutting direction only.**
Keep the tool clear in X. Jog the carriage in the cutting direction until
the tool tip is over the threaded section. Then stop. A jog in the cutting
direction loads the backlash on the side that a real pass starts from.
That is the key to the procedure. Do **not** adjust the position by
jogging. An adjustment reverses across the backlash, and that does not
repeat. Later, it can also drive a tool that is down in a groove sideways
into a flank.

**2. Move the tool into the groove without the carriage.**
Turn the **spindle by hand**. Feed the **cross-slide** in until the tool
tip sits in the existing groove. The spindle has no backlash to fall into.
The cross-slide moves at right angles to the synced motion. Thus neither
one disturbs the anchor. Do not move the carriage. The wizard monitors the
Z scale all the time and tells you if the carriage moves.

**3. Confirm.**
The **Confirm — latch here** button becomes active when the carriage has
held its position and the spindle has been still for a moment. Press it.
The controller captures the spindle position and the carriage position
together, in one instant. That pair is the thread anchor for the rest of
the job.

**4. Run an air pass.**
Move the tool slightly clear in X. Run one pass. **Watch the tip track
the existing groove.** The software cannot detect a tool that you
confirmed at a wrong spot that looks correct. The air pass shows that
error. If the tip tracks the groove, feed in and cut. A small alignment
error cleans up over the first cutting passes.

## If It Says the Carriage Moved

After the jog, all of the leadscrew free play is on one side. So the
carriage can only creep further in the cutting direction. Push it back by
hand until it seats against the leadscrew. Then press **Re-seated**.

The re-seat pushes the carriage against a hard mechanical stop that it
was already against. So the Z readout must return to within a couple of
counts of its earlier value. **If it does not, stop.** The wizard refuses,
and it is correct to refuse. If a position readout does not survive a
return to a mechanical stop, the Z scale chain is losing counts. That
same fault would silently corrupt every ELS operation, not only this one.
Do not try again until you find the cause. (The other side of this: a
clean re-seat is a free integrity check on the whole Z chain.)

## Notes

- The groove you pick does not matter. On a single-start thread every
  groove is the same helix, and the controller folds the phase within one
  pitch. Only *where in the groove* the tip sits matters. The air pass
  verifies that.
- You do not need precision beyond what your eye can see. The correction
  resolves far finer than any thread pitch. Your visual seating causes
  most of the error, and a small amount of cutting cleans it up.
- The anchor belongs to the current job. Disengaging the ELS stop discards
  it. To pick up the thread again, re-engage and sync again.
