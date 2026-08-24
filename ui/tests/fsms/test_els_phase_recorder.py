"""The phase-correction recorder: the instrument that was missing on 2026-08-24.

A re-synced cut came out up to half a thread off and the machine could not say
by how much, because the four numbers explaining it were published by the
firmware and stored by nobody. These tests pin the recorder that fixes that,
and specifically the three ways such a recorder fails uselessly: it records
nothing, it records duplicates until the signal is buried, or it records values
assembled from a failed read.
"""
import json

from reflex.fsms.els_phase_recorder import (
    CONTEXT_FIELDS,
    IDENTITY_FIELDS,
    MAX_FAILURES,
    PhaseCorrectionRecorder,
)


def _snapshot(**overrides):
    """A complete elsStop snapshot, as board.py hands one over."""
    snap = {
        "lastIdealAdvance": 0.0, "lastActualAdvance": 0.0,
        "lastPhaseError": 0.0, "lastCorrection": 0.0,
        "latchedZ": 0, "latchedSpindle": 0, "referenceLatched": 0,
        "threadPitchSteps": 711.1, "zCountsPerPitch": 200.0,
        "phaseOffsetSteps": 0, "backlashSteps": 444,
        "takeupSeq": 0, "takeupResult": 0,
        "lastTakeupZDelta": 0, "takeupThreshCounts": 7,
        "enable": 1, "active": 0, "stopDirection": 1, "scaleIndex": 1,
    }
    snap.update(overrides)
    return snap


class _Board:
    def __init__(self, snapshot=None):
        self.els_stop_values = snapshot


def _recorder(tmp_path, board):
    return PhaseCorrectionRecorder(board, path=tmp_path / "phase.jsonl")


