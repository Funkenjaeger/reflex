"""Flight recorder: the 30 Hz poll stream, persisted instead of discarded.

WHY THIS EXISTS. Board.update() reads the whole fastData block and the whole
elsStop block thirty times a second, hands them to a dozen pollers, and throws
them away. Every real cutting session on this machine is therefore a dataset
that existed for 33 ms and then did not. The cost is not hypothetical: the
one-probe-at-a-time firmware scratchpad (fw/DIAG.md) exists because the only
way to see what the machine did during a pass has been to decide the question
in advance, compile a probe for it, flash, and go cut. Ramps.h's machineMode
block records that failure being generalised once already. This generalises the
other half -- the data the UI *already has* -- with no firmware change at all.

PHASE 1 IS DELIBERATELY THE CHEAP HALF. A firmware event ring at the tail of
the register map (with the protocolVersion bump that implies) is phase 2, and
it is not worth doing until this proves that recorded passes answer questions
that currently cost a bench session. The falsifier is written down and it is
real: if two weeks of these logs answer none of the standing
measure-at-the-machine asks, the probe system was correctly sized, and the
right move is to delete this and the log with it.

────────────────────────────────────────────────────────────────────────────
WHAT IT RECORDS, AND WHAT IT CANNOT
────────────────────────────────────────────────────────────────────────────

IT SAMPLES AT THE BOARD TICK, ~30 Hz. That is the whole of its resolution and
the first thing to check any question against. The take-up gate confirms
50 ISR ticks (~0.5 ms) after the last pulse, the ELS stop latches and drains in
milliseconds, and the step pulse train runs at 100 kHz -- NONE of that has a
shape here. What this can see is anything that persists across several board
ticks: a pass, a take-up window, a stall, a decoupling. Questions about
microseconds still need a firmware probe, and always will.

IT RECORDS ONLY WHAT THE POLL STREAM ALREADY CARRIES. fastData and elsStop, as
refreshed by Board.update(). It issues NO Modbus traffic of its own -- not one
extra exchange -- which is not a nicety: six of six cuts lost comms on
2026-08-23 because tick-driven pollers were making their own per-field reads,
and the ISR is still measured against a budget it has been over.
Notably absent for that reason: the per-scale ``syncEnable`` flags live in the
``scales[]`` block, which nothing snapshots, so this file cannot say whether
sync was armed on a given scale. ``servoMode`` (fastData) is the available
proxy for "a sync/index feed is commanded at all".

IT IS OPEN-LOOP ON THE COMMANDED SIDE, exactly like the firmware. servoCurrent
and servoDesired are a LEDGER OF PULSES EMITTED, not of motion performed. The
drive closes its own loop on its own encoder and never reports back. The Z
scale (``sc<n>``, an independent glass scale) is the only physical witness in
this file, which is precisely what makes the pairing worth recording.

────────────────────────────────────────────────────────────────────────────
THE FIRST KNOWN CONSUMER, and what it will and will not be able to conclude
────────────────────────────────────────────────────────────────────────────

"Detect the half nut being opened mid-cut and drop to stopped" is to be built
as a DERIVED CHECK over this stream rather than as another live poller. The
signature it is after is Z NOT ADVANCING WHILE SYNC STEPS ARE COMMANDED, and it
has to survive two innocent look-alikes. Both are separable here, and it is
worth being explicit about which field does the separating:

  THE PRE-CUT TAKE-UP deliberately commands steps with little or no Z movement
  at the start of EVERY pass -- turning included since 2026-08-21. It is
  therefore the same signature by construction. The discriminator is
  ``takeupPending``, which the firmware holds set for exactly that window and
  which is a per-sample column here. Corroborating, at pass granularity:
  ``takeupSeq`` (increments once per completed take-up), ``takeupResult``,
  ``lastTakeupZDelta`` (the Z the take-up actually saw) and ``backlashSteps``
  (how far it was commanded to go), all in the context record.

  MEASURED, not assumed (emulator, 403 backlash steps, 2026-09-03): the
  take-up window is 11 samples / 393 ms wide at 30 Hz. So the discriminator
  has real width -- a check does not have to catch a single sample -- but it
  is also only about a third of a second, which is why it is a per-sample
  column and not something inferred from ``takeupSeq`` edges alone.

  A LOADED OR STALLING SPINDLE stops the sync source, so commanded steps stop
  too -- the signature does not arise. ``spindleSpeed`` (the firmware's own
  filtered scaleSpeed[0]) and the raw cumulative ``sc0`` are both per-sample,
  so a check can require the spindle to be TURNING before it accuses anything,
  and can tell a stall from a slow-down.

  A THIRD LOOK-ALIKE, FOUND BY RECORDING RATHER THAN BY REASONING. The first
  emulator capture this recorder ever took (2026-09-03) produced a confident
  false positive that no amount of thinking about half nuts had predicted: THE
  RETRACT-SIDE BACKLASH TRAVERSE. ``on_enter_retracting`` deliberately adds
  ``els_backlash_steps`` to the commanded move, because the nut is parked
  against the cut-side wall and the first steps of a retract only walk it
  across the play window. Measured in that capture: ~105 commanded steps over
  ~163 ms with the Z count completely stationary -- the decoupled signature
  exactly, at about 40% of the pre-cut take-up's duration.

  It is cleanly separable and cheap to exclude: it happens only while the FSM
  is in 'retracting', which is a per-sample column. But it is NOT covered by
  ``takeupPending`` -- that flag belongs to the firmware's pre-cut gate and is
  0 throughout the retract -- so a check written against the two look-alikes
  above and nothing else WILL fire on every retract. This paragraph is the
  reason phase 1 was worth building before the check was.

WHAT IT WILL NOT BE ABLE TO ANSWER, stated here rather than discovered later:

  * THERE IS NO HALF-NUT SENSOR. Nothing in this file observes the half nut.
    Every verdict is an inference from a decoupling, and a decoupling has other
    causes -- a slipping leadscrew coupling, a drive that faulted and stopped
    honouring the pulse train, a Z scale that stopped counting. This stream
    cannot distinguish those from each other. It can only say the ledger and
    the scale disagreed.
  * FINE FEEDS NEED A LONGER WINDOW. Z resolution on elspi is ~5 um per count
    (200 counts/mm). At 0.1 mm/rev and 200 rpm the carriage advances ~2 counts
    per board tick, so "Z is not advancing" is not decidable from two samples;
    it needs a window of ten or more. At threading pitches it is tens of counts
    per tick and the discrimination is easy. A check that does not scale its
    window to the commanded feed will cry wolf at the slow end.
  * A DECOUPLING BRIEFER THAN ~66 ms IS INVISIBLE. Two samples is the floor for
    seeing anything at all. Note what that floor sits next to: the retract
    backlash traverse above is ~163 ms and the pre-cut take-up ~393 ms, so the
    innocent look-alikes are only 2.5x to 6x the detection floor. A check
    cannot separate them by duration alone; it has to use the state columns.
  * ONLY THE PASSES THAT WERE RECORDED. See the arming gate below: a session is
    opened when the machine is doing something. A pass that somehow ran with
    enable == 0, servoMode == 0 and the FSM in 'stopped' is not in the file --
    and the status file is how you find out that is what happened.

────────────────────────────────────────────────────────────────────────────
A RECORDER THAT CAN SILENTLY RECORD NOTHING IS A CHECK THAT CANNOT FAIL
────────────────────────────────────────────────────────────────────────────

"No lines in the log" has at least five causes and only one of them is "the
machine was idle". The others -- the service is not running, the directory is
not writable, the SD card is full, the link is down, the poll stream stalled --
would all produce the same confident silence, and the whole point of a two-week
falsifier is that silence gets INTERPRETED at the end of it.

So the recorder maintains ``flight_status.json``, rewritten every few seconds
whether or not anything is being recorded, and rewritten immediately on any
state change. It is written atomically (temp file + os.replace) so a collector
can never read a half-written one. Read it like this:

  file ABSENT, or ``updated_utc`` older than a minute
      NOT RECORDING, and not even running. The UI process is down, or it never
      reached this constructor. Nothing else in this file means anything.

  ``state`` == "recording"
      A session is open right now. ``samples_written`` climbs.

  ``state`` == "idle_disarmed", ``ticks_seen`` climbing
      Alive, healthy, machine idle. THIS is the state in which "no lines" means
      "nothing happened", and it is the only one.

  ``state`` == "blocked_no_disk" / "blocked_write_error" / "disabled"
      Alive and NOT recording, with ``reason`` saying why. They are distinct
      on purpose and each is reachable:
        * "blocked_no_disk"      -- free space fell under the floor.
        * "blocked_write_error"  -- the log directory is not writable
                                    (REASON_NOT_WRITABLE). A real write fault.
        * "disabled"             -- MAX_FAILURES consecutive tick failures from
                                    ANY cause (REASON_TICK_FAILURES): a board
                                    read that raised, a serialisation bug, a
                                    write. Deliberately NOT called a write
                                    error, because most of the time it is not.
      NOTE ``write_failures`` in the status file counts those SAME all-cause
      tick failures. The key name predates this distinction and is kept
      because ot-state on elspi already publishes it; read it as
      "tick failures". An operator notice
      was posted once when this began (see NOTICE_NOT_RECORDING) because at the
      lathe there is a touchscreen and no terminal, so a log line is a message
      to whoever reads the file next week.

  ``link_up`` false
      Alive, but the board is not answering. Board.update() keeps ticking at
      2 Hz while disconnected, so the status file stays FRESH while link_up
      goes false -- which is exactly how "the controller is unplugged" is told
      apart from "the UI is dead".

Inside a segment the same rule is enforced against the poll stream itself: ticks
on which no elsStop snapshot existed are counted, and a run of them publishes a
``stall`` record carrying the count and duration when the stream comes back. A
session that ends blind says so in its ``session_end``. There is no arrangement
of these files in which a gap in the samples is indistinguishable from a quiet
machine.

────────────────────────────────────────────────────────────────────────────
THE FILE FORMAT, and why it is shaped this way
────────────────────────────────────────────────────────────────────────────

JSONL, one JSON value per line, in rotating segments named
``flight-<UTC timestamp>-<nnn>.jsonl``. Lexicographic name order IS chronological
order.

A line that parses to an OBJECT is a record, tagged by ``kind``. A line that
parses to an ARRAY is a sample, positional, in the order declared by the
``fields`` list of that segment's header. The split is the whole reason this is
affordable: 30 Hz of ``{"servoCurrent": 41231, ...}`` is roughly twice the bytes
of ``[...]``, and bytes are what decide how many days fit in the budget.

Record kinds:

  ``session_start``  first line of EVERY segment, including continuation
                     segments after a rotation (``segment`` > 0). Carries the
                     field list, the FSM state vocabulary, the schema version,
                     the wall clock, and the gate that armed the session.
  ``context``        the slow half of the machine state -- geometry, the stop,
                     the take-up outcome, the thread reference. Emitted at
                     session start and then ONLY when any of it changes, which
                     makes it the seq-edge event stream: a takeupSeq or latchSeq
                     edge is a context line by construction.
  ``fsm``            an ELS domain FSM transition. Redundant with the per-sample
                     ``fsm`` column and kept anyway, because pass boundaries are
                     the thing every analysis starts from and they should be
                     greppable without decoding column positions.
  ``link``           board connectivity changed.
  ``stall``          the poll stream went blind for N ticks and came back.
  ``session_end``    why the session closed, and its totals.

Sample rows carry ``t_ms``, milliseconds since that SESSION's start (not since
the segment's), so a rotation never restarts the clock. ``started_utc`` in each
header pins it to wall time.
"""

