
from kivy.properties import StringProperty, BooleanProperty, NumericProperty
from kivy.event import EventDispatcher
from kivy.clock import Clock
from kivy.logger import Logger

from reflex.dispatchers import els, board
from reflex.fsms.ui_fsm import ElsUiFsm
from reflex.fsms.els_fsm import ElsFsm
from reflex.fsms.els_stop_hal import ElsStopHal
from reflex.fsms.els_diag import ElsDiagRecorder
from reflex.fsms.els_phase_recorder import (
    PhaseCorrectionRecorder, PhaseLiveTracker, SpindleCountWatch)
from reflex.fsms.els_mode_watch import ElsModeWatch
from reflex.utils.devices import (takeup_failure_text,
                                  ELS_DIAG_SCHEMA_MODE_WATCH,
                                  ELS_DIAG_SCHEMA_MODE_WATCH_V2)
from reflex.utils.notices import (NoticeCenter, NOTICE_INFO, NOTICE_WARNING)
from reflex.fsms.fsm_event_bus import fsm_event_bus as bus

log = Logger.getChild(__name__)

UI_POLICY = {
    "idle":          {"action_button_text": "", "can_stop": True, "instruction_text": ""},
    "set_stop_z":    {"action_button_text": "Set", "can_stop": True, "instruction_text": "Go to stop Z position and press Set"},
    "set_retract_z": {"action_button_text": "Set", "can_stop": True, "instruction_text": "Go to start Z position and press Set"},
    "set_start_dia": {"action_button_text": "Set", "can_stop": True, "instruction_text": "Go to start diameter and press Set"},
    "set_stop_dia":  {"action_button_text": "Set", "can_stop": True, "instruction_text": "Go to or enter stop diameter and press Set"},
    "confirm":       {"action_button_text": "Confirm", "can_stop": True, "instruction_text": "Confirm half nut is engaged"},
    "in_cycle.waiting_to_cut":     {"action_button_text": "Cut", "can_stop": True, "instruction_text": "Ready to cut"},
    "in_cycle.cutting":            {"action_button_text": "", "can_stop": False, "instruction_text": "Cutting..."},
    "in_cycle.waiting_to_retract": {"action_button_text": "Retract", "can_stop": True, "instruction_text": "Ready to retract"},
    "in_cycle.retracting":         {"action_button_text": "", "can_stop": False, "instruction_text": "Retracting..."},
    "alarm":         {"action_button_text": "", "can_stop": False, "instruction_text": ""},
}

# Which input button should "blink" (signal operator entry) per UI state.
# kv binds `blink_enable: root.controller.active_input == "stop_z"`.
BLINK_TARGET = {
    "set_stop_z":    "stop_z",
    "set_retract_z": "start_z",
    "set_start_dia": "major_dia",
    "set_stop_dia":  "minor_dia",
}

# How often the notice queue is swept for expiry. 10 Hz: fast enough that a
# notice never visibly outstays the duration it was given (a tenth of a second
# on a 4 s strip is not perceptible), slow enough to be free. The sweep does no
# I/O and touches no registers -- it compares one float against another.
NOTICE_SWEEP_SECONDS = 0.1


# ── Thread-phase offset readout ──────────────────────────────────────────────
# The operator-facing text lives HERE rather than in utils/devices.py (where
# takeup_failure_text lives) because it needs the live display format — units
# and decimal places — and devices.py is the register-map module, deliberately
# free of any dependency on the running app's formatting state.

def phase_offset_fraction_text(fraction: float) -> str:
    """The share of one pitch, ALWAYS as a decimal: "0.333", "0.500", "0.275".

    ONE FORMAT, NO BRANCH. Until 2026-08-29 an exact-ish 1/N was rendered by
    name ("1/3") and everything else as a decimal, on the theory that a round
    fraction reads faster against the one-pitch bound. Evan's call, and it is
    his screen: he reads 0.333 without difficulty, so the naming bought nothing
    and cost a second format for the same quantity.

    Deleting the branch takes a real hazard with it. Naming was a CLAIM, and it
    needed a tolerance and a denominator cap to keep from asserting that an
    arbitrary widening offset was a deliberate clean division. A decimal
    asserts nothing, so there is no longer anything to get wrong -- and no
    reason for the modal and the status strip to ever disagree.

    The caller supplies the unit: the wording is "0.333 x pitch", never
    "of a pitch".
    """
    return f"{fraction:0.3f}"


def phase_offset_value_parts(steps, distance, fraction, formats):
    """The two numbers of a live phase offset as text: ``(widening, bound)``.

    THE ONE PLACE EITHER NUMBER IS FORMATTED. Two readouts show this offset --
    the long status line and the short gutter chip -- and they differ only in
    how they are LABELLED and what they join the parts with. Splitting them any
    further down than this would give the machine two renderings of the same
    register that could disagree about units, decimals or the pitch fraction
    while both looked right in isolation; a chip reading "+0.500 mm" beside a
    line reading "+0.0197 in" is worse than either alone. Callers pick the
    joiner, never the arithmetic.

    BOTH NUMBERS, for the reason ElsFsm.phase_offset_display gives, with the
    distance first. Step-overs all go one way, so the total IS the widening:
    how far the groove has grown past the width of the cutter, measurable on
    the part. The fraction trails it as the aliasing bound — the share of the
    one pitch an offset may accumulate before it is refused.
    """
    try:
        pos_fmt = formats.position_format or "{:+0.3f}"
        unit = "mm" if formats.current_format == "MM" else "in"
    except AttributeError:
        pos_fmt, unit = "{:+0.3f}", "mm"

    if not distance:
        # A nonzero step total that converts to nothing: no spindle pitch, no
        # servo ratio, or a zero display factor. Say the raw register value
        # rather than rendering "+0.000 mm", which reads as "no offset" — the
        # single thing these readouts exist to contradict.
        return (f"{int(steps):+d} leadscrew steps", "thread geometry unavailable")

    try:
        distance_text = pos_fmt.format(distance)
    except (ValueError, IndexError, KeyError, AttributeError):
        distance_text = f"{distance:+0.3f}"
    return (f"{distance_text} {unit}",
            f"{phase_offset_fraction_text(fraction)} x pitch")


def phase_offset_readout(steps, distance, fraction, formats) -> str:
    """The long form: label and both numbers on one line, bullet-separated.

    Its own label carries the un-convertible case ("... OFFSET SET"), because
    this line has to stand alone -- there is no separate label beside it to
    qualify. On this line the distance leads by position; the modal, which had
    them as co-equal halves of one line, separates them by size and weight
    instead.
    """
    widening, bound = phase_offset_value_parts(steps, distance, fraction, formats)
    label = "THREAD PHASE OFFSET" if distance else "THREAD PHASE OFFSET SET"
    return f"{label}  •  {widening}  •  {bound}"


def phase_offset_chip_readout(steps, distance, fraction, formats) -> str:
    """The short form: both numbers, NO label — the chip draws that itself.

    A StatusChip renders its label and its value as two type weights, so the
    label must not be baked into the value here; and inside a chip the bullets
    are noise, since the pill's own edge already bounds the group.
    """
    widening, bound = phase_offset_value_parts(steps, distance, fraction, formats)
    return f"{widening}  {bound}"


