"""Backlash calibration wizard.

A modal that walks the operator through one closed-loop calibration run:
confirm the machine is safe, run it, judge the result, accept or reject.

WHY THIS IS A MODAL AND NOT A BACKGROUND ACTION
-----------------------------------------------
The run physically moves the carriage, bidirectionally, with the half-nut
engaged. Travel is small (a few lash widths — well under a millimetre) but it is
unannounced motion on a machine tool, so it gets an explicit "I have checked
this" gate rather than a button that just starts moving things.

WHAT IT REFUSES TO DO
---------------------
It does not widen the acceptance spread to make a run succeed, and it offers no
"use it anyway" on an inconsistent result. Three measurements that disagree mean
the measurement is not reproducible, and that is a finding about the machine —
the same class of fault that would silently corrupt every other ELS operation.
The wizard's job there is to say so clearly and stop.
"""
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import BooleanProperty, ObjectProperty, StringProperty
from kivy.uix.popup import Popup

from reflex.fsms.els_cal import BacklashCalibration, CalState
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)

load_kv(__file__)


PROMPT_TEXT = (
    "This will move the carriage a short distance back and forth to measure "
    "leadscrew backlash.\n\n"
    "Before starting:\n"
    "  1.  Engage the half-nut.\n"
    "  2.  Move the tool clear of the workpiece and the chuck.\n"
    "  3.  Stop the spindle.\n\n"
    "Travel is under a millimetre, but the carriage WILL move."
)


class BacklashCalPopup(Popup):
    bar = ObjectProperty(None)

    # "prompt" | "running" | "passed" | "inconsistent" | "refused"
    state = StringProperty("prompt")
    body_text = StringProperty(PROMPT_TEXT)
    detail_text = StringProperty("")
    busy = BooleanProperty(False)

    def __init__(self, **kv):
        super().__init__(**kv)
        from reflex.app import MainApp
        self.app = MainApp.get_running_app()
        self._cal = BacklashCalibration(self.app.els_uic.hal, self.app.els)
        self._poll_ev = None
        self._refresh()

    # ── actions ──────────────────────────────────────────────────────
    def start(self):
        if self.state == "running":
            return
        if not self._cal.start():
            self.state = "refused"
            self._refresh()
            return
        self.state = "running"
        self.busy = True
        self._refresh()
        # Poll at UI cadence. The firmware's own ceiling bounds the run; this
        # only observes it, and BacklashCalibration carries a liveness timeout
        # so a wedged link cannot leave the modal spinning forever.
        self._poll_ev = Clock.schedule_interval(self._tick, 1 / 30.0)

    def _tick(self, _dt):
        result = self._cal.poll()
        if result == CalState.RUNNING:
            # Keep the modal visibly alive. A slow sweep moves the carriage
            # under a millimetre over tens of seconds, which is indistinguishable
            # from a hung machine — that is how a healthy run got abandoned.
            self.detail_text = self._cal.progress_text
            return
        self._stop_polling()
        self.busy = False
        self.state = {
            CalState.PASSED: "passed",
            CalState.INCONSISTENT: "inconsistent",
            CalState.REFUSED: "refused",
        }.get(result, "refused")
        self._refresh()

    def accept(self):
        """Commit a passed run, then close."""
        if self._cal.commit():
            log.info("els_cal wizard: accepted %d steps (command %d)",
                     self._cal.mean_steps, self._cal.command_steps)
            if self.bar is not None and hasattr(self.bar, "refresh_backlash"):
                self.bar.refresh_backlash()
        self.dismiss()

    def retry(self):
        self.state = "prompt"
        self._cal.cancel()
        self._refresh()

    def cancel(self):
        self._stop_polling()
        self._cal.cancel()
        self.dismiss()

    def on_dismiss(self):
        # A run already in flight finishes in firmware regardless; dropping the
        # poll just stops us watching. Nothing is committed unless accept() ran.
        self._stop_polling()
        return super().on_dismiss()

    # ── presentation ─────────────────────────────────────────────────
    def _stop_polling(self):
        if self._poll_ev is not None:
            self._poll_ev.cancel()
            self._poll_ev = None

    def _refresh(self):
        if self.state == "prompt":
            self.title = "Calibrate leadscrew backlash"
            self.body_text = PROMPT_TEXT
            self.detail_text = self._previous_text()
        elif self.state == "running":
            self.title = "Calibrating…"
            self.body_text = (
                "Measuring backlash — three reversals.\n\n"
                "Keep clear of the carriage."
            )
            self.detail_text = ""
        elif self.state == "passed":
            self.title = "Calibration complete"
            self.body_text = self._cal.message
            self.detail_text = self._drift_text()
        elif self.state == "inconsistent":
            self.title = "Calibration not accepted"
            self.body_text = self._cal.message
            self.detail_text = (
                "This usually means something in the drivetrain is not "
                "repeatable. It is worth finding before cutting a thread."
            )
        else:
            self.title = "Calibration failed"
            self.body_text = self._cal.message or "Calibration failed."
            self.detail_text = ""

    def _previous_text(self) -> str:
        previous = int(self.app.els.els_cal_last_measured_steps or 0)
        if not previous:
            return "No previous calibration stored."
        command = int(self.app.els.els_backlash_steps or 0)
        return (f"Previous: {previous} steps measured, "
                f"take-up commanded at {command} steps.")

    def _drift_text(self) -> str:
        drift = self._cal.drift_steps
        if not drift:
            return ""
        direction = "more" if drift > 0 else "less"
        return (f"That is {abs(drift)} steps {direction} than the last stored "
                f"measurement. A large change is worth investigating.")
