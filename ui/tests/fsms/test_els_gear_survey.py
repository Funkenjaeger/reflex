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


def run(position, readings, table=ABC):
    s = gs.GearSurvey(position, table)
    msgs = [s.add(v(r)) for r in readings]
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
    m1 = s.add(R.Verdict(R.UNKNOWN))
    m2 = s.add(R.Verdict(R.DECOUPLED, r_since_start=0.0))
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
    s.add(v(5.0))
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
            seen.append(s.add(v(r)))
        seen.append(s.message)
    s = gs.GearSurvey("Xyz", table)
    seen.append(s.add(R.Verdict(R.UNKNOWN)))
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
        rig.guard.on_gear_pass(v(r))


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
                     hold=hold, on_held=held.append)
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
    assert held[0].r_since_start == pytest.approx(2.0, rel=0.01)


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
    assert held[0].r_since_start == pytest.approx(1.0, rel=0.01)


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
        ctrl.overshoot.on_gear_pass(v(r))
    assert ctrl.overshoot.save_gear_survey() is None
    assert board.servo.gearbox_table == "A=1, B=3/2, C=1/2"


def test_the_typed_gearbox_table_path_parses_unchanged():
    ctrl, board, els = _controller()
    assert ctrl.set_gear_table("A=1 B=3/2, C=0.5") is True
    assert board.servo.gearbox_table == "A=1, B=3/2, C=1/2"
    assert ctrl.set_gear_table("A=1, B=1.1") is False
    assert ctrl.set_gear_table("") is True
    assert board.servo.gearbox_table == ""
