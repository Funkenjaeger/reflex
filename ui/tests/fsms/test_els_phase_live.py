"""PhaseLiveTracker: the physical-vs-commanded phase instrument.

Built for the 2026-08-25 investigation: the emulator acquitted the ISR's
correction design, so the question moved to whether commanded steps physically
execute. This tracker answers it from the scales. These tests pin the four
ways such an instrument lies: it records when there is nothing meaningful to
measure, its math drifts from els_phase.h's, it poisons the file with a
fabricated sync ratio, or it floods the file at tick rate.
"""
import json
import math

from reflex.fsms.els_phase_recorder import (
    LIVE_SAMPLE_SECONDS,
    MAX_FAILURES,
    PhaseLiveTracker,
)

PITCH = 711.111
ZCPP = 282.2222


def _snapshot(**overrides):
    snap = {
        "referenceLatched": 1, "enable": 1,
        "threadPitchSteps": PITCH, "zCountsPerPitch": ZCPP,
        "latchedSpindle": 1000000, "latchedZ": -2000,
        "latchSeq": 3, "scaleIndex": 1, "stopDirection": -1,
        "phaseOffsetSteps": 0, "active": 0, "takeupPending": 0,
    }
    snap.update(overrides)
    return snap


def _fast(**overrides):
    fast = {
        "scaleCurrent": [1000000, -2000, 0, 0],
        "servoCurrent": 500, "servoDesired": 500, "stepsToGo": 0,
    }
    fast.update(overrides)
    return fast


class _CM:
    def __init__(self):
        self.read_failures = 0

    def reads_failed_since(self, baseline):
        return self.read_failures != baseline


class _Scales:
    """board.device['scales'][i][reg] with per-read fabrication control."""

    def __init__(self, cm, num=360, den=100, fabricate=False):
        self._cm = cm
        self.num, self.den = num, den
        self.fabricate = fabricate
        self.reads = 0

    def __getitem__(self, idx):
        outer = self

        class _Block:
            def __getitem__(self, key):
                outer.reads += 1
                # HALF-fabricate: only the num read fails. A both-zero
                # fabrication is catchable by value checks alone, and a test
                # built on it passed with the counter guard deleted -- the
                # mutation run caught that. One real value and one fabricated
                # is the case only the counter (or the per-value zero check
                # on the RIGHT register) can see.
                if outer.fabricate and key == "syncRatioNum":
                    outer._cm.read_failures += 1
                    return 0
                return {"syncRatioNum": outer.num,
                        "syncRatioDen": outer.den}[key]
        return _Block()


class _Board:
    def __init__(self, snapshot=None, fast=None, num=360, den=100,
                 fabricate_ratio=False):
        self.els_stop_values = snapshot
        self.fast_data_values = fast
        self.connection_manager = _CM()
        self._scales = _Scales(self.connection_manager, num, den,
                               fabricate_ratio)
        self.device = {"scales": self._scales}


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _tracker(tmp_path, board, clock=None):
    return PhaseLiveTracker(board, path=tmp_path / "live.jsonl",
                            now=clock or _Clock())


def _lines(t):
    if not t.path.exists():
        return []
    return [json.loads(l) for l in t.path.read_text().splitlines() if l.strip()]


# ── the math is els_phase.h's ──────────────────────────────────────────────

def test_in_phase_reads_zero(tmp_path):
    """Spindle and Z advanced in exact ratio -> error ~0. elspi polarity:
    num +360/100, stopDirection -1 -> droSign -1 -> err = ideal + actual.
    One pitch of spindle (711.111*100/360 counts) against one pitch of Z
    IN THE CUTTING direction (Z negative for this polarity)."""
    # Quantization-friendly inputs: 1000 spindle counts -> ideal 3600.0 steps
    # exactly; the matching Z advance is 3600 steps = 1428.75 counts, and the
    # 0.25-count rounding is the only error the tracker should report.
    board = _Board(_snapshot(),
                   _fast(scaleCurrent=[1000000 + 1000, -2000 - 1429, 0, 0]))
    t = _tracker(tmp_path, board)

    t.poll()

    rows = _lines(t)
    assert len(rows) == 1
    assert abs(rows[0]["phaseErrSteps"]) < 1.5


