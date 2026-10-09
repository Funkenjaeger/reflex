"""CouplingRatioCheck (reflex/fsms/els_ratio.py) on synthetic passes.

The thresholds themselves were chosen by replaying 205 recorded passes outside
the repo (the recordings are not public); these tests pin the RULES: what each
verdict needs, what it ignores, and what can never happen.
"""
import pytest

from reflex.fsms import els_ratio as R

K = 12800.0 / 5080.0          # elspi: steps per Z count in gear A
MM_PER_COUNT = 0.005
POLL_MS = 40


def _check(gear="A"):
    k = K / {"A": 1.0, "B": 2.0, "C": 0.5}[gear]
    chk = R.CouplingRatioCheck(steps_per_count=k, gear=gear,
                               mm_per_count=MM_PER_COUNT)
    chk.start_pass()
    return chk


def _run(chk, ratio, *, polls=60, steps_per_poll=120, start_polls=0,
         takeup_polls=0, correction_polls=0, ratio_at=None):
    """Feed a pass. `ratio` is the absolute coupling (reference terms) the
    carriage really has; ratio_at(i) overrides it per poll. Returns the
    verdict after every poll."""
    servo, z, zf = 0, 0, 0.0
    out = []
    t = 0.0
    for i in range(start_polls + takeup_polls + correction_polls + polls):
        takeup = i < takeup_polls
        stg = 50 if takeup_polls <= i < takeup_polls + correction_polls else 0
        servo += steps_per_poll
        r = ratio_at(i) if ratio_at else ratio
        zf += steps_per_poll * r / K
        z = int(round(zf))
        out.append(chk.feed(t_ms=t, servo_steps=servo, z_counts=z, cutting=True,
                            takeup_pending=takeup, steps_to_go=stg))
        t += POLL_MS
    return out


def test_nearest_gear():
    assert R.nearest_gear(1.03) == ("A", pytest.approx(0.03))
    assert R.nearest_gear(1.9)[0] == "B"
    assert R.nearest_gear(0.52)[0] == "C"
    assert R.nearest_gear(0.0)[0] == "C"


def test_a_coupled_pass_reads_ok_and_never_aborts():
    vs = _run(_check("A"), 1.0)
    assert not any(v.kind in R.TERMINAL for v in vs)
    assert vs[-1].kind == R.OK


@pytest.mark.parametrize("real, expect", [(2.0, "B"), (0.5, "C")])
def test_a_moved_lever_is_a_confident_gear_change(real, expect):
    v = _run(_check("A"), real)[-1]
    assert v.kind == R.GEAR_CHANGE and v.gear == expect


def test_from_c_a_lever_in_b_reads_as_b():
    v = _run(_check("C"), 2.0)[-1]
    assert v.kind == R.GEAR_CHANGE and v.gear == "B"


@pytest.mark.parametrize("gear, real", [("B", 2.0), ("C", 0.5)])
def test_after_adoption_the_same_lever_reads_ok(gear, real):
    vs = _run(_check(gear), real)
    assert not any(v.kind in R.TERMINAL for v in vs)
    assert vs[-1].kind == R.OK


def test_between_positions_is_ratio_wrong_at_the_coarse_check():
    """1.4 is +40 % from A and -30 % from B: no position is within 15 %."""
    vs = _run(_check("A"), 1.4)
    v = vs[-1]
    assert v.kind == R.RATIO_WRONG
    assert v.elapsed_ms >= R.COARSE_MIN_MS


def test_near_a_but_never_tight_is_ratio_wrong_at_the_deadline():
    """+10 %: inside the coarse band, never inside 5 %."""
    v = _run(_check("A"), 1.10)[-1]
    assert v.kind == R.RATIO_WRONG
    assert v.elapsed_ms >= R.SETTLE_DEADLINE_MS
    assert v.travel_mm >= R.SETTLE_DEADLINE_MM


def test_the_deadline_waits_for_distance_on_a_slow_pass():
    """At a crawl the time passes long before the travel does; a bare time
    deadline would fail a slow finishing pass that simply has not moved enough
    scale counts to settle."""
    slow = _run(_check("A"), 1.10, polls=60, steps_per_poll=10)
    assert all(v.kind != R.RATIO_WRONG or v.travel_mm >= R.SETTLE_DEADLINE_MM
               for v in slow)


