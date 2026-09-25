"""Stop-overshoot correction: the table, the sign, the limiter, the wiring.

The correction fires the ELS stop EARLY by stopOffset counts (protocolVersion
11). The carriage settles at target - offset + overshoot, so it lands short only
if offset > overshoot -- test_sign_offset_exceeds_prediction_everywhere is the
test that property lives or dies by. Seen red 2026-09-18, two mutations of
correction(), each reverted: offset = round(predicted * 0.9) failed at the
first table point ("rate 0: assert 0 > 0.0"); ceil(predicted * 0.9) + margin
survived the low points and failed at 1640 counts/s ("assert 12 > 12.0") -- the
snapshot-keyed table of that day; the same point is 1600 on the stream-keyed
table of 2026-09-19, with the same 12 counts.

Steadiness (2026-09-19): the live rate is a position-delta window and the
offset has an asymmetric hold; see the ZRateWindow / OffsetHold sections and
test_els_overshoot_replay for the bench recording that motivated both. Seen
red 2026-09-19 by mutation, each reverted: a symmetric hold (target = wanted)
failed 7 tests here (e.g. test_a_fall_waits_a_second_of_low_rate "assert
[30, 29, 19, 10] == [30]"); dropping the window restart at Cut failed
test_the_retract_is_not_averaged_into_the_next_approach ("assert [30, 44] ==
[30]"); dropping the hold interrupt while held failed
test_held_at_the_shoulder_a_low_stretch_is_interrupted ("assert [30, 10] ==
[30]").
"""
import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from reflex.fsms import els_overshoot as eo
from reflex.fsms.els_overshoot import (
    ABOVE_RANGE_NOTICE,
    DEFAULT_MARGIN_COUNTS,
    ELS_STOP_OFFSET_MAX,
    OVERSHOOT_TABLE,
    OffsetHold,
    OffsetWriteLimiter,
    StopOffsetCorrector,
    ZRateWindow,
    correction,
    offset,
    predicted,
)

REPO = Path(__file__).resolve().parents[3]


# ─── the table ───────────────────────────────────────────────────────────

def test_shipped_table_is_the_decided_envelope():
    """The 2026-09-18 re-derivation, verbatim. A change here is a change to
    where this machine's stop lands, and should cost a deliberate edit.

    Re-keyed 2026-09-19 (Evan) from the trigger-snapshot rate (300, 596, 1187,
    1640, 1692, 2500, 2507, 3567) to the STREAM rate of the same same-speed
    sets, because the live input is now measured that way; the overshoot
    column is unchanged."""
    assert OVERSHOOT_TABLE == ((0, 0), (280, 1), (583, 3), (1185, 9), (1600, 12),
                               (1679, 17), (2430, 29), (2469, 32), (3529, 43))
    assert DEFAULT_MARGIN_COUNTS == 1


def test_table_is_monotonic_and_validated():
    for (r0, v0), (r1, v1) in zip(OVERSHOOT_TABLE, OVERSHOOT_TABLE[1:]):
        assert r1 > r0 and v1 >= v0
    with pytest.raises(ValueError):
        eo._validate(((0, 0), (100, 5), (90, 6)))
    with pytest.raises(ValueError):
        eo._validate(((0, 0), (100, 5), (200, 4)))
    with pytest.raises(ValueError):
        eo._validate(((10, 0), (100, 5)))


def test_offset_ceiling_matches_the_firmware():
    """ELS_STOP_OFFSET_MAX is mirrored, so it is checked against Ramps.h."""
    h = (REPO / "fw" / "Core" / "Inc" / "Ramps.h").read_text(encoding="utf-8")
    m = re.search(r"^#define\s+ELS_STOP_OFFSET_MAX\s+(\d+)\s*$", h, re.M)
    assert m, "ELS_STOP_OFFSET_MAX not found in fw/Core/Inc/Ramps.h"
    assert int(m.group(1)) == ELS_STOP_OFFSET_MAX


# ─── predicted() ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("rate,value", OVERSHOOT_TABLE)
def test_prediction_is_exact_at_every_table_point(rate, value):
    p = predicted(rate)
    assert p.counts == pytest.approx(value)
    assert p.above_range is False


def test_prediction_interpolates_linearly_between_points():
    # halfway between (1185, 9) and (1600, 12)
    assert predicted((1185 + 1600) / 2).counts == pytest.approx(10.5)
    # a quarter of the way between (2469, 32) and (3529, 43)
    assert predicted(2469 + 265).counts == pytest.approx(32 + 11 * 0.25)


