"""Stop-overshoot correction: the table, the sign, the limiter, the wiring.

The correction fires the ELS stop EARLY by stopOffset counts (protocolVersion
11). The carriage settles at target - offset + overshoot, so it lands short only
if offset > overshoot -- test_sign_offset_exceeds_prediction_everywhere is the
test that property lives or dies by. Seen red 2026-09-18, two mutations of
correction(), each reverted: offset = round(predicted * 0.9) failed at the
first table point ("rate 0: assert 0 > 0.0"); ceil(predicted * 0.9) + margin
survived the low points and failed at 1640 counts/s ("assert 12 > 12.0").
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
    OffsetWriteLimiter,
    StopOffsetCorrector,
    correction,
    offset,
    predicted,
)

REPO = Path(__file__).resolve().parents[3]


# ─── the table ───────────────────────────────────────────────────────────

def test_shipped_table_is_the_decided_envelope():
    """The 2026-09-18 re-derivation, verbatim. A change here is a change to
    where this machine's stop lands, and should cost a deliberate edit."""
    assert OVERSHOOT_TABLE == ((0, 0), (300, 1), (596, 3), (1187, 9), (1640, 12),
                               (1692, 17), (2500, 29), (2507, 32), (3567, 43))
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
    # halfway between (1187, 9) and (1640, 12)
    assert predicted((1187 + 1640) / 2).counts == pytest.approx(10.5)
    # a quarter of the way between (2507, 32) and (3567, 43)
    assert predicted(2507 + 265).counts == pytest.approx(32 + 11 * 0.25)


def test_prediction_below_the_lowest_point_runs_to_zero():
    assert predicted(0).counts == 0
    assert predicted(150).counts == pytest.approx(0.5)
    assert predicted(30).counts == pytest.approx(0.1)


def test_prediction_uses_the_magnitude_of_the_rate():
    """Either approach direction: the coast depends on speed, not sign."""
    for r in (0, 150, 1187, 2000, 3567, 9000):
        assert predicted(-r) == predicted(r)


def test_above_the_top_point_the_prediction_is_held_and_flagged():
    for r in (3568, 5000, 40000):
        p = predicted(r)
        assert p.counts == 43
        assert p.above_range is True
    assert predicted(3567).above_range is False


# ─── offset() / correction() ─────────────────────────────────────────────

def test_offset_is_ceil_prediction_plus_margin():
    assert offset(1187) == 9 + 1
    assert offset((1187 + 1640) / 2) == math.ceil(10.5) + 1
    assert offset(150) == 1 + 1                     # ceil(0.5) + 1
    assert offset(0) == 0 + 1
    assert offset(9000) == 43 + 1                   # held top value + margin
    assert correction(1187, margin=3).offset == 12
    assert correction(1187, margin=0).offset == 9


def test_negative_margin_is_floored_at_zero():
    """A negative margin would aim below the prediction -- past the target."""
    assert correction(1187, margin=-5).offset == 9


def test_offset_is_clamped_to_the_firmware_ceiling():
    assert correction(3567, margin=10_000).offset == ELS_STOP_OFFSET_MAX


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


def _rig(enabled=True, margin=1, **snap):
    clk = _Clock()
    board = SimpleNamespace(
        connected=True,
        els_stop_values={"enable": 1, "active": 0, "scaleIndex": 1, **snap},
        fast_data_values={"scaleSpeed": [0, 0, 0, 0]},
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
                           notices=notices, settings=settings)


def _ticks(r, n, rate=None, dt=1 / 30, **snap):
    for _ in range(n):
        if rate is not None:
            r.board.fast_data_values = {"scaleSpeed": [0, rate, 0, 0]}
        r.board.els_stop_values.update(snap)
        r.cor.poll()
        r.clk.t += dt


def test_disabled_writes_zero_once_and_nothing_else():
    r = _rig(enabled=False)
    _ticks(r, 60, rate=3000)
    assert r.hal.writes == [("stopOffset", 0)]


def test_enabled_and_armed_writes_the_offset_for_the_live_z_rate():
    r = _rig()
    _ticks(r, 1, rate=1187)
    assert r.hal.writes == [("stopOffset", 10)]
    assert r.cor.offset_in_effect == 10


def test_reads_the_rate_of_the_scale_the_firmware_compares():
    r = _rig(scaleIndex=2)
    r.board.fast_data_values = {"scaleSpeed": [0, 3567, 1187, 0]}
    r.cor.poll()
    assert r.hal.writes == [("stopOffset", 10)], "scaleIndex 2's rate, not 1's"


def test_steady_rate_costs_no_exchanges_after_the_first():
    r = _rig()
    _ticks(r, 90, rate=-2500)                     # 3 s, approach in -Z
    assert r.hal.writes == [("stopOffset", 30)]


def test_rate_change_is_written_through_the_limiter():
    r = _rig()
    _ticks(r, 1, rate=1187)                       # 10
    _ticks(r, 3, rate=3567)                       # 100 ms: held
    assert r.hal.writes == [("stopOffset", 10)]
    _ticks(r, 6, rate=3567)                       # past 250 ms
    assert r.hal.writes == [("stopOffset", 10), ("stopOffset", 44)]