def test_half_nut_opening_mid_pass_is_decoupled():
    vs = _run(_check("A"), 1.0, polls=80, ratio_at=lambda i: 1.0 if i < 40 else 0.0)
    assert vs[-1].kind == R.DECOUPLED
    first = next(i for i, v in enumerate(vs) if v.kind == R.DECOUPLED)
    assert first - 40 <= R.WINDOW_POLLS + R.DECOUPLED_POLLS


def test_half_nut_opening_late_in_a_long_pass_is_decoupled_not_ratio_wrong():
    """2026-10-03 shadow bench: a half nut opened ~5 s into a pass pulled the
    since-start estimate 5 % low before the trailing window reached the
    decoupled floor, and the drift rule called RATIO_WRONG first. A drift
    counts only once the trailing window agrees, so this is DECOUPLED."""
    # One of 40 such shapes (of 96 swept) the old rule called RATIO_WRONG.
    open_at = 40                                     # ~1.6 s in, past the deadline
    riding = 8                                       # the nut rides ~0.3 s first
    vs = _run(_check("A"), 1.0, polls=200,
              ratio_at=lambda i: 1.0 if i < open_at
              else 0.5 if i < open_at + riding else 0.0)
    assert not any(v.kind == R.RATIO_WRONG for v in vs)
    assert vs[-1].kind == R.DECOUPLED


def test_a_steady_partial_slip_after_settling_is_still_ratio_wrong():
    """The other side of that rule: a coupling slipping 10 % for good is not
    a half nut, and must still be called once the trailing window has sat
    off the gear for a full window."""
    vs = _run(_check("A"), 1.0, polls=300,
              ratio_at=lambda i: 1.0 if i < 60 else 0.9)
    assert vs[-1].kind == R.RATIO_WRONG
    assert not any(v.kind == R.DECOUPLED for v in vs)


def test_half_nut_opening_at_the_start_is_never_a_gear_change():
    """The since-start estimate falls from 1 to 0 through C's band; only the
    trailing window agreeing can make it a gear, and here it never does."""
    vs = _run(_check("A"), 1.0, polls=80, ratio_at=lambda i: 1.0 if i < 6 else 0.0)
    assert not any(v.kind == R.GEAR_CHANGE for v in vs)
    assert vs[-1].kind == R.DECOUPLED


def test_take_up_and_correction_are_not_listened_to():
    """Steps commanded during the take-up or while a correction drains move
    the carriage a different amount (lash, drive lag) and are ignored."""
    chk = _check("A")
    vs = _run(chk, 1.0, takeup_polls=10, correction_polls=5,
              ratio_at=lambda i: 0.0 if i < 15 else 1.0)
    assert not any(v.kind in R.TERMINAL for v in vs)
    assert vs[-1].kind == R.OK


def test_not_cutting_feeds_nothing():
    chk = _check("A")
    for i in range(50):
        v = chk.feed(t_ms=i * POLL_MS, servo_steps=i * 100, z_counts=0,
                     cutting=False, takeup_pending=False, steps_to_go=0)
    assert v.kind == R.UNKNOWN


def test_a_verdict_is_latched_for_the_pass_and_cleared_by_the_next():
    chk = _check("A")
    _run(chk, 2.0)
    assert chk.verdict.kind == R.GEAR_CHANGE
    chk.start_pass()
    assert chk.verdict.kind == R.UNKNOWN


def test_short_pass_steady_on_another_gear_is_a_gear_change():
    """Ran out of pass after the coarse check but before settling: steady on
    B is still a gear change (decided 2026-10-03)."""
    chk = _check("A")
    # Enough for the coarse check, not enough for SETTLE_POLLS inside 5 %:
    # a ratio 8 % off B stays outside the tight band.
    vs = _run(chk, 2.16, polls=12)
    assert vs[-1].kind not in R.TERMINAL
    v = chk.end_pass()
    assert v.kind == R.GEAR_CHANGE and v.gear == "B"


def test_short_pass_before_the_coarse_check_decides_nothing():
    chk = _check("A")
    _run(chk, 2.0, polls=3)
    assert chk.end_pass().kind == R.UNKNOWN


def test_decoupled_threshold_sits_below_gear_c():
    """Gear C reads ~0.5 and its windows dipped to 0.43 on 10-03; the
    2026-09-20 threshold of 0.5 would have aborted every pass in C."""
    assert R.DECOUPLED_BELOW < 0.43
    vs = _run(_check("C"), 0.45)
    assert not any(v.kind == R.DECOUPLED for v in vs)


