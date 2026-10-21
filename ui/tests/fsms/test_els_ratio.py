"""CouplingRatioCheck (reflex/fsms/els_ratio.py) on synthetic passes.

The thresholds themselves were chosen by replaying 205 recorded passes outside
the repo (the recordings are not public); these tests pin the RULES: what each
verdict needs, what it ignores, and what can never happen.
"""
import pytest

from reflex.fsms import els_ratio as R
from reflex.fsms.els_gear import NO_GEARBOX, GearTable

K = 12800.0 / 5080.0          # the recorded lathe: steps per Z count in gear A
MM_PER_COUNT = 0.005
POLL_MS = 40

#: The recorded lathe's lever: reference A, B twice as fast, C half.
ABC = GearTable.parse("A=1, B=2, C=1/2")
#: Owner names that are neither letters nor in order, factors not 2x apart.
NUM = GearTable.parse("1=1, 2=3/2, 3=4")


def _check(gear="A", table=ABC):
    k = K / float(table.factor(gear))
    chk = R.CouplingRatioCheck(steps_per_count=k, gear=gear,
                               mm_per_count=MM_PER_COUNT, table=table)
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
    assert R.nearest_gear(1.03, ABC) == ("A", pytest.approx(0.03))
    assert R.nearest_gear(1.9, ABC)[0] == "B"
    assert R.nearest_gear(0.52, ABC)[0] == "C"
    assert R.nearest_gear(0.0, ABC)[0] == "C"


# ── A configured table: the owner's names and factors ──────────────────

@pytest.mark.parametrize("real, expect", [(1.5, "2"), (4.0, "3")])
def test_gear_change_onto_non_alphabetic_non_binary_positions(real, expect):
    v = _run(_check("1", NUM), real)[-1]
    assert v.kind == R.GEAR_CHANGE and v.gear == expect


@pytest.mark.parametrize("gear, real", [("1", 1.0), ("2", 1.5), ("3", 4.0)])
def test_each_configured_position_reads_ok_when_expected(gear, real):
    vs = _run(_check(gear, NUM), real)
    assert not any(v.kind in R.TERMINAL for v in vs)
    assert vs[-1].kind == R.OK


def test_from_the_fastest_configured_position_back_to_the_reference():
    v = _run(_check("3", NUM), 1.0)[-1]
    assert v.kind == R.GEAR_CHANGE and v.gear == "1"


def test_a_ratio_between_configured_positions_is_ratio_wrong():
    """2.4 is +60 % from "2" and -40 % from "3": no position within 15 %."""
    assert _run(_check("1", NUM), 2.4)[-1].kind == R.RATIO_WRONG


def test_a_name_not_in_the_table_expects_the_reference():
    chk = _check("Q", NUM)
    assert chk.gear == "1"
    assert _run(chk, 1.0)[-1].kind == R.OK


# ── No gearbox: one expected ratio, nothing to adopt ───────────────────

def test_no_gearbox_coupled_pass_reads_ok():
    vs = _run(_check("", NO_GEARBOX), 1.0)
    assert not any(v.kind in R.TERMINAL for v in vs)
    assert vs[-1].kind == R.OK


@pytest.mark.parametrize("real", [2.0, 0.5, 4.0, 1.4])
def test_no_gearbox_never_reads_a_gear_change(real):
    """A ratio that would be another position on a geared lathe is simply
    wrong here: RATIO_WRONG, never GEAR_CHANGE."""
    chk = _check("", NO_GEARBOX)
    vs = _run(chk, real)
    assert not any(v.kind == R.GEAR_CHANGE for v in vs)
    assert vs[-1].kind == R.RATIO_WRONG
    assert chk.end_pass().kind == R.RATIO_WRONG


def test_no_gearbox_short_pass_off_ratio_is_not_a_gear_change():
    chk = _check("", NO_GEARBOX)
    _run(chk, 2.16, polls=12)
    assert chk.end_pass().kind != R.GEAR_CHANGE


