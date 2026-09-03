"""FlightRecorder: the 30 Hz poll stream, persisted instead of discarded.

Three families of assertion here, and they are not equally interesting.

The ordinary ones pin the format: a segment header that names its own columns,
rows in that order, context on change, a bounded directory.

The ones that matter pin the HOUSE RULE. A recorder that can silently record
nothing is a check that cannot fail, and "no lines in the log" has five causes
of which only one is "the machine was idle". So there are tests that assert an
idle machine leaves a positive statement of its idleness on disk, that an
unwritable directory is loud at construction rather than at the first cut, and
that a stalled poll stream publishes its own gap rather than shortening a pass.

And one is an INSTRUMENT CONTRACT: the fields the half-nut detector needs to
tell an open half nut from the pre-cut take-up and from a stalling spindle are
asserted by name. Dropping one of them to save bytes would leave every other
test in this file green.
"""
import json

import pytest

from reflex.fsms.els_flight_recorder import (
    CONTEXT_FIELDS,
    FAST_FIELDS,
    HOLD_SECONDS,
    MAX_FAILURES,
    NOTICE_NOT_RECORDING,
    PREROLL_SAMPLES,
    REASON_NO_DISK,
    REASON_NOT_WRITABLE,
    SEGMENT_GLOB,
    STALL_TICKS,
    STATUS_INTERVAL_SECONDS,
    FlightRecorder,
)

TICK = 1.0 / 30.0

#: The ELS domain FSM's state vocabulary, duplicated so these tests do not have
#: to stand up an FSM. test_state_vocabulary_matches_the_fsm keeps the copy
#: honest -- a state added there and not here is a row column whose meaning has
#: quietly shifted, which is exactly the failure the header's own fsm_states
#: list exists to make impossible for a reader.
STATES = ["disabled", "stopped", "retracting", "cutting", "alarm"]


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, s=TICK):
        self.t += s


def _fast(servoMode=0, servoCurrent=0, servoDesired=0, stepsToGo=0,
          servoSpeed=0.0, scales=(0, 0, 0, 0), speeds=(0, 0, 0, 0)):
    return {
        "servoMode": servoMode,
        "servoCurrent": servoCurrent,
        "servoDesired": servoDesired,
        "stepsToGo": stepsToGo,
        "servoSpeed": servoSpeed,
        "scaleCurrent": list(scales),
        "scaleSpeed": list(speeds),
        "cycles": 0,
        "executionInterval": 0,
    }


def _snap(enable=0, active=0, takeupPending=0, takeupSeq=0, **over):
    snap = {
        "enable": enable, "active": active, "takeupPending": takeupPending,
        "scaleIndex": 1, "stopDirection": 1, "stopPosition": 12345,
        "hysteresis": 4,
        "backlashSteps": 385, "takeupSeq": takeupSeq, "takeupResult": 0,
        "lastTakeupZDelta": 0, "takeupThreshCounts": 11,
        "referenceLatched": 0, "latchSeq": 0, "latchedZ": 0,
        "latchedSpindle": 0,
        "threadPitchSteps": 0.0, "zCountsPerPitch": 200.0,
        "phaseOffsetSteps": 0,
        "lastIdealAdvance": 0.0, "lastActualAdvance": 0.0,
        "lastPhaseError": 0.0, "lastCorrection": 0.0,
        "machineMode": 0, "protocolVersion": 7,
        "stepPulseMinCycles": 900, "stepPulseRuntCount": 0,
    }
    snap.update(over)
    return snap


class _Board:
    def __init__(self, fast=None, snap=None):
        self.fast_data_values = _fast() if fast is None else fast
        self.els_stop_values = _snap() if snap is None else snap
        self.connected = True


class _Free:
    """Mutable free-space stub, so a test can fill the card mid-run."""

    def __init__(self, free=10 ** 12):
        self.free = free

    def __call__(self, _path):
        return self.free


def _recorder(tmp_path, board, clock, state="stopped", free=None, **kw):
    holder = {"state": state}
    rec = FlightRecorder(
        board,
        fsm_state=lambda: holder["state"],
        fsm_states=STATES,
        directory=tmp_path,
        now=clock,
        free_bytes=free or _Free(),
        **kw,
    )
    rec._test_state = holder          # tests flip this to move the FSM
    return rec


