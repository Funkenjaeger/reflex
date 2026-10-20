"""GearSurveyPopup (reflex/components/home/els_gear_survey_popup.py): which
state it shows and what each button does to the measurement.

Same approach as test_els_overshoot_popup.py: the real methods are bound onto
a bare stand-in that carries `.app`, so no Popup or kv tree is built. The
guard is the real OvershootGuard; the refusals come from the real
ElsUiController.start_gear_survey, called unbound against a stand-in.
"""
import datetime
from fractions import Fraction
from types import SimpleNamespace

from reflex.components.home.els_gear_survey_popup import (
    PROMPT_TEXT,
    RUNNING_TEXT,
    GearSurveyPopup,
)
from reflex.fsms import els_gear_survey as gs
from reflex.fsms import els_ratio as R
from reflex.fsms.els_gear import GearTable
from reflex.fsms.els_overshoot_guard import OvershootGuard
from reflex.fsms.els_overshoot_table import Binding
from reflex.fsms.ui_controller import ElsUiController

DEV = Binding(Fraction(1, 200), Fraction(127, 64000), "A=1, B=2, C=1/2")


class _Uic:
    start_gear_survey = ElsUiController.start_gear_survey

    def __init__(self, guard):
        self.overshoot = guard
        self._overshoot = guard
        self.notices = []
        self._els_fsm = SimpleNamespace(state="idle")

    def notify(self, message, *_a, **_k):
        self.notices.append(message)


class _Owner:
    def __init__(self):
        self.dismissed = 0

    def dismiss(self):
        self.dismissed += 1


class _Bare:
    _guard = GearSurveyPopup._guard
    _refresh = GearSurveyPopup._refresh
    next_position = GearSurveyPopup.next_position
    start = GearSurveyPopup.start
    stop = GearSurveyPopup.stop
    save = GearSurveyPopup.save
    close = GearSurveyPopup.close

    def __init__(self, owner=None, text="A=1, B=2, C=1/2"):
        self.owner = owner
        self.text = text
        els = SimpleNamespace(
            els_overshoot_correction=False, els_overshoot_table="",
            els_overshoot_mm_per_count="", els_overshoot_mm_per_step="",
            els_overshoot_gearbox="", els_overshoot_source="")

        def apply(t):
            self.text = t
            return True

        guard = OvershootGuard(els, binding=lambda: DEV,
                               notify=lambda *a, **k: None,
                               today=lambda: datetime.date(2026, 10, 17),
                               gear_table=lambda: GearTable.parse(self.text),
                               apply_gear_table=apply)
        self.app = SimpleNamespace(els_uic=_Uic(guard))
        self.dismissed = 0
        self.state = self.title = self.body_text = self.detail_text = ""
        self.position = ""
        self.refusal = ""
        self._refresh()

    def dismiss(self):
        self.dismissed += 1


def _passes(p, *readings):
    for r in readings:
        p.app.els_uic.overshoot.on_gear_pass(
            R.Verdict(R.GEAR_CHANGE, "B", r_since_start=r),
            gs.PassReading(r, counts=5000, polls=100))


def test_the_prompt_offers_the_first_non_reference_position():
    p = _Bare()
    assert p.state == "prompt"
    assert p.body_text == PROMPT_TEXT
    assert p.position == "B"


def test_next_position_cycles_the_non_reference_positions_only():
    p = _Bare()
    p.next_position()
    assert p.position == "C"
    p.next_position()
    assert p.position == "B"


def test_the_prompt_with_no_gearbox_offers_no_position():
    p = _Bare(text="")
    assert p.position == ""
    p.start()
    assert p.dismissed == 0
    assert p.refusal


def test_start_closes_the_popup_and_advanced_settings_to_give_the_bar_back():
    owner = _Owner()
    p = _Bare(owner=owner)
    p.start()
    assert p.dismissed == 1 and owner.dismissed == 1
    assert p.app.els_uic.overshoot.gear_survey.position == "B"


def test_a_refused_start_keeps_both_windows_open_and_says_why():
    owner = _Owner()
    p = _Bare(owner=owner)
    p.app.els_uic._els_fsm.state = "cutting"
    p.start()
    assert p.dismissed == 0 and owner.dismissed == 0
    assert p.detail_text.splitlines()[0] == "Pass is running. Wait for the stop."
    assert p.state == "prompt"


def test_running_shows_the_running_text_and_the_latest_line():
    p = _Bare()
    p.start()
    _passes(p, 2.0)
    p._refresh()
    assert p.state == "running"
    assert p.body_text == RUNNING_TEXT
    assert p.detail_text == p.app.els_uic.overshoot.gear_survey.message


def test_proposed_shows_the_factor_and_save_writes_it():
    p = _Bare()
    p.start()
    _passes(p, 1.5, 1.5, 1.5)
    p._refresh()
    assert p.state == "proposed"
    assert "3/2" in p.body_text
    p.save()
    assert p.text == "A=1, B=3/2, C=1/2"
    assert p.dismissed == 2          # once at start, once at save
    assert p.app.els_uic.notices
    assert p.app.els_uic.overshoot.gear_survey is None


def test_discard_writes_nothing_and_returns_to_the_prompt():
    p = _Bare()
    p.start()
    _passes(p, 1.5, 1.5, 1.5)
    p._refresh()
    p.stop()
    assert p.state == "prompt" and p.text == "A=1, B=2, C=1/2"
    assert p.app.els_uic.overshoot.gear_survey is None


def test_a_refused_measurement_is_a_result_and_close_clears_it():
    p = _Bare()
    p.start()
    _passes(p, 1.0, 2.0, 3.0)
    p._refresh()
    assert p.state == "result"
    assert "spread" in p.body_text
    p.close()
    assert p.app.els_uic.overshoot.gear_survey is None


def test_close_keeps_a_running_measurement_going():
    p = _Bare()
    p.start()
    p.close()
    assert p.app.els_uic.overshoot.gear_survey_running


def test_a_refused_save_is_shown_in_the_window():
    p = _Bare()
    p.start()
    _passes(p, 1.5, 1.5, 1.5)
    p.text = "A=1, B=2, C=1/3"
    p._refresh()
    p.save()
    assert "changed" in p.detail_text
    assert p.state == "proposed" and p.text == "A=1, B=2, C=1/3"