def test_no_gearbox_ignores_a_stored_position():
    chk = _check("B", NO_GEARBOX)
    assert chk.gear == ""
    assert _run(chk, 1.0)[-1].kind == R.OK


def test_no_gearbox_half_nut_opening_is_decoupled():
    vs = _run(_check("", NO_GEARBOX), 1.0, polls=80,
              ratio_at=lambda i: 1.0 if i < 40 else 0.0)
    assert vs[-1].kind == R.DECOUPLED


def test_decoupled_floor_follows_the_slowest_position():
    """0.25 while nothing is slower than x1/2; half the slowest below that,
    so a legitimate x1/4 pass is not called decoupled."""
    assert R.decoupled_below(NO_GEARBOX) == R.DECOUPLED_BELOW
    assert R.decoupled_below(ABC) == R.DECOUPLED_BELOW
    slow = GearTable.parse("A=1, S=1/4")
    assert R.decoupled_below(slow) == pytest.approx(0.125)
    vs = _run(_check("S", slow), 0.25)
    assert not any(v.kind in R.TERMINAL for v in vs)
    assert vs[-1].kind == R.OK


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


def test_take_up_is_not_listened_to():
    """Steps commanded during the take-up walk the nut through the lash: the
    carriage barely moves, and that is not a decoupling."""
    chk = _check("A")
    vs = _run(chk, 1.0, takeup_polls=10, ratio_at=lambda i: 0.0 if i < 10 else 1.0)
    assert not any(v.kind in R.TERMINAL for v in vs)
    assert vs[-1].kind == R.OK


def _first_terminal(vs):
    return next(i for i, v in enumerate(vs) if v.kind in R.TERMINAL)


def test_a_phase_correction_is_listened_to():
    """D8 (2026-10-11): the check starts when the take-up ends, not when the
    phase correction has drained. A wrong gear is called as many polls after
    the take-up as on a pass with no correction at all: the correction's polls
    count. Under the old rule it came correction_polls later.

    Seen red 2026-10-11 with the old gate restored (takeup_pending or
    steps_to_go != 0): the verdict came 10 polls later, at the poll the
    correction drained plus the no-correction latency."""
    base = _run(_check("A"), 2.0, polls=80)
    n0 = _first_terminal(base)
    vs = _run(_check("A"), 2.0, takeup_polls=5, correction_polls=10, polls=70)
    assert vs[_first_terminal(vs)].kind == R.GEAR_CHANGE
    assert vs[_first_terminal(vs)].gear == "B"
    assert _first_terminal(vs) - 5 == n0


def test_a_correction_burst_the_drive_lags_is_not_a_verdict():
    """The drive lags a correction burst (09-07 threading set: first windows as
    low as 0.82). Modelled as the carriage falling 18 % behind for the burst
    and making it up afterwards: no verdict, and the pass settles OK. The
    stage-1 rule waits while the trailing window disagrees with the estimate
    since the start, which is what a lag being made up looks like."""
    def lagging(i):
        if i < 5:
            return 0.82          # the burst: 200 ms at 82 %
        if i < 10:
            return 1.18          # caught up over the next 200 ms
        return 1.0
    vs = _run(_check("A"), 1.0, correction_polls=10, polls=70, ratio_at=lagging)
    assert not any(v.kind in R.TERMINAL for v in vs)
    assert vs[-1].kind == R.OK


def test_a_lag_held_through_the_coarse_window_is_ratio_wrong():
    """THE LIMIT of the decision, pinned so it is visible: a carriage that
    really reads 82 % through the whole coarse window (350 ms and 0.75 mm)
    is outside 15 % of every position and is RATIO_WRONG, whether or not a
    correction is draining. On the lathe this would show as a red 'Carriage
    speed is incorrect' on a thread pass in the right gear; the bench visit
    checks for exactly that."""
    vs = _run(_check("A"), 0.82, correction_polls=20, polls=60)
    assert vs[_first_terminal(vs)].kind == R.RATIO_WRONG


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
    assert R.decoupled_below(ABC) < 0.43
    vs = _run(_check("C"), 0.45)
    assert not any(v.kind == R.DECOUPLED for v in vs)