import json
import logging
import os
import shutil
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from reflex.utils.notices import NOTICE_WARNING
from reflex.utils.operator_notice import notify_operator
from reflex.utils.paths import flight_dir

log = logging.getLogger(__name__)

#: Bumped when the meaning of a field changes or a field is removed. APPENDING
#: a field to FAST_FIELDS does not need a bump -- readers key on the header's
#: own ``fields`` list, never on a position they assumed.
SCHEMA = 1

STATUS_FILENAME = "flight_status.json"
SEGMENT_PREFIX = "flight-"
SEGMENT_SUFFIX = ".jsonl"
SEGMENT_GLOB = SEGMENT_PREFIX + "*" + SEGMENT_SUFFIX

#: Decimation. 1 = every board tick (~30 Hz), the native rate and the default.
#: Exposed as a constant so the rate can be turned down on a machine that is
#: short of card without anyone having to redesign the format -- the header
#: publishes it, so a reader never has to assume the sample interval.
SAMPLE_EVERY_N_TICKS = 1

#: Samples held before a session opens and flushed into it when one does. The
#: pre-cut take-up begins on the same tick the FSM enters 'cutting', so without
#: a pre-roll the most interesting two seconds of every pass are the ones the
#: gate was still deciding about.
PREROLL_SAMPLES = 60

