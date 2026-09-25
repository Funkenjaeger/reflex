"""Replay of the 2026-09-19 bench recording through StopOffsetCorrector.

THE FINDING. Three corrected air passes at .040 in/rev, ~342 rpm: every stop
landed 1-2 counts SHORT (the sign works), but the UI of that day (affdfd8)
wrote stopOffset 15-21 times PER PASS, dithering 9 -> 10 -> 11 -> 10 -> 9 about
every 270 ms through the steady part, because it sized from the single-tick
Z speed register, which wanders ~1040-1140 counts/s and crosses a table count
boundary on every wobble. The offset in effect at the trigger (9 or 10) was
just whichever write came last. `recorded_offset_writes` in the fixture is
that day's sequence as the recorder filed it.

THE FIXTURE (tests/fsms/data/els_overshoot_replay_20260919.json) is the Z scale
position, elsStop.active and host time of those three passes, cut from the
flight recording flight-20260919T112827Z-000.jsonl -- nothing else.

WHAT IT DOES NOT HAVE: the recorder does not file the Z scale's `speed`
register (its speed column is the spindle's). The replay therefore feeds, as
the board's scaleSpeed[1], the single-tick derivative of the recorded Z
position. That stand-in is NOISIER than the register was (it swings roughly
900-1570 counts/s on these passes, against the 1040-1140 the day's writes
imply), so it is a harsher input for a corrector that reads the register --
the pre-fix corrector's failure here is the same failure as on the bench, but
not a count-for-count reproduction of it. A corrector that sizes from
position deltas (the fix) never reads scaleSpeed, so for it the replay is the
recorded motion, exactly.

SEEN RED 2026-09-19 against 18cf22a (the pre-fix corrector), 2 of 4 failed:
  test_replay_writes_at_most_a_few_times_in_the_steady_part:
    "pass at 311562: 18 steady-part writes [...]" / "assert 18 <= 3"
  test_replay_offset_never_falls_in_the_last_second:
    "pass at 311562: offset fell 11 -> 8 within 1 s of the trigger" / "assert 8 >= 11"
test_replay_offset_at_the_trigger_covers_the_true_rate PASSED on the pre-fix
code with this stand-in input (11, 10, 10 in effect): the noisier stand-in
happened to end each pass on a high write. On the bench the same code had 10,
9, 9 in effect (stopTriggerOffset), 9 being below the 10 this test requires.
After the fix: 5 / 0 / 0 writes from Cut to the stop (1 / 0 / 0 in the steady
part), 10 in effect at every trigger.
"""
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from reflex.fsms.els_overshoot import (
    OffsetWriteLimiter,
    StopOffsetCorrector,
    correction,
)

FIXTURE = Path(__file__).parent / "data" / "els_overshoot_replay_20260919.json"

#: The pass's steady TRUE Z rate: 342 rpm x .040 in/rev x 5080 counts/in / 60.
#: The firmware's trigger snapshot read 1200 on these passes; the position
#: stream sits at ~1156. The table is keyed on the stream (see els_overshoot).
TRUE_RATE = 342 * 0.040 * 5080 / 60                      # 1158.2 counts/s

#: "Steady part" of a pass: from this long after Cut to the stop firing.
#: The acceleration ramp and the window filling are over well inside it.
STEADY_AFTER_CUT_MS = 1500

MAX_STEADY_WRITES = 3


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Tick:
    def __init__(self, board):
        self._b = board

    def enable(self):
        return bool(self._b.els_stop_values["enable"])

    def active(self):
        return bool(self._b.els_stop_values["active"])

    def scale_index(self):
        return int(self._b.els_stop_values["scaleIndex"])


class _Hal:
    def __init__(self, board, clock):
        self.tick = _Tick(board)
        self._clock = clock
        self.writes = []                 # (t_ms, value)

    def set_stop_offset(self, counts):
        self.writes.append((round(self._clock() * 1000), int(counts)))