def test_every_operator_text_fits_the_strip():
    """Same 65-character contract as the take-up strip (utils/devices.py),
    at the longest names a table allows."""
    widest = GearTable.parse("WWW=1, MMM=8, QQQ=1/8")
    texts = [R.gear_change_text(g) for t in (ABC, NUM, widest)
             for g in t.names]
    texts += [R.abort_text(k) for k in (R.DECOUPLED, R.RATIO_WRONG)]
    for t in texts:
        assert len(t) <= 65, t


def test_gear_change_text_names_the_position_and_nothing_else():
    """Names are unique, so the name alone says which position. No speed
    hint: "fast" or "slow" depends on which position is the reference, and
    several positions can share one."""
    for g in ("Hi", "Lo", "Top"):
        text = R.gear_change_text(g)
        assert f" {g}." in text
        assert "fast" not in text and "slow" not in text


def test_red_refusal_text_never_mentions_gears():
    """One text for a wrong ratio with or without a gearbox, so it has to be
    right on a lathe with none: it names the drivetrain, never gears."""
    for kind in (R.DECOUPLED, R.RATIO_WRONG):
        assert "gear" not in R.abort_text(kind).lower()


# ─── RatioWatch: the per-tick glue ──────────────────────────────────────

from types import SimpleNamespace   # noqa: E402


class _Board:
    def __init__(self):
        self.fast_data_values = {}
        self.els_stop_values = {}


def _watch(state, verdicts, *, gear="A", k=K, table=ABC):
    board = _Board()
    tick = SimpleNamespace(scale_index=lambda: 1,
                           takeup_pending=lambda: bool(board.takeup))
    board.takeup = False
    hal = SimpleNamespace(tick=tick)
    clock = {"t": 0.0}
    w = R.RatioWatch(hal, board, fsm_state=lambda: state["s"],
                     steps_per_count=lambda: k, gear=lambda: gear,
                     gear_table=lambda: table,
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


def test_watch_with_no_gearbox_reports_ratio_wrong_not_a_gear():
    state, verdicts = {"s": "stopped"}, []
    w, board, clock = _watch(state, verdicts, gear="", table=NO_GEARBOX)
    w.poll()
    state["s"] = "cutting"
    _drive(w, board, clock, state, 2.0, 60)
    state["s"] = "stopped"
    w.poll()
    assert [v.kind for v in verdicts] == [R.RATIO_WRONG]


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


def _controller(table=ABC):
    import os
    os.environ.setdefault("KIVY_WINDOW", "mock")
    os.environ.setdefault("KIVY_GL_BACKEND", "mock")
    from unittest.mock import MagicMock
    from reflex.fsms.ui_controller import ElsUiController
    from tests.fsms.test_ui_controller import (_make_collaborators, _make_x_axis,
                                               _make_z_axis, _pump)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=_make_x_axis())
    board.axes = []
    board.servo.gear_table = table
    board.servo.gearbox_position = table.reference
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
    assert ctrl.ratio_warning == R.gear_change_text("B")
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


def test_acting_gear_change_with_owner_names():
    ctrl, board, els = _controller(NUM)
    els.els_ratio_check = True
    ctrl._on_ratio_verdict(R.Verdict(R.GEAR_CHANGE, "3", reason="test"))
    assert ctrl.ratio_warning_yellow is True
    assert ctrl.ratio_warning == R.gear_change_text("3")
    ctrl._adopt_pending_gear()
    assert board.servo.gearbox_position == "3"


@pytest.mark.parametrize("kind", [R.DECOUPLED, R.RATIO_WRONG, R.GEAR_CHANGE])
def test_no_gearbox_acting_verdict_is_red_neutral_and_adopts_nothing(kind):
    """With no gearbox the check stops the pass when it acts, says nothing
    about gears, and never adopts -- even handed a GEAR_CHANGE verdict."""
    ctrl, board, els = _controller(NO_GEARBOX)
    els.els_ratio_check = True
    ctrl._on_ratio_verdict(R.Verdict(kind, "B", reason="test"))
    assert ctrl.ratio_warning_yellow is False
    assert "gear" not in ctrl.ratio_warning.lower()
    assert ctrl.ratio_warning == R.abort_text(
        R.RATIO_WRONG if kind == R.GEAR_CHANGE else kind)
    assert ctrl._pending_gear is None
    ctrl._adopt_pending_gear()
    assert board.servo.gearbox_position == ""


