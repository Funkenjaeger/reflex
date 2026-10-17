"""OvershootSurveyPopup (reflex/components/home/els_overshoot_popup.py): which
state it shows, and what each button does to the measurement.

The same approach as test_els_settings_popup.py: the real methods are bound
onto a bare stand-in that carries `.app`, so no Popup or kv tree is built. The
guard behind it is the real OvershootGuard over plain settings, and the
refusals come from the real ElsUiController.start_overshoot_survey, called
unbound against the same kind of stand-in.
"""
import datetime
import logging
from fractions import Fraction
from types import SimpleNamespace

from reflex.components.home.els_overshoot_popup import (
    PROMPT_TEXT,
    RUNNING_TEXT,
    OvershootSurveyPopup,
)
from reflex.fsms import els_overshoot_survey as sv
from reflex.fsms.ui_controller import ElsUiController
from reflex.fsms.els_overshoot_guard import OvershootGuard
from reflex.fsms.els_overshoot_table import Binding
from reflex.fsms.els_stop_outcome import StopOutcome

DEV = Binding(Fraction(1, 200), Fraction(127, 64000), "")


class _Uic:
    start_overshoot_survey = ElsUiController.start_overshoot_survey

    def __init__(self, guard):
        self.overshoot = guard
        self._overshoot = guard
        self.notices = []
        self.is_threading = False
        self._els_fsm = SimpleNamespace(state="idle")

    def notify(self, message, *_a, **_k):
        self.notices.append(message)


class _Bare:
    _guard = OvershootSurveyPopup._guard
    _refresh = OvershootSurveyPopup._refresh
    start = OvershootSurveyPopup.start
    stop = OvershootSurveyPopup.stop
    save = OvershootSurveyPopup.save
    close = OvershootSurveyPopup.close

    def __init__(self, owner=None):
        self.owner = owner
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
        self.refusal = ""
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


class _Owner:
    def __init__(self):
        self.dismissed = 0

    def dismiss(self):
        self.dismissed += 1


def test_start_closes_advanced_els_settings_too():
    """first run on the development lathe, 2026-10-11: Detect closed the Measure window but left
    Advanced ELS Settings open on top of the ELS bar."""
    owner = _Owner()
    p = _Bare(owner=owner)
    p.start("detect")
    assert p.dismissed == 1 and owner.dismissed == 1


def test_a_refused_start_leaves_advanced_els_settings_open():
    owner = _Owner()
    p = _Bare(owner=owner)
    p.app.els_uic.is_threading = True
    p.start("sweep")
    assert owner.dismissed == 0


def test_the_prompt_shows_the_last_corrected_landing():
    p = _Bare()
    p.app.els_uic.overshoot.last_landing = "Stop landed 2 counts (0.010 mm) short."
    p._refresh()
    assert p.detail_text.endswith("Last corrected stop: landed 2 counts (0.010 mm) short.")


def test_a_refused_start_keeps_the_popup_open():
    p = _Bare()
    p.app.els_uic.is_threading = True
    p.start("detect")
    assert p.dismissed == 0 and p.state == "prompt"


THREAD_MODE = "Thread mode is on. Set Feed mode to measure."


def test_a_refusal_in_thread_mode_is_shown_in_the_window_and_logged(caplog):
    """The status bar notice is covered by this window and by Advanced ELS
    Settings, so the refusal has to be readable in the window itself."""
    p = _Bare()
    p.app.els_uic.is_threading = True
    with caplog.at_level(logging.WARNING):
        p.start("detect")
    assert p.detail_text.splitlines()[0] == THREAD_MODE
    assert "Table: None stored." in p.detail_text
    assert p.state == "prompt" and p.dismissed == 0
    assert any("overshoot measurement refused: " + THREAD_MODE
               in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
    assert p.app.els_uic.notices == [THREAD_MODE]


def test_a_refusal_while_a_pass_runs_shows_its_reason():
    p = _Bare()
    p.app.els_uic._els_fsm.state = "cutting"
    p.start("sweep")
    assert p.detail_text.splitlines()[0] == "Pass is running. Wait for the stop."
    assert p.dismissed == 0


def test_a_refusal_from_the_guard_shows_its_reason():
    p = _Bare()
    p.app.els_uic.overshoot._binding = lambda: None
    p.start("detect")
    assert p.detail_text.splitlines()[0] == "Set the Z scale and the leadscrew first."


def test_the_refusal_stays_until_the_next_press_then_goes():
    p = _Bare()
    uic = p.app.els_uic
    uic.is_threading = True
    p.start("detect")
    p._refresh()
    assert p.detail_text.startswith(THREAD_MODE)
    uic.is_threading = False
    p.start("detect")
    assert p.refusal == ""
    assert p.dismissed == 1


def test_stop_clears_the_refusal():
    p = _Bare()
    p.app.els_uic.is_threading = True
    p.start("detect")
    p.stop()
    assert p.refusal == "" and THREAD_MODE not in p.detail_text


def test_a_refused_save_is_shown_in_the_window():
    p = _Bare()
    p.start("sweep")
    g = p.app.els_uic.overshoot
    _full_sweep(g.survey)
    p._refresh()
    g._binding = lambda: Binding(Fraction(1, 100), Fraction(127, 64000), "")
    p.save()
    why = "Settings changed during the sweep. Run it again."
    assert p.state == "fitted" and p.detail_text.splitlines()[0] == why
    assert p.dismissed == 1


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
