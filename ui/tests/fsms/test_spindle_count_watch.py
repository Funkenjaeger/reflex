"""SpindleCountWatch: the belt-off experiment's instrument.

The phase tracker is job-gated by design, which makes it useless for the one
experiment that needs NO job: VFD running, belt off, encoder stationary, where
the correct count is exactly zero and any movement is noise. These pin the
watch's contract: jobless, change-only, cumulative-honest, and incapable of
taking the UI down.
"""
import json

from reflex.fsms.els_phase_recorder import (
    MAX_FAILURES,
    SpindleCountWatch,
    WATCH_SAMPLE_SECONDS,
)


def _fast(spindle=0, z=0, mode=1):
    return {"scaleCurrent": [spindle, z, 0, 0], "servoMode": mode,
            "servoCurrent": 0, "servoDesired": 0, "stepsToGo": 0}


class _Board:
    def __init__(self, fast=None):
        self.fast_data_values = fast


class _Clock:
    def __init__(self):
        self.t = 50.0

    def __call__(self):
        return self.t

    def tick(self, s=WATCH_SAMPLE_SECONDS + 0.01):
        self.t += s


def _watch(tmp_path, board, clock):
    return SpindleCountWatch(board, path=tmp_path / "w.jsonl", now=clock)


def _lines(w):
    if not w.path.exists():
        return []
    return [json.loads(l) for l in w.path.read_text().splitlines() if l.strip()]


def test_needs_no_job_no_latch_no_geometry(tmp_path):
    """The whole point: fastData alone. Nothing els-shaped is consulted."""
    clock = _Clock()
    board = _Board(_fast(spindle=100))
    w = _watch(tmp_path, board, clock)

    w.poll()

    rows = _lines(w)
    assert len(rows) == 1
    assert rows[0]["spindleCount"] == 100
    assert rows[0]["delta"] is None       # baseline, not motion


def test_a_stationary_counter_writes_nothing(tmp_path):
    """An idle machine must cost zero lines -- and in the belt-off run,
    silence IS the healthy result."""
    clock = _Clock()
    board = _Board(_fast(spindle=100))
    w = _watch(tmp_path, board, clock)
    w.poll()

    for _ in range(30):
        clock.tick()
        w.poll()

    assert len(_lines(w)) == 1            # baseline only


def test_a_count_change_is_recorded_with_its_delta(tmp_path):
    """A burst briefer than the sample interval still displaces the
    CUMULATIVE counter; the next sample carries the net as delta."""
    clock = _Clock()
    board = _Board(_fast(spindle=100))
    w = _watch(tmp_path, board, clock)
    w.poll()

    board.fast_data_values = _fast(spindle=496)   # +396: one EMI burst
    clock.tick()
    w.poll()

    rows = _lines(w)
    assert len(rows) == 2
    assert rows[1]["delta"] == 396


def test_negative_movement_is_a_delta_too(tmp_path):
    clock = _Clock()
    board = _Board(_fast(spindle=100))
    w = _watch(tmp_path, board, clock)
    w.poll()
    board.fast_data_values = _fast(spindle=40)
    clock.tick()
    w.poll()

    assert _lines(w)[1]["delta"] == -60


def test_rate_limited_to_the_sample_interval(tmp_path):
    """A spinning spindle changes the count every tick; the file must grow at
    ~1 Hz, not at tick rate."""
    clock = _Clock()
    board = _Board(_fast(spindle=0))
    w = _watch(tmp_path, board, clock)

    for i in range(30):                    # 30 ticks inside one second
        board.fast_data_values = _fast(spindle=i * 10)
        w.poll()
        clock.t += 1.0 / 30

    assert len(_lines(w)) == 1


def test_no_link_is_a_clean_skip(tmp_path):
    clock = _Clock()
    board = _Board(None)
    w = _watch(tmp_path, board, clock)

    w.poll()

    assert _lines(w) == []
    assert w._failures == 0


def test_context_rides_along(tmp_path):
    """Z count and servoMode on every line, so a belt-off session reads
    standalone -- no cross-referencing another file to know what the machine
    was doing."""
    clock = _Clock()
    board = _Board(_fast(spindle=7, z=-2000, mode=1))
    w = _watch(tmp_path, board, clock)

    w.poll()

    row = _lines(w)[0]
    assert row["zCount"] == -2000
    assert row["servoMode"] == 1


def test_poll_never_raises_and_disables_after_repeated_failures(tmp_path):
    class Exploding:
        @property
        def fast_data_values(self):
            raise RuntimeError("gone")

    w = SpindleCountWatch(Exploding(), path=tmp_path / "w.jsonl", now=_Clock())
    for _ in range(MAX_FAILURES + 3):
        w.poll()                           # must never raise

    assert w.disabled is True