def test_a_gear_change_to_a_position_no_longer_in_the_table_is_red():
    ctrl, board, els = _controller(ABC)
    els.els_ratio_check = True
    ctrl._on_ratio_verdict(R.Verdict(R.GEAR_CHANGE, "Q", reason="test"))
    assert ctrl.ratio_warning_yellow is False
    assert ctrl.ratio_warning == R.abort_text(R.RATIO_WRONG)
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
    fsm.gear_table.return_value = ABC
    fsm.state = "cutting"
    ctrl._els_fsm = fsm
    assert ctrl.select_gear("B") is False
    fsm.adopt_gear.assert_not_called()
    fsm.state = "stopped"
    assert ctrl.select_gear("B") is True
    fsm.adopt_gear.assert_called_once_with("B")


def test_manual_selection_outside_the_table_is_refused():
    from unittest.mock import MagicMock
    ctrl, board, els = _controller()
    fsm = MagicMock()
    fsm.state = "stopped"
    fsm.engaged_gear.return_value = ""
    fsm.gear_table.return_value = NO_GEARBOX
    ctrl._els_fsm = fsm
    assert ctrl.select_gear("A") is False
    fsm.gear_table.return_value = ABC
    fsm.engaged_gear.return_value = "A"
    assert ctrl.select_gear("Q") is False
    fsm.adopt_gear.assert_not_called()


# ─── Editing the table (ELS settings) ───────────────────────────────────

def _real_servo_controller(table_text=""):
    """A controller over a real ServoDispatcher, so set_gear_table runs
    through the persisted property and its derived table."""
    ctrl, board, els = _controller()
    from tests.dispatchers.conftest import MockBoard, MockFormats
    from reflex.dispatchers.servo import ServoDispatcher
    from unittest.mock import MagicMock
    sb = MockBoard()
    sb.device = MagicMock()
    servo = ServoDispatcher(board=sb, formats=MockFormats(), id_override="0")
    servo.gearbox_table = table_text
    board.servo = servo
    ctrl._els_fsm.servo = servo
    notes = []
    ctrl.notify = lambda msg, severity=None, **kw: notes.append(msg) or True
    return ctrl, servo, notes


@pytest.fixture
def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path))


def test_a_typed_table_is_applied_and_persisted(_cfg):
    ctrl, servo, notes = _real_servo_controller()
    assert ctrl.set_gear_table("Lo=1, Hi=1.5") is True
    assert servo.gearbox_table == "Lo=1, Hi=3/2"
    assert servo.gear_table.names == ("Lo", "Hi")
    assert servo.gearbox_engaged == "Lo"
    assert notes == []


def test_an_invalid_table_is_refused_with_the_reason(_cfg):
    ctrl, servo, notes = _real_servo_controller("A=1, B=2")
    assert ctrl.set_gear_table("A=1, B=1.1") is False
    assert servo.gearbox_table == "A=1, B=2"
    assert len(notes) == 1 and "too close" in notes[0]


def test_clearing_the_table_is_no_gearbox(_cfg):
    ctrl, servo, notes = _real_servo_controller("A=1, B=2")
    servo.gearbox_position = "B"
    assert ctrl.set_gear_table("") is True
    assert not servo.gear_table.has_gearbox
    assert servo.gearbox_engaged == ""


def test_the_table_cannot_change_during_a_pass(_cfg):
    from unittest.mock import PropertyMock, patch
    ctrl, servo, notes = _real_servo_controller("A=1, B=2")
    with patch.object(type(ctrl._els_fsm), "state", new_callable=PropertyMock,
                      return_value="cutting", create=True):
        assert ctrl.set_gear_table("A=1, B=3") is False
    assert servo.gearbox_table == "A=1, B=2"
    assert "during a pass" in notes[0]


