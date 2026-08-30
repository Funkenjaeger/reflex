"""Thread-phase offset entry (widening a groove past the cutter's width).

An expert-only surface for one job: cut a thread groove WIDER than the tool
that cuts it. Cut the groove, shift the controller's idea of thread phase by a
small distance, cut again, and repeat until the groove reaches the width you
want. The workpiece is never touched and the datum is never re-established.
The semantics belong to ``ElsFsm.apply_phase_offset`` and the ``phaseOffset*``
register block (fw/Core/Inc/Ramps.h); this modal only renders them.

THE RUNNING TOTAL IS THE WIDENING, WHICH IS WHY THE DISTANCE LEADS
------------------------------------------------------------------
Step-overs all go the SAME WAY — the operator opens one side of the groove and
keeps going until it is wide enough. So the cumulative total is not an abstract
controller state that happens to be displayed: it is exactly how far the groove
has grown past the width of the cutter, a distance that can be measured on the
part. That makes it the headline, and everything else on this screen a
qualifier of it.

The share of a pitch is kept alongside it, subordinated: it is not a width,
it is the SAFETY BOUND. Offsets alias at one pitch, so the fraction says how
much of that budget the total has eaten. On a widening job it stays small; a
fraction climbing toward one means the entries are not what the operator thinks
they are, not that the groove is nearly finished.

The software cannot compute a widening step for you, and deliberately offers no
preset that pretends otherwise: the step-over that is correct depends on the
width of the cutter in the toolpost, which nothing here knows.

WHY THERE IS NO +/- CONTROL
---------------------------
Because the job does not have one. Widening runs in a single direction away
from the groove cut first; there is no working outward from a centerline and
no second pass "the other way". An unsigned ADVANCE is the control that matches
that work, not a crippled half of a signed one, and the refusal below is
catching a slip rather than fencing off a workflow.

What a stray minus sign would DO is worth stating once, because the keypad has
a sign key and the answer is not "nothing". It does not step the phase back by
|offset|: the firmware's forward bias turns it into a forward jog of
pitch-|offset| (els_phase.h, T5) — a real cut, in the same groove (a whole
pitch is one turn of the same helix), taking material off the flank the
operator was not opening. That is a mistake worth naming, which is what the
NEGATIVE refusal does.

WHY THE TOTAL IS READ BACK FROM THE CONTROLLER RATHER THAN COUNTED HERE
-----------------------------------------------------------------------
The firmware owns the cumulative total and clears it on the enable 0->1 edge. A
UI-side running count would happily survive a job change the machine already
discarded, and would then be a confident lie about how much the groove has
already been widened. So every number on this screen comes from
``ElsFsm.phase_offset_display()`` and every entry goes to
``ElsFsm.apply_phase_offset()`` — no distance, pitch, fraction or refusal
condition is worked out locally.

WHY AN APPLY IS NOT BELIEVED UNTIL IT IS ACKED
----------------------------------------------
The firmware consumes ``phaseOffsetCommand`` WITHOUT incrementing
``phaseOffsetSeq`` when it will not honour it, so a write that never landed
looks exactly like one that did — an unchanged total and no explanation, which
the operator has no reason to read as a failure. This modal edge-detects the
seq against a baseline captured before the write (the same treatment the
pick-up-existing-thread wizard gives ``latchSeq``) and surfaces a missing ack
as a failure with a stated cause.
"""
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.popup import Popup

from reflex.fsms.els_fsm import ElsFsm
from reflex.fsms.ui_controller import phase_offset_fraction_text
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)

load_kv(__file__)


# ── BASE TEXT vs HELP TEXT (bench 2026-08-24) ────────────────────────
# The operator's words: "way too many words about the whole offset". The base
# strings below say only what to DO; every WHY moved to HELP_TEXT, rendered on
# demand by the HelpButton in the button row. The split is the fix for a real
# failure shape: prose long enough to scroll pushes its own last sentence — on
# this screen, always the instruction — below the fold.

INTRO_TEXT = (
    "Enter the WHOLE offset from the original groove and press Apply. It "
    "takes effect where the tool next re-enters the thread."
)

ENTRY_HINT = "The whole offset, not an amount to add."

HELP_TITLE = "Widening a groove"