def test_prediction_below_the_lowest_point_runs_to_zero():
    assert predicted(0).counts == 0
    assert predicted(140).counts == pytest.approx(0.5)
    assert predicted(28).counts == pytest.approx(0.1)


def test_prediction_uses_the_magnitude_of_the_rate():
    """Either approach direction: the coast depends on speed, not sign."""
    for r in (0, 140, 1185, 2000, 3529, 9000):
        assert predicted(-r) == predicted(r)


def test_above_the_top_point_the_prediction_is_held_and_flagged():
    for r in (3530, 5000, 40000):
        p = predicted(r)
        assert p.counts == 43
        assert p.above_range is True
    assert predicted(3529).above_range is False


# ─── offset() / correction() ─────────────────────────────────────────────

def test_offset_is_ceil_prediction_plus_margin():
    assert offset(1185) == 9 + 1
    assert offset((1185 + 1600) / 2) == math.ceil(10.5) + 1
    assert offset(140) == 1 + 1                     # ceil(0.5) + 1
    assert offset(0) == 0 + 1
    assert offset(9000) == 43 + 1                   # held top value + margin
    assert correction(1185, margin=3).offset == 12
    assert correction(1185, margin=0).offset == 9


def test_the_bench_true_rate_gets_ten():
    """2026-09-19 bench: 342 rpm x .040 in/rev = ~1158 counts/s true. On the
    stream-keyed table that is 8.7 predicted, 10 written -- the offset the
    replay test (test_els_overshoot_replay) requires at the trigger."""
    rate = 342 * 0.040 * 5080 / 60
    assert predicted(rate).counts == pytest.approx(8.73, abs=0.01)
    assert offset(rate) == 10


def test_negative_margin_is_floored_at_zero():
    """A negative margin would aim below the prediction -- past the target."""
    assert correction(1185, margin=-5).offset == 9


def test_offset_is_clamped_to_the_firmware_ceiling():
    assert correction(3529, margin=10_000).offset == ELS_STOP_OFFSET_MAX


def test_sign_offset_exceeds_prediction_everywhere():
    """THE SIGN. The carriage settles at target - offset + overshoot, so it
    lands SHORT only if offset > overshoot. At every table point and across a
    dense sweep (including above range), with the shipped margin, the offset
    must strictly exceed the prediction. An offset scaled to ~90% of the
    prediction -- the superseded first draft -- fails this."""
    for rate, _ in OVERSHOOT_TABLE:
        assert offset(rate) > predicted(rate).counts, f"rate {rate}"
    for rate in range(0, 6000, 7):
        assert offset(rate) > predicted(rate).counts, f"rate {rate}"


# ─── the write limiter ───────────────────────────────────────────────────

class _Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def test_limiter_first_value_always_goes_out():
    lim = OffsetWriteLimiter(clock=_Clock())
    assert lim.update(10) == 10
    assert lim.last_written == 10


def test_limiter_writes_nothing_at_a_steady_rate():
    clk = _Clock()
    lim = OffsetWriteLimiter(clock=clk)
    lim.update(10)
    for _ in range(100):
        clk.t += 1 / 30
        assert lim.update(10) is None


def test_limiter_writes_a_one_count_change_once_the_interval_has_passed():
    clk = _Clock()
    lim = OffsetWriteLimiter(clock=clk)
    lim.update(10)
    # binary-exact steps, so the boundary is tested and not float noise
    clk.t += 0.125
    assert lim.update(11) is None, "inside 250 ms: held"
    clk.t += 0.0625
    assert lim.update(11) is None, "187.5 ms: still held"
    clk.t += 0.0625
    assert lim.update(11) == 11, "250 ms and a 1-count change: written"
    assert lim.last_written == 11


def test_limiter_respects_the_interval_even_for_big_jumps():
    clk = _Clock()
    lim = OffsetWriteLimiter(clock=clk)
    lim.update(2)
    clk.t += 0.1875
    assert lim.update(44) is None
    clk.t += 0.0625
    assert lim.update(44) == 44


def test_limiter_constants_are_the_decided_ones():
    assert eo.WRITE_MIN_INTERVAL_S == 0.25
    assert eo.WRITE_MIN_DELTA_COUNTS == 1
    lim = OffsetWriteLimiter()
    assert lim.min_interval_s == 0.25 and lim.min_delta_counts == 1