def _pump(rec, clock, board, n, state=None, fast=None, snap=None):
    for _ in range(n):
        if state is not None:
            rec._test_state["state"] = state
        if fast is not None:
            board.fast_data_values = fast
        if snap is not None:
            board.els_stop_values = snap
        rec.poll()
        clock.tick()


def _segments(tmp_path):
    return sorted(tmp_path.glob(SEGMENT_GLOB))


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _all(tmp_path):
    """(records, samples) across every segment, in file order."""
    records, samples = [], []
    for p in _segments(tmp_path):
        for value in _lines(p):
            (samples if isinstance(value, list) else records).append(value)
    return records, samples


def _status(rec):
    return json.loads(rec.status_path.read_text())


def _kinds(records, kind):
    return [r for r in records if r.get("kind") == kind]


# ════════════════════════════════════════════════════════════════════════
# A recorder that can silently record nothing is a check that cannot fail
# ════════════════════════════════════════════════════════════════════════

def test_status_file_exists_before_the_first_tick(tmp_path):
    """The startup self-test. A directory this cannot write to is the single
    most likely way the whole feature records nothing, and it must be known at
    construction -- not discovered during a pass, when the operator is watching
    a cut rather than a log."""
    rec = _recorder(tmp_path, _Board(), _Clock())

    assert rec.status_path.exists()
    assert _status(rec)["state"] == "idle_disarmed"
    assert not rec.disabled


def test_an_idle_machine_leaves_a_positive_statement_of_its_idleness(tmp_path):
    """THE distinction the house rule demands: "recorded, and nothing happened"
    must not look like "did not record". No segment is written -- and the status
    file says, in so many words, that the recorder was alive and watching while
    that was true."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)

    _pump(rec, clock, board, 300)          # 10 s of a quiet, connected machine

    assert _segments(tmp_path) == []
    st = _status(rec)
    assert st["state"] == "idle_disarmed"
    assert st["recording"] is False
    assert st["link_up"] is True
    # The heartbeat. Absence of samples is only readable next to this.
    assert st["ticks_seen"] >= 150
    assert st["samples_written"] == 0


def test_status_heartbeat_keeps_updating_while_the_link_is_down(tmp_path):
    """Board.update() keeps ticking at 2 Hz while disconnected, so a dead
    controller and a dead UI must not produce the same file. link_up goes false
    while updated_utc stays fresh."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)
    _pump(rec, clock, board, 5)
    before = _status(rec)["ticks_seen"]

    board.els_stop_values = {}             # Board CLEARS on a failed refresh
    _pump(rec, clock, board, 300)

    st = _status(rec)
    assert st["link_up"] is False
    assert st["ticks_seen"] > before       # alive, and saying so
    assert st["state"] == "idle_disarmed"


def test_an_unwritable_directory_disables_loudly_at_construction(tmp_path):
    """A read-only mount or a wrong-owner directory is silent by nature. At the
    lathe there is a touchscreen and no terminal, so the log is not the channel
    -- the operator notice is."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    notices = []

    board, clock = _Board(), _Clock()
    holder = {"state": "stopped"}
    rec = FlightRecorder.__new__(FlightRecorder)
    rec._notify_operator = lambda m, s: notices.append((m, s)) or True
    FlightRecorder.__init__(
        rec, board, fsm_state=lambda: holder["state"], fsm_states=STATES,
        directory=blocker / "flight", now=clock, free_bytes=_Free())

    assert rec.disabled
    assert REASON_NOT_WRITABLE in rec.disabled_reason
    assert len(notices) == 1
    assert notices[0][0].startswith(NOTICE_NOT_RECORDING)
    # And it stays inert rather than retrying into the update loop forever.
    rec.poll()
    assert rec.recording is False


def test_low_disk_blocks_recording_and_says_so_once(tmp_path):
    """A lathe that will not boot is worse than a missing dataset, so the
    recorder stops rather than taking the last of the card. Blocking is a
    STATE -- named in the status file -- not a silence."""
    clock, board = _Clock(), _Board()
    free = _Free(free=1024)                # far under the floor
    rec = _recorder(tmp_path, board, clock, free=free)
    notices = []
    rec._notify_operator = lambda m, s: notices.append((m, s)) or True

    _pump(rec, clock, board, 100, state="cutting",
          snap=_snap(enable=1), fast=_fast(servoMode=1))

    assert _segments(tmp_path) == []
    st = _status(rec)
    assert st["state"] == "blocked_no_disk"
    assert st["reason"] == REASON_NO_DISK
    # One notice per condition, not one per tick.
    assert len(notices) == 1


def test_a_blind_poll_stream_publishes_its_own_gap(tmp_path):
    """A run of ticks with no elsStop snapshot must not shorten a pass into a
    plausible short one. The stall record carries the count and the duration."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)

    armed, live = _snap(enable=1), _fast(servoMode=1)
    _pump(rec, clock, board, 5, state="cutting", snap=armed, fast=live)
    board.els_stop_values = {}
    _pump(rec, clock, board, STALL_TICKS + 5)
    board.els_stop_values = armed
    _pump(rec, clock, board, 5)

    records, _ = _all(tmp_path)
    stalls = _kinds(records, "stall")
    assert len(stalls) == 1
    assert stalls[0]["ticks"] == STALL_TICKS + 5
    assert stalls[0]["ms"] > 0
    # And the link transition is recorded on both edges.
    assert [r["up"] for r in _kinds(records, "link")] == [False, True]