# Everything the old intro and entry hint explained, plus the questions the
# refusals used to answer inline. Not bound by MESSAGE_CHAR_BUDGET: this
# renders in the HelpButton's own scrollable popup, never in the message
# viewport the budget protects.
HELP_TEXT = (
    "This screen cuts a thread groove WIDER than the tool that cuts it: cut, "
    "apply a small offset, cut again, until the groove is wide enough. The "
    "workpiece is never touched and the datum is never re-established.\n\n"
    "The entry is the WHOLE offset from the original groove — Apply SETS the "
    "offset, it does not add to it. The box opens holding the current offset, "
    "so applying without editing changes nothing.\n\n"
    "There are no preset step-overs because the right step depends on the "
    "width of the cutter in the toolpost, which nothing here knows.\n\n"
    "Applying moves nothing by itself. The shift takes effect where the tool "
    "next re-enters the thread — run an air pass before cutting metal.\n\n"
    "There is no minus because widening runs one way, away from the groove "
    "cut first. The firmware's forward bias would turn a negative into a "
    "forward move of nearly a whole pitch — a real cut, on the flank you "
    "were not opening.\n\n"
    "The dim line under the total is the safety bound: offsets alias at one "
    "full pitch, so it shows how much of that budget the total has used. On "
    "a widening job it stays small — a fraction climbing toward one means "
    "the entries are not what you think they are."
)

APPLIED_TEXT = (
    "Applied. The offset is shown above; it takes effect where the tool next "
    "re-enters the thread. Run an air pass before cutting metal."
)

CLEARED_TEXT = (
    "Offset cleared. The controller is back on the groove it began the job on."
)

WAITING_TEXT = "Waiting for the controller to acknowledge…"

NO_ACK_TEXT = (
    "The controller never acknowledged the offset, so it was NOT applied — "
    "usually the job disengaged mid-write. Check the ELS stop is still "
    "engaged and try again."
)

# ── HOW LONG A MESSAGE MAY BE, AND WHY THERE IS A NUMBER FOR IT ──────
# This screen's message viewport is whatever the popup can spare after its
# five fixed rows, and the popup can grow only as far as the machine's 600 px
# screen. Measured 2026-08-23 at the target 1024x600: four of the eight
# messages this modal can show overflowed a 92 px viewport, and the half that
# went below the fold was in every case the LAST sentence — the one that says
# what to do. AT_PITCH ended on "…so rather than"; NEGATIVE hid "enter the
# rest of the pitch"; NO_ACK — which exists precisely because a dropped write
# is otherwise silent — hid "check the ELS stop is still engaged". (Those are
# the strings of that day. The catalogue was re-cut later the same day for the
# groove-widening framing; the budget it blew through is unchanged.)
#
# The layout fix is in the kv (the popup sizes to its content and the scroller
# has a real affordance). This budget is the other half: a modal that grows to
# the screen still has a ceiling, so the strings have one too. 52 chars per
# rendered line was measured across the whole message catalogue at this width
# and font, and 5 lines is what fits with the popup at full screen height.
# previews/preview_walkthrough_shots.py measures the real pixels; this is the
# cheap guard that fails in the unit suite first.
MESSAGE_WRAP_CHARS = 52
MESSAGE_LINE_BUDGET = 5
MESSAGE_CHAR_BUDGET = MESSAGE_WRAP_CHARS * MESSAGE_LINE_BUDGET

# One message per outcome code. Deliberately one per code rather than a shared
# "could not apply": the fix for "engage the stop" and the fix for "you are not
# cutting a thread" are different actions, and a merged message would send the
# operator to the wrong one. Trimmed with the base text (bench 2026-08-24) to
# one clause of why plus the next step — each must still NAME that step, which
# is the property the tests hold them to.
REFUSAL_TEXT = {
    ElsFsm.PHASE_OFFSET_OFFLINE: (
        "Not connected to the controller, so there is nothing to apply the "
        "offset to. Reconnect and try again."
    ),
    ElsFsm.PHASE_OFFSET_NO_JOB: (
        "No threading job is engaged, and the controller discards the offset "
        "when one starts. Engage the ELS stop first."
    ),
    ElsFsm.PHASE_OFFSET_NO_PITCH: (
        "No thread pitch is set — turning has no thread phase to shift. "
        "Choose a threading mode and a pitch first."
    ),
    ElsFsm.PHASE_OFFSET_NO_GEOMETRY: (
        "The machine geometry that turns a distance into leadscrew steps is "
        "missing or zero. Check the servo gearing in setup."
    ),
    ElsFsm.PHASE_OFFSET_NEGATIVE: (
        "A minus sign does not back the phase up — it becomes a forward move "
        "that opens the wrong side of the groove. Enter the distance without "
        "the sign, or press Clear."
    ),
    ElsFsm.PHASE_OFFSET_AT_PITCH: (
        "That is a full pitch or more, which lands back in the groove you "
        "started at. Type a smaller offset, or press Clear."
    ),
}


