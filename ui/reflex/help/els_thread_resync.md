Pick Up an Existing Thread
==========================

Syncs the controller to a thread that already exists — a part that was
re-chucked, a thread cut on another machine, or a damaged thread being
chased. Once synced, every pass follows the existing groove.

Normally the controller anchors thread phase automatically at the end of
the first cutting pass. That moment is not special in itself — it is just
a point where the machine is certain the carriage sits on the correct
side of the leadscrew backlash. This wizard lets you establish the same
anchor by hand, at a point you have physically verified against the
existing thread. Everything afterwards works exactly as normal threading
does.

## Before You Start

1. Configure the thread (pitch, direction) exactly as it was cut. The
   phase anchor cannot rescue a wrong pitch.
2. **Engage the ELS stop.** The reference belongs to a threading job, so
   a job must be armed first. It also must be a *fresh* job — if a
   reference already exists, disengage and re-engage.
3. Have the half-nut engaged, and the tool clear of the work in X.

## The Procedure

**1. Jog — cutting direction only.**
With the tool clear in X, jog the carriage in the cutting direction until
the tool tip is over the threaded section, and stop. Jogging in the
cutting direction loads the backlash on the side a real pass starts from
— which is the entire trick. Do **not** fine-tune by jogging: that would
mean reversing across the backlash, which does not repeat, and later
would risk driving a tool that is down in a groove sideways into a flank.

**2. Ease the tool into the groove — without the carriage.**
Rotate the **spindle by hand** and feed the **cross-slide** until the
tool tip nests in the existing groove. The spindle has no backlash to
fall into, and the cross-slide is at right angles to what is being
synced, so neither disturbs the anchor. The carriage stays put — the
wizard watches the Z scale the whole time and will say so if it moves.

**3. Confirm.**
The Confirm button arms once the carriage has held position and the
spindle has been still for a moment. Press it and the controller captures
the spindle and carriage position together in one instant. That pair is
the thread's anchor for the rest of the job.

**4. Run an air pass.**
Back the tool slightly clear in X and run one pass. **Watch the tip track
the existing groove.** The software cannot detect a tool that was
confirmed in a plausible-but-wrong spot — the air pass is what catches
it. If it tracks, feed in and cut; small alignment error cleans up over
the first cutting passes.

## If It Says the Carriage Moved

After the jog, the leadscrew's free play is all on one side, so the
carriage can only creep further in the cutting direction. Nudge it back
by hand until it seats against the leadscrew and press **Re-seated**.

That re-seat is a hard mechanical stop the carriage was already sitting
against, so the Z readout must come back to within a couple of counts of
where it was. **If it does not, stop.** The wizard will refuse, and it is
right to: a position readout that does not survive a return to a
mechanical stop means the Z scale chain is losing counts, and that same
fault would silently corrupt every ELS operation, not just this one. Do
not retry until the cause is found. (The flip side: a clean re-seat is a
free integrity check on the whole Z chain.)

## Notes

- Which groove you pick does not matter — on a single-start thread every
  groove is the same helix, and the controller folds phase within one
  pitch. Only *where in the groove* the tip sits matters, and the air
  pass verifies that.
- Precision beyond your eye is not required. The correction resolves far
  finer than any thread pitch; eyeball seating dominates the error, and
  "cutting a bit" cleans it up.
- The anchor lives inside the current job. Disengaging the ELS stop
  discards it; re-engage and re-sync to pick the thread up again.
