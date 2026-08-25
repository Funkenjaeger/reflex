"""Recorder for the phase-correction result the firmware publishes every pass.

WHY THIS EXISTS. On 2026-08-24 a re-synced cut came out visibly off the existing
thread -- sometimes close, at least once a full half-thread out, with no pattern
Evan could see across repeated trials. The firmware had already computed exactly
the four numbers that explain such a miss, in applyPhaseCorrection:

    lastIdealAdvance   what pure sync would have done since the latch
    lastActualAdvance  what the carriage actually did since the latch
    lastPhaseError     their difference, plus any deliberate offset
    lastCorrection     the pre-cut jog that difference was folded into

They are permanent registers, they have HAL readers, and NOTHING displayed or
stored them. So the machine could only ever report *that* it was off, never *by
what* -- which is the difference between one diagnostic pass and another evening
of trials. This is the same "computed, polled, rendered by nobody" defect that
hid the ISR peak until d10341d; that one cost a bench session too.

WHY A FILE AND NOT A READOUT ON THE BAR. Four floats glimpsed on a 1024x600
touchscreen mid-pass are transcription, not data -- and the ELS advanced bar's
overlay/height architecture is deliberately delicate (see els_advbar.kv), so a
fifth strip is real regression risk on a screen the operator depends on, for
something only needed while one bug is open. A JSONL line per correction is
richer, costs no screen space, and can be read off the machine over SSH.

WHY IT NEEDS NO FIRMWARE CHANGE. Unlike ElsDiagRecorder, this reads nothing
special: the four values live in the elsStop block, which board.py already
snapshots once per tick for every consumer. Recording them therefore adds
exactly zero Modbus traffic -- which matters more than usual here, because the
ISR was measured at 1106 cycles against a 1000-cycle budget on the very session
this was written for, and the link only survives on the slack left over.

THE ONE LIMITATION, stated rather than discovered later: this samples at the
board tick (~30 Hz) and records when the published tuple CHANGES. Two
corrections inside one tick interval would be recorded as one. Corrections
happen at pass starts, so that is not a realistic loss today -- but it is a
sampling recorder, not a firmware trace, and it must not be read as though it
could not miss.
"""

import json
import logging
from datetime import datetime, timezone

from reflex.utils.paths import diag_dir

log = logging.getLogger(__name__)

FILENAME = "phase_correction.jsonl"

# A new correction is one where any of these four changed. Deliberately the
# four OUTPUTS and not, say, takeupSeq: applyPhaseCorrection runs from more than
# one path in Ramps.c (the take-up completion at :868 and again at :1168), so
# keying on any single upstream event would silently miss whichever path did not
# raise it.
IDENTITY_FIELDS = (
    "lastIdealAdvance",
    "lastActualAdvance",
    "lastPhaseError",
    "lastCorrection",
)

# Everything else worth having beside the result. All of it is already in the
# snapshot, so breadth is free -- and the cost of a missing field is another
# bench session, which is not free.
CONTEXT_FIELDS = (
    # what the reference was -- latchSeq is the discriminator between the
    # operator's manual latch (which increments it) and the first-trigger
    # auto-latch (which does not). A reference that changes with no latchSeq
    # change was replaced behind the operator's back.
    "latchedZ", "latchedSpindle", "referenceLatched", "latchSeq",
    "stopPosition",
    # the geometry the correction was computed against
    "threadPitchSteps", "zCountsPerPitch", "phaseOffsetSteps",
    # the take-up that was supposed to seat the lash first
    "backlashSteps", "takeupSeq", "takeupResult",
    "lastTakeupZDelta", "takeupThreshCounts",
    # machine state at the moment of the reading
    "enable", "active", "stopDirection", "scaleIndex",
)

# Repeated failures disable the recorder rather than retrying forever against a
# fault that is not going to clear -- a full disk, a read-only mount. Same rule
# ElsDiagRecorder follows, and for the same reason: a diagnostic that can take
# the machine control surface down is worse than no diagnostic.
MAX_FAILURES = 5


class PhaseCorrectionRecorder:
    """Appends one JSONL line per distinct phase-correction result."""

    def __init__(self, board, path=None):
        self._board = board
        self._path = path
        self._last_identity = None
        self._failures = 0
        self._disabled = False

    @property
    def path(self):
        if self._path is None:
            self._path = diag_dir() / FILENAME
        return self._path

    @property
    def disabled(self) -> bool:
        return self._disabled

    def poll(self) -> None:
        """Called on every board tick. NEVER raises.

        Bound to update_tick like every other poller, so an exception escaping
        here would propagate into Kivy's event dispatch and take the UI down
        with it -- on a machine whose only interface is that UI.
        """
        if self._disabled:
            return
        try:
            self._poll()
        except Exception as e:
            self._failures += 1
            if self._failures >= MAX_FAILURES:
                self._disabled = True
                log.warning(
                    "phase-correction recorder disabled after %d failures: %s",
                    self._failures, e)
            else:
                log.debug("phase-correction record failed: %s", e, exc_info=True)

    def _poll(self) -> None:
        snapshot = self._board.els_stop_values
        if not snapshot:
            # No snapshot this tick: the refresh failed or there is no link.
            # board.py CLEARS the dict in that case rather than leaving a stale
            # one, so this is also the fabricated-read guard -- there is no
            # path here that records a value assembled from zeros.
            return

        identity = tuple(snapshot[f] for f in IDENTITY_FIELDS)
        if identity == self._last_identity:
            return

        first = self._last_identity is None
        self._last_identity = identity

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            # True for the first reading of a connection, which reports whatever
            # the registers already held rather than a correction observed
            # happening. Flagged instead of suppressed: the state at connect is
            # worth having, and silently dropping the first line of every
            # session is the sort of thing that gets rediscovered painfully.
            "at_connect": first,
        }
        for f in IDENTITY_FIELDS + CONTEXT_FIELDS:
            record[f] = snapshot[f]

        self._append(record)

    def _append(self, record: dict) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
