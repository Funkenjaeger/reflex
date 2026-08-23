"""Pick-up-existing-thread wizard (manual reference latch).

Walks the operator through establishing a thread reference on an existing
thread — a re-chucked part, a thread cut elsewhere, a damaged thread being
chased — so subsequent ELS passes follow the existing groove. The procedure
and its rationale live in ``reflex/fsms/els_resync.py``; this modal only
renders it.

WHY THE X-CLEAR WARNING IS TEXT, NOT AN INTERLOCK
-------------------------------------------------
The major diameter is only known to the software in wizard mode, and even
there it is operator-entered — so a hard interlock would gate on a number the
software cannot trust. The warning is carried prominently in the jog step
itself instead (decision 2026-08-08).
"""
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.popup import Popup

from reflex.fsms.els_resync import ResyncState, ThreadResync
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)

load_kv(__file__)


JOG_TEXT = (
    "Pick up an existing thread. Once synced, every pass will follow the "
    "existing groove.\n\n"
    "MAKE SURE THE TOOL IS CLEAR OF THE WORK IN X BEFORE JOGGING.\n\n"
    "  1.  Jog the carriage in the CUTTING direction only, until the tool "
    "tip is over the threaded section. Jogging in the cutting direction "
    "loads the leadscrew backlash on the side a pass starts from.\n"
    "  2.  Stop there. Do NOT fine-tune the position by jogging, and do not "
    "jog again after this point."
)

ALIGN_TEXT = (
    "Ease the tool into the groove — without jogging:\n\n"
    "  •  Rotate the SPINDLE by hand.\n"
    "  •  Feed the CROSS-SLIDE in until the tool tip nests in the "
    "existing groove.\n"
    "  •  Leave the carriage alone — the Z scale is being watched.\n\n"
    "Confirm arms when the tool position is held and the spindle has been "
    "still for a moment."
)

DRIFTED_TEXT = (
    "The carriage has drifted off its jogged position — the leadscrew's free "
    "play lets it creep toward the cut.\n\n"
    "Nudge the carriage BACK by hand (against the jog direction) until it "
    "seats firmly against the leadscrew, then press Re-seated. Because that "
    "is a mechanical stop the carriage was already sitting against, the Z "
    "readout must come back to almost exactly the same count."
)

AIR_PASS_TEXT = (
    "\n\nBefore cutting metal: run an AIR PASS. Back the tool slightly clear "
    "of the thread in X, run one pass, and watch the tip track the existing "
    "groove. Software cannot detect a tool confirmed in the wrong place — "
    "the air pass is what catches it. If it tracks, feed in and cut."
)


class ThreadResyncPopup(Popup):

    # "jog" | "align" | "drifted" | "latched" | "red_flag" | "refused"
    state = StringProperty("jog")
    body_text = StringProperty(JOG_TEXT)
    live_text = StringProperty("")
    confirm_enabled = BooleanProperty(False)

    def __init__(self, **kv):
        super().__init__(**kv)
        from reflex.app import MainApp
        self.app = MainApp.get_running_app()
        self._resync = self._build_controller()
        self._poll_ev = None

    def _build_controller(self):
        els = self.app.els
        z_axis = els.get_z_axis()
        spindle_axis = els.get_spindle_axis()
        z_input = z_axis._primary_input() if z_axis is not None else None
        sp_input = (spindle_axis._primary_input()
                    if spindle_axis is not None else None)
        if z_input is None or sp_input is None:
            return None
        return ThreadResync(
            self.app.els_uic.hal,
            els,
            read_z_counts=lambda: int(z_input.encoderCurrent),
            read_spindle_counts=lambda: int(sp_input.encoderCurrent),
        )

    # ── actions ──────────────────────────────────────────────────────
    def begin(self):
        """Operator finished the coarse jog."""
        if self._resync is None:
            self.state = "refused"
            self.body_text = ("No Z or spindle axis is assigned — map them in "
                              "setup and connect the controller first.")
            return
        if not self._resync.begin_alignment():
            self.state = "refused"
            self.body_text = self._resync.message
            return
        self.state = "align"
        self.body_text = ALIGN_TEXT
        self._poll_ev = Clock.schedule_interval(self._tick, 1 / 30.0)

    def confirm(self):
        if self._resync is None or not self._resync.request_latch():
            return
        self.confirm_enabled = False
        # State stays "align" while the ack round-trips (a few Modbus polls);
        # _tick routes to latched / refused / red_flag when it lands.

    def reseat(self):
        if self._resync is None:
            return
        if self._resync.reseat_check():
            self.state = "align"
            self.body_text = ALIGN_TEXT
        else:
            self._show_terminal("red_flag")

    def cancel(self):
        self._stop_polling()
        if self._resync is not None:
            self._resync.cancel()
        self.dismiss()

    def on_dismiss(self):
        # A latch already acked stays latched in firmware — dismissing only
        # stops the watching. Anything short of LATCHED leaves no state behind.
        self._stop_polling()
        return super().on_dismiss()

    # ── polling ──────────────────────────────────────────────────────
    def _tick(self, _dt):
        state = self._resync.poll()
        if state == ResyncState.ALIGNING or state == ResyncState.LATCH_REQUESTED:
            tol = self._resync.tolerance_counts
            spindle = ("still" if self._resync.spindle_still
                       else f"settling {int(self._resync.stillness_fraction * 100)}%")
            self.live_text = (
                f"Z hold: {self._resync.z_delta_counts:+d} counts "
                f"(tolerance ±{tol})    Spindle: {spindle}"
            )
            self.confirm_enabled = self._resync.confirm_allowed
            return
        if state == ResyncState.DRIFTED:
            if self.state != "drifted":
                self.state = "drifted"
                self.body_text = DRIFTED_TEXT
            self.live_text = (
                f"Z offset from baseline: {self._resync.z_delta_counts:+d} "
                f"counts (tolerance ±{self._resync.tolerance_counts})"
            )
            self.confirm_enabled = False
            return
        if state == ResyncState.LATCHED:
            self._stop_polling()
            self.state = "latched"
            self.body_text = self._resync.message + AIR_PASS_TEXT
            self.live_text = ""
            return
        if state in (ResyncState.RED_FLAG, ResyncState.REFUSED):
            self._show_terminal(
                "red_flag" if state == ResyncState.RED_FLAG else "refused")

    def _show_terminal(self, popup_state: str):
        self._stop_polling()
        self.state = popup_state
        self.body_text = self._resync.message
        self.live_text = ""
        self.confirm_enabled = False

    def _stop_polling(self):
        if self._poll_ev is not None:
            self._poll_ev.cancel()
            self._poll_ev = None