def test_a_session_killed_by_a_dead_link_is_not_filed_as_idle(tmp_path):
    """Both causes run out the same hold and close on the same line. A pass
    whose controller vanished mid-cut must not read as one the operator
    finished -- that is the difference between a bug and a normal day."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)
    _pump(rec, clock, board, 5, state="cutting",
          snap=_snap(enable=1), fast=_fast(servoMode=1))

    board.els_stop_values = {}
    clock.tick(HOLD_SECONDS)
    rec.poll()

    assert not rec.recording
    records, _ = _all(tmp_path)
    end = _kinds(records, "session_end")
    assert len(end) == 1
    assert end[0]["reason"] == "link_lost"
    assert end[0]["link_up"] is False
    assert end[0]["blind_ticks"] > 0


def test_no_snapshot_fabricates_no_sample(tmp_path):
    """Board clears els_stop_values rather than leaving a stale one precisely so
    readers cannot record last tick's numbers as this tick's. Honour that."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)
    _pump(rec, clock, board, 3, state="cutting",
          snap=_snap(enable=1), fast=_fast(servoMode=1))
    _, before = _all(tmp_path)

    board.els_stop_values = {}
    _pump(rec, clock, board, 30)

    _, after = _all(tmp_path)
    assert len(after) == len(before)


def test_poll_never_raises_and_disables_after_repeated_failures(tmp_path):
    """Bound to update_tick, so an escaping exception takes the UI down with it
    on a machine whose only interface is that UI."""
    class Exploding:
        connected = True
        els_stop_values = {"enable": 1}

        @property
        def fast_data_values(self):
            raise RuntimeError("boom")

    clock = _Clock()
    rec = _recorder(tmp_path, Exploding(), clock)
    notices = []
    rec._notify_operator = lambda m, s: notices.append((m, s)) or True

    for _ in range(MAX_FAILURES + 3):
        rec.poll()                          # must not raise
        clock.tick()

    assert rec.disabled
    assert _status(rec)["state"] == "disabled"
    assert len(notices) == 1


# ════════════════════════════════════════════════════════════════════════
# The instrument contract: what the half-nut consumer needs
# ════════════════════════════════════════════════════════════════════════

def test_the_half_nut_discriminators_are_all_in_the_stream(tmp_path):
    """"Z not advancing while sync steps are commanded" has two innocent
    look-alikes, and each is separated by a specific field. This asserts those
    fields by NAME, because dropping one to save bytes leaves every other test
    in this file green and the derived check quietly unbuildable.

    takeupPending is per-sample and not merely per-pass: the pre-cut take-up is
    the same signature by construction, and a check that could only see it at
    pass granularity would have to guess where its window ended.
    """
    # the signature itself
    assert "servoCurrent" in FAST_FIELDS and "servoDesired" in FAST_FIELDS
    assert "stepsToGo" in FAST_FIELDS
    assert {"sc0", "sc1", "sc2", "sc3"} <= set(FAST_FIELDS)   # Z, physically
    assert "scaleIndex" in CONTEXT_FIELDS                     # which one is Z
    # look-alike 1: the pre-cut take-up, which commands steps with no Z
    assert "takeupPending" in FAST_FIELDS
    for f in ("takeupSeq", "takeupResult", "lastTakeupZDelta", "backlashSteps"):
        assert f in CONTEXT_FIELDS
    # look-alike 2: a loaded or stalling spindle
    assert "spindleSpeed" in FAST_FIELDS
    assert "sc0" in FAST_FIELDS                               # raw spindle count
    # look-alike 3, FOUND BY RECORDING (2026-09-03): the retract-side backlash
    # traverse commands ~105 steps over ~163 ms with Z stationary, and
    # takeupPending is 0 throughout it. `fsm` is the ONLY column that excludes
    # it, which is why it is per-sample rather than left to the fsm records.
    assert "fsm" in FAST_FIELDS
    # pass boundaries, so a window can be scoped to one pass
    assert "enable" in FAST_FIELDS and "active" in FAST_FIELDS


