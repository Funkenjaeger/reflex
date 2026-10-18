"""The guided gearbox-factor measurement (els_gear_survey) and its guard side.

Pins: three air passes of one non-reference position; the spread gate (at the
limit accepted, over it refused and named); the proposal rounded to a factor
GearTable accepts and keeps apart from the other positions; the reference
position never measured; every operator line within 65 characters; the ratio
check held while a measurement runs and acting again after Save or Discard;
Save writing one position's factor through GearTable parsing; and the typed
gearbox_table path unchanged.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

import datetime
from fractions import Fraction
from types import SimpleNamespace

import pytest

from reflex.fsms import els_gear_survey as gs
from reflex.fsms import els_overshoot_guard as g
from reflex.fsms import els_overshoot_survey as osv
from reflex.fsms import els_ratio as R
from reflex.fsms.els_gear import NO_GEARBOX, GearTable
from reflex.fsms.els_overshoot_table import Binding

ABC = GearTable.parse("A=1, B=2, C=1/2")
DEV = Binding(Fraction(1, 200), Fraction(127, 64000), "A=1, B=2, C=1/2")


def v(r, kind=R.GEAR_CHANGE):
    return R.Verdict(kind, "B", r_since_start=r, r_trailing=r)


def rd(r):
    """A whole pass that read `r`, long enough to count."""
    return gs.PassReading(r, counts=5000, polls=100)


def run(position, readings, table=ABC):
    s = gs.GearSurvey(position, table)
    msgs = [s.add(v(r), rd(r)) for r in readings]
    return s, msgs


# ─── the pure survey ─────────────────────────────────────────────────────

def test_three_passes_are_asked_for():
    assert gs.GEAR_PASSES == 3


def test_passes_before_the_last_ask_for_the_next_pass():
    s, msgs = run("B", [2.001, 1.999])
    assert s.state == gs.RUNNING and not s.done
    assert all(m.endswith(osv.NEXT_PASS) for m in msgs)
    assert s.factor is None


def test_cues_are_the_overshoot_survey_constants_not_copies():
    assert gs.NEXT_PASS is osv.NEXT_PASS
    assert gs.NEXT_SETTING is osv.NEXT_SETTING


def test_three_agreeing_passes_propose_the_factor():
    s, _ = run("B", [1.998, 2.001, 2.000])
    assert s.state == gs.PROPOSED and s.done
    assert s.factor == Fraction(2)
    assert "B" in s.message and "2" in s.message


def test_spread_at_the_limit_is_accepted():
    # 2.991 .. 3.009 about a mean of 3.000: a spread of exactly 0.6 %.
    s, _ = run("B", [2.991, 3.000, 3.009], table=GearTable.parse("A=1, B=2"))
    assert s.state == gs.PROPOSED, s.detail


def test_spread_over_the_limit_is_refused_and_named():
    s, _ = run("B", [2.989, 3.000, 3.009], table=GearTable.parse("A=1, B=2"))
    assert s.state == gs.REFUSED and s.done
    assert s.factor is None
    assert "spread" in s.message and "B" in s.message
    assert "0.67%" in s.message


def test_the_gate_is_relative_so_a_fast_position_is_not_penalized():
    # 0.5 % of 2: a spread of 0.010 on a mean of 2.000.
    s, _ = run("B", [1.995, 2.000, 2.005])
    assert s.state == gs.PROPOSED


@pytest.mark.parametrize("readings, expect", [
    ([2.003, 1.998, 2.000], Fraction(2)),
    ([0.501, 0.499, 0.500], Fraction(1, 2)),
    ([1.499, 1.501, 1.500], Fraction(3, 2)),
    ([1.370, 1.371, 1.369], Fraction(137, 100)),
])
def test_proposal_is_rounded_to_a_factor_gear_table_accepts(readings, expect):
    table = GearTable.parse("A=1, B=2, C=3")
    s, _ = run("B", readings, table=table)
    assert s.state == gs.PROPOSED, s.detail
    assert s.factor == expect
    updated = s.updated_table()
    assert GearTable.parse(updated.text()) == updated
    assert dict(updated.positions)["B"] == expect
    # The other positions are untouched.
    assert dict(updated.positions)["A"] == 1 and dict(updated.positions)["C"] == 3


def test_a_factor_outside_the_limits_is_refused():
    s, _ = run("B", [9.0, 9.0, 9.0])
    assert s.state == gs.REFUSED
    assert "1/8" in s.detail and "8" in s.detail


def test_a_factor_too_close_to_another_position_is_refused_naming_it():
    s, _ = run("B", [1.1, 1.1, 1.1])
    assert s.state == gs.REFUSED
    assert "A" in s.message and "close" in s.message


def test_a_pass_with_no_reading_or_a_decoupled_one_is_not_counted():
    s = gs.GearSurvey("B", ABC)
    m1 = s.add(R.Verdict(R.UNKNOWN), None)
    # Decoupled is decided by the check, whatever the whole pass read.
    m2 = s.add(R.Verdict(R.DECOUPLED, r_since_start=0.0), rd(2.0))
    assert "not counted" in m1 and "not counted" in m2
    assert s.readings == [] and s.state == gs.RUNNING
    assert m1.endswith(osv.NEXT_PASS)


def test_the_reference_position_is_refused():
    assert gs.refusal_for(ABC, "A")
    assert "reference" in gs.refusal_for(ABC, "A")
    assert gs.refusal_for(ABC, "B") is None


def test_an_unknown_position_and_no_gearbox_are_refused():
    assert gs.refusal_for(ABC, "Q")
    assert gs.refusal_for(NO_GEARBOX, "A")


def test_a_finished_survey_ignores_further_passes():
    s, _ = run("B", [2.0, 2.0, 2.0])
    f = s.factor
    s.add(v(5.0), rd(5.0))
    assert s.factor == f and len(s.readings) == gs.GEAR_PASSES


def test_every_message_fits_65_characters():
    table = GearTable.parse("Abc=1, Xyz=2, Q=1/2")
    seen = []
    for position, readings in (("Xyz", [2.0, 2.0, 2.0]),
                               ("Xyz", [1.9, 2.0, 2.1]),
                               ("Xyz", [9.0, 9.0, 9.0]),
                               ("Xyz", [1.1, 1.1, 1.1]),
                               ("Q", [0.5, 0.5, 0.5])):
        s = gs.GearSurvey(position, table)
        seen.append(s.message)
        for r in readings:
            seen.append(s.add(v(r), rd(r)))
        seen.append(s.message)
    s = gs.GearSurvey("Xyz", table)
    seen.append(s.add(R.Verdict(R.UNKNOWN), None))
    seen.append(s.add(v(2.0), gs.PassReading(None, 10, 3, "too short: x")))
    seen.append(gs.refusal_for(table, "Abc"))
    seen.append(gs.refusal_for(table, "Zzz"))
    seen.append(gs.refusal_for(NO_GEARBOX, "Abc"))
    assert all(seen)
    for m in seen:
        assert len(m) <= 65, m


# ─── the guard side ──────────────────────────────────────────────────────

class Rig:
    def __init__(self, text="A=1, B=2, C=1/2", live=DEV):
        self.els = SimpleNamespace(
            els_overshoot_correction=False, els_overshoot_table="",
            els_overshoot_mm_per_count="", els_overshoot_mm_per_step="",
            els_overshoot_gearbox="", els_overshoot_source="")
        self.text = text
        self.applied = []
        self.cues = []
        self.results = []
        self.levers = []
        self.accept = True
        self.live = [live]
        self.guard = g.OvershootGuard(
            self.els, lambda: self.live[0], lambda *a, **k: None,
            today=lambda: datetime.date(2026, 10, 17),
            cue=self.cues.append,
            gear_table=lambda: GearTable.parse(self.text),
            apply_gear_table=self._apply,
            on_gear_result=self.results.append,
            on_lever_change=self.levers.append)

    def _apply(self, text):
        self.applied.append(text)
        if self.accept:
            self.text = text
        return self.accept


def feed(rig, *readings):
    for r in readings:
        rig.guard.on_gear_pass(v(r), rd(r))


def test_start_refuses_the_reference_and_starts_nothing():
    r = Rig()
    why = r.guard.start_gear_survey("A")
    assert why and "reference" in why
    assert r.guard.gear_survey is None and not r.guard.gear_survey_running


def test_start_needs_a_gearbox_and_a_live_binding():
    assert Rig(text="").guard.start_gear_survey("B")
    assert Rig(live=None).guard.start_gear_survey("B")


def test_start_is_refused_while_an_overshoot_measurement_runs():
    r = Rig()
    assert r.guard.start_survey(osv.DETECT) is None
    assert r.guard.start_gear_survey("B")
    assert r.guard.gear_survey is None


def test_start_marks_the_lever_seam_and_cues():
    r = Rig()
    assert r.guard.start_gear_survey("B") is None
    assert r.guard.gear_survey_running
    assert r.levers == ["B"]
    assert r.cues[-1] == r.guard.gear_survey.message


def test_passes_feed_the_survey_and_a_proposal_opens_the_result():
    r = Rig()
    r.guard.start_gear_survey("B")
    feed(r, 1.5, 1.501)
    assert r.guard.gear_survey_running and r.results == []
    assert r.cues[-1].endswith(osv.NEXT_PASS)
    feed(r, 1.499)
    assert not r.guard.gear_survey_running
    assert r.guard.gear_survey.state == gs.PROPOSED
    assert r.results == [r.guard.gear_survey]


def test_a_refusal_opens_the_result_and_saves_nothing():
    r = Rig()
    r.guard.start_gear_survey("B")
    feed(r, 1.0, 2.0, 3.0)
    assert r.guard.gear_survey.state == gs.REFUSED
    assert r.results == [r.guard.gear_survey]
    assert r.guard.save_gear_survey()
    assert r.applied == []


def test_passes_with_no_survey_are_ignored():
    r = Rig()
    feed(r, 1.5)
    assert r.cues == [] and r.results == []


def test_save_writes_the_one_position_through_gear_table():
    r = Rig()
    r.guard.start_gear_survey("B")
    feed(r, 1.5, 1.501, 1.499)
    assert r.guard.save_gear_survey() is None
    assert r.applied == ["A=1, B=3/2, C=1/2"]
    assert GearTable.parse(r.text).factor("B") == Fraction(3, 2)
    assert GearTable.parse(r.text).factor("C") == Fraction(1, 2)
    assert r.guard.gear_survey is None
    assert r.cues[-1] == "" or r.cues[-1] == osv.NEXT_SETTING


def test_save_cues_the_next_setting_when_another_position_remains():
    r = Rig()
    r.guard.start_gear_survey("B")
    feed(r, 1.5, 1.5, 1.5)
    r.guard.save_gear_survey()
    assert r.cues[-1] == osv.NEXT_SETTING
    r2 = Rig()
    r2.guard.start_gear_survey("C")
    feed(r2, 0.5, 0.5, 0.5)
    r2.guard.save_gear_survey()
    assert r2.cues[-1] == ""


def test_save_without_a_proposal_is_refused():
    r = Rig()
    assert r.guard.save_gear_survey()
    r.guard.start_gear_survey("B")
    feed(r, 1.5)
    assert r.guard.save_gear_survey()
    assert r.applied == []


def test_save_is_refused_when_the_table_changed_during_the_measurement():
    r = Rig()
    r.guard.start_gear_survey("B")
    feed(r, 1.5, 1.5, 1.5)
    r.text = "A=1, B=2, C=1/3"
    why = r.guard.save_gear_survey()
    assert why and "changed" in why
    assert r.applied == []


def test_save_reports_a_controller_refusal_and_keeps_the_proposal():
    r = Rig()
    r.accept = False
    r.guard.start_gear_survey("B")
    feed(r, 1.5, 1.5, 1.5)
    assert r.guard.save_gear_survey()
    assert r.guard.gear_survey is not None


def test_cancel_discards_and_writes_nothing():
    r = Rig()
    r.guard.start_gear_survey("B")
    feed(r, 1.5)
    r.guard.cancel_gear_survey()
    assert r.guard.gear_survey is None and not r.guard.gear_survey_running
    assert r.applied == [] and r.cues[-1] == ""


# ─── the ratio check hold ────────────────────────────────────────────────

K = 12800.0 / 5080.0
MM_PER_COUNT = 0.005
POLL_MS = 40


def _watch(hold, held, verdicts, state, *, gear="A", table=ABC):
    board = SimpleNamespace(fast_data_values={}, els_stop_values={})
    tick = SimpleNamespace(scale_index=lambda: 1, takeup_pending=lambda: False)
    hal = SimpleNamespace(tick=tick)
    clock = {"t": 0.0}
    w = R.RatioWatch(hal, board, fsm_state=lambda: state["s"],
                     steps_per_count=lambda: K, gear=lambda: gear,
                     gear_table=lambda: table, mm_per_count=lambda: MM_PER_COUNT,
                     on_verdict=verdicts.append, now_ms=lambda: clock["t"],
                     hold=hold,
                     on_held=lambda vv, reading: held.append((vv, reading)),
                     measure=gs.WholePassRatio)
    return w, board, clock


def _pass(w, board, clock, state, ratio, polls=60):
    state["s"] = "cutting"
    w.poll()
    servo, zf = 0, 0.0
    for _ in range(polls):
        servo += 120
        zf += 120 * ratio / K
        board.fast_data_values = {"servoCurrent": servo, "stepsToGo": 0,
                                  "scaleCurrent": [0, int(round(zf)), 0, 0]}
        w.poll()
        clock["t"] += POLL_MS
    state["s"] = "stopped"
    w.poll()


def test_the_ratio_check_does_not_act_while_a_measurement_runs():
    state, verdicts, held, flag = {"s": "stopped"}, [], [], {"on": True}
    w, board, clock = _watch(lambda: flag["on"], held, verdicts, state)
    w.poll()
    _pass(w, board, clock, state, 2.0)
    assert verdicts == []
    assert len(held) == 1
    assert held[0][0].r_since_start == pytest.approx(2.0, rel=0.01)
    assert held[0][1].r == pytest.approx(2.0, rel=0.001)


def test_the_ratio_check_acts_again_after_the_hold_is_released():
    state, verdicts, held, flag = {"s": "stopped"}, [], [], {"on": True}
    w, board, clock = _watch(lambda: flag["on"], held, verdicts, state)
    w.poll()
    _pass(w, board, clock, state, 2.0)
    flag["on"] = False
    _pass(w, board, clock, state, 2.0)
    assert [x.kind for x in verdicts] == [R.GEAR_CHANGE]
    assert len(held) == 1


def test_a_pass_in_the_expected_position_still_reaches_the_survey():
    """A pass the check calls fine never produces a terminal verdict; the
    survey still needs its reading."""
    state, verdicts, held = {"s": "stopped"}, [], []
    w, board, clock = _watch(lambda: True, held, verdicts, state)
    w.poll()
    _pass(w, board, clock, state, 1.0)
    assert verdicts == [] and len(held) == 1
    assert held[0][0].r_since_start == pytest.approx(1.0, rel=0.01)
    assert held[0][1].r == pytest.approx(1.0, rel=0.001)


def test_no_hold_arguments_leaves_the_watch_as_it_was():
    state, verdicts = {"s": "stopped"}, []
    board = SimpleNamespace(fast_data_values={}, els_stop_values={})
    tick = SimpleNamespace(scale_index=lambda: 1, takeup_pending=lambda: False)
    clock = {"t": 0.0}
    w = R.RatioWatch(SimpleNamespace(tick=tick), board,
                     fsm_state=lambda: state["s"], steps_per_count=lambda: K,
                     gear=lambda: "A", gear_table=lambda: ABC,
                     mm_per_count=lambda: MM_PER_COUNT,
                     on_verdict=verdicts.append, now_ms=lambda: clock["t"])
    w.poll()
    _pass(w, board, clock, state, 2.0)
    assert [x.kind for x in verdicts] == [R.GEAR_CHANGE]


def test_the_guard_is_the_hold_and_releases_on_discard_and_on_save():
    r = Rig()
    assert r.guard.gear_survey_running is False
    r.guard.start_gear_survey("B")
    assert r.guard.gear_survey_running is True
    r.guard.cancel_gear_survey()
    assert r.guard.gear_survey_running is False
    r.guard.start_gear_survey("B")
    feed(r, 1.5, 1.5, 1.5)
    assert r.guard.gear_survey_running is False
    assert r.guard.save_gear_survey() is None
    assert r.guard.gear_survey_running is False


# ─── the controller: wired, and the typed path unchanged ─────────────────

@pytest.fixture(autouse=True)
def _isolate_event_bus():
    from reflex.fsms.fsm_event_bus import fsm_event_bus as bus
    saved = {k: list(x) for k, x in bus._subs.items()}
    yield
    bus._subs.clear()
    for k, x in saved.items():
        bus._subs[k] = x


def _controller():
    from tests.fsms.test_els_ratio import _controller
    return _controller(ABC)


def test_the_controller_holds_its_ratio_watch_during_a_measurement():
    ctrl, board, els = _controller()
    ctrl._overshoot._binding = lambda: DEV
    assert ctrl._ratio_watch._hold() is False
    assert ctrl.start_gear_survey("B") is None
    assert ctrl._ratio_watch._hold() is True
    ctrl.overshoot.cancel_gear_survey()
    assert ctrl._ratio_watch._hold() is False


def test_the_controller_refuses_a_measurement_during_a_pass():
    from unittest.mock import PropertyMock, patch
    ctrl, board, els = _controller()
    with patch.object(type(ctrl._els_fsm), "state", new_callable=PropertyMock,
                      return_value="cutting", create=True):
        why = ctrl.start_gear_survey("B")
    assert why == "Pass is running. Wait for the stop."
    assert ctrl.overshoot.gear_survey is None


def test_the_controller_saves_through_set_gear_table():
    ctrl, board, els = _controller()
    ctrl._overshoot._binding = lambda: DEV
    ctrl.start_gear_survey("B")
    for r in (1.5, 1.5, 1.5):
        ctrl.overshoot.on_gear_pass(v(r), rd(r))
    assert ctrl.overshoot.save_gear_survey() is None
    assert board.servo.gearbox_table == "A=1, B=3/2, C=1/2"


def test_the_typed_gearbox_table_path_parses_unchanged():
    ctrl, board, els = _controller()
    assert ctrl.set_gear_table("A=1 B=3/2, C=0.5") is True
    assert board.servo.gearbox_table == "A=1, B=3/2, C=1/2"
    assert ctrl.set_gear_table("A=1, B=1.1") is False
    assert ctrl.set_gear_table("") is True
    assert board.servo.gearbox_table == ""


# ─── the whole-pass reading ──────────────────────────────────────────────
# The coupling check latches its verdict once the reading has settled within
# 5 % of a position, under a second into the pass; its r_since_start is the
# reading at that moment. On the 2026-10-18 bench that spread B over
# 1.928-2.021 and C over 0.478-0.512, and both were refused, while the same
# passes measured over their whole length agreed to 0.1 %.

def _profile_pass(w, board, clock, state, ratio_at, *, polls=250, step=120,
                  servo0=0, takeup_polls=0, tick=None):
    """One pass: `ratio_at(t_ms)` is the coupling (reference terms, gear A
    expected) of the steps commanded in the poll at t_ms after the pass
    started. Z is rounded to whole counts, as a scale reports it. The first
    `takeup_polls` polls are the take-up: they move the carriage through the
    lash at a ratio that says nothing about the gearing."""
    state["s"] = "cutting"
    w.poll()
    servo, zf, t0 = servo0, 0.0, clock["t"]
    for i in range(polls):
        in_takeup = i < takeup_polls
        if tick is not None:
            tick.takeup_pending = lambda p=in_takeup: p
        servo += step
        zf += step * (0.3 if in_takeup else ratio_at(clock["t"] - t0)) / K
        board.fast_data_values = {
            "servoCurrent": servo & 0xFFFFFFFF, "stepsToGo": 0,
            "scaleCurrent": [0, int(round(zf)), 0, 0]}
        w.poll()
        clock["t"] += POLL_MS
    state["s"] = "stopped"
    w.poll()


def _early_off(early, true=2.0, until_ms=800):
    return lambda t: early if t < until_ms else true


def test_a_pass_that_reads_off_early_but_true_over_the_whole_pass_is_accepted():
    """Through the controller's own wiring: its RatioWatch, held by its
    guard, feeding its survey. Three B passes whose first 0.8 s read 4 %
    low, 2.5 % high and true, then 2.0 for the remaining 9 s. Taken from the
    check's latched verdict they read 1.920, 1.992 and 2.004, a 4.3 % spread,
    and were refused; the whole passes agree, and the factor is 2."""
    ctrl, board, els = _controller()
    ctrl._overshoot._binding = lambda: DEV
    assert ctrl.start_gear_survey("B") is None
    w = ctrl._ratio_watch
    state, clock = {"s": "stopped"}, {"t": 0.0}
    tick = SimpleNamespace(scale_index=lambda: 1, takeup_pending=lambda: False)
    fake = SimpleNamespace(fast_data_values={}, els_stop_values={})
    w._fsm_state = lambda: state["s"]
    w._steps_per_count = lambda: K
    w._mm_per_count = lambda: MM_PER_COUNT
    w._gear = lambda: "A"
    w._gear_table = lambda: ABC
    w._now_ms = lambda: clock["t"]
    w._hal = SimpleNamespace(tick=tick)
    w._board = fake
    w.poll()
    for early in (1.92, 2.05, 2.0):
        _profile_pass(w, fake, clock, state, _early_off(early))
    s = ctrl.overshoot.gear_survey
    assert s.state == gs.PROPOSED, (s.message, s.readings)
    assert s.factor == 2
    assert max(s.readings) / min(s.readings) - 1 < float(gs.GEAR_MAX_SPREAD)


def _held_watch(table=ABC):
    state, verdicts, held = {"s": "stopped"}, [], []
    w, board, clock = _watch(lambda: True, held, verdicts, state, table=table)
    w.poll()
    return w, board, clock, state, held


def test_the_reading_leaves_out_the_take_up_and_the_settle():
    """The take-up polls move the carriage at 0.3 and the next 300 ms read
    1.5; neither is in the reading."""
    w, board, clock, state, held = _held_watch()
    tick = w._hal.tick
    _profile_pass(w, board, clock, state,
                  lambda t: 1.5 if t < 10 * POLL_MS + gs.SETTLE_MS else 2.0,
                  takeup_polls=10, tick=tick)
    reading = held[0][1]
    assert reading.r == pytest.approx(2.0, rel=0.0005)


def test_quantized_counts_read_within_a_twentieth_of_the_gate():
    """Z in whole counts, an irrational-looking factor, a pass of about
    1500 counts: the fitted reading is within 0.03 % of the truth."""
    w, board, clock, state, held = _held_watch()
    _profile_pass(w, board, clock, state, lambda t: 0.4987, polls=80, step=100)
    reading = held[0][1]
    assert reading.counts >= gs.MIN_COUNTS
    assert reading.r == pytest.approx(0.4987, rel=0.0003)


def test_a_short_pass_is_not_counted_and_says_so():
    w, board, clock, state, held = _held_watch()
    # About 700 counts after the settle, over 22 polls.
    _profile_pass(w, board, clock, state, lambda t: 2.0, polls=30, step=40)
    v_, reading = held[0]
    assert reading.r is None and reading.counts < gs.MIN_COUNTS
    s = gs.GearSurvey("B", ABC)
    msg = s.add(v_, reading)
    assert msg.startswith("Pass too short") and msg.endswith(osv.NEXT_PASS)
    assert len(msg) <= 65 and s.readings == []


def test_a_pass_whose_take_up_never_ended_is_not_counted_not_short():
    """A refused take-up ends the pass inside it (2026-10-18, three times
    in position C): nothing was measured, which is not a short pass."""
    w, board, clock, state, held = _held_watch()
    _profile_pass(w, board, clock, state, lambda t: 0.5, polls=30,
                  takeup_polls=30, tick=w._hal.tick)
    v_, reading = held[0]
    assert reading.r is None and reading.polls == 0
    msg = gs.GearSurvey("C", ABC).add(v_, reading)
    assert msg.startswith("Pass not counted")


def test_a_pass_of_too_few_polls_is_not_counted():
    m = gs.WholePassRatio(K)
    for i in range(gs.MIN_POLLS - 1 + 8):        # 8 polls inside the settle
        m.feed(t_ms=i * POLL_MS, servo_steps=i * 5000, z_counts=i * 4000,
               takeup_pending=False)
    reading = m.reading()
    assert reading.r is None and reading.polls < gs.MIN_POLLS


def test_the_reading_survives_the_step_counter_wrapping():
    """servoCurrent is a uint32; RatioWatch unwraps it before the meter."""
    w, board, clock, state, held = _held_watch()
    _profile_pass(w, board, clock, state, lambda t: 2.0,
                  servo0=0xFFFFFFFF - 5000)
    assert held[0][1].r == pytest.approx(2.0, rel=0.0005)


def test_a_pass_toward_the_tailstock_reads_the_same():
    w, board, clock, state, held = _held_watch()
    _profile_pass(w, board, clock, state, lambda t: 2.0, step=-120,
                  servo0=10_000_000)
    assert held[0][1].r == pytest.approx(2.0, rel=0.0005)


def test_no_meter_is_built_for_a_pass_that_is_not_held():
    state, verdicts, held, flag = {"s": "stopped"}, [], [], {"on": False}
    built = []

    def measure(k):
        built.append(k)
        return gs.WholePassRatio(k)

    board = SimpleNamespace(fast_data_values={}, els_stop_values={})
    tick = SimpleNamespace(scale_index=lambda: 1, takeup_pending=lambda: False)
    clock = {"t": 0.0}
    w = R.RatioWatch(SimpleNamespace(tick=tick), board,
                     fsm_state=lambda: state["s"], steps_per_count=lambda: K,
                     gear=lambda: "A", gear_table=lambda: ABC,
                     mm_per_count=lambda: MM_PER_COUNT,
                     on_verdict=verdicts.append, now_ms=lambda: clock["t"],
                     hold=lambda: flag["on"],
                     on_held=lambda vv, r: held.append((vv, r)),
                     measure=measure)
    w.poll()
    _pass(w, board, clock, state, 2.0)
    assert built == [] and held == []
    assert [x.kind for x in verdicts] == [R.GEAR_CHANGE]
    flag["on"] = True
    _pass(w, board, clock, state, 2.0)
    assert built == [pytest.approx(K)] and len(held) == 1


def test_the_meter_is_in_reference_terms_when_another_position_is_expected():
    """steps_per_count carries the expected position's factor; the meter is
    given the reference's, so a pass in B reads 2 whichever position the
    controller expected."""
    state, verdicts, held = {"s": "stopped"}, [], []
    w, board, clock = _watch(lambda: True, held, verdicts, state, gear="C")
    w._steps_per_count = lambda: K * 2      # C = 1/2: twice the steps per count
    w.poll()
    _profile_pass(w, board, clock, state, lambda t: 2.0)   # the lever in B
    assert held[0][1].r == pytest.approx(2.0, rel=0.0005)