#: Keep recording this long after the machine goes quiet, so the END of a pass
#: -- the stop latching, the carriage settling, whatever drains afterwards -- is
#: inside the session rather than immediately outside it.
HOLD_SECONDS = 5.0

STATUS_INTERVAL_SECONDS = 5.0

#: Consecutive blind ticks (no elsStop snapshot) before the gap is worth a
#: record of its own. ~0.5 s at 30 Hz: shorter than that is a dropped frame,
#: longer is a stream that stopped.
STALL_TICKS = 15

# ── The disk bound. elspi is a Pi with one SD card in a machine shop, so an
# unbounded log is a failure mode, not a feature. Three independent limits,
# because they fail in different directions:
#
#   SEGMENT_MAX_BYTES  keeps any single file small enough to scp and to open.
#   MAX_TOTAL_BYTES    the HARD cap on everything this recorder owns. Enforced
#                      by deleting the oldest segments after every rotation and
#                      at startup, so a crashed previous run cannot leave the
#                      budget already spent.
#   MIN_FREE_BYTES     the card is shared with the OS and the app's own logs.
#                      Below this floor the recorder STOPS rather than taking
#                      the last of the space -- a lathe that will not boot is a
#                      worse outcome than a missing dataset, and this is the
#                      one failure the machine cannot recover from by itself.
#
# CHOSEN FROM A MEASUREMENT, not from counting characters in the field list.
# previews/preview_flight_recorder_budget.py drives the real recorder with
# real counter magnitudes (servoCurrent is a uint32 that reaches ten digits;
# a rate measured at zero understates every row) and reports:
#
#     84 bytes per sample, 8.6 MB per ARMED hour at 30 Hz  (2026-09-03)
#
# so 512 MB retains ~59 armed hours and 4 MB is ~28 armed minutes per file.
# ARMED hours, not wall-clock: the gate writes nothing while the machine sits
# engaged and idle, so a shop day costs only the passes in it. That is sized
# for the two-week falsifier this feature is on trial for -- the whole test is
# "do a fortnight of these logs answer anything", and a budget that quietly
# pruned week one would fail it by construction rather than on the merits.
# Re-run the preview after adding or removing a per-sample column.
#
# 512 MB is also under 5% of the smallest card this machine plausibly runs, but
# that is a guess about hardware this code cannot see -- so REFLEX_FLIGHT_MAX_MB
# exists to change it on the machine without a code change and a reflash.
SEGMENT_MAX_BYTES = 4 * 1024 * 1024
MIN_FREE_BYTES = 128 * 1024 * 1024


def _max_total_bytes() -> int:
    """The retention budget, overridable per machine by REFLEX_FLIGHT_MAX_MB.

    Read at import. A value that will not parse, or one below a single segment,
    is REFUSED back to the default with a log line rather than silently
    honoured: a budget of 0 would make every session prune itself immediately
    and produce exactly the confident empty directory this whole module is
    built to make impossible.
    """
    raw = os.environ.get("REFLEX_FLIGHT_MAX_MB")
    if not raw or not raw.strip():
        return 512 * 1024 * 1024
    try:
        value = int(float(raw.strip()) * 1024 * 1024)
    except ValueError:
        log.warning("REFLEX_FLIGHT_MAX_MB=%r is not a number; using the default",
                    raw)
        return 512 * 1024 * 1024
    if value < SEGMENT_MAX_BYTES:
        log.warning("REFLEX_FLIGHT_MAX_MB=%r is below one segment (%d bytes); "
                    "using the default", raw, SEGMENT_MAX_BYTES)
        return 512 * 1024 * 1024
    return value


