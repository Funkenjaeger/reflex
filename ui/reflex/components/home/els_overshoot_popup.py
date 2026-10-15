"""Stop overshoot measurement: the detection pass and the sweep.

Opened from Advanced ELS Settings. Unlike the backlash calibration, the
controller does not move the carriage itself: the operator cuts air passes
with the ELS bar as usual. So this popup only STARTS a measurement and then
closes, together with Advanced ELS Settings underneath it, to give the ELS
bar back. Each stop's result shows in a strip on the ELS bar until the next
pass starts; open the popup again to see where the measurement is or stop it.
At the end of a sweep the controller opens it by itself, for Save or Discard.
The prompt carries a link and a QR code to the step-by-step guide on the docs
site (first run on the development lathe, 2026-10-11: more instructions, but not more UI). The
rules are in reflex/fsms/els_overshoot_survey.py.
"""
from kivy.logger import Logger
from kivy.properties import ObjectProperty, StringProperty
from kivy.uix.popup import Popup

from reflex.fsms import els_overshoot_survey as sv
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)

load_kv(__file__)


PROMPT_TEXT = (
    "Measure how far the carriage coasts after the stop fires.\n\n"
    "Detect: 1 pass. Does this machine need the correction?\n"
    "Sweep: 3 passes at each of 4 feeds, about 5 minutes.\n\n"
    "Feed mode, stop on, tool clear: every pass is in air."
)

#: The step-by-step guide on the docs site (docs/guide/measuring-stop-overshoot.md).
GUIDE_URL = "https://funkenjaeger.github.io/reflex/guide/measuring-stop-overshoot/"
GUIDE_TEXT = "Step-by-step guide:\nfunkenjaeger.github.io/reflex/guide/measuring-stop-overshoot"

RUNNING_TEXT = (
    "Cut each pass to the stop. The strip on the ELS bar shows each result.\n\n"
    "Sweep: 3 passes at each feed, slowest feed first. The fastest feed is "
    "the top of the table."
)


class OvershootSurveyPopup(Popup):
    # "prompt" | "running" | "fitted" | "result"
    state = StringProperty("prompt")
    body_text = StringProperty(PROMPT_TEXT)
    detail_text = StringProperty("")

    #: The window this one was opened from (Advanced ELS Settings), closed
    #: with it when a measurement starts. None when the controller opened it.
    owner = ObjectProperty(None, allownone=True)
    guide_url = StringProperty(GUIDE_URL)
    guide_text = StringProperty(GUIDE_TEXT)

    def __init__(self, **kv):
        super().__init__(**kv)
        from reflex.app import MainApp
        self.app = MainApp.get_running_app()
        self._refresh()

    @property
    def _guard(self):
        return self.app.els_uic.overshoot

    # ── actions ──────────────────────────────────────────────────────
    def start(self, kind: str):
        if self.app.els_uic.start_overshoot_survey(kind):
            self.dismiss()
            if self.owner is not None:
                self.owner.dismiss()
        else:
            self._refresh()

    def stop(self):
        self._guard.cancel_survey()
        self._refresh()

    def save(self):
        why = self._guard.save_survey()
        if why:
            self.app.els_uic.notify(why)
            self._refresh()
            return
        self.app.els_uic.notify("Overshoot table saved. Set the correction to on to use it.")
        self.dismiss()

    def close(self):
        s = self._guard.survey
        if s is not None and s.done and s.state != sv.FITTED:
            self._guard.cancel_survey()
        self.dismiss()

    # ── presentation ─────────────────────────────────────────────────
    def _refresh(self):
        g = self._guard
        s = g.survey
        status = f"Table: {g.status_text()}."
        if g.last_landing:
            status += f" Last corrected stop: {g.last_landing[len('Stop '):]}"
        if s is None:
            self.state = "prompt"
            self.title = "Measure stop overshoot"
            self.body_text = PROMPT_TEXT
            self.detail_text = status
        elif not s.done:
            self.state = "running"
            self.title = ("Detecting overshoot" if s.kind == sv.DETECT
                          else "Overshoot sweep")
            self.body_text = RUNNING_TEXT
            self.detail_text = s.message
        elif s.state == sv.FITTED:
            self.state = "fitted"
            self.title = "Sweep complete"
            self.body_text = s.detail
            self.detail_text = (f"{status} Save makes this the table. It does "
                                f"not set the correction to on.")
        else:
            self.state = "result"
            self.title = "Overshoot measurement"
            self.body_text = s.detail
            self.detail_text = status
