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

WHY SEVERITY IS A PROPERTY AND NOT A COLOUR IN THE KV
-----------------------------------------------------
Every state's body used to render in ``app.theme.text``, so RED_FLAG — whose
own text says "Do not cut" — arrived in the same neutral grey as the routine
jog instructions and the success message. It is derived here rather than in
the kv so the mapping is one table that can be enumerated and tested, and so
adding a state without deciding how loud it is fails loudly instead of
silently inheriting "routine".
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


# How loud each state is, and the caption that names it. Five severities for
# six states, because these are LEVELS rather than a rename of the state: the
# two walkthrough steps are one level ("info", the operator is being walked
# through a procedure), then "caution" is something to go fix at the machine
# before continuing, "success" is done, "refused" is a button that did not take,
# and "fault" is the machine itself being wrong.
#
# The captions exist because colour cannot separate the last two: an ordinary
# refusal and a custody fault are both danger_text. RED_FLAG is the only state
# that means "stop, and go look at the drivetrain", and it is the one state the
# operator must not read as "you pressed that at the wrong time".
SEVERITY_INFO = "info"
SEVERITY_CAUTION = "caution"
SEVERITY_SUCCESS = "success"
SEVERITY_REFUSED = "refused"
SEVERITY_FAULT = "fault"

STATE_SEVERITY = {
    "jog":      (SEVERITY_INFO, ""),
    "align":    (SEVERITY_INFO, ""),
    "drifted":  (SEVERITY_CAUTION, "CARRIAGE DRIFTED — RE-SEAT IT BY HAND"),
    "latched":  (SEVERITY_SUCCESS, "THREAD REFERENCE LATCHED"),
    "refused":  (SEVERITY_REFUSED, "REFUSED — NOTHING WAS LATCHED"),
    "red_flag": (SEVERITY_FAULT, "DO NOT CUT — Z POSITION NOT TRUSTWORTHY"),
}

# An unmapped state is a programming error, and it is coerced UP — the same
# rule reflex/utils/notices.py applies to an unknown notice severity, for the
# same reason: over-warning is noise, under-warning is a message the operator
# learns to ignore. Raising here instead would take the app down at the lathe.
FALLBACK_SEVERITY = (SEVERITY_FAULT, "UNEXPECTED STATE — DO NOT CUT")


class ThreadResyncPopup(Popup):

    # "jog" | "align" | "drifted" | "latched" | "red_flag" | "refused"
    state = StringProperty("jog")
    body_text = StringProperty(JOG_TEXT)
    live_text = StringProperty("")
    confirm_enabled = BooleanProperty(False)

    # Derived from `state`; the kv colours the whole modal off these two.
    severity = StringProperty(SEVERITY_INFO)
    severity_caption = StringProperty("")

    def __init__(self, **kv):
        super().__init__(**kv)
        from reflex.app import MainApp
        self.app = MainApp.get_running_app()
        self._resync = self._build_controller()
        self._poll_ev = None
        # on_state does not fire for the property's default, so the opening
        # state has to be classified explicitly rather than relying on the
        # declared defaults happening to agree with the table.
        self._apply_severity(self.state)

    # ── severity ─────────────────────────────────────────────────────
    def on_state(self, _instance, value):
        self._apply_severity(value)

    def _apply_severity(self, state: str) -> None:
        pair = STATE_SEVERITY.get(state)
        if pair is None:
            log.error("els_resync popup: no severity for state %r", state)
            pair = FALLBACK_SEVERITY
        self.severity, self.severity_caption = pair

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
            # Two explicit lines rather than one long one padded with spaces:
            # the readout is rendered a fifth larger than the body prose now,
            # and a single line of it wraps at this modal's width — where it
            # breaks would then be decided by the length of the spindle word.
            self.live_text = (
                f"Z hold: {self._resync.z_delta_counts:+d} counts "
                f"(tolerance ±{tol})\n"
                f"Spindle: {spindle}"
            )
            self.confirm_enabled = self._resync.confirm_allowed
            return
        if state == ResyncState.DRIFTED:
            if self.state != "drifted":
                self.state = "drifted"
                self.body_text = DRIFTED_TEXT
            self.live_text = (
                f"Z offset from baseline: "
                f"{self._resync.z_delta_counts:+d} counts\n"
                f"Tolerance: ±{self._resync.tolerance_counts} counts"
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
