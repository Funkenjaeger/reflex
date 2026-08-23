"""Thread-phase offset entry (multi-start threading).

An expert-only surface for one job: cut an N-start thread from a single datum
by shifting the controller's idea of thread phase between starts, instead of
re-indexing the workpiece in the chuck. The semantics belong to
``ElsFsm.apply_phase_offset`` and the ``phaseOffset*`` register block
(fw/Core/Inc/Ramps.h); this modal only renders them.

WHY THERE IS NO +/- CONTROL
---------------------------
Entry is an unsigned ADVANCE. A negative offset does not step the phase back by
|offset|: the firmware's forward bias turns it into a forward jog of
pitch-|offset| (els_phase.h, T5), so a symmetric control would put a label on a
button that does not describe what the button does. Going "the other way" is
entering the complement, and the running total on screen is what makes that
arithmetic checkable instead of hidden.

WHY THE TOTAL IS READ BACK FROM THE CONTROLLER RATHER THAN COUNTED HERE
-----------------------------------------------------------------------
The firmware owns the cumulative total and clears it on the enable 0->1 edge. A
UI-side running count would happily survive a job change the machine already
discarded, and would then be a confident lie about which start the next pass
lands on. So every number on this screen comes from
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


INTRO_TEXT = (
    "Advance the thread phase to cut the next start of a multi-start thread. "
    "Entries add up; applying one MOVES NOTHING — it takes effect where the "
    "tool next re-enters the thread."
)

ENTRY_HINT = "Enter an amount, or fill it from a fraction of the pitch."

APPLIED_TEXT = (
    "Applied. The new total is shown above; it takes effect where the tool "
    "next re-enters the thread. Run an air pass before cutting metal."
)

CLEARED_TEXT = (
    "Offset cleared. The controller is back on the start it began the job on."
)

WAITING_TEXT = "Waiting for the controller to acknowledge…"

NO_ACK_TEXT = (
    "The controller never acknowledged the offset, so it was NOT applied. "
    "That normally means the threading job disengaged between the button "
    "press and the write landing — the controller ignores a phase offset "
    "outside a job because the next job would wipe it anyway. Check the ELS "
    "stop is still engaged, check the total above, and try again."
)

# One message per outcome code. Deliberately one per code rather than a shared
# "could not apply": the fix for "engage the stop" and the fix for "you are not
# cutting a thread" are different actions, and a merged message would send the
# operator to the wrong one.
REFUSAL_TEXT = {
    ElsFsm.PHASE_OFFSET_OFFLINE: (
        "Not connected to the controller. The phase offset lives in the "
        "controller, so there is nothing here to apply it to — reconnect and "
        "try again."
    ),
    ElsFsm.PHASE_OFFSET_NO_JOB: (
        "No threading job is engaged. Engage the ELS stop first: the "
        "controller clears the offset every time a job starts, so one applied "
        "now would be thrown away without a word."
    ),
    ElsFsm.PHASE_OFFSET_NO_PITCH: (
        "No thread pitch is set. A phase offset shifts where a thread starts, "
        "and turning has no thread phase to shift — choose a threading mode "
        "and a pitch first."
    ),
    ElsFsm.PHASE_OFFSET_NO_GEOMETRY: (
        "The machine geometry needed to turn a distance into leadscrew steps "
        "is missing or zero. Check the servo gearing in setup — until it is "
        "right, nothing on this screen can be converted into a real distance."
    ),
    ElsFsm.PHASE_OFFSET_NEGATIVE: (
        "Enter a plain positive distance to ADVANCE by. Backing the phase up "
        "is not the mirror of advancing it — the controller's forward bias "
        "turns a negative entry into a forward move of one pitch minus what "
        "you typed, so a minus sign would not do what it reads as. To reach a "
        "start behind this one, enter the rest of the pitch, or press Clear "
        "and start over."
    ),
    ElsFsm.PHASE_OFFSET_AT_PITCH: (
        "That would put the total at a full pitch or more. One whole pitch is "
        "the same start you began on, and anything past it cannot be told "
        "apart from the leftover — so rather than quietly hand back a "
        "different start than the one you asked for, the controller refuses. "
        "Enter a smaller amount, or press Clear and build the total again."
    ),
}


class PhaseOffsetPopup(Popup):

    # "entry" | "waiting" | "applied" | "refused"
    state = StringProperty("entry")
    body_intro = StringProperty(INTRO_TEXT)
    total_text = StringProperty("")
    entry = NumericProperty(0.0)
    entry_text = StringProperty("0")
    unit_label = StringProperty("mm")
    message = StringProperty(ENTRY_HINT)
    busy = BooleanProperty(False)

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

    # ── actions ──────────────────────────────────────────────────────
    def fill_fraction(self, denominator: int):
        """Put one pitch / ``denominator`` into the entry field.

        Fills only — it never applies. A button that both computed and applied
        would make a mis-tap a phase change on a job in progress.
        """
        if self.busy:
            return
        pitch_steps = self._pitch_steps()
        if pitch_steps <= 0.0:
            self._show(self._refusal_text(ElsFsm.PHASE_OFFSET_NO_PITCH),
                       state="refused")
            return
        distance = self._steps_to_display(pitch_steps / float(denominator))
        if distance is None:
            self._show(self._refusal_text(ElsFsm.PHASE_OFFSET_NO_GEOMETRY),
                       state="refused")
            return
        # Snapped to the readout's own precision so the number shown IS the
        # number applied — the entry field's whole job is to let the operator
        # check the value against a dial before committing it. The rounding
        # error a thirded pitch carries (well under a micron) is orders below
        # the phase error of any thread this feature is used on.
        self.entry = self._round_to_display(distance)
        self._show(ENTRY_HINT, state="entry")

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
        Keypad().show(self, "entry")

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

        try:
            seq = self._fsm.phase_offset_seq()
        except Exception:
            log.exception("phase offset: ack read failed")
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

    def _stop_polling(self):
        if self._poll_ev is not None:
            self._poll_ev.cancel()
            self._poll_ev = None

    # ── readouts ─────────────────────────────────────────────────────
    def _refresh_total(self):
        """Re-read the running total from the controller and render it.

        Both halves of ``phase_offset_display()`` are shown, because they
        answer different questions: the distance is what the operator typed and
        can check against a dial, the fraction is what says "this is start 2 of
        3". Either one alone leaves modular arithmetic to be done at the
        machine or leaves the entry unverifiable.

        The fraction is NAMED through the same helper the advanced-bar status
        strip uses -- "1/3" when it really is a third, the raw decimal when it
        is not. Sharing it is the point: two naming rules for one number on one
        screen is how the modal and the bar come to describe the same offset
        differently, and the operator has no way to tell which is lying.
        """
        try:
            distance, fraction = self._fsm.phase_offset_display()
        except Exception:
            log.exception("phase offset: total unavailable")
            self.total_text = "Offset now:  unavailable"
            return
        self.total_text = (
            f"Offset now:  {self._format_distance(distance)} {self.unit_label}"
            f"     {phase_offset_fraction_text(fraction)} of a pitch"
        )

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
    # Nothing here converts between units. `_steps_to_display` routes through
    # the FSM's own converter and the rest is formatting: the app's configured
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

    def _round_to_display(self, value: float) -> float:
        return float(self._format_distance(value))

    def _pitch_steps(self) -> float:
        """One pitch in leadscrew steps; 0.0 when the machine cannot say.

        Returned raw rather than folded to None, because the caller
        distinguishes "no pitch" from "no geometry" and those are different
        sentences to an operator: one means pick a threading mode, the other
        means the servo gearing is wrong. ``thread_pitch_steps()`` returns 0.0
        rather than raising for either — including the unmapped-spindle case,
        which it did raise on until 2026-08-22.
        """
        return float(self._fsm.thread_pitch_steps())

    def _steps_to_display(self, steps: float):
        """Leadscrew steps -> display units, THROUGH the FSM's own converter.

        Deliberately not recomputed from servo gearing and the format factor
        here: two implementations of that conversion is precisely how a fill
        button and the running total end up disagreeing about what half a pitch
        is, on a screen whose only value is that the two agree.

        ``steps_to_display`` is public for exactly this reason and returns 0.0
        when the geometry is missing or degenerate, which the FSM separately
        reports as NO_GEOMETRY on any actual apply.
        """
        distance = self._fsm.steps_to_display(steps)
        return distance if distance else None