MAX_TOTAL_BYTES = _max_total_bytes()

#: Repeated write failures disable the recorder rather than retrying forever
#: against a fault that is not going to clear. Same rule ElsDiagRecorder and the
#: phase recorders follow: a diagnostic that can take the machine control
#: surface down is worse than no diagnostic.
MAX_FAILURES = 5

#: What the operator sees when this stops being able to record. Constants so the
#: tests assert on the strings that ship rather than on a paraphrase of them.
#: Deliberately short: it renders in the same top status strip the take-up
#: refusals do, and text on top of the ELS chips is unreadable text.
NOTICE_NOT_RECORDING = "Flight recorder is not recording"
REASON_NO_DISK = "no space left"
REASON_NOT_WRITABLE = "log folder not writable"
# NOT a write error, despite where it is raised from: poll() catches ANY
# exception out of _poll() -- board reads, serialisation, disk writes alike
# -- and trips this after MAX_FAILURES of them. Named for what it actually
# counts (2026-09-04); it used to be REASON_WRITE_ERRORS, which is why the
# status word for it must stay the generic "disabled" and not
# "blocked_write_error".
REASON_TICK_FAILURES = "repeated tick failures"

# ── Per-sample columns. THE FAST HALF: everything that moves at 30 Hz, and
# nothing that does not. Positional in the row, declared by name in every
# segment header.
#
# (fast_data_values key, row name) for the plain ones; the derived ones are
# assembled in _sample(). Keep this tuple and _sample() in step -- the
# field-order test asserts they agree.
FAST_FIELDS = (
    "t_ms",             # ms since session start
    "fsm",              # index into ElsFsm.STATES, or -1 when unknown
    "servoMode",        # 0 = feed off. The available proxy for "sync commanded"
    "enable",           # elsStop.enable: a job is armed
    "active",           # elsStop.active: the stop is latched / holding
    "takeupPending",    # THE take-up discriminator -- see the class doc
    "servoCurrent",     # pulses emitted (ledger, not motion)
    "servoDesired",     # pulses wanted
    "stepsToGo",        # commanded backlog
    "servoSpeed",       # steps/s, firmware-filtered
    "sc0", "sc1", "sc2", "sc3",   # raw scale counters; scaleIndex says which is Z
    "spindleSpeed",     # scaleSpeed[0], firmware-filtered
)