def test_force_zero_writes_zero_once_regardless_of_interval():
    clk = _Clock()
    lim = OffsetWriteLimiter(clock=clk)
    lim.update(30)
    assert lim.force_zero() == 0, "not rate limited: off must take effect now"
    for _ in range(10):
        clk.t += 1
        assert lim.force_zero() is None, "and only once"


def test_force_zero_from_unknown_writes_once():
    """A register left nonzero by a previous UI session (the firmware outlives
    UI restarts) must be cleared, not trusted to already be 0."""
    lim = OffsetWriteLimiter(clock=_Clock())
    assert lim.force_zero() == 0
    assert lim.force_zero() is None


def test_reset_forgets_the_last_write():
    lim = OffsetWriteLimiter(clock=_Clock())
    lim.update(10)
    lim.reset()
    assert lim.last_written is None
    assert lim.update(10) == 10


# ─── the live rate window (ZRateWindow) ──────────────────────────────────

def test_rate_window_needs_the_minimum_span():
    w = ZRateWindow()
    assert w.rate() is None
    w.add(0.0, 0)
    assert w.rate() is None, "one sample is no rate"
    w.add(0.125, 150)
    assert w.rate() is None, "125 ms < RATE_MIN_SPAN_S: a short delta is noise"
    w.add(0.25, 300)
    assert w.rate() == pytest.approx(1200)


def test_rate_window_is_the_position_delta_over_about_half_a_second():
    w = ZRateWindow()
    for i in range(40):                       # 1.25 s at 1152 counts/s, 1/32 s ticks
        w.add(i / 32, 36 * i)
    span = w._samples[-1][0] - w._samples[0][0]
    assert span == eo.RATE_WINDOW_S, "the shortest tail that spans the window"
    assert w.rate() == pytest.approx(1152)
    # A 20-count wobble in one tick is 640 counts/s to a single-tick delta;
    # across the window it is 40.
    w.add(40 / 32, 36 * 40 + 20)
    assert w.rate() == pytest.approx(1152 + 40)


def test_rate_window_is_signed_and_restarts_across_a_gap():
    w = ZRateWindow()
    for i in range(10):
        w.add(i * 0.05, -60 * i)
    assert w.rate() == pytest.approx(-1200)
    w.add(0.45 + eo.RATE_MAX_GAP_S + 0.01, 0)
    assert w.rate() is None, "a gap starts the window over"


def test_rate_window_ignores_a_repeated_instant():
    w = ZRateWindow()
    w.add(0.0, 0)
    w.add(0.3, 360)
    w.add(0.3, 9999)
    assert w.rate() == pytest.approx(1200)


def test_rate_window_constants_are_the_decided_ones():
    assert eo.RATE_WINDOW_S == 0.5
    assert eo.FALL_HOLD_S == 1.0


# ─── the asymmetric hold (OffsetHold) ────────────────────────────────────

def test_hold_rises_immediately():
    h = OffsetHold()
    assert h.update(10, 0.0) == 10
    assert h.update(11, 0.01) == 11, "bigger = lands shorter = safe: no wait"
    assert h.update(30, 0.02) == 30


def test_hold_falls_only_after_a_second_low_continuously():
    h = OffsetHold()
    h.update(11, 0.0)
    for i in range(1, 32):                        # 0.03125 .. 0.96875 s
        assert h.update(10, i / 32) == 11, f"{i / 32:.3f} s low: held"
    assert h.update(10, 1.0) == 10, "1 s low: falls"


def test_hold_wobble_back_up_restarts_the_second():
    h = OffsetHold()
    h.update(11, 0.0)
    h.update(10, 0.25)
    assert h.update(11, 0.75) == 11                # back up: the second restarts
    assert h.update(10, 1.5) == 11
    assert h.update(10, 1.625) == 11
    assert h.update(10, 1.75) == 10, "1 s after 11 was last wanted"


def test_hold_falls_to_the_largest_of_the_last_second_not_a_dip():
    h = OffsetHold()
    h.update(30, 0.0)
    h.update(12, 0.25)
    h.update(5, 0.5)                               # a momentary dip
    h.update(10, 0.75)
    assert h.update(9, 1.0) == 12, "30 has aged out; 12 has not"
    assert h.update(9, 1.25) == 10, "then 12 ages out; the dip to 5 never shows"
    assert h.update(9, 1.75) == 9


