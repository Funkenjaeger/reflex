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


# ── BASE TEXT vs HELP TEXT (bench 2026-08-24) ────────────────────────
# The jog body used to open with two sentences of what-happens-next, so
# reading the bottom of the steps pushed the next stage off the 600 px
# screen. The base strings below are bare steps IN EXECUTION ORDER — what to
# do, nothing about why — and every rationale (why anti-cutting, what loading
# the lash means, why Z is watched) moved to HELP_TEXT, rendered on demand by
# the HelpButton in the button row. The ANTI-CUTTING instruction and the
# tool-clear-in-X warning stay in the base: they are the safety content, and
# help is optional reading.

JOG_TEXT = (
    "MAKE SURE THE TOOL IS CLEAR OF THE WORK IN X.\n\n"
    "  1.  Move the carriage BY HAND until the tool tip is over the "
    "threaded section.\n"
    "  2.  Close the HALF NUT.\n"
    "  3.  Pull the carriage back BY HAND in the ANTI-CUTTING direction "
    "(opposite the feed) until it seats firmly.\n"
    "  4.  Stop. Do not move the carriage again."
)

ALIGN_TEXT = (
    "Ease the tool into the groove — no jogging:\n\n"
    "  1.  Rotate the SPINDLE by hand.\n"
    "  2.  Feed the CROSS-SLIDE in until the tool tip nests in the "
    "groove.\n"
    "  3.  Leave the carriage alone.\n\n"
    "Confirm arms when Z holds and the spindle is still."
)

DRIFTED_TEXT = (
    "The carriage has crept off its seated position.\n\n"
    "Nudge it BACK by hand — the ANTI-CUTTING direction, the same way you "
    "seated it — until it seats firmly, then press Re-seated."
)

HELP_TITLE = "Picking up an existing thread"

HELP_TEXT = (
    "Use this on a thread this job did not cut — a re-chucked part, a "
    "thread cut elsewhere, a damaged thread being chased. Once the "
    "reference is latched, every pass follows the existing groove.\n\n"
    "Why pull back in the ANTI-CUTTING direction: the half nut sits on the "
    "leadscrew with free play (backlash). Pulling against the feed seats "
    "the carriage on the flank the leadscrew pushes from during a pass, so "
    "the reference is taken with that play already used up. Seated the "
    "other way, the first pass starts by falling through the free play, and "
    "the reference is off by the whole clearance. The same free play is "
    "what lets a seated carriage creep toward the cut — which is why the "
    "wizard may ask you to re-seat it.\n\n"
    "Why the tool must be clear in X: the software cannot know the major "
    "diameter, so nothing can stop the tool touching the work while the "
    "carriage is moved by hand.\n\n"
    "Why Z is watched: from the seat onward, the carriage position IS the "
    "reference. A re-seat presses against the same mechanical stop, so the "
    "Z readout must return to almost exactly the same count — landing "
    "anywhere else means the Z scale cannot be trusted, and the wizard "
    "says so.\n\n"
    "After latching, run an AIR PASS before cutting metal: back the tool "
    "clear in X, run one pass, and watch the tip track the existing "
    "groove. Software cannot detect a tool confirmed in the wrong place — "
    "the air pass is what catches it."
)

# ── HOW LONG A WALKTHROUGH BODY MAY BE ───────────────────────────────
# The same cheap guard els_phase_offset_popup.py keeps for its messages,
# adapted for bodies that carry their own newlines: the budget is rendered
# LINES (wrapped at the 52 chars/line measured there at this same width and
# font), because a flat character count cannot see a numbered list.
#
# Ceilings from the machine's 600 px screen at font_size 24 (~29 px/line):
# jog shows neither banner nor live readout, ~13 lines fit; align adds the
# live readout, ~10; drifted adds the banner too, ~9. Each budget sits a
# line under its ceiling so a sentence added in review fails in the unit
# suite before it scrolls at the lathe.
BODY_WRAP_CHARS = 52
BODY_LINE_BUDGETS = {"jog": 12, "align": 9, "drifted": 8}

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
    # A question, so "caution" rather than "refused": nothing has gone wrong
    # and no button failed -- the operator is being asked to authorise
    # something consequential.
    "confirm_overwrite": (SEVERITY_CAUTION, "THIS JOB ALREADY HAS A REFERENCE"),
    "red_flag": (SEVERITY_FAULT, "DO NOT CUT — Z POSITION NOT TRUSTWORTHY"),
}

