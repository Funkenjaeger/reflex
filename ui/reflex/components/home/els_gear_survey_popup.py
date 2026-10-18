"""Gearbox factor measurement: three air passes in one lever position.

Opened from Advanced ELS Settings, next to the typed Gearbox table field,
which stays as it was. Like the overshoot measurement the controller does not
move the carriage: the operator puts the lever in the position, cuts the passes
with the ELS bar as usual, and each result shows in the strip on the ELS bar.
So this popup only STARTS a measurement and then closes, together with
Advanced ELS Settings underneath it, to give the ELS bar back. At the end the
controller opens it by itself, for Save or Discard. The rules are in
reflex/fsms/els_gear_survey.py; the hold on the coupling check while it runs is
in els_ratio.RatioWatch.
"""
from kivy.logger import Logger
from kivy.properties import ObjectProperty, StringProperty
from kivy.uix.popup import Popup

from reflex.fsms import els_gear_survey as sv
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)

load_kv(__file__)


PROMPT_TEXT = (
    "Measure one gearbox position's factor.\n\n"
    "Put the lever in the position shown, with the stop on and the tool "
    "clear. Then cut 3 passes in air in Feed mode.\n\n"
    "The first position is the reference: its factor is 1."
)

RUNNING_TEXT = (
    "Cut each pass to the stop. The strip on the ELS bar shows each result.\n\n"
    "The coupling check does not act while this runs."
)


class GearSurveyPopup(Popup):
    # "prompt" | "running" | "proposed" | "result"
    state = StringProperty("prompt")
    body_text = StringProperty(PROMPT_TEXT)
    detail_text = StringProperty("")
    #: The lever position to measure; the prompt cycles it.
    position = StringProperty("")
    #: Why the last Start or Save press was refused. Shown on a line of its
    #: own at the top of the detail text until the next press or Discard.
    refusal = StringProperty("")

    #: The window this one was opened from (Advanced ELS Settings), closed
    #: with it when a measurement starts. None when the controller opened it.
    owner = ObjectProperty(None, allownone=True)

    def __init__(self, **kv):
        super().__init__(**kv)
        from reflex.app import MainApp
        self.app = MainApp.get_running_app()
        self._refresh()

    @property
    def _guard(self):
        return self.app.els_uic.overshoot

    # ── actions ──────────────────────────────────────────────────────
    def next_position(self):
        names = self._guard.gear_table().names[1:]
        if names:
            i = names.index(self.position) + 1 if self.position in names else 0
            self.position = names[i % len(names)]

    def start(self):
        self.refusal = ""
        why = self.app.els_uic.start_gear_survey(self.position)
        if why is None:
            self.dismiss()
            if self.owner is not None:
                self.owner.dismiss()
        else:
            self.refusal = why
            self._refresh()

    def stop(self):
        self.refusal = ""
        self._guard.cancel_gear_survey()
        self._refresh()

    def save(self):
        self.refusal = ""
        why = self._guard.save_gear_survey()
        if why:
            self.app.els_uic.notify(why)
            self.refusal = why
            self._refresh()
            return
        self.app.els_uic.notify("Gearbox factor saved.")
        self.dismiss()

    def close(self):
        s = self._guard.gear_survey
        if s is not None and s.done and s.state != sv.PROPOSED:
            self._guard.cancel_gear_survey()
        self.dismiss()

    # ── presentation ─────────────────────────────────────────────────
    def _refresh(self):
        g = self._guard
        s = g.gear_survey
        names = g.gear_table().names[1:]
        if self.position not in names:
            self.position = names[0] if names else ""
        if s is None:
            self.state = "prompt"
            self.title = "Measure gearbox factor"
            self.body_text = PROMPT_TEXT
            self.detail_text = (f"Position: {self.position}." if self.position
                                else "No gearbox table.")
        elif not s.done:
            self.state = "running"
            self.title = f"Measuring gear {s.position}"
            self.body_text = RUNNING_TEXT
            self.detail_text = s.message
        elif s.state == sv.PROPOSED:
            self.state = "proposed"
            self.title = f"Gear {s.position} measured"
            self.body_text = s.detail
            self.detail_text = ("Save writes it to the gearbox table. "
                                "Discard keeps the table as it is.")
        else:
            self.state = "result"
            self.title = "Gearbox measurement"
            self.body_text = s.detail
            self.detail_text = ""
        if self.refusal:
            self.detail_text = f"{self.refusal}\n{self.detail_text}"