def test_state_vocabulary_matches_the_fsm(tmp_path):
    """The fsm column is an INDEX. A state added to the FSM and not reflected
    here shifts every index after it -- data that reads perfectly and means
    something else."""
    from reflex.fsms.els_fsm import ElsFsm
    assert list(ElsFsm.STATES) == STATES


# ════════════════════════════════════════════════════════════════════════
# The format
# ════════════════════════════════════════════════════════════════════════

def test_arming_opens_a_session_whose_header_names_its_own_columns(tmp_path):
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)

    _pump(rec, clock, board, 3, state="cutting",
          snap=_snap(enable=1), fast=_fast(servoMode=1))

    segments = _segments(tmp_path)
    assert len(segments) == 1
    head = _lines(segments[0])[0]
    assert head["kind"] == "session_start"
    assert head["segment"] == 0
    assert head["fields"] == list(FAST_FIELDS)
    assert head["context_fields"] == list(CONTEXT_FIELDS)
    assert head["fsm_states"] == STATES
    assert head["gate"]["fsm"] == "cutting"
    assert head["gate"]["protocolVersion"] == 7
    assert _status(rec)["state"] == "recording"


def test_rows_are_positional_in_the_header_order(tmp_path):
    """The positional decoding contract. A column that silently shifted is data
    that parses cleanly and means something else."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)

    fast = _fast(servoMode=1, servoCurrent=4111, servoDesired=4200,
                 stepsToGo=89, servoSpeed=1234.567,
                 scales=(70000, 41000, 0, 0), speeds=(600, 12, 0, 0))
    _pump(rec, clock, board, 1, state="cutting",
          snap=_snap(enable=1, active=0, takeupPending=1), fast=fast)

    _, samples = _all(tmp_path)
    row = dict(zip(FAST_FIELDS, samples[0]))
    assert row["fsm"] == STATES.index("cutting")
    assert row["servoMode"] == 1
    assert row["enable"] == 1
    assert row["active"] == 0
    assert row["takeupPending"] == 1
    assert row["servoCurrent"] == 4111
    assert row["servoDesired"] == 4200
    assert row["stepsToGo"] == 89
    assert row["servoSpeed"] == 1234.57            # rounded, deliberately
    assert (row["sc0"], row["sc1"]) == (70000, 41000)
    assert row["spindleSpeed"] == 600
    assert len(samples[0]) == len(FAST_FIELDS)


def test_preroll_lands_in_the_segment_with_negative_timestamps(tmp_path):
    """The pre-cut take-up begins on the same tick the FSM enters 'cutting', so
    without a pre-roll the most interesting seconds of every pass are the ones
    the gate was still deciding about."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)

    _pump(rec, clock, board, PREROLL_SAMPLES + 40)      # quiet, but watched
    assert _segments(tmp_path) == []
    _pump(rec, clock, board, 1, state="cutting",
          snap=_snap(enable=1), fast=_fast(servoMode=1))

    _, samples = _all(tmp_path)
    assert len(samples) == PREROLL_SAMPLES + 1
    prerolled = [s[0] for s in samples[:PREROLL_SAMPLES]]
    assert all(t < 0 for t in prerolled)
    assert prerolled == sorted(prerolled)              # oldest first
    assert samples[-1][0] == 0                         # the arming tick itself