# An unmapped state is a programming error, and it is coerced UP — the same
# rule reflex/utils/notices.py applies to an unknown notice severity, for the
# same reason: over-warning is noise, under-warning is a message the operator
# learns to ignore. Raising here instead would take the app down at the lathe.
FALLBACK_SEVERITY = (SEVERITY_FAULT, "UNEXPECTED STATE — DO NOT CUT")


def z_distance_formatter(z_input, formats, unit_label):
    """Build the callable ThreadResync renders Z distances with.

    MODULE-LEVEL ON PURPOSE. It was a closure inside _build_controller for
    exactly one morning, and in that time previews/preview_walkthrough_shots.py
    -- which constructs its own ThreadResync rather than going through the
    popup -- kept rendering raw counts into every shipped re-sync screenshot
    while the machine rendered millimetres. Anything that stands this screen up
    outside the app calls this, and there is one definition to drift from.

    A DELTA, so no offsets and no absolute frame: counts x the input's ratio x
    the unit factor. Axis.scaled_from_encoder would add abs_offset and the
    active work offset, which is right for a position and wrong for the gap
    between two of them.
    """
    def format_z(counts, signed=True):
        mm = (float(counts) * z_input.ratioNum / z_input.ratioDen
              * float(formats.factor))
        text = formats.position_format.format(mm)
        if not signed:
            text = text.lstrip("+")
        return f"{text} {unit_label}"

    return format_z


class ThreadResyncPopup(Popup):

    # "jog" | "align" | "drifted" | "latched" | "red_flag" | "refused"
    state = StringProperty("jog")
    body_text = StringProperty(JOG_TEXT)
    live_text = StringProperty("")
    # Read at capture time rather than bound: this popup is opened for one
    # alignment and dismissed, so a units switch mid-alignment is not a case,
    # and a stale label on a live readout would be worse than a static one.
    unit_label = StringProperty("mm")
    confirm_enabled = BooleanProperty(False)
    # The on-demand half of the base/help split; the kv hands both to the
    # HelpButton in the button row.
    help_title = StringProperty(HELP_TITLE)
    help_text = StringProperty(HELP_TEXT)

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

        self.unit_label = "mm" if self.app.formats.current_format == "MM" else "in"

        format_z = z_distance_formatter(z_input, self.app.formats,
                                        self.unit_label)
        self._format_z = format_z
        return ThreadResync(
            self.app.els_uic.hal,
            els,
            read_z_counts=lambda: int(z_input.encoderCurrent),
            read_spindle_counts=lambda: int(sp_input.encoderCurrent),
            format_z=format_z,
        )

    # ── actions ──────────────────────────────────────────────────────
    def begin(self):
        """Operator finished the coarse jog."""
        if self._resync is None:
            self.state = "refused"
            self.body_text = ("No Z or spindle axis is assigned — map them in "
                              "setup and connect the controller first.")
            return
        self._start_alignment()

    def overwrite(self):
        """Operator authorised replacing the reference this job already has."""
        self._start_alignment(force=True)

    def _start_alignment(self, force: bool = False):
        if not self._resync.begin_alignment(force=force):
            # CONFIRM_OVERWRITE is not a refusal, and must not be shown as one:
            # nothing failed and nothing was refused -- the wizard is asking a
            # question, and its state carries the two buttons that answer it.
            if self._resync.state == ResyncState.CONFIRM_OVERWRITE:
                self.state = "confirm_overwrite"
            else:
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
                f"Z hold: {self._resync.fmt_z(self._resync.z_delta_counts)} "
                f"(tolerance ±{self._resync.fmt_z(tol, False)})\n"
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
                f"{self._resync.fmt_z(self._resync.z_delta_counts)}\n"
                f"Tolerance: ±"
                f"{self._resync.fmt_z(self._resync.tolerance_counts, False)}"
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