def test_writes_are_bounded_to_four_a_second_on_a_ramping_rate():
    r = _rig()
    for i in range(30):                            # 1 s, rate climbing every tick
        _ticks(r, 1, rate=100 * i)
    assert len(r.hal.writes) <= 5                  # first + one per 250 ms


def test_turning_the_correction_off_writes_zero_once():
    r = _rig()
    _ticks(r, 5, rate=2500)
    r.settings["enabled"] = False
    _ticks(r, 30, rate=2500)
    assert r.hal.writes == [("stopOffset", 30), ("stopOffset", 0)]


def test_disarm_writes_zero_once_and_rearm_resumes():
    r = _rig()
    _ticks(r, 5, rate=2500)
    _ticks(r, 30, rate=2500, enable=0)
    assert r.hal.writes == [("stopOffset", 30), ("stopOffset", 0)]
    _ticks(r, 1, rate=2500, enable=1)
    assert r.hal.writes[-1] == ("stopOffset", 30)


def test_stop_position_is_never_written():
    r = _rig()
    _ticks(r, 10, rate=1000)
    _ticks(r, 10, rate=3000, enable=0)
    r.settings["enabled"] = False
    _ticks(r, 10, rate=3000, enable=1)
    assert all(k == "stopOffset" for k, _ in r.hal.writes)


def test_no_snapshot_no_write():
    r = _rig()
    r.board.els_stop_values = {}
    r.board.fast_data_values = {"scaleSpeed": [0, 3000, 0, 0]}
    for _ in range(10):
        r.cor.poll()
    assert r.hal.writes == []


def test_disconnected_no_write():
    r = _rig()
    r.board.connected = False
    _ticks(r, 10, rate=3000)
    assert r.hal.writes == []


def test_reset_re_sends_after_a_reconnect():
    r = _rig()
    _ticks(r, 3, rate=1187)
    r.cor.reset()
    _ticks(r, 1, rate=1187)
    assert r.hal.writes == [("stopOffset", 10), ("stopOffset", 10)]


def test_an_unacknowledged_write_is_forgotten():
    r = _rig()
    r.board.connection_manager.connected = False  # the write helper dropped the link
    _ticks(r, 1, rate=1187)
    assert r.cor.offset_in_effect is None
    r.board.connection_manager.connected = True
    _ticks(r, 1, rate=1187)
    assert r.cor.offset_in_effect == 10


def test_above_range_posts_one_notice_per_pass():
    r = _rig()
    _ticks(r, 60, rate=5000)
    assert r.notices == [ABOVE_RANGE_NOTICE.format(top=3567)]
    assert "3567" in r.notices[0] and "held" in r.notices[0]
    assert r.hal.writes == [("stopOffset", 44)]
    # stop fires, carriage held at the shoulder, then the next pass
    _ticks(r, 10, rate=0, active=1)
    _ticks(r, 30, rate=5000, active=0)
    assert len(r.notices) == 2, "a new pass earns its own notice"


def test_held_at_the_shoulder_the_offset_is_held():
    """active == 1: the firmware cannot fire, and the carriage RETRACTS here at
    rapid speed. Following that rate would spend exchanges on a register the
    ISR ignores and post an above-range notice on every retract."""
    r = _rig()
    _ticks(r, 30, rate=2500)                          # approach: 30
    _ticks(r, 60, rate=-12000, active=1)              # stop fired; rapid retract
    assert r.hal.writes == [("stopOffset", 30)]
    assert r.notices == []
    _ticks(r, 1, rate=0, active=0)                    # Cut: live rate takes over
    assert r.hal.writes[-1] == ("stopOffset", 1)


def test_held_state_still_overwrites_an_unknown_register_once():
    """Armed-idle (active == 1) right after connect: whatever a previous
    session left in stopOffset is overwritten once, then held."""
    r = _rig(active=1)
    _ticks(r, 30, rate=0)
    assert r.hal.writes == [("stopOffset", 1)]


def test_no_notice_within_range():
    r = _rig()
    _ticks(r, 60, rate=3567)
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
                            fast_data_values={"scaleSpeed": [0, 1187, 0, 0]},
                            connection_manager=SimpleNamespace(connected=True))
    hal = ElsStopHal(board)
    cor = StopOffsetCorrector(hal, board, enabled=lambda: True, margin=lambda: 1,
                              notify=lambda _m: None,
                              limiter=OffsetWriteLimiter(clock=_Clock()))
    cor.poll()
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
    board.fast_data_values = {"scaleSpeed": [0, 3567, 0, 0]}

    # The collaborator is a MagicMock, whose attributes are truthy but not
    # True: the correction must stay OFF (and clear the register once).
    ctrl._poll_stop_offset()
    ctrl._poll_stop_offset()
    assert rec.assignments == [("stopOffset", 0)]

    els.els_overshoot_correction = True
    els.els_overshoot_margin_counts = 1
    ctrl.stop_offset_corrector.limiter._clock = lambda: 1e9   # past the interval
    ctrl._poll_stop_offset()
    assert rec.assignments[-1] == ("stopOffset", 44)
    assert all(k == "stopOffset" for k, _ in rec.assignments)
    assert ctrl._flight_recorder._stop_offset() == 44