def test_hold_interrupt_keeps_the_target_and_restarts_its_second():
    h = OffsetHold()
    h.update(30, 0.0)
    h.update(10, 0.5)
    h.interrupt()
    assert h.target == 30
    assert h.update(10, 5.0) == 30, "time held is not time low"
    assert h.update(10, 5.875) == 30
    assert h.update(10, 6.0) == 10


def test_hold_reset_forgets():
    h = OffsetHold()
    h.update(30, 0.0)
    h.reset()
    assert h.target is None
    assert h.update(10, 0.25) == 10


# ─── the wiring (StopOffsetCorrector against a recording HAL) ────────────

class _Tick:
    def __init__(self, board):
        self._board = board

    def enable(self):
        return bool(self._board.els_stop_values.get("enable", 0))

    def active(self):
        return bool(self._board.els_stop_values.get("active", 0))

    def scale_index(self):
        return int(self._board.els_stop_values.get("scaleIndex", 0))


class _Hal:
    def __init__(self, board):
        self.tick = _Tick(board)
        self.writes = []

    def set_stop_offset(self, counts):
        self.writes.append(("stopOffset", counts))

    def set_stop_position(self, counts):          # must never be called
        self.writes.append(("stopPosition", counts))


#: Ticks at 30 Hz, and the ticks after which the rate window first spans
#: RATE_MIN_SPAN_S (8 intervals = 267 ms). Rates in these tests are whole
#: counts per tick (x30) and sit away from table points, so integer positions
#: give exactly the rate meant and no ceil() lands on a boundary.
DT = 1 / 30
FILL = 9


def _rig(enabled=True, margin=1, **snap):
    clk = _Clock()
    board = SimpleNamespace(
        connected=True,
        els_stop_values={"enable": 1, "active": 0, "scaleIndex": 1, **snap},
        fast_data_values={"scaleCurrent": [0, 0, 0, 0]},
        connection_manager=SimpleNamespace(connected=True),
    )
    hal = _Hal(board)
    notices = []
    settings = {"enabled": enabled, "margin": margin}
    cor = StopOffsetCorrector(
        hal, board,
        enabled=lambda: settings["enabled"],
        margin=lambda: settings["margin"],
        notify=notices.append,
        limiter=OffsetWriteLimiter(clock=clk),
    )
    return SimpleNamespace(cor=cor, hal=hal, board=board, clk=clk,
                           notices=notices, settings=settings,
                           z=[0.0, 0.0, 0.0, 0.0], v=[0.0, 0.0, 0.0, 0.0])


def _ticks(r, n, rate=None, dt=DT, rates=None, **snap):
    """n board ticks. `rate` is scale 1's velocity (counts/s) from now on;
    `rates` sets all four. Each tick the positions advance by velocity x dt
    and the corrector sees them as fastData.scaleCurrent."""
    if rate is not None:
        r.v[1] = float(rate)
    if rates is not None:
        r.v = [float(x) for x in rates]
    for _ in range(n):
        r.board.els_stop_values.update(snap)
        r.board.fast_data_values = {"scaleCurrent": [int(round(z)) for z in r.z]}
        r.cor.poll()
        r.clk.t += dt
        r.z = [z + v * dt for z, v in zip(r.z, r.v)]


def _offsets(r):
    return [v for k, v in r.hal.writes]


def test_disabled_writes_zero_once_and_nothing_else():
    r = _rig(enabled=False)
    _ticks(r, 60, rate=3000)
    assert r.hal.writes == [("stopOffset", 0)]


def test_enabled_and_armed_writes_the_offset_for_the_live_z_rate():
    """1170 counts/s: 8.85 predicted, 10 written -- once the window spans
    RATE_MIN_SPAN_S, and not before (a two-tick delta is the noise the
    2026-09-19 bench dithered on)."""
    r = _rig()
    _ticks(r, FILL - 1, rate=1170)
    assert r.hal.writes == []
    _ticks(r, 1)
    assert r.hal.writes == [("stopOffset", 10)]
    assert r.cor.offset_in_effect == 10
    assert r.cor.last_rate == pytest.approx(1170)