def test_a_table_change_drops_a_pending_adoption(_cfg):
    ctrl, servo, notes = _real_servo_controller("A=1, B=2")
    ctrl._pending_gear = "B"
    ctrl.ratio_warning = "x"
    assert ctrl.set_gear_table("Lo=1, Hi=2") is True
    assert ctrl._pending_gear is None and ctrl.ratio_warning == ""
    ctrl._adopt_pending_gear()
    assert servo.gearbox_engaged == "Lo"


def test_the_same_table_typed_differently_changes_nothing(_cfg):
    ctrl, servo, notes = _real_servo_controller("A=1, B=2")
    ctrl._pending_gear = "B"
    assert ctrl.set_gear_table("A B=2.0") is True
    assert ctrl._pending_gear == "B"


def test_flight_recorder_files_the_verdict_and_the_gear():
    from reflex.fsms.els_flight_recorder import (FlightRecorder, GEARBOX_KEY,
                                                 RATIO_VERDICT_KEY)
    rec = FlightRecorder(_Board(), ratio_check=lambda: (R.GEAR_CHANGE, "A"))
    ctx = rec._context({})
    assert ctx[RATIO_VERDICT_KEY] == R.GEAR_CHANGE
    assert ctx[GEARBOX_KEY] == "A"
    broken = FlightRecorder(_Board(), ratio_check=lambda: 1 / 0)
    assert broken._context({})[RATIO_VERDICT_KEY] is None


# ─── A gear adoption clears a latched thread reference ─────────────────
#
# The reference was measured in the old gear, so it is wrong in the new one.
# The host writes the firmware's clearRefCommand on every adoption that
# finds a reference latched, and writes nothing when none is.

class _RecordingRegs(dict):
    """The elsStop block as a dict that remembers every write."""

    def __init__(self, **regs):
        super().__init__(**regs)
        self.writes = []

    def __setitem__(self, key, value):
        self.writes.append((key, value))
        super().__setitem__(key, value)


def _clear_writes(regs):
    return [w for w in regs.writes if w[0] == "clearRefCommand"]


def _wire(board, latched):
    regs = _RecordingRegs(referenceLatched=1 if latched else 0)
    board.device = {"elsStop": regs}
    board.connected = True
    return regs


@pytest.mark.parametrize("adopt", ["select_gear", "pending_then_cut"])
def test_a_gear_adoption_with_a_ref_latched_writes_the_clear_once(adopt):
    ctrl, board, els = _controller()
    regs = _wire(board, latched=True)
    if adopt == "select_gear":
        assert ctrl.select_gear("B") is True
    else:
        ctrl._pending_gear = "B"
        ctrl._adopt_pending_gear()
    assert board.servo.gearbox_position == "B"
    assert _clear_writes(regs) == [("clearRefCommand", 1)]


def test_a_gear_adoption_with_no_ref_latched_writes_nothing():
    ctrl, board, els = _controller()
    regs = _wire(board, latched=False)
    assert ctrl.select_gear("B") is True
    assert board.servo.gearbox_position == "B"
    assert _clear_writes(regs) == []


def test_a_table_change_that_moves_the_engaged_factor_clears_the_ref(_cfg):
    ctrl, servo, notes = _real_servo_controller("A=1, B=2")
    servo.gearbox_position = "B"
    regs = _wire(ctrl._board, latched=True)
    assert ctrl.set_gear_table("A=1, B=3") is True
    assert _clear_writes(regs) == [("clearRefCommand", 1)]


def test_a_table_change_that_keeps_the_engaged_factor_writes_nothing(_cfg):
    """Engaged in the reference: its factor is 1 in both tables."""
    ctrl, servo, notes = _real_servo_controller("A=1, B=2")
    servo.gearbox_position = "A"
    regs = _wire(ctrl._board, latched=True)
    assert ctrl.set_gear_table("A=1, B=3") is True
    assert _clear_writes(regs) == []