def test_every_operator_text_fits_the_strip():
    """Same 65-character contract as the take-up strip (utils/devices.py)."""
    texts = [R.gear_change_text(g) for g in ("A", "B", "C")]
    texts += [R.abort_text(R.DECOUPLED), R.abort_text(R.RATIO_WRONG)]
    for t in texts:
        assert t.startswith("Cut aborted")
        assert len(t) <= 65, t


# ─── RatioWatch: the per-tick glue ──────────────────────────────────────

from types import SimpleNamespace   # noqa: E402


class _Board:
    def __init__(self):
        self.fast_data_values = {}
        self.els_stop_values = {}


def _watch(state, verdicts, *, gear="A", k=K):
    board = _Board()
    tick = SimpleNamespace(scale_index=lambda: 1,
                           takeup_pending=lambda: bool(board.takeup))
    board.takeup = False
    hal = SimpleNamespace(tick=tick)
    clock = {"t": 0.0}
    w = R.RatioWatch(hal, board, fsm_state=lambda: state["s"],
                     steps_per_count=lambda: k, gear=lambda: gear,
                     mm_per_count=lambda: MM_PER_COUNT,
                     on_verdict=verdicts.append, now_ms=lambda: clock["t"])
    return w, board, clock


def _drive(w, board, clock, state, ratio, polls, *, servo0=0, steps=120):
    zf = 0.0
    servo = servo0
    for _ in range(polls):
        servo = (servo + steps) % (1 << 32)
        zf += steps * ratio / K
        board.fast_data_values = {"servoCurrent": servo, "stepsToGo": 0,
                                  "scaleCurrent": [0, int(round(zf)), 0, 0]}
        w.poll()
        clock["t"] += POLL_MS


def test_watch_reports_a_gear_change_once_per_pass():
    state, verdicts = {"s": "stopped"}, []
    w, board, clock = _watch(state, verdicts)
    w.poll()
    state["s"] = "cutting"
    _drive(w, board, clock, state, 2.0, 60)
    state["s"] = "stopped"
    w.poll()
    assert [(v.kind, v.gear) for v in verdicts] == [(R.GEAR_CHANGE, "B")]


def test_watch_unwraps_the_uint32_step_counter():
    """servoCurrent rolls over at 2**32; a coupled pass across the rollover
    must still read coupled."""
    state, verdicts = {"s": "cutting"}, []
    w, board, clock = _watch(state, verdicts)
    _drive(w, board, clock, state, 1.0, 60, servo0=(1 << 32) - 3000)
    assert verdicts == []
    assert w.verdict.kind == R.OK


def test_watch_starts_fresh_each_pass():
    state, verdicts = {"s": "cutting"}, []
    w, board, clock = _watch(state, verdicts)
    _drive(w, board, clock, state, 2.0, 60)
    state["s"] = "stopped"
    w.poll()
    state["s"] = "cutting"
    w.poll()
    assert w.verdict.kind == R.UNKNOWN
    _drive(w, board, clock, state, 2.0, 60)
    assert len(verdicts) == 2


def test_watch_without_geometry_does_nothing():
    state, verdicts = {"s": "cutting"}, []
    w, board, clock = _watch(state, verdicts, k=None)
    _drive(w, board, clock, state, 2.0, 60)
    assert verdicts == [] and w.verdict.kind == R.UNKNOWN


# ─── The controller: shadow by default, acts only when switched on ──────

@pytest.fixture(autouse=True)
def _isolate_event_bus():
    """Controllers built here subscribe to the app-wide event bus and would
    outlive the test: a later test's publish then reaches them, and one opens
    a popup under Kivy's mock GL, which segfaults the run. Restore the
    subscriber lists after every test in this module."""
    from reflex.fsms.fsm_event_bus import fsm_event_bus as bus
    saved = {k: list(v) for k, v in bus._subs.items()}
    yield
    bus._subs.clear()
    for k, v in saved.items():
        bus._subs[k] = v


def _controller():
    import os
    os.environ.setdefault("KIVY_WINDOW", "mock")
    os.environ.setdefault("KIVY_GL_BACKEND", "mock")
    from unittest.mock import MagicMock
    from reflex.fsms.ui_controller import ElsUiController
    from tests.fsms.test_ui_controller import (_make_collaborators, _make_x_axis,
                                               _make_z_axis, _pump)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=_make_x_axis())
    board.axes = []
    ctrl = ElsUiController(els=els, board=board)
    _pump()
    ctrl._hal = MagicMock(wraps=ctrl._hal)
    return ctrl, board, els