def test_the_rate_is_measured_from_positions_not_the_speed_register():
    """The single-tick scaleSpeed register is what dithered. It is ignored:
    a board whose register says 3600 while the scale position is still is
    sized at rest."""
    r = _rig()
    for _ in range(FILL):
        r.board.fast_data_values = {"scaleCurrent": [0, 500, 0, 0],
                                    "scaleSpeed": [0, 3600, 0, 0]}
        r.cor.poll()
        r.clk.t += DT
    assert r.hal.writes == [("stopOffset", 1)]


def test_reads_the_rate_of_the_scale_the_firmware_compares():
    r = _rig(scaleIndex=2)
    _ticks(r, FILL, rates=[0, 3600, 1170, 0])
    assert r.hal.writes == [("stopOffset", 10)], "scaleIndex 2's rate, not 1's"


def test_a_scale_index_change_restarts_the_window():
    r = _rig()
    _ticks(r, FILL, rates=[0, 1170, 90000, 0])
    assert _offsets(r) == [10]
    r.board.els_stop_values["scaleIndex"] = 2
    _ticks(r, 1)
    assert r.cor.last_rate is None, "no delta across two scales"


def test_steady_rate_costs_no_exchanges_after_the_first():
    r = _rig()
    _ticks(r, 90, rate=-2400)                     # 3 s, approach in -Z
    assert r.hal.writes == [("stopOffset", 30)]


def test_a_wandering_rate_is_written_once():
    """The 2026-09-19 shape in miniature: the position advances by a
    wandering 36..42 counts a tick (1080..1260 counts/s tick to tick, mean
    1170) for 5 s. The window rate stays near 1170, and the hold swallows any
    brush with the next count up: one write, maybe two -- not one every
    270 ms."""
    r = _rig()
    wobble = [36, 42, 39, 41, 37, 40, 38, 42, 36, 39]
    for i in range(150):
        r.board.fast_data_values = {"scaleCurrent": [0, int(r.z[1]), 0, 0]}
        r.cor.poll()
        r.clk.t += DT
        r.z[1] -= wobble[i % len(wobble)]
    assert 1 <= len(r.hal.writes) <= 2, r.hal.writes
    assert r.hal.writes[0] == ("stopOffset", 10)


def test_a_rise_is_taken_at_once_through_the_limiter():
    """Rise immediately: a faster approach needs a bigger offset now. The
    limiter still spaces the writes 250 ms apart."""
    r = _rig()
    _ticks(r, 15, rate=1170)
    assert _offsets(r) == [10]
    n = len(r.hal.writes)
    _ticks(r, 30, rate=3600)                       # 1 s at a rate above the table
    assert _offsets(r)[-1] == 44
    ups = _offsets(r)[n - 1:]
    assert ups == sorted(ups), "rising only, never a dip on the way up"
    assert len(r.hal.writes) - n <= 4, "<= one write per 250 ms"


def test_a_fall_waits_a_second_of_low_rate():
    """Fall after ~1 s: a slower approach is written only once the low rate
    has held for FALL_HOLD_S, continuously."""
    r = _rig()
    _ticks(r, 30, rate=2400)
    assert _offsets(r) == [30]
    _ticks(r, 30, rate=1170)                       # 1 s slower
    assert _offsets(r) == [30], "not yet: 30 was wanted less than 1 s ago"
    _ticks(r, 30)
    down = _offsets(r)[1:]
    assert down and down[-1] == 10, down
    assert down == sorted(down, reverse=True) and min(down) == 10, (
        "steps down with the window, never below the new rate's offset")
    assert len(down) <= 4


def test_writes_are_bounded_to_four_a_second_on_a_ramping_rate():
    r = _rig()
    for i in range(30):                            # 1 s, rate climbing every tick
        _ticks(r, 1, rate=100 * i)
    assert len(r.hal.writes) <= 5                  # first + one per 250 ms


def test_turning_the_correction_off_writes_zero_once():
    r = _rig()
    _ticks(r, FILL + 1, rate=2400)
    r.settings["enabled"] = False
    _ticks(r, 30, rate=2400)
    assert r.hal.writes == [("stopOffset", 30), ("stopOffset", 0)]


def test_disarm_writes_zero_once_and_rearm_resumes():
    r = _rig()
    _ticks(r, FILL + 1, rate=2400)
    _ticks(r, 30, rate=2400, enable=0)
    assert r.hal.writes == [("stopOffset", 30), ("stopOffset", 0)]
    _ticks(r, 1, rate=2400, enable=1)
    assert r.hal.writes[-1] == ("stopOffset", 30)