def test_a_physical_lag_shows_as_error(tmp_path):
    """The reason the instrument exists: Z short of ideal by 100 steps of
    carriage motion -> error ~100, even though nothing commanded-side says so."""
    d_sp = round(PITCH * 100 / 360)
    z_lag_counts = round(100 * ZCPP / PITCH)     # 100 steps expressed in counts
    board = _Board(_snapshot(),
                   _fast(scaleCurrent=[1000000 + d_sp,
                                       -2000 - round(ZCPP) + z_lag_counts,
                                       0, 0]))
    t = _tracker(tmp_path, board)

    t.poll()

    err = _lines(t)[0]["phaseErrSteps"]
    assert 95 < abs(err) < 105


def test_the_error_folds_to_half_a_pitch(tmp_path):
    """A full pitch of error is the same groove; the honest distance is the
    fold to +-pitch/2, with NO forward bias -- that is a jog policy, not a
    measurement."""
    d_sp = round(0.9 * PITCH * 100 / 360)    # 0.9 pitch ahead, Z never moved
    board = _Board(_snapshot(),
                   _fast(scaleCurrent=[1000000 + d_sp, -2000, 0, 0]))
    t = _tracker(tmp_path, board)

    t.poll()

    row = _lines(t)[0]
    assert -0.5 <= row["phaseErrPitch"] <= 0.5
    assert row["phaseErrPitch"] < 0          # 0.9 forward folds to -0.1


def test_the_deliberate_offset_is_part_of_intended_phase(tmp_path):
    """A groove-widening offset is intent, not error: with the offset set and
    Z exactly tracking the UNSHIFTED helix, the tracker must report the
    offset's worth of error -- matching els_phase.h, where the offset is
    summed into phaseError."""
    board = _Board(_snapshot(phaseOffsetSteps=200),
                   _fast())
    t = _tracker(tmp_path, board)

    t.poll()

    assert abs(_lines(t)[0]["phaseErrSteps"] - 200) < 1.0


def test_the_commanded_ledger_rides_along(tmp_path):
    board = _Board(_snapshot(),
                   _fast(servoDesired=800, servoCurrent=500, stepsToGo=120))
    t = _tracker(tmp_path, board)

    t.poll()

    row = _lines(t)[0]
    assert row["servoBacklog"] == 300
    assert row["stepsToGo"] == 120


def test_the_backlog_survives_the_uint32_seam(tmp_path):
    """desired wrapped past 2^32 while current has not: the difference is
    small and positive, not four billion."""
    board = _Board(_snapshot(),
                   _fast(servoDesired=5, servoCurrent=2**32 - 10))
    t = _tracker(tmp_path, board)

    t.poll()

    assert _lines(t)[0]["servoBacklog"] == 15


# ── it records only when the number means something ────────────────────────

def test_no_line_without_a_latched_reference(tmp_path):
    t = _tracker(tmp_path, _Board(_snapshot(referenceLatched=0), _fast()))
    t.poll()
    assert _lines(t) == []
    assert t._failures == 0


def test_no_line_without_an_armed_job(tmp_path):
    t = _tracker(tmp_path, _Board(_snapshot(enable=0), _fast()))
    t.poll()
    assert _lines(t) == []
    assert t._failures == 0


def test_no_line_in_turning_mode(tmp_path):
    t = _tracker(tmp_path, _Board(_snapshot(threadPitchSteps=0.0), _fast()))
    t.poll()
    assert _lines(t) == []
    assert t._failures == 0


def test_no_snapshot_records_nothing_and_counts_nothing(tmp_path):
    t = _tracker(tmp_path, _Board(None, _fast()))
    t.poll()
    assert _lines(t) == []
    assert t._failures == 0


def test_no_fastdata_records_nothing_and_counts_nothing(tmp_path):
    t = _tracker(tmp_path, _Board(_snapshot(), None))
    t.poll()
    assert _lines(t) == []
    assert t._failures == 0


