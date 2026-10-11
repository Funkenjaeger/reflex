"""OvershootSurveyPopup (reflex/components/home/els_overshoot_popup.py): which
state it shows, and what each button does to the measurement.

The same approach as test_els_settings_popup.py: the real methods are bound
onto a bare stand-in that carries `.app`, so no Popup or kv tree is built. The
guard behind it is the real OvershootGuard over plain settings.
"""
import datetime
from fractions import Fraction
from types import SimpleNamespace

from reflex.components.home.els_overshoot_popup import (
    PROMPT_TEXT,
    RUNNING_TEXT,
    OvershootSurveyPopup,
)
from reflex.fsms import els_overshoot_survey as sv
from reflex.fsms.els_overshoot_guard import OvershootGuard
from reflex.fsms.els_overshoot_table import Binding
from reflex.fsms.els_stop_outcome import StopOutcome

DEV = Binding(Fraction(1, 200), Fraction(127, 64000), "")


class _Uic:
    def __init__(self, guard):
        self.overshoot = guard
        self.notices = []
        self.refuse = False

    def notify(self, message, *_a, **_k):
        self.notices.append(message)

    def start_overshoot_survey(self, kind):
        if self.refuse:
            return False
        return self.overshoot.start_survey(kind) is None


class _Bare:
    _guard = OvershootSurveyPopup._guard
    _refresh = OvershootSurveyPopup._refresh
    start = OvershootSurveyPopup.start
    stop = OvershootSurveyPopup.stop
    save = OvershootSurveyPopup.save
    close = OvershootSurveyPopup.close

    def __init__(self):
        self.els = SimpleNamespace(
            els_overshoot_correction=False, els_overshoot_table="",
            els_overshoot_mm_per_count="", els_overshoot_mm_per_step="",
            els_overshoot_gearbox="", els_overshoot_source="")
        guard = OvershootGuard(self.els, binding=lambda: DEV,
                               notify=lambda *a, **k: None,
                               today=lambda: datetime.date(2026, 10, 11))
        self.app = SimpleNamespace(els_uic=_Uic(guard))
        self.dismissed = 0
        self.state = self.title = self.body_text = self.detail_text = ""
        self._refresh()

    def dismiss(self):
        self.dismissed += 1


def _stop(coast, rate, seq):
    return StopOutcome(seq=seq, offset=0, rate=rate, stop_position=1000,
                       trigger_z=1000, settled_z=1000 - coast, direction=-1,
                       steps_to_go=0)


def _full_sweep(survey):
    seq = 0
    for rate, coasts in ((600.0, (3, 3, 4)), (1200.0, (8, 9, 9)),
                         (2400.0, (28, 29, 29)), (3500.0, (41, 42, 43))):
        for c in coasts:
            seq += 1
            survey.add(_stop(c, rate, seq))


def test_no_measurement_shows_the_prompt_and_the_table_status():
    p = _Bare()
    assert p.state == "prompt"
    assert p.body_text == PROMPT_TEXT
    assert p.detail_text == "Table: None stored."


def test_start_closes_the_popup_to_give_the_els_bar_back():
    p = _Bare()
    p.start("sweep")
    assert p.dismissed == 1
    assert p.app.els_uic.overshoot.survey.kind == sv.SWEEP


def test_a_refused_start_keeps_the_popup_open():
    p = _Bare()
    p.app.els_uic.refuse = True
    p.start("detect")
    assert p.dismissed == 0 and p.state == "prompt"


def test_reopened_during_a_sweep_shows_progress_and_stop_cancels_it():
    p = _Bare()
    p.start("sweep")
    g = p.app.els_uic.overshoot
    g.survey.add(_stop(3, 600.0, 1))
    p._refresh()
    assert p.state == "running" and p.body_text == RUNNING_TEXT
    assert p.detail_text == g.survey.message
    p.stop()
    assert g.survey is None and p.state == "prompt"


def test_a_fitted_sweep_offers_save_and_save_stores_without_turning_it_on():
    p = _Bare()
    p.start("sweep")
    _full_sweep(p.app.els_uic.overshoot.survey)
    p._refresh()
    assert p.state == "fitted"
    p.save()
    assert p.els.els_overshoot_table.endswith(":0.215")
    assert p.els.els_overshoot_source == "measured 2026-10-11"
    assert p.els.els_overshoot_correction is False
    assert p.dismissed == 2


def test_closing_a_finished_detection_clears_it():
    p = _Bare()
    p.start("detect")
    g = p.app.els_uic.overshoot
    g.survey.add(_stop(9, 1200.0, 1))
    p._refresh()
    assert p.state == "result" and "yes" in p.body_text.lower()
    p.close()
    assert g.survey is None


def test_closing_a_fitted_sweep_keeps_it_for_saving():
    p = _Bare()
    p.start("sweep")
    g = p.app.els_uic.overshoot
    _full_sweep(g.survey)
    p.close()
    assert g.survey is not None and g.survey.state == sv.FITTED