def _lines(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ── it records at all ──────────────────────────────────────────────────────

def test_a_new_correction_is_recorded(tmp_path):
    board = _Board(_snapshot(lastPhaseError=355.0, lastCorrection=355.0))
    rec = _recorder(tmp_path, board)

    rec.poll()

    rows = _lines(rec.path)
    assert len(rows) == 1
    assert rows[0]["lastPhaseError"] == 355.0


def test_the_record_carries_every_declared_field(tmp_path):
    """A field missing from the record is another bench session. The cost of
    breadth here is bytes; the cost of a gap is an evening at the lathe."""
    board = _Board(_snapshot(lastCorrection=12.0))
    rec = _recorder(tmp_path, board)

    rec.poll()

    row = _lines(rec.path)[0]
    for field in IDENTITY_FIELDS + CONTEXT_FIELDS:
        assert field in row, f"{field} missing from the recorded line"


def test_the_first_reading_is_flagged_as_a_connect_observation(tmp_path):
    """It reports whatever the registers already held, not a correction seen
    happening. Flagged rather than dropped -- silently discarding the first
    line of every session is the kind of thing rediscovered painfully."""
    board = _Board(_snapshot(lastCorrection=99.0))
    rec = _recorder(tmp_path, board)

    rec.poll()

    assert _lines(rec.path)[0]["at_connect"] is True


# ── it does not bury the signal ────────────────────────────────────────────

def test_an_unchanged_reading_is_not_recorded_again(tmp_path):
    """The poller fires ~30x a second and the registers hold their value until
    the next pass. Without this the file is thousands of identical lines an
    hour and the correction that matters is unfindable."""
    board = _Board(_snapshot(lastCorrection=42.0))
    rec = _recorder(tmp_path, board)

    for _ in range(50):
        rec.poll()

    assert len(_lines(rec.path)) == 1


def test_a_later_different_correction_is_recorded(tmp_path):
    board = _Board(_snapshot(lastCorrection=42.0))
    rec = _recorder(tmp_path, board)
    rec.poll()

    board.els_stop_values = _snapshot(lastCorrection=-17.5)
    rec.poll()

    rows = _lines(rec.path)
    assert [r["lastCorrection"] for r in rows] == [42.0, -17.5]
    assert rows[1]["at_connect"] is False


def test_a_value_returning_to_an_earlier_one_is_still_recorded(tmp_path):
    """Compares against the PREVIOUS reading, not against everything seen. Two
    passes that legitimately correct by the same amount are two events."""
    board = _Board(_snapshot(lastCorrection=5.0))
    rec = _recorder(tmp_path, board)
    rec.poll()
    board.els_stop_values = _snapshot(lastCorrection=9.0)
    rec.poll()
    board.els_stop_values = _snapshot(lastCorrection=5.0)
    rec.poll()

    assert [r["lastCorrection"] for r in _lines(rec.path)] == [5.0, 9.0, 5.0]


# ── it never records a value the controller did not send ───────────────────

def test_no_snapshot_records_nothing(tmp_path):
    """board.py CLEARS the dict when the refresh fails, so an empty snapshot is
    exactly the fabricated-read case. Recording zeros here would put a phantom
    'ideal 0, actual 0, error 0' in the file and it would read as a perfect
    pass -- the most misleading line the file could possibly contain."""
    board = _Board(None)
    rec = _recorder(tmp_path, board)

    rec.poll()

    assert _lines(rec.path) == []
    # AND it was skipped cleanly rather than throwing into poll()'s own
    # handler. Without this the assertion above passes with the guard
    # deleted, which is how the mutation survived.
    assert rec._failures == 0


def test_an_empty_snapshot_records_nothing(tmp_path):
    board = _Board({})
    rec = _recorder(tmp_path, board)

    rec.poll()

    assert _lines(rec.path) == []
    assert rec._failures == 0


def test_a_dropout_between_readings_does_not_fabricate_a_line(tmp_path):
    board = _Board(_snapshot(lastCorrection=7.0))
    rec = _recorder(tmp_path, board)
    rec.poll()

    board.els_stop_values = None
    for _ in range(10):
        rec.poll()

    assert len(_lines(rec.path)) == 1
    assert rec._failures == 0


# ── it cannot take the UI down ─────────────────────────────────────────────

def test_poll_never_raises_on_a_broken_board(tmp_path):
    """Bound to update_tick, so an escaping exception propagates into Kivy's
    dispatch and takes down the only interface this machine has."""
    class Exploding:
        @property
        def els_stop_values(self):
            raise RuntimeError("board is gone")

    rec = _recorder(tmp_path, Exploding())
    rec.poll()          # must not raise


def test_poll_never_raises_on_an_unwritable_path(tmp_path):
    board = _Board(_snapshot(lastCorrection=1.0))
    rec = PhaseCorrectionRecorder(board, path=tmp_path / "nope\x00bad" / "p.jsonl")
    rec.poll()          # must not raise


def test_a_missing_register_does_not_pass_silently(tmp_path):
    """A name the register map does not have is a BUG, not a runtime condition.
    It must not be swallowed into the same quiet path as 'no link' -- but it
    also must not raise into the Clock, so it surfaces as a failure count."""
    board = _Board(_snapshot())
    del board.els_stop_values["takeupThreshCounts"]
    rec = _recorder(tmp_path, board)

    rec.poll()

    assert _lines(rec.path) == []
    assert rec._failures == 1


def test_repeated_failures_disable_the_recorder(tmp_path):
    """Rather than retrying forever against a fault that will not clear."""
    class Exploding:
        @property
        def els_stop_values(self):
            raise RuntimeError("full disk")

    rec = _recorder(tmp_path, Exploding())
    for _ in range(MAX_FAILURES):
        rec.poll()

    assert rec.disabled is True


def test_a_disabled_recorder_stops_touching_the_board(tmp_path):
    calls = []

    class Counting:
        @property
        def els_stop_values(self):
            calls.append(1)
            raise RuntimeError("nope")

    rec = _recorder(tmp_path, Counting())
    for _ in range(MAX_FAILURES + 20):
        rec.poll()

    assert len(calls) == MAX_FAILURES