def test_hold_keeps_recording_past_the_end_of_a_pass(tmp_path):
    """The stop latching, the carriage settling and whatever drains afterwards
    belong INSIDE the session rather than immediately outside it."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)
    _pump(rec, clock, board, 3, state="cutting",
          snap=_snap(enable=1), fast=_fast(servoMode=1))

    # Disarmed, but still inside the hold.
    _pump(rec, clock, board, 30, state="stopped", snap=_snap(), fast=_fast())
    assert rec.recording
    _, during = _all(tmp_path)

    # Past it.
    clock.tick(HOLD_SECONDS)
    rec.poll()

    assert not rec.recording
    records, after = _all(tmp_path)
    assert len(after) > len(during)
    end = _kinds(records, "session_end")
    assert len(end) == 1
    assert end[0]["reason"] == "idle"
    assert end[0]["duration_ms"] > 0
    assert _status(rec)["state"] == "idle_disarmed"


def test_context_is_emitted_on_a_seq_edge_not_every_tick(tmp_path):
    """The slow half of the machine state rides a record emitted ON CHANGE,
    which is what makes takeupSeq / latchSeq edges events by construction."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)

    live = _fast(servoMode=1)
    _pump(rec, clock, board, 20, state="cutting", snap=_snap(enable=1), fast=live)
    records, _ = _all(tmp_path)
    assert len(_kinds(records, "context")) == 1       # session start only

    _pump(rec, clock, board, 20, snap=_snap(enable=1, takeupSeq=1, takeupResult=0))
    records, _ = _all(tmp_path)
    ctx = _kinds(records, "context")
    assert len(ctx) == 2
    assert ctx[-1]["takeupSeq"] == 1
    assert set(CONTEXT_FIELDS) <= set(ctx[-1])       # whole block, not a delta


def test_fsm_transitions_are_their_own_records(tmp_path):
    """Redundant with the fsm column and kept anyway: pass boundaries are where
    every analysis starts and should be greppable without decoding positions."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)

    armed, live = _snap(enable=1), _fast(servoMode=1)
    _pump(rec, clock, board, 3, state="stopped", snap=armed, fast=live)
    _pump(rec, clock, board, 3, state="cutting")
    _pump(rec, clock, board, 3, state="stopped")

    records, _ = _all(tmp_path)
    moves = [(r.get("was"), r["to"]) for r in _kinds(records, "fsm")]
    assert ("stopped", "cutting") in moves
    assert ("cutting", "stopped") in moves


def test_close_ends_an_open_session(tmp_path):
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)
    _pump(rec, clock, board, 5, state="cutting",
          snap=_snap(enable=1), fast=_fast(servoMode=1))

    rec.close("shutdown")

    records, _ = _all(tmp_path)
    end = _kinds(records, "session_end")
    assert len(end) == 1 and end[0]["reason"] == "shutdown"
    assert not rec.recording
    rec.close()                                       # idempotent, never raises


# ════════════════════════════════════════════════════════════════════════
# The disk bound: elspi is a Pi with one SD card in a machine shop
# ════════════════════════════════════════════════════════════════════════

def test_rotation_starts_a_new_segment_with_its_own_header(tmp_path):
    """Each segment must stand alone -- a reader that opened only the newest
    file still has the field list it needs to decode it."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock,
                    segment_max_bytes=600, max_total_bytes=10 ** 7)

    _pump(rec, clock, board, 120, state="cutting",
          snap=_snap(enable=1), fast=_fast(servoMode=1, servoCurrent=999999))

    segments = _segments(tmp_path)
    assert len(segments) > 1
    for i, p in enumerate(segments):
        head = _lines(p)[0]
        assert head["kind"] == "session_start"
        assert head["segment"] == i
        assert head["fields"] == list(FAST_FIELDS)
        assert head["session"] == segments[0].name.split("-")[1]


def test_the_directory_stays_inside_its_byte_budget(tmp_path):
    """An unbounded log on an SD card in a machine shop is a failure mode, not
    a feature. The bound is on what is KEPT: pruning never deletes the segment
    currently open, so the ceiling is the budget plus one segment."""
    clock, board = _Clock(), _Board()
    segment_max, total_max = 600, 2400
    rec = _recorder(tmp_path, board, clock,
                    segment_max_bytes=segment_max, max_total_bytes=total_max)

    _pump(rec, clock, board, 600, state="cutting",
          snap=_snap(enable=1), fast=_fast(servoMode=1, servoCurrent=987654321))
    rec.close("test")

    segments = _segments(tmp_path)
    total = sum(p.stat().st_size for p in segments)
    assert total <= total_max + segment_max
    # Prove pruning actually happened rather than the run being too short to
    # need it -- a bound that was never exercised is a bound that is untested.
    assert rec._writer is None
    assert len(segments) >= 1
    numbers = [int(p.stem.split("-")[-1]) for p in segments]
    assert min(numbers) > 0            # the earliest segments were deleted