class ElsUiController(EventDispatcher):
    # ── Operator-mode flags (read by FSM conditions / on_enter_stopped) ─
    retract_enabled     = BooleanProperty(False)
    wizard_enabled      = BooleanProperty(False)
    els_forward         = BooleanProperty(True)

    # ── Hardware-state mirrors (kv binds to these) ─────────────────────
    engaged             = BooleanProperty(False)   # True iff domain FSM not in 'disabled'
    els_stop_active     = BooleanProperty(False)   # mirrors HAL read_active()

    # Inline take-up warning, shown in the ELS bar. Non-empty means the last
    # attempted pass was REFUSED because the firmware could not confirm the
    # backlash take-up actually moved the carriage.
    #
    # The firmware aborts such a pass back to the stopped state with the thread
    # phase reference intact, so recovery is simply pressing Cut again — this
    # string is the only thing that tells the operator why nothing happened.
    # Without it a refusal and a hung machine are indistinguishable, which is
    # exactly how it presented on hardware 2026-08-08.
    takeup_warning      = StringProperty("")

    # ── Transient operator notices (top status bar) ────────────────────
    # The live notice, republished from the NoticeCenter for kv to bind to.
    # Empty text means nothing is showing; `notice_severity` picks the colour.
    #
    # WHY TWO PROPERTIES AND NOT THE Notice OBJECT. kv binds to properties, and
    # an ObjectProperty holding a mutable dataclass does not fire when a field
    # inside it changes -- the strip would go stale exactly when one notice
    # replaced another with the same object identity. Flattening to the two
    # values the renderer actually needs keeps every update a real property
    # change.
    #
    # WHY THE TEXT DOES NOT DOUBLE AS THE VISIBILITY FLAG the way
    # `takeup_warning` does, and why that asymmetry is correct: a take-up
    # refusal is a LATCH -- it is true until something clears it -- so its text
    # and its truth are the same fact. A notice is an EVENT with a lifetime, and
    # the lifetime is owned by the NoticeCenter. Nothing outside it may decide
    # the strip is up; the strip is up because there is text.
    notice_text         = StringProperty("")
    notice_severity     = StringProperty("")

    # ── Thread-phase offset (widening a groove past the cutter) ────────
    # True whenever elsStop.phaseOffsetSteps is nonzero, i.e. the machine is
    # cutting at a DELIBERATE phase shift. Persistent job state, not an event:
    # it survives every pass until the operator clears it or the firmware wipes
    # it on the next enable 0->1 edge, and an operator who forgets it is set
    # ruins the next thread. That is why visibility hangs off this flag and not
    # off the text being non-empty, the way takeup_warning does — a readout
    # that could not be formatted (missing thread geometry) must still SHOW,
    # saying so, rather than silently reading as "no offset".
    phase_offset_active = BooleanProperty(False)
    phase_offset_text   = StringProperty("")
    # The same offset, as the VALUE half of the gutter's phase chip -- both
    # numbers, no label (the chip draws "PHASE OFFSET" itself, in its own type
    # weight). Both strings come from phase_offset_value_parts, so the chip and
    # any long-form readout cannot disagree about units or decimals.
    phase_offset_chip_value = StringProperty("")

    # ── Thread reference latch (elsStop.referenceLatched, gated on enable) ──
    # True while the firmware BOTH holds a thread reference AND is enabled.
    # Persistent job state like the offset above -- the latch is banked on the
    # first Cut of a job and consumed by every subsequent pass -- and until
    # 2026-08-24 nothing on screen showed it, so "is anything latched?" could
    # not be answered at the bench. The enable term is not decoration: the
    # firmware clears referenceLatched on the enable RISING edge, not on
    # disengage, so between jobs the register still holds the OLD job's latch
    # and showing it would claim a reference the next Cut will not use.
    thread_ref_latched  = BooleanProperty(False)

    # ── Operator-job descriptors (mirrored from the widget) ────────────
    # is_threading toggles the X-clear-of-start-dia gate in waiting_to_retract.
    # is_inner flips the "outside" comparison: OD work retracts to larger X,
    # ID work retracts to smaller X.
    is_threading        = BooleanProperty(False)
    is_inner            = BooleanProperty(False)

    # ── Derived UI state ───────────────────────────────────────────────
    in_cycle            = BooleanProperty(False)   # True while FSM is in any in_cycle.* state
    x_z_inputs_enabled  = BooleanProperty(False)
    start_stop_enabled  = BooleanProperty(False)
    start_not_stop      = BooleanProperty(False)
    action_allowed      = BooleanProperty(True)    # gated by state-specific checks
    # Informational latch: set once a cut in this cycle reaches stop_dia,
    # cleared when the cycle ends (return to idle, i.e. operator pressed Stop).
    # Lets the operator see the milestone even while doing additional cleanup
    # / spring passes that don't visually change the dimension.
    depth_reached       = BooleanProperty(False)

    instruction_text    = StringProperty("")
    action_button_text  = StringProperty("")
    alarm_text          = StringProperty("")
    active_input        = StringProperty("")  # which TextHeaderButton blinks

    stop_z              = NumericProperty(0.0)
    retract_z           = NumericProperty(0.0)
    start_dia           = NumericProperty(0.0)
    stop_dia            = NumericProperty(0.0)

    stop_z_error    = StringProperty("")
    retract_z_error = StringProperty("")
    start_dia_error = StringProperty("")
    stop_dia_error  = StringProperty("")

    stop_z_valid    = BooleanProperty(False)
    retract_z_valid = BooleanProperty(False)
    start_dia_valid = BooleanProperty(False)
    stop_dia_valid  = BooleanProperty(False)

    # ── Re-reference notify (a committed target's displayed value changed due to
    #    a DRO re-zero / coordinate-system switch — the physical target is
    #    unchanged; this just flags it per the operator's notify preference).
    targets_reframed_warn  = BooleanProperty(False)  # 'warn': amber field flag
    reframe_confirm_pending = BooleanProperty(False)  # 'confirm': show the bar
    reframe_message        = StringProperty("")       # human text for warn/confirm

    ui_state = StringProperty("idle")

    @property
    def hal(self):
        """Read-only access to the elsStop HAL.

        Exposed so auxiliary flows (the backlash calibration wizard) can talk to
        the register block without reaching into `_hal` or standing up a second
        HAL against the same board. The FSM remains the only writer during a
        threading cycle; callers using this must not fight it.
        """
        return self._hal

    @property
    def els_fsm(self):
        """Read-only access to the ELS domain FSM.

        Exposed for the same reason `hal` is, one level up: auxiliary flows need
        the RULES, not the registers. The thread-phase offset modal is the case
        that forced it — every refusal it renders, and every unit conversion it
        shows, belongs to the FSM, and the alternative to reaching it was
        re-implementing those refusals in a popup where they would drift.

        Read-only in the sense that matters: callers may ask this object
        questions and invoke its documented operations, but the threading cycle
        is still driven from here, and a caller that fights that will lose.
        """
        return self._els_fsm

    def __init__(self, els: els, board: board, **kw):
        super().__init__(**kw)
        self._els = els
        self._board = board
        self._hal = ElsStopHal(board)
        self._diag_recorder = ElsDiagRecorder(self._hal, board)
        self._phase_recorder = PhaseCorrectionRecorder(board)
        self._phase_tracker = PhaseLiveTracker(board)
        self._spindle_watch = SpindleCountWatch(board)

        # Built FIRST, before any FSM or poller exists, because `notify()` must
        # be safe to call from anywhere below -- including from construction, if
        # a future reconcile-on-connect wants to say something.
        self._notices = NoticeCenter()

        # Rung-2 sampler (log-only): compares the domain FSM's state against
        # the firmware-published machine mode. Runs against every build now
        # that machineMode is a permanent register (2026-08-22); it used to be
        # gated on a mode-watch probe being flashed, which made the census
        # uncollectable during any session that needed a different probe.
        self._mode_watch = ElsModeWatch()
        self._mode_watch_tick = 0

        # ── Encoder-anchored ELS targets ─────────────────────────────────────
        # stop_z / retract_z are stored as the PHYSICAL Z-leadscrew encoder count
        # captured when the operator set them (source of truth), NOT the scaled
        # display value. The `stop_z`/`retract_z` NumericProperties are a live
        # DERIVED mirror (re-rendered each tick from the frozen encoder in the
        # current frame) used only for display + safe-side comparisons; the cut
        # writes the frozen encoder straight to firmware. This makes an ELS stop
        # behave like every other position in the app (offsets are stored
        # canonically, see axis.py): a DRO re-zero or a mm↔in switch just
        # re-renders it — the physical shoulder never moves — instead of
        # silently corrupting it (the old scaled-storage bug). `*_valid` gates a
        # never-set target; only never-set and an ELS Z-axis remap invalidate.
        self._prev_takeup_seq = 0         # edge-detect baseline for takeup outcomes
        self._pending_takeup_seq = None   # a seq edge seen once; acted on when seen twice
        self._prev_phase_offset_steps = 0   # last raw total seen; renders on the 2nd sighting
        self._logged_phase_offset_steps = 0 # last total written to the log, so a units
                                            # switch re-renders without re-logging
        self._stop_z_encoder = None       # int leadscrew encoder count, or None
        self._retract_z_encoder = None
        self._stop_z_committed = False
        self._retract_z_committed = False
        # Diameters get the same treatment on the X (cross-slide) axis. They're
        # only used for X-gating comparisons (not written to firmware), but the
        # same scaled-storage fragility applies.
        self._start_dia_encoder = None
        self._stop_dia_encoder = None
        self._start_dia_committed = False
        self._stop_dia_committed = False
        # Track the display factor so the reframe poll can tell a units switch
        # (silent, re-render only) from a DRO re-zero / coordinate change (the
        # notify-worthy event).
        try:
            self._last_factor = float(self._board.formats.factor)
        except Exception:
            self._last_factor = 1.0

        # Diameter validators fire on change (they're also settable directly).
        self.bind(start_dia=lambda *_: self._validate_start_dia(),
                  stop_dia=lambda *_: self._validate_stop_dia())

        # Invalidate the captured targets when the ELS axis mapping changes — a
        # captured encoder belongs to a different physical axis now.
        self._els.bind(z_axis_index=lambda *_: self._invalidate_z_targets())
        self._els.bind(x_axis_index=lambda *_: self._invalidate_x_targets())

        # 2. Build domain FSM (HAL injected; controller doubles as modes
        # source). The diag recorder rides along so the dark rung-3 adopt path
        # can gate on its learned schema -- inert until els_adopt flips.
        self._els_fsm = ElsFsm(els, board, self._hal, self,
                               diag_recorder=self._diag_recorder)

        # 3. Eagerly compute initial validation so FSM guards don't start False.
        self._validate_stop_z()
        self._validate_retract_z()
        self._validate_start_dia()
        self._validate_stop_dia()

        # 4. Build UI FSM last (depends on a fully-constructed controller).
        self._ui_fsm = ElsUiFsm(self)

        # 5. Subscribe to bus events after both FSMs exist.
        bus.subscribe("ui_state_changed", self._on_ui_state_changed)
        bus.subscribe("state_changed", self._on_domain_state_changed)
        bus.subscribe("alarm_raised", self._on_alarm)
        bus.subscribe("els_pass_interrupted", self._on_pass_interrupted)

        # 6. Apply initial state policy and connect HW bindings.
        self._apply_policy()
        self._board.bind(connected=self._on_connected_changed)
        # If the board is ALREADY connected at build time the bind above will
        # never fire True, so reconcile the firmware's retained elsStop state
        # here too (normally `connected` first goes True on a post-build update
        # tick and the bind handles it — this covers the other ordering).
        if self._board.connected:
            Clock.schedule_once(
                lambda _dt: self._els_fsm.reconcile_firmware_on_connect(), 0)
        self._board.bind(update_tick=self._poll_els_stop_active)
        self._board.bind(update_tick=self._poll_carriage_retracted)
        self._board.bind(update_tick=self._poll_apply_policy)
        self._board.bind(update_tick=self._poll_reframe_targets)
        self._board.bind(update_tick=self._poll_takeup_outcome)
        self._board.bind(update_tick=self._poll_phase_offset)
        self._board.bind(update_tick=self._poll_thread_ref_latched)
        # Firmware diagnostic scratchpad. Dormant against any release build --
        # it reads diagSchema once per connection and, finding 0, issues no
        # further reads at all. See reflex/fsms/els_diag.py.
        self._board.bind(update_tick=self._poll_diag_capture)
        # Phase-correction results. Reads the per-tick elsStop snapshot only,
        # so unlike the scratchpad recorder above it runs against EVERY build
        # and costs no Modbus traffic. See els_phase_recorder.py.
        self._board.bind(update_tick=self._poll_phase_correction)
        # Live physical phase, ~1 Hz while a reference is latched. Scale
        # positions from fastData, reference from the elsStop snapshot; the
        # one non-snapshot input (sync ratio) is read once per reference.
        self._board.bind(update_tick=self._poll_phase_live)
        # Raw spindle counter, change-only, no job required -- the belt-off
        # EMI experiment's instrument. See SpindleCountWatch.
        self._board.bind(update_tick=self._poll_spindle_watch)
        # Rung-2 mode sampler; equally dormant without the schema-4 probe.
        self._board.bind(update_tick=self._poll_mode_watch)

        # Notice expiry runs off the Kivy Clock, NOT off board.update_tick like
        # every other poller in this file, and the difference is load-bearing:
        # update_tick only fires while a board is talking to us, and the very
        # first notice migrated onto this surface ("no ELS Z axis is assigned --
        # map it in ELS settings") fires most often when NOTHING is connected.
        # On update_tick that notice would appear and then stay on screen
        # forever, which is precisely the persistent-banner failure this surface
        # is defined not to be.
        Clock.schedule_interval(self._poll_notices, NOTICE_SWEEP_SECONDS)

        # 7. Re-arm HAL when mode flags change so firmware tracks the operator.
        self.bind(retract_enabled=self._on_modes_changed,
                  wizard_enabled=self._on_modes_changed,
                  els_forward=self._on_modes_changed,
                  is_threading=self._on_modes_changed)

        # 8. Re-evaluate UI policy when validity flags change so the action
        # button enables/disables live as the operator types values in.
        self.bind(stop_z_valid=lambda *_: self._apply_policy(),
                  retract_z_valid=lambda *_: self._apply_policy())

        # 9. Land in the correct UI state for the (default) mode flags. The
        # widget mirrors persisted enable_* flags into the controller after
        # this point, which will retrigger _on_modes_changed and re-sync.
        self._sync_ui_state_to_modes()

    # ——— event handlers ———
    # Bus events may originate on the ConnectionManager polling thread, so
    # all assignments to Kivy properties are marshaled to the main thread.
    def _on_ui_state_changed(self, state):
        log.debug("ui controller _on_ui_state_changed()")
        def _apply(_dt):
            self.ui_state = state
            self._apply_policy()
        Clock.schedule_once(_apply, 0)

    def _on_alarm(self, reason):
        Clock.schedule_once(lambda _dt: setattr(self, "alarm_text", reason), 0)

    def _on_connected_changed(self, instance, value):
        def _apply(_dt):
            # The board on the other end may have been REFLASHED between
            # connections, so anything learned about its firmware last time --
            # including whether it carries a diagnostic probe, and which one --
            # says nothing about this one. Re-interrogate from scratch.
            self._diag_recorder.reset()
            if value:
                # A (re)connection means the firmware may hold elsStop state
                # from a previous session (or have rebooted and lost ours) —
                # reconcile it to THIS session's FSM state before anything
                # (e.g. the feed guard) trusts firmware bits.
                self._els_fsm.reconcile_firmware_on_connect()
            self._apply_policy()
        Clock.schedule_once(_apply, 0)

    def _on_domain_state_changed(self, state):
        # Mirror domain FSM state into a Kivy property the widget can bind to.
        Clock.schedule_once(lambda _dt: self._sync_engaged(state), 0)

    def _sync_engaged(self, state):
        self.engaged = state != "disabled"
        # Recover the UI FSM from its own alarm state when the operator clears a
        # fault by disengaging (domain → disabled). Without this the domain
        # recovers but the UI FSM stays parked in 'alarm' forever (Start/Stop and
        # the action button disabled), soft-locking ELS until an app restart.
        if state == "disabled" and self._ui_fsm.state == "alarm":
            if self._ui_fsm.may_ack_alarm():
                self._ui_fsm.ack_alarm()          # alarm → idle
            # Re-land in the correct resting UI state for the current mode
            # (non-wizard auto-advances to in_cycle.waiting_to_cut).
            self._sync_ui_state_to_modes()
        self._apply_policy()

    # ——— transient operator notices ———
    def notify(self, message: str, severity: str = NOTICE_INFO, *,
               seconds: float | None = None) -> bool:
        """Tell the operator something, on the top status bar, for a few seconds.

        THE GENERAL CHANNEL. Anything in the app that wants the operator to know
        something happened calls this: `app.els_uic.notify(...)`. It is
        deliberately not ELS-specific -- it lives here because this is where
        every other value republished into kv already lives, not because the
        notices are about threading.

        WHAT BELONGS HERE, and it is a narrow set: an EVENT the operator would
        otherwise experience as nothing happening. A refused button press, an
        action that completed silently, a condition that must change before the
        machine will do what was asked. Two things do NOT belong here:

          * PERSISTENT MACHINE STATE. A live thread-phase offset or an armed
            stop is true for the length of a job, and a strip that covers
            something for a whole job needs its placement argued on its own
            merits -- see the two overlays in els_advbar.kv. `seconds` is
            clamped (notices.MAX_SECONDS) so this cannot be bent into one.
          * ANYTHING THE OPERATOR MUST ACKNOWLEDGE OR DECIDE. A notice has no
            buttons and vanishes on a timer; if the answer matters, it is a
            CustomPopup (see _on_pass_interrupted).

        Returns True iff something will be shown. False means the message was
        empty -- i.e. the caller built it out of nothing -- and is worth
        checking in a test, never worth branching on at a call site.

        Safe to call from the board polling thread, like the other property
        writes in this file: the queue itself is locked, and a Kivy property
        assignment is atomic enough for a string.
        """
        posted = self._notices.post(message, severity, seconds=seconds)
        self._publish_notice()
        return posted is not None

    def _publish_notice(self):
        """Mirror whatever the NoticeCenter says is current into the two kv
        properties. Assignment is guarded so an unchanged notice does not
        re-fire every binding on the status bar 10 times a second."""
        current = self._notices.showing
        text = current.message if current is not None else ""
        severity = current.severity if current is not None else ""
        # Severity FIRST: the renderer picks its colour from it, and setting the
        # text first would paint one frame of the new message in the old
        # message's colour -- an amber refusal flashing cyan.
        if severity != self.notice_severity:
            self.notice_severity = severity
        if text != self.notice_text:
            self.notice_text = text

    def _poll_notices(self, _dt):
        """Retire an expired notice and promote whatever was waiting behind it.

        Republishes only when the current notice actually changed, so the steady
        state (nothing showing) costs one float comparison per tick.
        """
        if self._notices.poll():
            self._publish_notice()

    def _poll_takeup_outcome(self, *args):
        """Log every backlash take-up outcome so it is visible AT THE MACHINE.

        At the lathe there is the touchscreen UI and the log viewer, not a
        register browser — so anything worth knowing during commissioning has to
        reach one of those or it may as well not exist.

        The headroom figure is the reason this exists. A REFUSED take-up already
        names both numbers in its message, but a successful one says nothing, so
        there is no way to tell a gate with comfortable margin from one sitting a
        single count above its threshold. The latter will start intermittently
        refusing good passes later, and without this line the first evidence
        would be a machine that mysteriously stops working.

        Edge-detected on takeupSeq, which increments once per OUTCOME (not per
        tick, and not per take-up attempt).
        """
        # Snapshot the read-failure counter BEFORE any read in this poll. A
        # failed read returns 0 (communication.py), and in this register map
        # zero is not a neutral value -- it is a sequence reset. On elspi
        # 2026-08-21 a CRC-failed 148-byte frame of zeros made takeupSeq read
        # 2 -> 0; this poller took that for an edge, read zeros for result,
        # moved and needed through the same failing path, logged a phantom
        # "CONFIRMED: moved 0 counts, needed 0" and CLEARED a live refusal.
        # The two-poll guard below does not cover it: a zero frame is only
        # consistent across two polls if it repeats, and it did not.
        #
        # Every read in this poll now comes from the board's once-per-tick
        # elsStop snapshot rather than four separate exchanges, and the guard
        # below is unchanged BECAUSE the snapshot reports its own failure the
        # same way: no snapshot means each read here returns its fallback and
        # bumps read_failures, exactly as a timed-out per-field read did.
        #
        # The snapshot also makes the four values MORE consistent, not less:
        # takeupResult (register 32) and takeupSeq (33) now arrive in the same
        # FC3 response instead of two exchanges milliseconds apart. It does not
        # close the tear the two-poll guard below exists for -- the firmware's
        # process_FC3 still copies the block a register at a time and the ISR
        # can still land mid-copy -- so that guard stays.
        cm = self._board.connection_manager
        reads_baseline = cm.read_failures

        seq = self._hal.tick.takeup_seq()
        if seq == self._prev_takeup_seq:
            self._pending_takeup_seq = None
            return
        # A seq edge. Do NOT act on THIS poll's result. The firmware writes
        # takeupResult and takeupSeq in one ISR pass, but the Modbus task copies
        # the register block one 16-bit register at a time (Modbus.c
        # process_FC3) and the 100 kHz ISR can land between the two. Result
        # sits at the lower address, so a torn snapshot reads as (stale
        # result, new seq). Observed on elspi 2026-08-21: a REFUSED first pass
        # logged as "CONFIRMED: moved 0 counts, needed 2 (headroom -2)" -- a
        # confirmation with less motion than required, which cannot happen --
        # and the warning the operator was waiting for was cleared instead of
        # shown. Acting on the NEXT poll at which seq is unchanged costs one
        # poll (~100 ms) and reads one consistent snapshot.
        if self._pending_takeup_seq != seq:
            self._pending_takeup_seq = seq
            return
        self._pending_takeup_seq = None
        prev_seq_before_commit = self._prev_takeup_seq
        self._prev_takeup_seq = seq

        result = self._hal.tick.takeup_result()
        moved = self._hal.tick.last_takeup_z_delta()
        needed = self._hal.tick.takeup_thresh_counts()
        if cm.reads_failed_since(reads_baseline):
            # ANY read in this poll failed -- the seq read that produced the
            # edge, or one of the three that make up the outcome. The baseline
            # is taken at the top of the poll deliberately, so this ONE check
            # covers the whole poll and an earlier duplicate of it was removed
            # as unkillable: no mutation could distinguish it from this.
            #
            # Reporting now would announce an outcome assembled from zeros --
            # the phantom CONFIRMED. _prev_takeup_seq has already been
            # advanced, so roll it back and let the next poll re-detect the
            # edge cleanly; a real outcome is deferred by a tick, never lost.
            self._prev_takeup_seq = prev_seq_before_commit
            self._pending_takeup_seq = None
            return

        if result == 0:
            self.takeup_warning = ""
            log.info(
                "ELS takeup #%s CONFIRMED: moved %s counts, needed %s (headroom %+d)",
                seq, moved, needed, int(moved) - int(needed))
        else:
            # `moved` only, and `needed` deliberately not passed: the screen
            # text uses the SIGN of the motion to pick the wrong-way branch and
            # names no counts at all. Both numbers still go to the log line
            # below, which since 2026-08-29 is the only place they exist -- see
            # takeup_failure_text's docstring. Do not "restore" them to the
            # warning: it is pinned to 85 characters so the notice strip cannot
            # land on top of the status chips.
            self.takeup_warning = takeup_failure_text(result, moved)
            log.warning(
                "ELS takeup #%s REFUSED (result=%s): moved %s counts, needed %s",
                seq, result, moved, needed)

    def _poll_phase_offset(self, *args):
        """Republish the firmware's live thread-phase offset as screen state.

        THE FIRMWARE OWNS THE TOTAL, so this reads it every tick rather than
        mirroring what the UI last applied. elsStop.phaseOffsetSteps is cleared
        on the enable 0->1 edge and replaced (not accumulated) by every apply,
        so a UI-side copy would happily survive a job change the machine
        already discarded — and would then be showing a phase shift that is not
        being cut, which is worse than showing nothing.

        SEEN TWICE BEFORE IT IS BELIEVED, for the reason spelled out in
        _poll_takeup_outcome: the Modbus task copies the register block one
        16-bit register at a time, so a 32-bit value can be read half-old and
        half-new. Here that would flash an arbitrary distance onto the most
        load-bearing readout on the screen. A genuine change costs one extra
        poll (~100 ms) to appear; a torn one never appears at all.

        Once the total is stable the text is recomputed each poll and assigned
        only when it differs, so a mm<->in switch or a feed-table change
        re-renders the readout without the step count having moved.

        Never raises into the update loop: phase_offset_display reaches through
        the spindle axis, which is None until ELS is configured.
        """
        # A FAILED READ MUST NOT READ AS "NO OFFSET". The read helpers return 0
        # on a checksum/timeout failure, and 0 here means exactly "no phase
        # shift is being cut" -- so a bad frame would clear this readout while
        # the machine is still cutting at an offset, which is the one thing it
        # exists to prevent the operator forgetting. Same counter the take-up
        # poller uses; see the elspi 2026-08-21 phantom CONFIRMED.
        # Read from the board's once-per-tick elsStop snapshot, not live: this
        # is a poller, it runs once per tick, and the snapshot is this tick's.
        # An absent snapshot bumps read_failures just as a failed live read did,
        # so the guard below is unchanged.
        cm = self._board.connection_manager
        reads_baseline = cm.read_failures
        try:
            steps = self._hal.tick.phase_offset_steps()
        except Exception:
            return
        if cm.reads_failed_since(reads_baseline):
            return

        if steps != self._prev_phase_offset_steps:
            self._prev_phase_offset_steps = steps
            return

        if steps == 0:
            if self.phase_offset_active:
                log.info("ELS thread-phase offset CLEARED (was %s steps)",
                         self._logged_phase_offset_steps)
                self.phase_offset_active = False
                self.phase_offset_text = ""
                self.phase_offset_chip_value = ""
            self._logged_phase_offset_steps = 0
            return

        try:
            # Hand it the total already read above. Calling this bare would
            # read phaseOffsetSteps a SECOND time on every rendered tick, and
            # the two reads could straddle a firmware update — rendering a
            # distance from one total and a fraction from another.
            distance, fraction = self._els_fsm.phase_offset_display(steps)
        except Exception:
            # Same meaning as a zero distance below: nothing here can convert
            # the total, but the total is real and must still be announced.
            distance, fraction = 0.0, 0.0

        formats = getattr(self._board, "formats", None)
        text = phase_offset_readout(steps, distance, fraction, formats)
        if text != self.phase_offset_text:
            self.phase_offset_text = text
        # Rendered from the same conversion, in the same poll, so the two
        # readouts can never be a tick out of step with each other.
        chip_value = phase_offset_chip_readout(steps, distance, fraction, formats)
        if chip_value != self.phase_offset_chip_value:
            self.phase_offset_chip_value = chip_value
        if not self.phase_offset_active:
            self.phase_offset_active = True
        if steps != self._logged_phase_offset_steps:
            self._logged_phase_offset_steps = steps
            # No colon in this message. Kivy's logger reads the text up to the
            # first ":" as a log CATEGORY and reformats around it, which turned
            # a readable line into "[ELS thread-phase offset now N steps] ..."
            # in the captured output.
            log.info("ELS thread-phase offset now %s steps -> %s", steps, text)

    def _poll_thread_ref_latched(self, *args):
        """Republish the firmware's thread-reference latch as screen state.

        Same data path as _poll_phase_offset -- both values from the board's
        once-per-tick elsStop snapshot, no extra Modbus traffic -- but the
        OPPOSITE failure policy, because the dangerous lie points the other
        way. There, clearing on a failed read would hide a live offset the
        operator must not forget, so a failed poll HOLDS the last state. Here
        the hazard is a lamp claiming a reference that may not exist: an
        operator who trusts a stale "latched" re-enters a thread the machine
        cannot actually pick up. So a failed or absent snapshot HIDES the
        lamp, and it relights on the first good tick that still says latched.
        A dark lamp only ever costs a re-latch.

        No seen-twice guard, deliberately: referenceLatched and enable are
        single uint16 registers, so unlike the 32-bit offset total they cannot
        be read torn.

        Never raises into Kivy's dispatch. The only exception _get can raise
        is a KeyError for a register name the map does not have -- a bug, not
        a runtime condition -- and it is logged, not swallowed silently.
        """
        cm = self._board.connection_manager
        reads_baseline = cm.read_failures
        try:
            latched = self._hal.tick.reference_latched()
            enabled = self._hal.tick.enable()
        except Exception as e:
            log.error("thread-ref-latch poll failed: %s", e)
            latched = enabled = False
        else:
            if cm.reads_failed_since(reads_baseline):
                # Fabricated fallbacks (no snapshot this tick). Both False by
                # construction already; made explicit so this poller's
                # hide-don't-hold policy survives a fallback-value change.
                latched = enabled = False

        show = latched and enabled
        if show != self.thread_ref_latched:
            self.thread_ref_latched = show
            # Both terms in the line, because the indicator going dark has
            # three distinct causes (unlatched, disengaged, no snapshot) and
            # a session log that cannot tell them apart is how the next
            # phantom gets chased. No colon -- Kivy's logger eats everything
            # before one as a category.
            log.info("ELS thread-ref indicator %s -- latched=%s enable=%s",
                     "SHOWN" if show else "hidden", latched, enabled)

    def _poll_diag_capture(self, *args):
        """Drain any completed firmware settle capture to disk.

        Deliberately a separate poller from _poll_takeup_outcome even though both
        fire on the same event: the take-up outcome is published the moment the
        gate decides, while the settle capture keeps running for the rest of its
        window precisely to see what Z does AFTER that decision. Folding this
        into the outcome poller would read the block while it was still being
        written and record a truncated trace as if it were a complete one.

        Never raises -- see ElsDiagRecorder.poll().
        """
        self._diag_recorder.poll()

    def _poll_phase_correction(self, *args):
        """Record each new phase-correction result to the diag directory.

        Separate from _poll_diag_capture because they answer to different
        firmware: that one is dormant unless the build carries ELS_DIAG_SCRATCH,
        while these four registers are permanent and published by every build.
        Folding this into it would make the phase data disappear on exactly the
        release firmware the machine actually runs.

        Never raises -- see PhaseCorrectionRecorder.poll().
        """
        self._phase_recorder.poll()

    def _poll_phase_live(self, *args):
        """Sample the physical phase error against the commanded ledger.

        Separate from _poll_phase_correction: that records what the firmware
        DECIDED at each pass start; this records whether the machine is
        physically in phase while the pass RUNS -- the measurement that
        separates dropped steps from every other theory of the 2026-08-24
        re-sync misalignment. Never raises -- see PhaseLiveTracker.poll().
        """
        self._phase_tracker.poll()

    def _poll_spindle_watch(self, *args):
        """Log raw spindle counter movement, jobless and change-only.

        Never raises -- see SpindleCountWatch.poll().
        """
        self._spindle_watch.poll()

    # Every 5th update tick ≈ 6 Hz against the firmware's ~10 Hz publication:
    # fast enough that no dwell in a mode is missed, slow enough that the one
    # extra single-register read stays a rounding error on the serial budget.
    MODE_WATCH_SAMPLE_EVERY = 5

    def _poll_mode_watch(self, *args):
        """Rung-2 sampler: (domain FSM state, published machine mode) into the
        mode watch. Log-only by design — see els_mode_watch.py.

        Runs against EVERY build, because the mode is now a permanent register
        rather than something a probe republishes into the scratchpad. It used
        to return immediately unless a mode-watch probe was flashed, and since
        the firmware allows one probe at a time, choosing any other probe
        silently chose to collect nothing — which is what happened across both
        lathe sessions on 2026-08-21/22. Do not reintroduce a schema gate here.

        Never raises into the update loop.
        """
        try:
            self._mode_watch_tick += 1
            if self._mode_watch_tick % self.MODE_WATCH_SAMPLE_EVERY:
                return
            # From the tick snapshot. The sample is now genuinely free rather
            # than "a rounding error on the serial budget" — the note on
            # MODE_WATCH_SAMPLE_EVERY above was the cost argument for sampling
            # only every 5th tick, and that cost is gone. The rate is left
            # alone anyway: 6 Hz against the firmware's ~10 Hz publication is
            # what the census was collected at, and changing the sampling rate
            # would change what the census means.
            mode = self._hal.tick.current_mode()
            self._mode_watch.feed(self._els_fsm.state, mode)
        except Exception as e:
            log.warning(f"mode-watch sample failed: {e}")

    def _poll_els_stop_active(self, *args):
        # Bound to board.update_tick. Kivy property writes from the polling
        # thread are an existing project-wide pattern; matching that here.
        #
        # Served from the tick snapshot (see ElsStopHal.TickReads): the domain
        # FSM's _on_board_update wants the same register on the same tick, and
        # before the snapshot the two spent an exchange each — and could
        # disagree, having read the register a few milliseconds apart.
        active = self._hal.tick.active()
        if active != self.els_stop_active:
            self.els_stop_active = active

    def _poll_carriage_retracted(self, *args):
        """Mirror manual carriage motion across retract_z into cycle state.

        When the operator hand-retracts past retract_z (waiting_to_retract →
        waiting_to_cut), or backs the carriage below retract_z while waiting
        to cut (waiting_to_cut → waiting_to_retract). Marshals FSM triggers
        to the main thread since this fires on the board polling thread.

        Only active when retract is enabled — in stop-only mode there is no
        retract threshold to cross, so polling here would incorrectly
        transition the UI FSM to waiting_to_retract.
        """
        if not self.retract_enabled:
            return
        state = self._ui_fsm.state
        if state not in ("in_cycle.waiting_to_retract", "in_cycle.waiting_to_cut"):
            return
        try:
            retracted = self._els_fsm.is_retracted()
        except Exception:
            return
        if state == "in_cycle.waiting_to_retract" and retracted:
            Clock.schedule_once(lambda _dt: self._fire_if_allowed("manual_retract_done"), 0)
        elif state == "in_cycle.waiting_to_cut" and not retracted:
            Clock.schedule_once(lambda _dt: self._fire_if_allowed("carriage_unretracted"), 0)
        # Refresh X-position-dependent instruction text / action gate.
        if state == "in_cycle.waiting_to_retract":
            Clock.schedule_once(lambda _dt: self._apply_policy(), 0)

    def _fire_if_allowed(self, trigger: str):
        may = getattr(self._ui_fsm, f"may_{trigger}", None)
        if may is None or not may():
            return
        getattr(self._ui_fsm, trigger)()

    def _on_modes_changed(self, *args):
        # Re-sync the UI FSM with the new operator modes. In non-wizard
        # modes the cycle states are the bar's idle landing spot; in wizard
        # mode the operator drives entry via the Start button.
        self._sync_ui_state_to_modes()
        # Action-button gating in waiting_to_cut depends on retract_enabled,
        # so reapply policy whenever a mode flag flips.
        self._apply_policy()
        # When engaged-and-idle, push current direction/hysteresis to firmware
        # without forcing an FSM transition.
        if self._els_fsm.state != "stopped":
            return
        # Switching to feed mode clears the thread PITCH so a stale value
        # cannot phase-correct a turning pass; the signed Z polarity and the
        # backlash stay live, because since 2026-08-21 the pre-cut take-up and
        # its confirmation run in turning too (els_fsm.push_turning_geometry).
        if not self.is_threading:
            self._els_fsm.push_turning_geometry()
            self._hal.set_backlash_steps(int(self._els.els_backlash_steps))
        self._hal.set_stop_direction(self._els.stop_direction_value(self.els_forward))
        if self.retract_enabled or self.wizard_enabled:
            self._hal.set_hysteresis_tight()
        else:
            self._hal.set_hysteresis_loose()

    def _sync_ui_state_to_modes(self):
        """Align the UI FSM with the current wizard_enabled / retract_enabled flags.

        Non-wizard mode has no Start button, so the bar must auto-advance
        into in_cycle.waiting_to_cut whenever the operator toggles wizard
        off (or the app starts up in a non-wizard mode). Toggling wizard
        on cancels back to idle so the wizard can be initiated via Start
        as today. Mid-cycle transitions are left alone — pulling the rug
        out from under a live cut would be surprising.

        Turning retract OFF additionally has to rescue a cycle parked in
        in_cycle.waiting_to_retract: that sub-state is unreachable-from and
        meaningless in stop-only mode (`_poll_carriage_retracted` returns early
        when retract is disabled, and stop-only cut_done routes to
        waiting_to_cut), so without this the bar sits forever on a disabled
        "Retract" button reading "Enter Start Z to retract" — which is exactly
        what "the action button never enables in stop-only mode" looked like.
        """
        state = self._ui_fsm.state
        if not self.wizard_enabled:
            if state == "idle":
                self._ui_fsm.start()
            elif state in ("set_stop_z", "set_retract_z",
                           "set_start_dia", "set_stop_dia", "confirm"):
                self._ui_fsm.cancel()
                self._ui_fsm.start()
        else:
            if state.startswith("in_cycle"):
                self._ui_fsm.cancel()

        # Re-read: the branches above may have moved us.
        if not self.retract_enabled and \
                self._ui_fsm.state == "in_cycle.waiting_to_retract":
            log.info("retract disabled while waiting_to_retract — returning the "
                     "cycle to waiting_to_cut")
            self._ui_fsm.retract_mode_off()

    def _apply_policy(self):
        # X/Z input buttons are usable only when the machine is not moving.
        self.x_z_inputs_enabled = self._els_fsm.state in ["stopped", "disabled"]
        self.start_not_stop = self._ui_fsm.state == "idle"
        self.in_cycle = self._ui_fsm.state in ("in_cycle.cutting", "in_cycle.retracting")

        state = self._ui_fsm.state
        p = UI_POLICY[state]
        # Start/Stop and action both require the domain FSM to be engaged —
        # otherwise the underlying FSM triggers (cut, retract) have no
        # valid transition from 'disabled' and raise MachineError.
        self.start_stop_enabled = (
            p["can_stop"] and self._board.connected and self.engaged
        )
        self.action_button_text = p["action_button_text"]
        self.active_input = BLINK_TARGET.get(state, "")

        # Dynamic instruction text + action gating (depends on live X position
        # and on per-field validity for the non-wizard cycle states, since
        # those states are reachable before the operator has entered values).
        text = p["instruction_text"]
        allowed = True
        if state == "alarm":
            # Surface the fault reason so the bar isn't a silent dead end; the
            # operator clears it with Disengage (the domain FSM allows disable
            # from 'alarm' → 'disabled').
            allowed = False
            text = self.alarm_text or "ELS fault — disengage to clear"
        elif not self.engaged:
            allowed = False
            text = "Engage to begin"
        elif state == "in_cycle.waiting_to_cut":
            allowed = self.stop_z_valid
            if self.retract_enabled:
                allowed = allowed and self.retract_z_valid
            # In stop-only mode, also gate on Z being on the safe side of
            # stop_z — same check as the domain FSM's is_ready_to_cut.
            if allowed and not self.retract_enabled:
                allowed = self._z_safe_for_cut()
            if not allowed:
                missing = []
                if not self.stop_z_valid:
                    missing.append("Stop Z")
                if self.retract_enabled and not self.retract_z_valid:
                    missing.append("Start Z")
                if missing:
                    text = f"Enter {' and '.join(missing)} to begin cutting"
                elif not self.retract_enabled and not allowed:
                    text = "Move Z to safe side of stop position"
        elif state == "in_cycle.waiting_to_retract":
            if not self.retract_z_valid:
                allowed = False
                text = "Enter Start Z to retract"
            elif self.is_threading and not self._x_clear_of_start_dia():
                text = "Move X clear of start diameter, then retract"
                allowed = False
        self.instruction_text = text
        self.action_allowed = allowed

        # Stop-diameter latch (informational; rendered by a dedicated label).
        # Latch on the first in-cycle pass that reaches stop_dia; hold through
        # any cleanup / spring passes; drop the moment we're back to idle.
        if state == "in_cycle.waiting_to_retract":
            if not self.depth_reached and self._x_reached_stop_dia():
                self.depth_reached = True
        elif state == "idle":
            if self.depth_reached:
                self.depth_reached = False

    def _poll_apply_policy(self, *args):
        """Re-evaluate UI policy on each board tick so the action button
        enables/disables live as Z moves (important in stop-only mode
        where _apply_policy is not triggered by other bindings)."""
        self._apply_policy()

    def _propagate_stop_z_to_firmware(self):
        """Push the operator's latest stop to firmware when the domain FSM is
        engaged-and-idle, and (re)arm against it.

        Arming here matters because engaging BEFORE setting a stop is the normal
        order of operations: the engage-time arm refuses when nothing is
        committed yet, so without this retry the operator would sit engaged with
        ELS disarmed until they pressed Cut.

        Only arms when ELS is NOT already armed. Re-arming sets active=1, which
        halts a running sync feed — correct at engage, but an unwanted surprise
        if the operator merely retypes the stop while feeding. Already-armed
        stays on the old push-the-position-only path."""
        if self._els_fsm.state != "stopped":
            return
        try:
            if self._hal.read_enable():
                self._els_fsm.push_stop_to_firmware()
            else:
                self._els_fsm.arm_idle_stop()
        except Exception:
            log.debug("_propagate_stop_z_to_firmware: failed to push/arm the stop")

    # ——— Z-position predicates ———
    def _z_safe_for_cut(self) -> bool:
        """True iff Z is on the safe side of stop_z AND at least
        _safety_margin_display away. Mirrors the domain FSM's is_ready_to_cut
        logic for stop-only mode."""
        z_axis = self._els.get_z_axis()
        if z_axis is None:
            log.debug("_z_safe_for_cut: z_axis is None → True (don't block)")
            return True  # Missing axis → don't block
        try:
            z_pos = float(z_axis.scaledPosition)
        except Exception:
            log.debug(f"_z_safe_for_cut: failed to read z_pos → True (don't block)")
            return True
        try:
            cut_dir = self._els.stop_direction_value(self.els_forward)
        except Exception:
            log.debug(f"_z_safe_for_cut: failed to read cut_dir → True (don't block)")
            return True
        margin = self._els_fsm._safety_margin_display()
        diff = (z_pos - self.stop_z) * cut_dir
        result = diff < -margin if margin > 0 else diff < 0
        log.debug(
            f"_z_safe_for_cut: z_pos={z_pos} stop_z={self.stop_z} "
            f"cut_dir={cut_dir} margin={margin:.4f} diff={diff:.4f} → {result}"
        )
        return result

    # ——— X-position predicates ———
    def _x_position(self):
        axis = self._els.get_x_axis()
        if axis is None:
            return None
        try:
            return float(axis.scaledPosition)
        except Exception:
            return None

    def _x_clear_of_start_dia(self) -> bool:
        """True iff X has been backed off the workpiece past start_dia.
        OD work (is_inner=False): clear means X > start_dia (radially out).
        ID work (is_inner=True):  clear means X < start_dia (radially in).
        Missing axis → assume clear, so the gate never blocks a misconfigured rig.

        Requires a COMMITTED start diameter, like every other gating target
        (audit H1 philosophy: a 0.0 default must never gate anything). Before
        this, the uncommitted default made the gate arbitrary by machine: on an
        X DRO that reads positive it was silently vacuous (always "clear"),
        while on one that sits below its power-on zero it blocked EVERY
        threading retract in non-wizard mode — a grayed Retract button with no
        way to satisfy a gate the operator never set (observed on the real
        lathe, 2026-08-02 checklist run).
        """
        if not self._start_dia_committed:
            return True
        x = self._x_position()
        if x is None:
            return True
        return x < self.start_dia if self.is_inner else x > self.start_dia

    def _x_reached_stop_dia(self) -> bool:
        """True iff the last cut has reached/passed the COMMITTED stop
        diameter. Uncommitted → False: the informational depth-reached latch
        must not fire off the 0.0 default (on a below-zero X DRO it would
        latch "depth reached" the moment the cycle starts)."""
        if not self._stop_dia_committed:
            return False
        x = self._x_position()
        if x is None:
            return False
        return x >= self.stop_dia if self.is_inner else x <= self.stop_dia

    # ——— intents from UI ———
    @property
    def is_feeding(self):
        """Sync motion is ARMED -- the servo is commanded.

        NOT "the carriage is moving". After engage the carriage is HELD by
        elsStop.active == 1 even with servoMode == 1, and it starts moving the
        instant that hold is released. The hazard is the state where motion CAN
        begin, so that is what this reports.

        A read-through property, not a mirrored BooleanProperty. kv binds
        `app.servo.servoMode` directly (as elsbar.kv already does), so a second
        copy here would add a synchronisation problem to solve a formatting one.
        This exists for the FSM-side refusal, where the value is read once at the
        moment of the decision.
        """
        return bool(self._board.servo.servoMode)

    def toggle_engage(self):
        """Engage/disengage button intent. Drives the domain FSM.

        EVERY REFUSAL BELOW ALSO NOTIFIES (migrated 2026-08-23). This method is
        the app's clearest example of the problem the notice surface exists for:
        four paths that end in `return` with nothing but a log line, on a button
        whose entire feedback is that the LED card changes state. The
        no-Z-axis path is not hypothetical -- an unmapped ELS Z axis (or simply
        no controller connected) makes Engage a button that does nothing, with
        the explanation written to a file the operator has no way to read while
        standing at the lathe.

        The log lines stay. The log is the record and the notice is the
        surface; they answer different questions ("what happened during that
        session" vs "why did nothing happen just now") and neither replaces the
        other.
        """
        if self.engaged and self.is_feeding and self._els.spindle_is_running:
            # REFUSE. "Disable the stop" while sync motion is armed is an
            # ambiguous instruction: it reads equally as "stop the carriage" and
            # as "remove the stop and keep going". The firmware resolves it the
            # second way -- clearing enable clears elsStop.active, which is what
            # HOLDS the carriage, so disengaging RELEASES it.
            #
            # The teardown in on_enter_disabled makes that safe (sync is cleared
            # before the hold is released), so this is not the safety mechanism.
            # It removes the ambiguous input instead of interpreting it: turning
            # sync off is the unambiguous way to stop the carriage, and it is
            # exactly equivalent to opening the half nut.
            #
            # Gated on is_feeding (servoMode != 0) AND the spindle actually
            # turning. With the spindle STOPPED there are no sync deltas, so
            # nothing is moving or can begin to move, and "disengage" stops
            # being ambiguous -- the refusal would be pure friction (operator
            # decision 2026-08-17, after round-1 hardware testing found the
            # greyed button annoying with the machine plainly inert). The
            # firmware side is safe either way as of the F1/F2 fixes: the
            # enable-fall handler consumes its own resume edge and cancels
            # pending motion, hardware-verified 2026-08-17. With the spindle
            # RUNNING the original reasoning stands: the escape hatch is the
            # Sync Enable button, which stays live -- press it, then disengage.
            log.info(
                "Disengage refused — sync motion is armed. Turn Sync Enable off "
                "first (equivalent to opening the half nut)."
            )
            # WARNING, not info: the operator has to go and change something
            # before this button will work. The notice says WHAT to change,
            # because "refused" on its own leaves them pressing it again.
            self.notify("Turn Sync Enable off before disengaging",
                        NOTICE_WARNING)
            return
        if self.engaged:
            # Guard the trigger: disable() is only valid from stopped/retracting/
            # alarm. The button is disabled mid-cycle, but check anyway so a
            # stray press can never raise MachineError inside a kv handler.
            if self._els_fsm.may_disable():
                self._els_fsm.disable()
            else:
                log.warning(
                    f"Disengage ignored — not valid from '{self._els_fsm.state}'"
                )
                # INFO, not warning: this branch is the double-tap race guard
                # (the button is already disabled mid-cycle), so nothing is
                # wrong with the machine and nothing needs fixing. It says the
                # press was seen and ignored, which is all the operator needs.
                self.notify(f"Disengage ignored — ELS is {self._els_fsm.state}",
                            NOTICE_INFO)
        elif self._els.get_z_axis() is None:
            # No Z (leadscrew) axis assigned — engaging would arm ELS against a
            # non-existent axis and crash on_enter_stopped. Refuse instead of
            # entering an invalid state. Happens when the ELS Z axis hasn't been
            # mapped in setup (index defaults to -1) or no board is connected.
            log.warning(
                "Cannot engage ELS: no Z axis assigned "
                "(map the ELS Z axis in setup, or connect the controller)"
            )
            # THE ONE THAT PROVES THE SURFACE. Reachable with the button fully
            # enabled, and before this the operator's entire feedback was a
            # press that did nothing. Names the fix, not the fault.
            self.notify("No ELS Z axis assigned — map it in ELS settings",
                        NOTICE_WARNING)
        elif self._els_fsm.may_enable():
            self._els_fsm.enable()
        else:
            # `engaged` is synced via Clock.schedule_once, so a double-tap within
            # one frame could otherwise call enable() from 'stopped' → MachineError
            # inside a kv handler. Guard it (mirrors the disable side above).
            log.warning(
                f"Engage ignored — not valid from '{self._els_fsm.state}'"
            )
            # INFO for the same reason as the disengage twin above: a stale or
            # doubled tap, not a machine problem.
            self.notify(f"Engage ignored — ELS is {self._els_fsm.state}",
                        NOTICE_INFO)

    def commit_standalone_stop_z(self, stop_z_value: float):
        """Stop-Z entered via the standalone keypad or long-press capture.

        Anchors the stop to the physical encoder for that display value (in the
        current frame). The action button is the sole initiator of motion; the
        cut writes the frozen encoder to firmware.
        """
        self._commit_stop_z(stop_z_value)

    def commit_standalone_retract_z(self, retract_z_value: float):
        """Start-Z (retract target) entered outside the wizard — anchored to the
        physical encoder (consumed by ElsFsm.on_enter_retracting)."""
        self._commit_retract_z(retract_z_value)

    def commit_standalone_start_dia(self, value: float):
        """Start diameter entered via keypad/long-press — anchored to the X
        encoder so an X re-zero / units switch re-references it correctly."""
        self._commit_start_dia(value)

    def commit_standalone_stop_dia(self, value: float):
        self._commit_stop_dia(value)

    def try_advance_wizard(self):
        """If the UI FSM is on a wizard configuration step, advance to the
        next one. Otherwise do nothing.

        Used by popup-keypad / long-press callbacks that have already
        committed the operator's typed (or captured-live) value. We
        deliberately don't go through on_action_button_clicked() because
        that captures the live axis position, which would clobber what
        the user just entered. We gate on state so a long-press in
        non-wizard mode (UI FSM in in_cycle.waiting_to_cut) doesn't
        immediately fire a Cut — only the action button initiates motion.
        """
        if self._ui_fsm.state not in ("set_stop_z", "set_retract_z",
                                      "set_start_dia", "set_stop_dia"):
            return
        if not self.action_allowed:
            return
        if not self._ui_fsm.may_action():
            return
        self._ui_fsm.action()

    def on_action_button_clicked(self):
        # Capture the live axis position FIRST so the FSM's transition
        # conditions (e.g. retract_z_valid: retract_z > stop_z) evaluate
        # against the freshly-captured value, not the stale one carried
        # over from the previous visit to this wizard step.
        #
        # The axis may be unmapped (None) if the operator hasn't assigned it in
        # ELS settings -- bail rather than dereferencing None (mirrors the
        # engage-time Z guard in toggle_engage).
        #
        # SAID OUT LOUD, not only logged. These are wizard steps: the operator
        # has been asked to set a value and taps to capture it, so a silent
        # return reads as a dead button in the middle of a procedure they were
        # instructed to follow -- the worst place for one. The log line stays;
        # the log is the record and the strip is the surface. Warning rather
        # than info because it names something the operator must go and change.
        state = self._ui_fsm.state
        if state in ("set_stop_z", "set_retract_z"):
            axis = self._els.get_z_axis()
            if axis is None:
                log.warning(f"action button: no Z (saddle) axis assigned in state '{state}'")
                self.notify("No ELS Z axis assigned — map it in ELS settings",
                            NOTICE_WARNING)
                return
            if state == "set_stop_z":
                # Anchor the stop to the physical encoder at the captured Z.
                self._commit_stop_z(axis.scaledPosition)
            else:
                self._commit_retract_z(axis.scaledPosition)
        elif state in ("set_start_dia", "set_stop_dia"):
            axis = self._els.get_x_axis()
            if axis is None:
                log.warning(f"action button: no X (cross-slide) axis assigned in state '{state}'")
                # Names the cross-slide explicitly. "X axis" alone would send an
                # operator hunting through settings for which of the two this
                # step wanted -- the Z message has the same problem solved the
                # same way.
                self.notify("No ELS X (cross-slide) axis assigned — map it in "
                            "ELS settings", NOTICE_WARNING)
                return
            if state == "set_start_dia":
                self._commit_start_dia(axis.scaledPosition)
            else:
                self._commit_stop_dia(axis.scaledPosition)
        elif state == "confirm":
            # write stop z down to FW
            pass
        # State-specific safety gate (e.g. threading + X still at depth).
        if not self.action_allowed:
            log.debug(f"action button gated in state '{self._ui_fsm.state}'")
            return
        # No-op when the current state offers no `action` transition (cutting,
        # retracting, alarm — kv also blanks/disables the button, this is a
        # safety net for race conditions and stale UI taps).
        if not self._ui_fsm.may_action():
            log.debug(f"action button ignored in state '{self._ui_fsm.state}'")
            return
        self._ui_fsm.action()

    def on_start_stop_button_clicked(self):
        if self._ui_fsm.state == "idle":
            self._ui_fsm.start()
        else:
            self._ui_fsm.cancel()

    def on_back_button_clicked(self):
        self._ui_fsm.back()

    def on_ack_alarm(self):
        self._ui_fsm.ack_alarm()

    # ——— encoder-anchored stop_z / retract_z ———
    @property
    def stop_z_encoder(self):
        """Frozen leadscrew encoder count for the stop (physical target), or
        None if not set. This is what the cut writes to firmware — immune to any
        display-frame change after it was captured."""
        return self._stop_z_encoder if self._stop_z_committed else None

    @property
    def retract_z_encoder(self):
        return self._retract_z_encoder if self._retract_z_committed else None

    def _commit_stop_z(self, scaled_value: float):
        """Freeze the stop from a display-unit value (keypad entry or the live Z
        position captured at 'Set'): convert to a raw encoder count ONCE, in the
        current frame, and anchor to that. The scaled `stop_z` becomes a derived
        display mirror."""
        z = self._els.get_z_axis()
        if z is None:
            log.warning("_commit_stop_z: no ELS Z axis assigned")
            return
        self._stop_z_encoder = z.position_to_encoder(scaled_value)
        self._stop_z_committed = True
        self.stop_z = z.scaled_from_encoder(self._stop_z_encoder)
        self._validate_stop_z()
        self._validate_retract_z()
        self._propagate_stop_z_to_firmware()
        self.clear_reframe_notice()   # re-setting addresses any re-reference flag

    def _commit_retract_z(self, scaled_value: float):
        z = self._els.get_z_axis()
        if z is None:
            log.warning("_commit_retract_z: no ELS Z axis assigned")
            return
        self._retract_z_encoder = z.position_to_encoder(scaled_value)
        self._retract_z_committed = True
        self.retract_z = z.scaled_from_encoder(self._retract_z_encoder)
        self._validate_retract_z()
        self.clear_reframe_notice()

    def _commit_start_dia(self, scaled_value: float):
        x = self._els.get_x_axis()
        if x is None:
            log.warning("_commit_start_dia: no ELS X axis assigned")
            return
        self._start_dia_encoder = x.position_to_encoder(scaled_value)
        self._start_dia_committed = True
        self.start_dia = x.scaled_from_encoder(self._start_dia_encoder)

    def _commit_stop_dia(self, scaled_value: float):
        x = self._els.get_x_axis()
        if x is None:
            log.warning("_commit_stop_dia: no ELS X axis assigned")
            return
        self._stop_dia_encoder = x.position_to_encoder(scaled_value)
        self._stop_dia_committed = True
        self.stop_dia = x.scaled_from_encoder(self._stop_dia_encoder)

    def _poll_reframe_targets(self, *args):
        """Re-render each committed target's derived scaled value from its frozen
        encoder every tick, so a DRO re-zero or units switch just re-displays the
        physical target (the encoder never moves) — the same way the DRO itself
        reframes. A re-render that is NOT a units change (units is on the
        operator) is a "re-reference" event → notify per the operator's setting."""
        z = self._els.get_z_axis()
        x = self._els.get_x_axis()
        try:
            factor = float(self._board.formats.factor)
        except Exception:
            factor = self._last_factor
        units_changed = factor != self._last_factor
        self._last_factor = factor

        z_reframed = False
        dia_reframed = False
        if z is not None:
            if self._stop_z_committed:
                new = z.scaled_from_encoder(self._stop_z_encoder)
                if new != self.stop_z:
                    self.stop_z = new
                    z_reframed = True
            if self._retract_z_committed:
                new = z.scaled_from_encoder(self._retract_z_encoder)
                if new != self.retract_z:
                    self.retract_z = new
                    z_reframed = True
        if x is not None:
            if self._start_dia_committed:
                new = x.scaled_from_encoder(self._start_dia_encoder)
                if new != self.start_dia:
                    self.start_dia = new
                    dia_reframed = True
            if self._stop_dia_committed:
                new = x.scaled_from_encoder(self._stop_dia_encoder)
                if new != self.stop_dia:
                    self.stop_dia = new
                    dia_reframed = True

        # Re-validate ONLY AFTER all mirrors are in the new frame — otherwise
        # _validate_retract_z would compute span = |retract_z - stop_z| across
        # MIXED frames (one reframed, one not) and durably flip retract_z_valid,
        # silently disabling the retract-mode safety margin after a re-zero.
        # (The physical span is frame-invariant, so validity shouldn't change —
        # but validate in a single consistent frame regardless.)
        if z_reframed:
            self._validate_retract_z()
        if dia_reframed:
            self._validate_stop_dia()

        # Notify only on a Z stop/retract re-reference (the safety targets) that
        # wasn't a units switch. A diameter-only (X) re-zero re-renders silently.
        if z_reframed and not units_changed:
            self._on_targets_reframed()

    # ——— re-reference notify ———
    def _reframe_notify_mode(self) -> str:
        mode = getattr(self._els, "stop_z_reframe_notify", "silent")
        return mode if mode in ("silent", "warn", "confirm") else "silent"

    def _on_targets_reframed(self):
        """A committed target's displayed value changed due to a DRO re-zero /
        coordinate-system switch (not units). The physical target is unchanged;
        flag it per the operator's notify preference."""
        mode = self._reframe_notify_mode()
        if mode == "silent":
            return
        self.reframe_message = "Stop/Start re-referenced after a zero/offset change"
        if mode == "confirm":
            self.reframe_confirm_pending = True
        else:  # warn
            self.targets_reframed_warn = True

    def clear_reframe_notice(self):
        """Dismiss the warn highlight / confirm bar — the operator acknowledged,
        re-set a value, or started the next cut. Physical targets are unchanged."""
        self.targets_reframed_warn = False
        self.reframe_confirm_pending = False
        self.reframe_message = ""

    def reset_reframed_targets(self):
        """'Reset both' from the confirm bar: invalidate Stop Z + Start Z (the
        safety targets the notice is about) so the operator re-sets them (→ '--').
        Diameters are not touched — the notice only fires on a Z re-reference."""
        self._invalidate_z_targets()
        self.clear_reframe_notice()

    # ——— invalidation ———
    def invalidate_stop_z(self):
        """Mark the stored stop_z as no longer a known target, so a cut is
        blocked until the operator sets it again (never-set or an ELS Z-axis
        remap — a display-frame change does NOT invalidate; it just reframes)."""
        self._stop_z_committed = False
        self._stop_z_encoder = None
        self._validate_stop_z()

    def invalidate_retract_z(self):
        self._retract_z_committed = False
        self._retract_z_encoder = None
        self._validate_retract_z()

    def invalidate_start_dia(self):
        self._start_dia_committed = False
        self._start_dia_encoder = None

    def invalidate_stop_dia(self):
        self._stop_dia_committed = False
        self._stop_dia_encoder = None

    def _invalidate_z_targets(self, *_):
        # An ELS Z-axis remap makes both captured encoders meaningless.
        self.invalidate_stop_z()
        self.invalidate_retract_z()

    def _invalidate_x_targets(self, *_):
        self.invalidate_start_dia()
        self.invalidate_stop_dia()

    # ——— validators ———
    def _validate_stop_z(self):
        # A stop_z is only "valid" once the operator has actually set one — the
        # 0.0 default must never be silently usable for a cut (audit H1). The
        # action gate + the "--" display both key off this.
        self.stop_z_valid = self._stop_z_committed
        self.stop_z_error = "" if self.stop_z_valid else "Stop Z not set"

    def _validate_retract_z(self):
        if not self._retract_z_committed:
            self.retract_z_valid = False
            self.retract_z_error = "Start Z not set"
            return
        margin = self._els_fsm._safety_margin_display()
        span = abs(self.retract_z - self.stop_z)
        self.retract_z_valid = span >= margin if margin > 0 else self.retract_z != self.stop_z
        self.retract_z_error = "" if self.retract_z_valid else "Retract Z too close to stop position"

    def _validate_start_dia(self):
        # No validation criteria
        self.start_dia_valid = True
        self.start_dia_error = ""

    def _validate_stop_dia(self):
        self.stop_dia_valid = self.stop_dia < self.start_dia # TODO: account for direction
        self.stop_dia_error = "" if self.stop_dia_valid else "Invalid stop diameter setting"

    # ——— UI FSM —> ELS FSM methods ———
    def may_cut(self) -> bool:
        """True iff the domain FSM would accept a cut right now — a FRESH check
        (not the cached action_allowed policy). Gates the UI
        waiting_to_cut→cutting transition so a stale click can't lock the UI in
        'Cutting…' (Stop disabled, no exit) when ElsFsm.cut() refuses. Uses the
        transitions-generated may_cut(), which checks BOTH the source state
        (must be 'stopped' — not e.g. 'alarm') AND is_ready_to_cut, so a cut is
        never driven from a faulted domain FSM. Any error → refuse."""
        try:
            return bool(self._els_fsm.may_cut())
        except Exception:
            log.debug("may_cut: domain may_cut() raised → refusing the cut")
            return False

    def may_retract(self) -> bool:
        """True iff the domain FSM would accept a retract right now — a FRESH
        check, the retract-side twin of :meth:`may_cut`. Gates the UI
        waiting_to_retract→retracting transition so a refused retract can't lock
        the UI in 'Retracting…' with no exit. Any error → refuse."""
        try:
            return bool(self._els_fsm.may_retract())
        except Exception:
            log.debug("may_retract: domain may_retract() raised → refusing")
            return False

    def feed_without_armed_stop(self) -> bool:
        """True iff enabling a sync feed right now would run with NO ELS stop to
        arrest it: ELS not engaged, or engaged but no armed stop
        (``elsStop.enable`` cleared, e.g. Z on the wrong side at engage). Used to
        decide whether a feed-enable needs operator confirmation (audit H2b/H6)."""
        if not self.engaged:
            return True
        try:
            return not bool(self._board.device['elsStop']['enable'])
        except Exception:
            return True  # can't tell → treat as unsafe, confirm

    def unarmed_stop_message(self) -> str:
        """Why enabling the feed right now has no armed stop behind it —
        operator-facing, cause-specific. With arming positionally
        unconditional (2026-08-17: the Z-past-stop refusal was deleted as the
        actual cause of round 2's misleading dialog), an engaged machine with
        a committed stop is always armed, so only two honest causes remain."""
        if not self.engaged:
            return ("ELS is not engaged. The feed will run with no "
                    "automatic stop.\n\nEnable feed anyway?")
        if self.stop_z_encoder is None:
            return ("No stop is set. The feed will run with no automatic "
                    "stop.\n\nEnable feed anyway?")
        return ("The ELS stop is not armed. The feed will run with no "
                "automatic stop.\n\nEnable feed anyway?")

    def _on_pass_interrupted(self):
        """Tell the operator we stopped a pass that the firmware was still running.

        Fired by ElsFsm.reconcile_firmware_on_connect when it comes up to find the
        carriage moving -- i.e. the previous session died mid-pass and the
        firmware carried on without it.

        THE WHOLE POINT IS REMOVING AMBIGUITY. Disabling sync on connect is a
        deliberate choice (safest in the large majority of cases; see the note in
        reconcile_firmware_on_connect), but its cost lands exactly where it is
        hardest to interpret -- a thread that stops partway with no explanation.
        Saying so plainly turns a mystery into a known event.

        Non-blocking and informational: the stop has ALREADY happened by the time
        this runs. There is nothing to confirm and nothing to undo, so it is an
        acknowledgement, not a choice.
        """
        fsm = self._els_fsm
        flight = getattr(fsm, 'interrupted_pass', None)
        if not flight:
            return
        fsm.interrupted_pass = None   # one-shot; do not re-warn on a later event

        armed = "with an armed stop" if flight.get('enable') else "with NO armed stop"
        log.warning(f"presenting interrupted-pass notice to operator: {flight}")
        try:
            from reflex.components.popups.custom_popup import CustomPopup
            CustomPopup(
                title="Feed Stopped On Startup",
                message="\n\n".join([
                    "The controller was still driving the carriage when this app "
                    f"started ({armed}).",
                    "That means the previous session ended mid-pass. Sync has been "
                    "disabled and the ELS stop cleared, so the carriage was stopped "
                    "HERE rather than at a shoulder.",
                    "Check the workpiece and tool before continuing.",
                ]),
                button_text="OK",
            ).open()
        except Exception as e:
            # A notice that cannot be drawn must not take the app down on startup.
            log.error(f"could not show interrupted-pass notice: {e}")

    def request_feed_enable(self, confirmed: bool = False) -> bool:
        """Operator asked to toggle the sync/power feed (advanced ELS context).

        Returns True if handled, False if enabling needs confirmation first —
        i.e. turning the feed ON with no armed ELS stop. The caller (UI) should
        then show a confirm dialog and, on confirm, call again with
        ``confirmed=True``. Turning the feed OFF is never gated."""
        servo = self._board.servo
        turning_on = servo.servoMode == 0
        if not turning_on:
            # Feed already on. A plain press toggles it OFF (never gated); a
            # confirm callback is a no-op (the feed the operator wanted is on —
            # don't toggle it off just because it was enabled meanwhile).
            if not confirmed:
                servo.toggle_enable()
            return True
        if not confirmed and self.feed_without_armed_stop():
            log.info("request_feed_enable: no armed ELS stop — confirmation required")
            return False
        servo.toggle_enable()
        return True

    def start_cut(self):
        self.clear_reframe_notice()   # starting the cut clears a re-reference flag
        # Clear a previous take-up refusal HERE, on initiation, rather than
        # waiting for the next outcome. The operator's response to the warning
        # is to close the half-nut and press Cut again; leaving the old warning
        # up through the retry reads as "still refusing" during the very window
        # where they are watching to see whether it worked.
        self.takeup_warning = ""
        self._els_fsm.push_stop_to_firmware()
        self._els_fsm.cut()

    def start_retract(self):
        self._els_fsm.retract()
 