# ── THE SLOW HALF. Changes at pass and job boundaries, so it rides a context
# record emitted on change rather than 30 columns nobody needed per tick.
# takeupSeq / latchSeq / diagSeq edges land here by construction, which is the
# "seq-edge events" half of what phase 1 was scoped to produce.
CONTEXT_FIELDS = (
    # which counter is Z, which way the pass runs, and where it stops
    "scaleIndex", "stopDirection", "stopPosition", "hysteresis",
    # the take-up: what was commanded, what happened, what Z saw
    "backlashSteps", "takeupSeq", "takeupResult", "lastTakeupZDelta",
    "takeupThreshCounts",
    # the thread reference and the geometry every correction is computed against
    "referenceLatched", "latchSeq", "latchedZ", "latchedSpindle",
    "threadPitchSteps", "zCountsPerPitch", "phaseOffsetSteps",
    # the firmware's own published verdicts on the last pass
    "lastIdealAdvance", "lastActualAdvance", "lastPhaseError", "lastCorrection",
    # what the firmware thinks it is doing, and what the register map is
    "machineMode", "protocolVersion",
    # the pulse-width instrument: a decoupling that coincides with runts is a
    # different finding from one that does not
    "stepPulseMinCycles", "stepPulseRuntCount",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_free_bytes(path: Path) -> int:
    return shutil.disk_usage(str(path)).free


class _SegmentWriter:
    """Append-only JSONL segments under a hard total-bytes budget.

    Rotation is by REOPEN, not by rename: a new segment gets a new name and the
    oldest are deleted. Renaming (logging.handlers' scheme) would work on the
    Pi and break on any host that keeps the handle open, and a recorder whose
    rotation is platform-dependent is one whose tests do not prove anything on
    the machine it runs on.

    The budget is enforced AFTER each rotation and at construction, and it never
    deletes the segment currently open -- so the true ceiling is
    ``max_total_bytes + segment_max_bytes`` in the worst instant, and the file
    on disk is what is bounded, not the intention.
    """

    def __init__(self, directory: Path, session_id: str, header_factory,
                 segment_max_bytes=SEGMENT_MAX_BYTES,
                 max_total_bytes=MAX_TOTAL_BYTES):
        self._dir = Path(directory)
        self._session_id = session_id
        self._header_factory = header_factory
        self._segment_max = int(segment_max_bytes)
        self._max_total = int(max_total_bytes)
        self._fh = None
        self._path = None
        self._segment = -1
        self._bytes = 0
        self.pruned = 0

    @property
    def path(self):
        return self._path

    @property
    def segment(self) -> int:
        return self._segment

    def open(self) -> None:
        self._roll()

    def _roll(self) -> None:
        self.close_file()
        self._segment += 1
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / (
            f"{SEGMENT_PREFIX}{self._session_id}-{self._segment:03d}{SEGMENT_SUFFIX}")
        # Line buffered. One write syscall per record and no fsync: 30 syscalls
        # a second is nothing, and it means a SIGKILL loses no completed line
        # rather than losing whatever was still in a Python buffer. fsync is
        # deliberately not used at all -- an SD card that is asked to barrier
        # 30 times a second is an SD card with a shorter life than the lathe.
        self._fh = open(self._path, "a", encoding="utf-8", buffering=1)
        self._bytes = self._path.stat().st_size
        header = self._header_factory(self._segment)
        if header is not None:
            self.write(header, rotate=False)

    def write(self, obj, rotate=True) -> None:
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        self._fh.write(line)
        self._bytes += len(line)
        if rotate and self._bytes >= self._segment_max:
            self._roll()
            self.prune()

    def close_file(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

    def close(self, footer=None) -> None:
        if self._fh is not None and footer is not None:
            self.write(footer, rotate=False)
        self.close_file()
        self.prune()

    def prune(self) -> int:
        """Delete oldest segments until the directory is inside its budget.

        Returns how many were deleted this call. NEVER deletes the open file --
        which is also why the budget has to be read as a bound on what is kept,
        not as a promise about the instant of a rotation.
        """
        try:
            segments = sorted(self._dir.glob(SEGMENT_GLOB))
        except OSError:
            return 0
        sizes = {}
        total = 0
        for p in segments:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            sizes[p] = size
            total += size

        removed = 0
        for p in segments:
            if total <= self._max_total:
                break
            if self._path is not None and p == self._path:
                continue
            try:
                p.unlink()
            except OSError:
                continue
            total -= sizes.get(p, 0)
            removed += 1
        if removed:
            self.pruned += removed
            log.info("flight recorder pruned %d old segment(s) to stay under "
                     "%d bytes", removed, self._max_total)
        return removed


class FlightRecorder:
    """Persists the board poll stream while the machine is doing something.

    Bound to ``update_tick`` like every other poller, so :meth:`poll` NEVER
    raises: an exception escaping here propagates into Kivy's event dispatch and
    takes the UI down with it, on a machine whose only interface is that UI.
    """

    def __init__(self, board, fsm_state=None, fsm_states=None, directory=None,
                 now=time.monotonic, free_bytes=None,
                 segment_max_bytes=SEGMENT_MAX_BYTES,
                 max_total_bytes=MAX_TOTAL_BYTES,
                 min_free_bytes=MIN_FREE_BYTES,
                 sample_every_n_ticks=SAMPLE_EVERY_N_TICKS):
        self._board = board
        # A CALLABLE, not a reference to the FSM. The recorder is built beside
        # the FSM and must not care whether it exists yet, and a lambda over
        # `controller.els_fsm.state` also keeps a rebuilt FSM from stranding a
        # stale object here.
        self._fsm_state = fsm_state
        self._states = list(fsm_states) if fsm_states else []
        self._dir = Path(directory) if directory is not None else None
        self._now = now
        self._free_bytes = free_bytes or _default_free_bytes
        self._segment_max = int(segment_max_bytes)
        self._max_total = int(max_total_bytes)
        self._min_free = int(min_free_bytes)
        self._every = max(1, int(sample_every_n_ticks))

        self._writer = None
        self._preroll = deque(maxlen=PREROLL_SAMPLES)
        self._session_id = None
        self._session_start = None
        self._session_started_utc = None
        self._last_armed = None
        self._last_context = None
        self._last_fsm = None
        self._link_up = None
        self._blind_ticks = 0
        self._blind_since = None
        self._tick = 0
        self._need_sample_status = False

        self._failures = 0
        self._disabled = False
        self._disabled_reason = ""
        self._disabled_reason_short = ""   # which _fail path; picks the state word
        self._blocked_reason = ""
        self._notified_reason = None
        self._last_status = None

        # Counters the status file publishes. `ticks_seen` is the one that makes
        # "alive but idle" a positive statement rather than an absence.
        self.ticks_seen = 0
        self.samples_written = 0
        self.sessions = 0
        self.blind_ticks_total = 0
        self._last_sample_utc = None
        self._last_armed_utc = None

        self._startup_check()

    # ─────────────────────────── public surface ───────────────────────────

    @property
    def directory(self) -> Path:
        if self._dir is None:
            self._dir = flight_dir()
        return self._dir

    @property
    def status_path(self) -> Path:
        return self.directory / STATUS_FILENAME

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def disabled_reason(self) -> str:
        return self._disabled_reason

    @property
    def recording(self) -> bool:
        return self._writer is not None

    def poll(self) -> None:
        """Called on every board tick. NEVER raises -- see the class doc."""
        if self._disabled:
            return
        try:
            self._poll()
        except Exception as e:
            self._failures += 1
            if self._failures >= MAX_FAILURES:
                self._fail(REASON_TICK_FAILURES, f"{e}")
            else:
                log.debug("flight recorder tick failed: %s", e, exc_info=True)

    def close(self, reason="shutdown") -> None:
        """End any open session cleanly. Safe to call twice, never raises."""
        try:
            if self._writer is not None:
                self._end_session(reason)
            self._write_status()
        except Exception as e:
            log.debug("flight recorder close failed: %s", e, exc_info=True)

    # ───────────────────────────── internals ──────────────────────────────

    def _startup_check(self) -> None:
        """Prove the directory is writable NOW, not at the first cut.

        A read-only mount or a wrong-owner directory is the single most likely
        way this ends up recording nothing, and it is silent by nature: the
        first attempt would be during a pass, when the operator is watching a
        cut rather than a log. So the recorder pays for one mkdir and one status
        write at construction, and a failure is loud on the one surface the
        operator has.
        """
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # Sweep whatever a previous run left behind BEFORE recording again,
            # so a crash cannot leave the byte budget already spent and the
            # first new session immediately pruning its own pre-roll.
            _SegmentWriter(self.directory, "startup", lambda _s: None,
                           self._segment_max, self._max_total).prune()
            self._write_status(force=True)
        except Exception as e:
            self._fail(REASON_NOT_WRITABLE, f"{self.directory}: {e}")

    def _fail(self, reason_short: str, detail: str) -> None:
        self._disabled = True
        self._disabled_reason = f"{reason_short} ({detail})"
        self._disabled_reason_short = reason_short
        log.error("flight recorder DISABLED -- %s", self._disabled_reason)
        self._notify_once(reason_short)
        try:
            if self._writer is not None:
                self._writer.close_file()
        finally:
            self._writer = None
        try:
            self._write_status(force=True)
        except Exception:
            pass

    def _notify_once(self, reason_short: str) -> bool:
        """One notice per distinct condition, never one per tick.

        A watchdog that floods is a watchdog nobody reads; the point is that the
        operator learns ONCE that the machine has stopped recording, and the
        status file carries the detail for whoever looks afterwards.
        """
        if self._notified_reason == reason_short:
            return False
        self._notified_reason = reason_short
        return self._notify_operator(
            f"{NOTICE_NOT_RECORDING} — {reason_short}", NOTICE_WARNING)

    def _notify_operator(self, message, severity) -> bool:
        """Seam the tests patch -- see ServoDispatcher._notify_operator."""
        return notify_operator(message, severity)

    # ── the tick ──────────────────────────────────────────────────────────

    def _poll(self) -> None:
        now = self._now()
        self._tick += 1
        self.ticks_seen += 1

        fast = self._board.fast_data_values
        snap = self._board.els_stop_values
        # An EMPTY elsStop dict is Board's way of saying this tick has no
        # snapshot (the refresh failed, or there is no link) -- it CLEARS rather
        # than leaving a stale one precisely so readers cannot record a value
        # assembled from last tick's numbers. fast_data_values is NOT cleared on
        # disconnect, so it is checked but not trusted alone.
        link_up = bool(snap) and bool(fast)

        if link_up != self._link_up:
            self._link_up = link_up
            if self._writer is not None:
                self._event("link", up=link_up)

        if not link_up:
            self._blind_ticks += 1
            self.blind_ticks_total += 1
            if self._blind_since is None:
                self._blind_since = now
            # A session already open is HELD through the blindness rather than
            # closed: a link lost mid-pass is a gap in a pass, and closing here
            # would file it as a completed session that simply stopped early.
            self._maybe_close(now)
            self._maybe_status(now)
            return

        if self._blind_ticks:
            blind, since = self._blind_ticks, self._blind_since
            self._blind_ticks = 0
            self._blind_since = None
            if blind >= STALL_TICKS and self._writer is not None:
                self._event("stall", ticks=blind,
                            ms=int(round((now - since) * 1000.0)))

        state = self._read_state()
        armed = self._armed(fast, snap, state)
        row = self._sample(now, fast, snap, state)

        if armed:
            self._last_armed = now
            self._last_armed_utc = _utc_now_iso()
            if self._writer is None:
                self._start_session(now, fast, snap, state)

        if self._writer is not None:
            self._emit_context_if_changed(snap)
            if state != self._last_fsm:
                self._event("fsm", to=state, was=self._last_fsm)
            self._last_fsm = state
            if self._tick % self._every == 0:
                # Stamped HERE, not in _sample(): on the tick a session opens,
                # the row was built before _session_start existed. Recomputing
                # at the write is the only place both facts are known.
                row[0] = self._t_ms(now)
                self._writer.write(row)
                self.samples_written += 1
                self._last_sample_utc = _utc_now_iso()
                if self._need_sample_status:
                    # CORROBORATE THE SESSION with a sample, immediately.
                    # Without this the status file can sit for a whole status
                    # interval saying `state: recording` with `last_sample_utc:
                    # null`, which is indistinguishable from a session that
                    # opened and went blind on the very next tick -- the exact
                    # ambiguity this file exists to remove. One extra atomic
                    # write per session buys it.
                    self._need_sample_status = False
                    self._write_status(force=True)
            self._maybe_close(now)
        else:
            self._last_fsm = state
            if self._tick % self._every == 0:
                # Carried with its own monotonic time so the flush can rebase
                # it against a session start that has not happened yet.
                self._preroll.append((now, row))

        self._maybe_status(now)

    def _t_ms(self, now) -> int:
        if self._session_start is None:
            return 0
        return int(round((now - self._session_start) * 1000.0))

    def _read_state(self):
        if self._fsm_state is None:
            return None
        try:
            return self._fsm_state()
        except Exception:
            # An FSM that cannot answer is not a reason to stop recording the
            # machine. -1 in the column says "unknown", which is honest.
            return None

    def _armed(self, fast, snap, state) -> bool:
        """Is the machine doing something worth a session?

        THREE INDEPENDENT TERMS, deliberately OR-ed rather than reduced to the
        FSM state alone. The FSM is this app's opinion; ``enable`` and
        ``servoMode`` are the firmware's, and the interesting failures are
        exactly the ones where those two disagree. An arrangement that recorded
        only what the FSM believed was happening could not capture a firmware
        that kept feeding after the UI said stop -- which is a defect this
        codebase has already had (ServoDispatcher's divergence watchdog).
        """
        return bool(
            int(snap.get("enable", 0))
            or int(fast.get("servoMode", 0))
            or state in ("cutting", "retracting")
        )

    def _sample(self, now, fast, snap, state):
        """One positional row, in FAST_FIELDS order.

        Kept in step with FAST_FIELDS by a test rather than by care: a row whose
        columns have silently shifted is data that reads perfectly and means
        something else, which is the failure mode the whole register-map
        contract test exists to prevent one layer down.
        """
        scales = fast.get("scaleCurrent") or (0, 0, 0, 0)
        speeds = fast.get("scaleSpeed") or (0, 0, 0, 0)
        return [
            self._t_ms(now),
            self._state_index(state),
            int(fast.get("servoMode", 0)),
            int(snap.get("enable", 0)),
            int(snap.get("active", 0)),
            int(snap.get("takeupPending", 0)),
            int(fast.get("servoCurrent", 0)),
            int(fast.get("servoDesired", 0)),
            int(fast.get("stepsToGo", 0)),
            round(float(fast.get("servoSpeed", 0.0)), 2),
            int(scales[0]), int(scales[1]), int(scales[2]), int(scales[3]),
            int(speeds[0]),
        ]

    def _state_index(self, state) -> int:
        try:
            return self._states.index(state)
        except (ValueError, AttributeError):
            return -1

    def _context(self, snap) -> dict:
        return {f: snap[f] for f in CONTEXT_FIELDS if f in snap}

    def _emit_context_if_changed(self, snap) -> None:
        ctx = self._context(snap)
        if ctx == self._last_context:
            return
        self._last_context = ctx
        self._event("context", **ctx)

    # ── sessions ──────────────────────────────────────────────────────────

    def _start_session(self, now, fast, snap, state) -> None:
        free = self._check_free()
        if free is None:
            return
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._session_started_utc = _utc_now_iso()
        self._last_context = None
        writer = _SegmentWriter(
            self.directory, self._session_id,
            lambda segment: self._header(segment, snap, state),
            self._segment_max, self._max_total)
        # OPEN FIRST, then adopt. A writer that raises here (permissions, a full
        # card) must leave the recorder in the state it was in -- setting
        # _session_start before the open succeeded strands a clock with no file
        # under it, and every later t_ms is then measured from a session that
        # never existed.
        writer.open()
        self._session_start = now
        self._writer = writer
        self.sessions += 1
        self._blocked_reason = ""
        self._notified_reason = None
        self._need_sample_status = True
        self._emit_context_if_changed(snap)
        # The pre-roll is written AFTER the context so a reader hitting the top
        # of a segment always has the geometry before the first sample. Its rows
        # carry NEGATIVE t_ms, because they happened before the session did --
        # said out loud here because a negative timestamp otherwise reads as a
        # bug rather than as the pre-roll doing its job.
        for sampled_at, row in self._preroll:
            row[0] = self._t_ms(sampled_at)
            self._writer.write(row)
            self.samples_written += 1
            # Stamped even though these rows are historical: the field means
            # "when a sample was last WRITTEN", and leaving it null while
            # samples_written climbs would recreate, one field over, exactly the
            # ambiguity _need_sample_status was added to remove.
            self._last_sample_utc = _utc_now_iso()
        self._preroll.clear()
        log.info("flight recorder session %s started -> %s",
                 self._session_id, self._writer.path)
        self._write_status(force=True)

    def _maybe_close(self, now) -> None:
        if self._writer is None or self._last_armed is None:
            return
        if (now - self._last_armed) < HOLD_SECONDS:
            return
        # NAME THE REAL CAUSE. A session held open through a dead link runs out
        # its hold and closes here too -- and calling that "idle" would file a
        # pass whose controller vanished mid-cut as one the operator finished.
        # The two are the same code path and must not be the same word.
        self._end_session("idle" if self._link_up else "link_lost")

    def _end_session(self, reason) -> None:
        writer, self._writer = self._writer, None
        duration = 0
        if self._session_start is not None:
            duration = int(round((self._now() - self._session_start) * 1000.0))
        try:
            writer.close({
                "kind": "session_end",
                "reason": reason,
                "ended_utc": _utc_now_iso(),
                "duration_ms": duration,
                "samples_written": self.samples_written,
                # A session that ended while the poll stream was dark says so.
                # Otherwise its short sample count reads as a short pass.
                "blind_ticks": self._blind_ticks,
                "link_up": bool(self._link_up),
            })
        finally:
            self._session_start = None
            self._last_armed = None
            self._last_context = None
        log.info("flight recorder session %s ended (%s)", self._session_id, reason)
        self._write_status(force=True)

    def _event(self, kind, **fields) -> None:
        if self._writer is None:
            return
        record = {"kind": kind, "t_ms": 0}
        if self._session_start is not None:
            record["t_ms"] = int(round(
                (self._now() - self._session_start) * 1000.0))
        record.update(fields)
        self._writer.write(record)

    def _header(self, segment, snap, state) -> dict:
        return {
            "kind": "session_start",
            "schema": SCHEMA,
            "session": self._session_id,
            "segment": segment,
            "started_utc": self._session_started_utc,
            "segment_utc": _utc_now_iso(),
            # Positional decoding key for every array line in THIS segment. A
            # reader that hard-codes an order instead of reading this is a
            # reader that breaks silently on the next appended column.
            "fields": list(FAST_FIELDS),
            "context_fields": list(CONTEXT_FIELDS),
            "fsm_states": list(self._states),
            "sample_every_n_ticks": self._every,
            "board_tick_hz": 30,
            "gate": {
                "fsm": state,
                "enable": int(snap.get("enable", 0)),
                "protocolVersion": int(snap.get("protocolVersion", 0)),
            },
            "pid": os.getpid(),
        }

    # ── disk ──────────────────────────────────────────────────────────────

    def _check_free(self):
        """Free bytes, or None when the floor says stop.

        Blocking is a STATE, not a silence: it sets ``_blocked_reason``, which
        the status file publishes and the operator notice announces once.
        """
        try:
            free = int(self._free_bytes(self.directory))
        except Exception as e:
            # FAIL OPEN, deliberately. An unmeasurable disk is not a full disk,
            # and blocking here would stop the recorder for a reason that has
            # nothing to do with space. If the card really is full the WRITE
            # fails, which is the backstop: MAX_FAILURES of those disable the
            # recorder and post the operator notice.
            log.debug("flight recorder free-space check failed: %s", e)
            return 0
        if free < self._min_free:
            if self._blocked_reason != REASON_NO_DISK:
                log.error("flight recorder blocked: %d bytes free, floor is %d",
                          free, self._min_free)
            self._blocked_reason = REASON_NO_DISK
            self._notify_once(REASON_NO_DISK)
            self._write_status(force=True)
            return None
        if self._blocked_reason == REASON_NO_DISK:
            self._blocked_reason = ""
            self._notified_reason = None
        return free

    def _bytes_on_disk(self):
        """Segment count and total bytes, by stat.

        Runs on the status cadence (every few seconds), not per tick, and the
        scan is bounded by MAX_TOTAL_BYTES / SEGMENT_MAX_BYTES = 128 files at
        the shipped constants. Worth knowing before anyone lowers
        SEGMENT_MAX_BYTES: it is the number of stat calls this makes, on the
        Kivy main thread, inside a 33 ms board tick.
        """
        try:
            segments = list(self.directory.glob(SEGMENT_GLOB))
        except OSError:
            return 0, 0
        total = 0
        for p in segments:
            try:
                total += p.stat().st_size
            except OSError:
                continue
        return len(segments), total

    # ── status ────────────────────────────────────────────────────────────

    def _maybe_status(self, now) -> None:
        if self._last_status is not None \
                and (now - self._last_status) < STATUS_INTERVAL_SECONDS:
            return
        self._last_status = now
        self._write_status()

    def _state_word(self) -> str:
        if self._disabled:
            # blocked_write_error is for a genuine WRITE problem only. A
            # disable from repeated tick failures reports "disabled",
            # because those come from any cause and calling them write
            # errors would be a confident false claim -- the thing this
            # file exists to refuse. See REASON_TICK_FAILURES.
            if self._disabled_reason_short == REASON_NOT_WRITABLE:
                return "blocked_write_error"
            return "disabled"
        if self._blocked_reason == REASON_NO_DISK:
            return "blocked_no_disk"
        if self._writer is not None:
            return "recording"
        return "idle_disarmed"

    def _write_status(self, force=False) -> None:
        """Rewrite ``flight_status.json`` atomically.

        UNCONDITIONAL. It is written whether or not anything is being recorded,
        which is the entire mechanism that makes "no lines in the log" readable
        -- see the module docstring's table. Written to a temp file and
        os.replace'd so a collector scraping it can never catch it half-written;
        os.replace is atomic on POSIX and on Windows for an existing target.
        """
        if force:
            self._last_status = self._now()
        segments, on_disk = self._bytes_on_disk()
        try:
            free = int(self._free_bytes(self.directory))
        except Exception:
            free = -1
        payload = {
            "schema": SCHEMA,
            "updated_utc": _utc_now_iso(),
            "pid": os.getpid(),
            "state": self._state_word(),
            "reason": self._disabled_reason or self._blocked_reason or "",
            # THE HEARTBEAT. Climbs on every board tick, connected or not. A
            # frozen `updated_utc` means the process is gone; a fresh one with a
            # frozen `ticks_seen` cannot happen, because this file is only
            # written from the tick.
            "ticks_seen": self.ticks_seen,
            "samples_written": self.samples_written,
            "sessions": self.sessions,
            "recording": self._writer is not None,
            "session": self._session_id if self._writer is not None else None,
            "last_sample_utc": self._last_sample_utc,
            "last_armed_utc": self._last_armed_utc,
            "link_up": bool(self._link_up),
            "blind_ticks_total": self.blind_ticks_total,
            "write_failures": self._failures,
            "dir": str(self.directory),
            "segments": segments,
            "bytes_on_disk": on_disk,
            "max_total_bytes": self._max_total,
            "free_bytes": free,
            "min_free_bytes": self._min_free,
        }
        path = self.status_path
        tmp = path.with_suffix(".json.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, path)