class PhaseOffsetPopup(Popup):

    # "entry" | "waiting" | "applied" | "refused"
    state = StringProperty("entry")
    body_intro = StringProperty(INTRO_TEXT)
    # The on-demand half of the base/help split; the kv hands both to the
    # HelpButton in the button row.
    help_title = StringProperty(HELP_TITLE)
    help_text = StringProperty(HELP_TEXT)
    # TWO PROPERTIES, NOT ONE STRING. The distance is the answer to the job's
    # question and the fraction is the safety bound; they are rendered at
    # different sizes and weights, so they cannot share a label.
    total_text = StringProperty("")
    fraction_text = StringProperty("")
    entry = NumericProperty(0.0)
    entry_text = StringProperty("0")
    unit_label = StringProperty("mm")
    message = StringProperty(ENTRY_HINT)
    busy = BooleanProperty(False)

    # Where the pitch share stops being a readout and starts being a warning.
    # Below this it is just the number; at or above it the line says what
    # happens at one full pitch. 0.75 rather than something tighter because a
    # widening job that has deliberately walked most of a pitch is a real
    # workflow -- the operator meets the bound as the END of that walk, not as
    # a surprise -- and a warning that starts at, say, a third would be lit for
    # most of every job, which is the cry-wolf failure this same release fixed
    # in the calibration drift notice.
    APPROACHING_PITCH = 0.75

    POLL_HZ = 10

    # Ack liveness. Consumption is a single ISR pass (~10 us), so this bounds
    # Modbus round-trips only: a timeout means the write was refused or the
    # link died, never that the offset is still "going in". Same ~2 s budget
    # ThreadResync.LATCH_TIMEOUT_POLLS allows the latch ack, for the same
    # reason.
    ACK_TIMEOUT_POLLS = 20

    def __init__(self, **kv):
        super().__init__(**kv)
        from reflex.app import MainApp
        self.app = MainApp.get_running_app()
        # The ELS domain FSM owns every rule this screen obeys, so it is the
        # only thing this modal talks to. Driving the HAL directly instead would
        # mean re-implementing every refusal here, where it would drift from the
        # one the machine actually applies.
        self._fsm = self.app.els_uic.els_fsm
        self._poll_ev = None
        self._baseline_seq = 0
        self._ack_polls = 0
        self._pending = None
        self._sync_units()
        self._refresh_total()
        self._seed_entry_from_current()

    # ── actions ──────────────────────────────────────────────────────
    # THERE IS NO FILL-FROM-A-FRACTION ROW, and its absence is deliberate
    # rather than unfinished. It held 1/2, 1/3 and 1/4 pitch buttons, which are
    # multi-start step-overs: a third of a pitch is most of the way round to
    # the next groove, not a widening pass. The equivalent quantity for
    # widening is "a bit less than the width of the cutter", and no register on
    # this machine knows how wide the cutter is — so the entry is typed, and
    # the only arithmetic help on screen is the running total.

    def edit_entry(self):
        """Open the numeric keypad on the entry field.

        Imported here rather than reached through ``Factory.Keypad`` in kv (the
        NumberItem idiom): the Factory only knows a widget class once something
        has imported its module, and every import of the keypad in this app is
        a lazy one inside a method. This modal is reachable from the settings
        screen before anything else in a session has touched a keypad.
        """
        if self.busy:
            return
        from reflex.components.popups.keypad import Keypad
        Keypad(nonnegative=True).show(self, "entry")

    def apply(self):
        self._command(lambda: self._fsm.apply_phase_offset(self.entry),
                      APPLIED_TEXT)

    def clear(self):
        self._command(self._fsm.clear_phase_offset, CLEARED_TEXT)

    def close(self):
        self._stop_polling()
        self.dismiss()

    def on_open(self):
        # Units and the total are read on open as well as in __init__: the
        # modal is constructed and opened in one breath today, but a stale
        # readout on a screen whose entire purpose is a running total is not a
        # failure worth leaving to construction order.
        self._sync_units()
        self._refresh_total()
        self._seed_entry_from_current()
        self._poll_ev = Clock.schedule_interval(self._tick, 1.0 / self.POLL_HZ)
        return super().on_open()

    def on_dismiss(self):
        # An offset already acked stays applied in firmware — dismissing only
        # stops the watching. An apply still in flight when this closes is NOT
        # cancelled by closing, which is why the modal says so rather than
        # implying a close undoes it.
        self._stop_polling()
        return super().on_dismiss()

    def on_entry(self, _instance, _value):
        self.entry_text = self._format_distance(self.entry)

    # ── command + ack ────────────────────────────────────────────────
    def _command(self, run, success_text: str):
        """Run one FSM command and wait for the firmware's ack.

        ``run`` is the FSM call itself, so the seq baseline has to be taken
        BEFORE it: the call performs the write, and a baseline read afterwards
        could already contain the very increment being waited for, turning a
        never-acked write into an instant false success.
        """
        if self.busy:
            return
        try:
            baseline = self._fsm.phase_offset_seq()
            code = run()
        except Exception:
            log.exception("phase offset: command failed")
            self._show(
                "The controller could not be reached to apply the offset. "
                "Nothing was applied — check the connection and try again.",
                state="refused")
            return

        if code != ElsFsm.PHASE_OFFSET_OK:
            self._show(self._refusal_text(code), state="refused")
            # Re-read even on a refusal: whatever made the controller refuse
            # (a job that just disengaged) may also have reset the total, and
            # leaving the old one on screen would misattribute it to this
            # button press.
            self._refresh_total()
            return

        self._baseline_seq = baseline
        self._ack_polls = 0
        self._pending = success_text
        self.busy = True
        self._show(WAITING_TEXT, state="waiting")

    def _tick(self, _dt):
        # The total is refreshed on EVERY tick, not only after an apply: the
        # firmware clears it on the enable 0->1 edge, so a job disengaged and
        # re-engaged from the bar behind this modal would otherwise leave a
        # stale number on screen that reads exactly like a live one.
        self._refresh_total()
        if self.state != "waiting":
            return

        # A FABRICATED SEQ IS NOT AN ACK. The whole command/ack contract rests
        # on "the absent ack IS the refusal" -- which only holds if a seq the
        # controller never sent cannot impersonate one. A failed frame or a
        # dropped link both hand back 0, and 0 differs from any nonzero
        # baseline, so without this the modal reports "Applied" for a write the
        # firmware refused. Discarding the poll costs a tick; the timeout below
        # still bounds the wait.
        baseline = self._fsm.reads_baseline()
        try:
            seq = self._fsm.phase_offset_seq()
        except Exception:
            log.exception("phase offset: ack read failed")
            self.busy = False
            self._show(NO_ACK_TEXT, state="refused")
            return
        if self._fsm.reads_fabricated_since(baseline):
            self._ack_polls += 1
            if self._ack_polls >= self.ACK_TIMEOUT_POLLS:
                log.error("phase offset: no readable ack after %d polls",
                          self._ack_polls)
                self.busy = False
                self._show(NO_ACK_TEXT, state="refused")
            return

        if seq == self._baseline_seq:
            self._ack_polls += 1
            if self._ack_polls >= self.ACK_TIMEOUT_POLLS:
                log.error("phase offset: no ack after %d polls (seq stuck at %d)",
                          self._ack_polls, self._baseline_seq)
                self.busy = False
                self._show(NO_ACK_TEXT, state="refused")
            return

        log.info("phase offset: acked, seq %d -> %d", self._baseline_seq, seq)
        self.busy = False
        self._show(self._pending or APPLIED_TEXT, state="applied")
        self._pending = None
        # Re-seed after ANY acknowledged command, not just on open. After a
        # Clear the offset really is 0 and the box must say so -- otherwise it
        # keeps showing the number that was just thrown away, and the next
        # Apply silently reinstates it.
        self._seed_entry_from_current()

    def _stop_polling(self):
        if self._poll_ev is not None:
            self._poll_ev.cancel()
            self._poll_ev = None

    # ── readouts ─────────────────────────────────────────────────────
    def _seed_entry_from_current(self):
        """Put the CURRENT offset in the entry box.

        Apply SETS the offset rather than adding to it, so this box is not an
        amount to add -- it is the value the offset will BECOME. Opening it at
        0.000 under those semantics reads as "the offset is zero", which is the
        one thing it must not imply while a widening job is live: applying
        without editing would then silently throw the real offset away.

        Read from the controller rather than remembered locally, for the same
        reason _refresh_total is: the firmware clears the offset on the enable
        0->1 edge, so a job disengaged and re-engaged behind this modal would
        otherwise seed the box from a number the machine no longer holds.
        """
        try:
            distance, _fraction = self._fsm.phase_offset_display()
        except Exception:
            # Leave whatever is in the box. The readout above is the same call
            # and reports its own failure, so this would be the second notice
            # of one fault -- and blanking the entry would look like a value.
            log.exception("phase offset: current offset unavailable to seed entry")
            return
        self.entry = distance

    def _refresh_total(self):
        """Re-read the current offset from the controller and render it.

        BOTH halves of ``phase_offset_display()`` are shown, but not as peers.
        The DISTANCE leads: step-overs all go one way, so the total IS the
        widening — how far the groove has grown past the cutter, a number that
        can be checked against a dial or measured on the part. The FRACTION
        follows, smaller and dimmer,
        because it is not a width — it is the aliasing bound, the share of the
        one pitch this feature is allowed to accumulate. Dropping it would take
        away the only warning that a total is drifting toward the point where
        the next pass cuts the neighboring groove instead of this one.

        The fraction is NAMED through the same helper the advanced-bar status
        strip uses -- always three decimals, "0.333", never "1/3". (The helper
        did pick fractions where they were exact until 2026-08-29; Evan's call
        was one consistent format, and this docstring described the old rule
        for a day after it was gone.) Sharing the helper is the point: two
        naming rules for one number on one screen is how the modal and the bar
        come to describe the same offset differently, and the operator has no
        way to tell which is lying.
        """
        try:
            distance, fraction = self._fsm.phase_offset_display()
        except Exception:
            log.exception("phase offset: total unavailable")
            self.total_text = "Widened by:  unavailable"
            self.fraction_text = ""
            return
        self.total_text = (
            f"Widened by:  {self._format_distance(distance)} "
            f"{self.unit_label}"
        )
        # THE BOUND IS NAMED ONLY WHEN IT IS NEAR (2026-08-30). This used to
        # append "— the offset is refused at a full one" unconditionally, so a
        # successful apply rendered the word "refused" directly under the
        # distance it had just accepted, three lines above "Applied."
        share = phase_offset_fraction_text(fraction)
        if fraction >= self.APPROACHING_PITCH:
            self.fraction_text = (f"{share} x pitch — nearly a full one, "
                                  f"where it is refused")
        else:
            self.fraction_text = f"{share} x pitch"

    def _refusal_text(self, code) -> str:
        text = REFUSAL_TEXT.get(code)
        if text is not None:
            return text
        # Never a bare code on screen. An unrecognised one means this screen is
        # older than the FSM driving it, and the only honest thing left to say
        # is that nothing was applied.
        log.error("phase offset: unrecognised outcome code %r", code)
        return (f"The controller refused the offset for a reason this screen "
                f"does not recognise (it reported '{code}'). Nothing was "
                f"applied — the total above is unchanged.")

    def _show(self, message: str, state: str):
        self.state = state
        self.message = message

    # ── unit plumbing ────────────────────────────────────────────────
    # Nothing here converts between units, and nothing here computes a
    # distance: every number on this screen arrives from the FSM already in
    # display units, and all that is left is formatting. The app's configured
    # position format decides how many digits a distance gets, so this modal
    # and the DRO can never disagree about what a displayed number means.
    def _sync_units(self):
        self.unit_label = "mm" if self.app.formats.current_format == "MM" else "in"
        self.entry_text = self._format_distance(self.entry)

    def _format_distance(self, value: float) -> str:
        # position_format carries a forced sign for the DRO's benefit; this is
        # an unsigned magnitude, so the sign is stripped rather than a second
        # precision rule invented here.
        return self.app.formats.position_format.format(float(value)).lstrip("+")