def test_setting_defaults_to_shadow():
    from reflex.dispatchers.els import ElsDispatcher
    assert ElsDispatcher.els_ratio_check.defaultvalue is False


def test_shadow_verdict_changes_nothing():
    """The collaborator is a MagicMock: els_ratio_check is truthy but not
    True, which must read as OFF."""
    ctrl, board, els = _controller()
    ctrl._on_ratio_verdict(R.Verdict(R.GEAR_CHANGE, "B", reason="test"))
    ctrl._on_ratio_verdict(R.Verdict(R.DECOUPLED, reason="test"))
    assert ctrl.ratio_warning == ""
    assert ctrl._pending_gear is None
    ctrl._hal.set_active.assert_not_called()


def test_acting_gear_change_is_yellow_and_adopted_on_the_next_move():
    ctrl, board, els = _controller()
    els.els_ratio_check = True
    ctrl._on_ratio_verdict(R.Verdict(R.GEAR_CHANGE, "B", reason="test"))
    assert ctrl.ratio_warning_yellow is True
    assert "B (fast)" in ctrl.ratio_warning
    assert ctrl._pending_gear == "B"
    ctrl._adopt_pending_gear()
    assert board.servo.gearbox_position == "B"
    assert ctrl.ratio_warning == "" and ctrl._pending_gear is None


@pytest.mark.parametrize("kind", [R.DECOUPLED, R.RATIO_WRONG])
def test_acting_red_verdicts_adopt_nothing(kind):
    ctrl, board, els = _controller()
    els.els_ratio_check = True
    ctrl._on_ratio_verdict(R.Verdict(R.GEAR_CHANGE, "B", reason="test"))
    ctrl._on_ratio_verdict(R.Verdict(kind, reason="test"))
    assert ctrl.ratio_warning_yellow is False
    assert ctrl.ratio_warning == R.abort_text(kind)
    assert ctrl._pending_gear is None


def test_acting_verdict_mid_cut_stops_sync():
    ctrl, board, els = _controller()
    els.els_ratio_check = True
    from unittest.mock import PropertyMock, patch
    with patch.object(type(ctrl._els_fsm), "state", new_callable=PropertyMock,
                      return_value="cutting", create=True):
        ctrl._on_ratio_verdict(R.Verdict(R.DECOUPLED, reason="test"))
    ctrl._hal.set_active.assert_called_once_with(True)


@pytest.mark.parametrize("action", ["start_cut", "start_retract"])
def test_cut_and_retract_both_adopt_first(action):
    from unittest.mock import MagicMock
    ctrl, board, els = _controller()
    order = []
    ctrl._adopt_pending_gear = lambda: order.append("adopt")
    fsm = MagicMock()
    fsm.push_stop_to_firmware.side_effect = lambda: order.append("push")
    fsm.cut.side_effect = lambda: order.append("move")
    fsm.retract.side_effect = lambda: order.append("move")
    ctrl._els_fsm = fsm
    getattr(ctrl, action)()
    assert order[0] == "adopt" and order[-1] == "move"


def test_manual_gear_selection_is_refused_during_a_pass():
    from unittest.mock import MagicMock
    ctrl, board, els = _controller()
    fsm = MagicMock()
    fsm.engaged_gear.return_value = "A"
    fsm.state = "cutting"
    ctrl._els_fsm = fsm
    assert ctrl.select_gear("B") is False
    fsm.adopt_gear.assert_not_called()
    fsm.state = "stopped"
    assert ctrl.select_gear("B") is True
    fsm.adopt_gear.assert_called_once_with("B")


def test_flight_recorder_files_the_verdict_and_the_gear():
    from reflex.fsms.els_flight_recorder import (FlightRecorder, GEARBOX_KEY,
                                                 RATIO_VERDICT_KEY)
    rec = FlightRecorder(_Board(), ratio_check=lambda: (R.GEAR_CHANGE, "A"))
    ctx = rec._context({})
    assert ctx[RATIO_VERDICT_KEY] == R.GEAR_CHANGE
    assert ctx[GEARBOX_KEY] == "A"
    broken = FlightRecorder(_Board(), ratio_check=lambda: 1 / 0)
    assert broken._context({})[RATIO_VERDICT_KEY] is None