def test_leftovers_from_a_previous_run_are_swept_at_startup(tmp_path):
    """A crash must not leave the budget already spent, with the first new
    session immediately pruning its own pre-roll."""
    for i in range(6):
        (tmp_path / f"flight-20260101T0000{i:02d}Z-000.jsonl").write_text("x" * 500)
    assert sum(p.stat().st_size for p in _segments(tmp_path)) == 3000

    _recorder(tmp_path, _Board(), _Clock(), max_total_bytes=1200)

    assert sum(p.stat().st_size for p in _segments(tmp_path)) <= 1200


@pytest.mark.parametrize("raw,expect_default", [
    (None, True),
    ("", True),
    ("   ", True),
    ("not-a-number", True),      # refused, not silently honoured
    ("1", True),                 # below one segment -- would prune every session
    ("0", True),                 # the confident-empty-directory value
    ("256", False),
    ("1024.5", False),
])
def test_the_budget_override_refuses_values_that_would_record_nothing(
        monkeypatch, raw, expect_default):
    """A budget of 0 makes every session prune itself immediately and leaves an
    empty directory -- exactly the confident silence this module exists to make
    impossible. Refusing back to the default with a log line is the only safe
    reading of a nonsense value."""
    from reflex.fsms import els_flight_recorder as mod
    if raw is None:
        monkeypatch.delenv("REFLEX_FLIGHT_MAX_MB", raising=False)
    else:
        monkeypatch.setenv("REFLEX_FLIGHT_MAX_MB", raw)

    value = mod._max_total_bytes()

    assert value >= mod.SEGMENT_MAX_BYTES
    if expect_default:
        assert value == 512 * 1024 * 1024
    else:
        assert value == int(float(raw) * 1024 * 1024)


def test_status_file_is_replaced_atomically(tmp_path):
    """A collector scraping this must never catch it half-written."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)

    for _ in range(int(STATUS_INTERVAL_SECONDS * 30) * 3):
        rec.poll()
        clock.tick()
        json.loads(rec.status_path.read_text())     # always parseable

    assert list(tmp_path.glob("*.tmp")) == []


def test_status_reports_the_bytes_it_is_holding(tmp_path):
    """The collector leg's summary. Everything needed to alert on the recorder
    itself -- alive, recording, blocked, and how much card it is using."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)
    _pump(rec, clock, board, 30, state="cutting",
          snap=_snap(enable=1), fast=_fast(servoMode=1))

    st = _status(rec)
    assert st["segments"] == 1
    assert st["bytes_on_disk"] > 0
    assert st["max_total_bytes"] > 0
    assert st["free_bytes"] > 0
    assert st["session"] is not None
    # A session claimed as "recording" is CORROBORATED by a sample without
    # waiting a status interval -- otherwise this is indistinguishable from a
    # session that opened and went blind on the next tick.
    assert st["samples_written"] > 0
    assert st["last_sample_utc"] is not None


@pytest.mark.parametrize("gate", ["enable", "servoMode", "fsm"])
def test_any_of_the_three_gate_terms_arms_a_session(tmp_path, gate):
    """OR-ed deliberately. The FSM is this app's opinion; enable and servoMode
    are the firmware's, and the interesting failures are the ones where they
    disagree -- a firmware that kept feeding after the UI said stop is a defect
    this codebase has already had."""
    clock, board = _Clock(), _Board()
    rec = _recorder(tmp_path, board, clock)

    kw = {"state": "stopped", "snap": _snap(), "fast": _fast()}
    if gate == "enable":
        kw["snap"] = _snap(enable=1)
    elif gate == "servoMode":
        kw["fast"] = _fast(servoMode=1)
    else:
        kw["state"] = "cutting"
    _pump(rec, clock, board, 3, **kw)

    assert rec.recording
    assert len(_segments(tmp_path)) == 1