# ── ~1 Hz, not tick rate ───────────────────────────────────────────────────

def test_thirty_ticks_in_a_second_write_one_line(tmp_path):
    clock = _Clock()
    t = _tracker(tmp_path, _Board(_snapshot(), _fast()), clock)

    for _ in range(30):
        t.poll()
        clock.t += 1.0 / 30

    assert len(_lines(t)) == 1


def test_a_second_later_a_second_line_lands(tmp_path):
    clock = _Clock()
    t = _tracker(tmp_path, _Board(_snapshot(), _fast()), clock)
    t.poll()
    clock.t += LIVE_SAMPLE_SECONDS + 0.01
    t.poll()
    assert len(_lines(t)) == 2


def test_a_gated_tick_does_not_consume_the_sample_slot(tmp_path):
    """A skip (no link, not latched) must not reset the cadence: the next
    good tick samples immediately rather than waiting a fresh second."""
    clock = _Clock()
    board = _Board(_snapshot(), _fast())
    t = _tracker(tmp_path, board, clock)
    t.poll()
    clock.t += LIVE_SAMPLE_SECONDS + 0.01
    board.els_stop_values = None
    t.poll()                                  # skipped
    board.els_stop_values = _snapshot()
    t.poll()                                  # must land now

    assert len(_lines(t)) == 2


# ── the sync ratio: once per reference, never fabricated ───────────────────

def test_the_ratio_is_read_once_per_reference(tmp_path):
    clock = _Clock()
    board = _Board(_snapshot(), _fast())
    t = _tracker(tmp_path, board, clock)

    for _ in range(5):
        t.poll()
        clock.t += LIVE_SAMPLE_SECONDS + 0.01

    assert len(_lines(t)) == 5
    assert board._scales.reads == 2           # one num + one den, ever


def test_a_new_reference_invalidates_the_ratio_cache(tmp_path):
    clock = _Clock()
    board = _Board(_snapshot(), _fast())
    t = _tracker(tmp_path, board, clock)
    t.poll()
    clock.t += LIVE_SAMPLE_SECONDS + 0.01
    board.els_stop_values = _snapshot(latchSeq=4, latchedSpindle=2000000)
    t.poll()

    assert board._scales.reads == 4           # re-read under the new latch


def test_a_fabricated_ratio_poisons_nothing(tmp_path):
    """A failed frame hands back 0. Caching (0, 0) -- or a half-fabricated
    pair -- would silently scale every subsequent sample. On any doubt: no
    line, retry next second."""
    clock = _Clock()
    board = _Board(_snapshot(), _fast(), fabricate_ratio=True)
    t = _tracker(tmp_path, board, clock)

    t.poll()
    assert _lines(t) == []

    board._scales.fabricate = False
    clock.t += LIVE_SAMPLE_SECONDS + 0.01
    t.poll()

    rows = _lines(t)
    assert len(rows) == 1
    assert rows[0]["syncRatioNum"] == 360


# ── it cannot take the UI down ─────────────────────────────────────────────

def test_poll_never_raises_on_a_broken_board(tmp_path):
    class Exploding:
        @property
        def els_stop_values(self):
            raise RuntimeError("gone")
        fast_data_values = None

    t = PhaseLiveTracker(Exploding(), path=tmp_path / "x.jsonl", now=_Clock())
    t.poll()          # must not raise


def test_a_missing_key_counts_as_a_failure(tmp_path):
    """A name the snapshot does not have is a BUG, not a runtime condition --
    surfaced as a failure count, never a quiet skip and never a raise."""
    snap = _snapshot()
    del snap["latchSeq"]
    t = _tracker(tmp_path, _Board(snap, _fast()))

    t.poll()

    assert _lines(t) == []
    assert t._failures == 1


def test_repeated_failures_disable_the_tracker(tmp_path):
    class Exploding:
        @property
        def els_stop_values(self):
            raise RuntimeError("disk full")
        fast_data_values = None

    t = PhaseLiveTracker(Exploding(), path=tmp_path / "x.jsonl", now=_Clock())
    for _ in range(MAX_FAILURES):
        t.poll()

    assert t.disabled is True
