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
import math
import time
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


# ════════════════════════════════════════════════════════════════════════
# Live phase tracker: is the machine PHYSICALLY in phase right now?
# ════════════════════════════════════════════════════════════════════════

LIVE_FILENAME = "phase_live.jsonl"
LIVE_SAMPLE_SECONDS = 1.0


class PhaseLiveTracker:
    """Samples the physical thread-phase error, ~1 Hz, while a reference holds.

    WHY THIS EXISTS. On 2026-08-24 powered-spindle re-syncs cut visibly out of
    phase while the closed-loop emulator proved the ISR's correction design
    converges (els_resync_powered_test). That acquits the ISR loop as modelled
    and indicts what the model omits -- and the sharpest remaining suspect is
    step loss the bookkeeping cannot see: sync is open-loop on COMMANDED
    steps, so a pulse the drive never registered leaves currentSteps saying
    "in phase" while the carriage is physically behind, forever.

    THE MEASUREMENT SETTLES THAT MECHANISM-AGNOSTICALLY. Each sample computes
    the phase error the way applyPhaseCorrection does (els_phase.h), but from
    the two SCALES -- spindle and Z, the physical truth -- and records the
    commanded-side ledger (desired/current/stepsToGo) beside it. If the
    scale-derived error sits near zero mid-pass, every commanded step
    physically executed and the whole dropped-step family of theories dies.
    If it holds an offset while the commanded backlog reads zero, steps were
    lost between the ledger and the metal.

    COSTS NOTHING ON THE WIRE, with one bounded exception. Scale positions
    ride fastData (refreshed every tick for the DRO); the reference and
    geometry ride the per-tick elsStop snapshot. The sync ratio alone lives in
    a register block nothing snapshots, so it is read ONCE PER REFERENCE --
    two live reads per re-sync, guarded against fabrication and retried until
    a clean pair lands. Never per sample, never per tick.

    NO FORWARD BIAS in the folding, deliberately: els_phase.h:89's bias is a
    JOG POLICY (never unload the lash); this is a MEASUREMENT, and the honest
    distance from phase is the fold to +-pitch/2.
    """

    #: Spindle is scales[0] BY FIRMWARE CONTRACT -- applyPhaseCorrection
    #: hardcodes it (Ramps.c), so this instrument reads the same channel the
    #: code under investigation reads, whatever the UI calls its axes.
    SPINDLE_SCALE = 0

    def __init__(self, board, path=None, now=time.monotonic):
        self._board = board
        self._path = path
        self._now = now
        self._last_sample = None
        self._failures = 0
        self._disabled = False
        # Ratio cache, keyed on the identity of the reference it was read
        # under. A new latch invalidates it: the operator may have changed
        # gearing between jobs, and a ratio from the wrong job silently scales
        # every error in the file.
        self._ratio = None          # (num, den)
        self._ratio_key = None

    @property
    def path(self):
        if self._path is None:
            self._path = diag_dir() / LIVE_FILENAME
        return self._path

    @property
    def disabled(self) -> bool:
        return self._disabled

    def poll(self) -> None:
        """Called on every board tick. NEVER raises (Kivy dispatch above)."""
        if self._disabled:
            return
        try:
            self._poll()
        except Exception as e:
            self._failures += 1
            if self._failures >= MAX_FAILURES:
                self._disabled = True
                log.warning(
                    "phase live tracker disabled after %d failures: %s",
                    self._failures, e)
            else:
                log.debug("phase live sample failed: %s", e, exc_info=True)

    def _poll(self) -> None:
        now = self._now()
        if self._last_sample is not None \
                and (now - self._last_sample) < LIVE_SAMPLE_SECONDS:
            return

        snapshot = self._board.els_stop_values
        fast = self._board.fast_data_values
        if not snapshot or not fast:
            return                              # clean skip, not a failure

        # Threading with a live reference, only. Turning has no phase and an
        # unlatched job has no datum -- a line written there would be a number
        # with no meaning, filed where meaningful numbers live.
        if not snapshot["referenceLatched"] or not snapshot["enable"]:
            return
        pitch = float(snapshot["threadPitchSteps"])
        zcpp = float(snapshot["zCountsPerPitch"])
        if pitch == 0.0 or zcpp == 0.0:
            return

        ratio = self._sync_ratio(snapshot)
        if ratio is None:
            return                              # unreadable this tick; retried
        num, den = ratio

        scales = fast["scaleCurrent"]
        d_sp = int(scales[self.SPINDLE_SCALE]) - int(snapshot["latchedSpindle"])
        d_z = int(scales[int(snapshot["scaleIndex"])]) - int(snapshot["latchedZ"])

        # els_phase.h, line for line -- except the forward bias (see class doc).
        cutting_dir = 1 if num > 0 else -1
        if pitch * zcpp < 0.0:
            cutting_dir = -cutting_dir
        dro_sign = int(snapshot["stopDirection"]) * cutting_dir
        ideal = d_sp * num / den
        actual = d_z * pitch / zcpp
        err = ideal - dro_sign * actual + float(snapshot["phaseOffsetSteps"])
        folded = math.fmod(err, pitch)
        half = abs(pitch) / 2.0
        if folded > half:
            folded -= abs(pitch)
        elif folded < -half:
            folded += abs(pitch)

        # Commanded-side ledger, wrap-safe: the fastData counters are uint16
        # halves of uint32s reassembled by the register layer, and desired
        # minus current must survive the 2^32 seam.
        backlog = ((int(fast["servoDesired"]) - int(fast["servoCurrent"])
                    + 2**31) % 2**32) - 2**31

        self._last_sample = now
        self._append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "phaseErrSteps": folded,
            "phaseErrPitch": folded / abs(pitch),
            "idealAdvance": ideal,
            "actualAdvance": actual,
            "deltaSpindle": d_sp,
            "deltaZ": d_z,
            "syncRatioNum": num,
            "syncRatioDen": den,
            # which reference this sample is measured against
            "latchSeq": snapshot["latchSeq"],
            "latchedSpindle": snapshot["latchedSpindle"],
            "latchedZ": snapshot["latchedZ"],
            "threadPitchSteps": pitch,
            "phaseOffsetSteps": snapshot["phaseOffsetSteps"],
            # machine state: an error while stopped at the shoulder and an
            # error mid-pass are different findings
            "active": snapshot["active"],
            "takeupPending": snapshot["takeupPending"],
            # commanded-side ledger: separates "jog still draining" from
            # "physically lost"
            "stepsToGo": fast["stepsToGo"],
            "servoBacklog": backlog,
            # the pulse-width instrument, correlated per sample: a phase
            # error that appears alongside a shrinking min / rising runt
            # count is the dropped-step signature caught in the act
            "stepPulseMinCycles": snapshot["stepPulseMinCycles"],
            "stepPulseRuntCount": snapshot["stepPulseRuntCount"],
        })

    def _sync_ratio(self, snapshot):
        """The ratio the firmware is using, read once per reference.

        Guarded like every decision-bearing read (c31c725): the two live
        reads fabricate 0 on a failed frame, and a fabricated den of 0 or a
        half-fabricated pair would poison every sample in the file. On any
        doubt: no cache, no sample, retry next second.
        """
        key = (snapshot["latchSeq"], snapshot["latchedSpindle"],
               snapshot["latchedZ"])
        if self._ratio is not None and self._ratio_key == key:
            return self._ratio

        cm = self._board.connection_manager
        baseline = cm.read_failures
        num = int(self._board.device["scales"][self.SPINDLE_SCALE]["syncRatioNum"])
        den = int(self._board.device["scales"][self.SPINDLE_SCALE]["syncRatioDen"])
        if cm.reads_failed_since(baseline) or den == 0 or num == 0:
            return None
        self._ratio = (num, den)
        self._ratio_key = key
        return self._ratio

    def _append(self, record: dict) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