def test_disarm_forgets_the_held_offset():
    """Zero on disarm resets the hold: a re-armed, slower pass is written at
    once, not held at the old pass's offset for a second."""
    r = _rig()
    _ticks(r, 30, rate=2400)
    _ticks(r, 10, enable=0)
    _ticks(r, 30, rate=1170)                       # disarmed while the rate settles
    _ticks(r, 1, enable=1)
    assert _offsets(r) == [30, 0, 10]


def test_stop_position_is_never_written():
    r = _rig()
    _ticks(r, 10, rate=1020)
    _ticks(r, 10, rate=3000, enable=0)
    r.settings["enabled"] = False
    _ticks(r, 10, rate=3000, enable=1)
    assert all(k == "stopOffset" for k, _ in r.hal.writes)


def test_no_snapshot_no_write():
    r = _rig()
    r.board.els_stop_values = {}
    for i in range(20):
        r.board.fast_data_values = {"scaleCurrent": [0, 100 * i, 0, 0]}
        r.cor.poll()
        r.clk.t += DT
    assert r.hal.writes == []


def test_disconnected_no_write():
    r = _rig()
    r.board.connected = False
    _ticks(r, 20, rate=3000)
    assert r.hal.writes == []


def test_reset_re_sends_after_a_reconnect():
    r = _rig()
    _ticks(r, FILL + 2, rate=1170)
    r.cor.reset()
    _ticks(r, FILL, rate=1170)
    assert r.hal.writes == [("stopOffset", 10), ("stopOffset", 10)]


def test_an_unacknowledged_write_is_forgotten():
    r = _rig()
    r.board.connection_manager.connected = False  # the write helper dropped the link
    _ticks(r, FILL, rate=1170)
    assert r.cor.offset_in_effect is None
    r.board.connection_manager.connected = True
    _ticks(r, 1, rate=1170)
    assert r.cor.offset_in_effect == 10


def test_above_range_posts_one_notice_per_pass():
    r = _rig()
    _ticks(r, 60, rate=5010)
    assert r.notices == [ABOVE_RANGE_NOTICE.format(top=3529)]
    assert "3529" in r.notices[0] and "held" in r.notices[0]
    assert r.hal.writes == [("stopOffset", 44)]
    # stop fires, carriage held at the shoulder, then the next pass
    _ticks(r, 10, rate=0, active=1)
    _ticks(r, 30, rate=5010, active=0)
    assert len(r.notices) == 2, "a new pass earns its own notice"


def test_held_at_the_shoulder_the_offset_is_held():
    """active == 1: the firmware cannot fire, and the carriage RETRACTS here at
    rapid speed. Following that rate would spend exchanges on a register the
    ISR ignores and post an above-range notice on every retract."""
    r = _rig()
    _ticks(r, 30, rate=2400)                          # approach: 30
    _ticks(r, 60, rate=12000, active=1)               # stop fired; rapid retract
    assert r.hal.writes == [("stopOffset", 30)]
    assert r.notices == []


def test_after_cut_a_slower_pass_falls_only_after_the_hold():
    """CHANGED 2026-09-19 (was: the live rate takes over on the first tick
    after Cut, writing 1 at rest). With the asymmetric hold the next pass
    starts at its predecessor's offset and falls only after the rate has been
    low for FALL_HOLD_S -- on the bench that is what kept every Cut from
    writing a 1 and then climbing back through 3, 10, 11 in the first
    second. Here the carriage stays at rest after Cut: the window needs
    RATE_MIN_SPAN_S, then one second low, then the fall."""
    r = _rig()
    _ticks(r, 30, rate=2400)
    _ticks(r, 20, rate=12000, active=1)
    _ticks(r, FILL + 27, rate=0, active=0)            # 0.27 s + 0.9 s at rest
    assert _offsets(r) == [30]
    _ticks(r, 6)
    assert _offsets(r) == [30, 1]


def test_the_retract_is_not_averaged_into_the_next_approach():
    """Cut restarts the window. A rapid retract that ended the tick before
    Cut must not read as a fast approach (44 and a notice) after it."""
    r = _rig()
    _ticks(r, 30, rate=2400)
    _ticks(r, 20, rate=-12000, active=1)              # retract right up to Cut
    _ticks(r, 30, rate=2400, active=0)
    assert _offsets(r) == [30]
    assert r.notices == []