def _load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _replay():
    """All three passes, in order, through ONE corrector (as on the bench:
    each pass starts from the offset its predecessor was held at)."""
    doc = _load()
    idx = doc["scaleIndex"]
    clk = _Clock()
    board = SimpleNamespace(
        connected=True,
        els_stop_values={"enable": 1, "active": 1, "scaleIndex": idx},
        fast_data_values={"scaleCurrent": [0, 0, 0, 0], "scaleSpeed": [0, 0, 0, 0]},
        connection_manager=SimpleNamespace(connected=True),
    )
    hal = _Hal(board, clk)
    cor = StopOffsetCorrector(hal, board, enabled=lambda: True, margin=lambda: 1,
                              notify=lambda _m: None,
                              limiter=OffsetWriteLimiter(clock=clk))
    results = []
    for p in doc["passes"]:
        n_before = len(hal.writes)
        in_effect_at_fire = None
        prev = None
        for t_ms, active, z in zip(p["t_ms"], p["active"], p["z"]):
            speed = 0.0
            if prev is not None and t_ms > prev[0]:
                speed = (z - prev[1]) * 1000.0 / (t_ms - prev[0])   # stand-in, see module doc
            prev = (t_ms, z)
            clk.t = t_ms / 1000.0
            scales = [0, 0, 0, 0]
            speeds = [0, 0, 0, 0]
            scales[idx] = z
            speeds[idx] = speed
            board.fast_data_values = {"scaleCurrent": scales, "scaleSpeed": speeds}
            board.els_stop_values = {"enable": 1, "active": active, "scaleIndex": idx}
            if t_ms == p["fire_seen_t_ms"]:
                in_effect_at_fire = cor.offset_in_effect   # before this tick's poll
            cor.poll()
        results.append(SimpleNamespace(p=p, writes=hal.writes[n_before:],
                                       in_effect_at_fire=in_effect_at_fire))
    return results


def test_fixture_is_the_recorded_bench_passes():
    """Guard the fixture itself: three passes, each stopped short, each with
    the day's dithering filed against it."""
    doc = _load()
    assert len(doc["passes"]) == 3
    for p in doc["passes"]:
        assert len(p["t_ms"]) == len(p["active"]) == len(p["z"]) > 80
        # stopDirection -1: short of the target means settled Z > stopPosition
        assert p["stopDirection"] == -1
        assert 1 <= p["settledZ"] - p["stopPosition"] <= 2
        assert len(p["recorded_offset_writes"]) >= 15


@pytest.fixture(scope="module")
def replay():
    return _replay()


def test_replay_writes_at_most_a_few_times_in_the_steady_part(replay):
    for r in replay:
        start = r.p["cut_t_ms"] + STEADY_AFTER_CUT_MS
        steady = [w for w in r.writes if start <= w[0] <= r.p["fire_seen_t_ms"]]
        assert len(steady) <= MAX_STEADY_WRITES, (
            f"pass at {r.p['cut_t_ms']}: {len(steady)} steady-part writes {steady}")


def test_replay_offset_at_the_trigger_covers_the_true_rate(replay):
    target = correction(TRUE_RATE).offset
    for r in replay:
        assert r.in_effect_at_fire is not None
        assert r.in_effect_at_fire >= target, (
            f"pass at {r.p['cut_t_ms']}: offset {r.in_effect_at_fire} in effect at "
            f"the trigger, below {target} for the true rate {TRUE_RATE:.0f}")


def test_replay_offset_never_falls_in_the_last_second(replay):
    for r in replay:
        fire = r.p["fire_seen_t_ms"]
        last = [w for w in r.writes if fire - 1000 <= w[0] < fire]
        before = [w for w in r.writes if w[0] < fire - 1000]
        seq = ([before[-1][1]] if before else []) + [v for _, v in last]
        for a, b in zip(seq, seq[1:]):
            assert b >= a, (f"pass at {r.p['cut_t_ms']}: offset fell {a} -> {b} "
                            f"within 1 s of the trigger: {last}")