def test_held_at_the_shoulder_a_low_stretch_is_interrupted():
    """A slow-down seen just before the stop fired does not carry through the
    shoulder: time held is not time low."""
    r = _rig()
    _ticks(r, 30, rate=2400)
    _ticks(r, 20, rate=1170)                          # low, but < 1 s of it
    _ticks(r, 60, rate=0, active=1)                   # 2 s at the shoulder
    _ticks(r, FILL + 3, rate=1170, active=0)
    assert _offsets(r) == [30]


def test_held_state_still_overwrites_an_unknown_register_once():
    """Armed-idle (active == 1) right after connect: whatever a previous
    session left in stopOffset is overwritten once, then held."""
    r = _rig(active=1)
    _ticks(r, 30, rate=0)
    assert r.hal.writes == [("stopOffset", 1)]


def test_no_notice_within_range():
    r = _rig()
    _ticks(r, 60, rate=3510)
    assert r.notices == []


# ─── the real HAL: set_stop_offset is its own register ───────────────────

class _Recording(dict):
    def __init__(self):
        super().__init__()
        self.assignments = []

    def __setitem__(self, key, value):
        self.assignments.append((key, value))
        super().__setitem__(key, value)


def test_hal_set_stop_offset_writes_only_stopOffset():
    from reflex.fsms.els_stop_hal import ElsStopHal
    els = _Recording()
    board = SimpleNamespace(connected=True, device={"elsStop": els},
                            els_stop_values={"enable": 1, "active": 0,
                                             "scaleIndex": 1},
                            fast_data_values={"scaleCurrent": [0, 0, 0, 0]},
                            connection_manager=SimpleNamespace(connected=True))
    hal = ElsStopHal(board)
    clk = _Clock()
    cor = StopOffsetCorrector(hal, board, enabled=lambda: True, margin=lambda: 1,
                              notify=lambda _m: None,
                              limiter=OffsetWriteLimiter(clock=clk))
    for i in range(FILL):
        board.fast_data_values = {"scaleCurrent": [0, 39 * i, 0, 0]}   # 1170 counts/s
        cor.poll()
        clk.t += DT
    assert els.assignments == [("stopOffset", 10)]
    assert hal.tick.scale_index() == 1


def test_hal_set_stop_offset_is_silent_when_disconnected():
    from reflex.fsms.els_stop_hal import ElsStopHal
    els = _Recording()
    hal = ElsStopHal(SimpleNamespace(connected=False, device={"elsStop": els}))
    hal.set_stop_offset(12)
    assert els.assignments == []


# ─── the setting ─────────────────────────────────────────────────────────

def test_setting_defaults_off_with_a_one_count_margin():
    from reflex.dispatchers.els import ElsDispatcher
    assert ElsDispatcher.els_overshoot_correction.defaultvalue is False
    assert ElsDispatcher.els_overshoot_margin_counts.defaultvalue == 1


# ─── the controller: bound, default off, and filed by the flight recorder ─

def test_controller_polls_the_corrector_and_the_recorder_files_its_offset():
    import os
    os.environ.setdefault("KIVY_WINDOW", "mock")
    os.environ.setdefault("KIVY_GL_BACKEND", "mock")
    from reflex.fsms.ui_controller import ElsUiController
    from tests.fsms.test_ui_controller import (_make_collaborators, _make_x_axis,
                                               _make_z_axis, _pump)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=_make_x_axis())
    ctrl = ElsUiController(els=els, board=board)
    _pump()
    rec = _Recording()
    board.device = {"elsStop": rec}
    board.connected = True
    board.els_stop_values = {"enable": 1, "active": 0, "scaleIndex": 1}
    clk = _Clock(1e9)
    ctrl.stop_offset_corrector.limiter._clock = clk

    def tick(i):
        board.fast_data_values = {"scaleCurrent": [0, 120 * i, 0, 0]}   # 3600 counts/s
        ctrl._poll_stop_offset()
        clk.t += DT

    # The collaborator is a MagicMock, whose attributes are truthy but not
    # True: the correction must stay OFF (and clear the register once).
    tick(0)
    tick(1)
    assert rec.assignments == [("stopOffset", 0)]

    els.els_overshoot_correction = True
    els.els_overshoot_margin_counts = 1
    for i in range(2, 2 + FILL):
        tick(i)
    assert rec.assignments[-1] == ("stopOffset", 44)
    assert all(k == "stopOffset" for k, _ in rec.assignments)
    assert ctrl._flight_recorder._stop_offset() == 44
