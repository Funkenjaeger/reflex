from kivy.properties import StringProperty, BooleanProperty, NumericProperty
from kivy.event import EventDispatcher
from kivy.clock import Clock
from kivy.logger import Logger

from rcp.dispatchers import els, board
from rcp.fsms.ui_fsm import ElsUiFsm
from rcp.fsms.els_fsm import ElsFsm
from rcp.fsms.els_stop_hal import ElsStopHal
from rcp.fsms.fsm_event_bus import fsm_event_bus as bus

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

class ElsUiController(EventDispatcher):
    # ── Operator-mode flags (read by FSM conditions / on_enter_stopped) ─
    retract_enabled     = BooleanProperty(False)
    wizard_enabled      = BooleanProperty(False)
    els_forward         = BooleanProperty(True)

    # ── Hardware-state mirrors (kv binds to these) ─────────────────────
    engaged             = BooleanProperty(False)   # True iff domain FSM not in 'disabled'
    els_stop_active     = BooleanProperty(False)   # mirrors HAL read_active()

    # ── Operator-job descriptors (mirrored from the widget) ────────────
    # is_threading toggles the X-clear-of-start-dia gate in waiting_to_retract.
    # is_inner flips the "outside" comparison: OD work retracts to larger X,
    # ID work retracts to smaller X.
    is_threading        = BooleanProperty(False)
    is_inner            = BooleanProperty(False)

    # ── Derived UI state ───────────────────────────────────────────────
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

    ui_state = StringProperty("idle")

    def __init__(self, els: els, board: board, **kw):
        super().__init__(**kw)
        self._els = els
        self._board = board
        self._hal = ElsStopHal(board)

        # 1. Wire validation bindings before anything observes *_valid flags.
        self.bind(stop_z=lambda *_: self._validate_stop_z(),
                  retract_z=lambda *_: self._validate_retract_z(),
                  start_dia=lambda *_: self._validate_start_dia(),
                  stop_dia=lambda *_: self._validate_stop_dia())

        # 2. Eagerly compute initial validation so FSM guards don't start False.
        self._validate_stop_z()
        self._validate_retract_z()
        self._validate_start_dia()
        self._validate_stop_dia()

        # 3. Build domain FSM (HAL injected; controller doubles as modes source).
        self._els_fsm = ElsFsm(els, board, self._hal, self)

        # 4. Build UI FSM last (depends on a fully-constructed controller).
        self._ui_fsm = ElsUiFsm(self)

        # 5. Subscribe to bus events after both FSMs exist.
        bus.subscribe("ui_state_changed", self._on_ui_state_changed)
        bus.subscribe("state_changed", self._on_domain_state_changed)
        bus.subscribe("alarm_raised", self._on_alarm)

        # 6. Apply initial state policy and connect HW bindings.
        self._apply_policy()
        self._board.bind(connected=self._on_connected_changed)
        self._board.bind(update_tick=self._poll_els_stop_active)
        self._board.bind(update_tick=self._poll_carriage_retracted)
        self._board.bind(update_tick=self._poll_apply_policy)

        # 7. Re-arm HAL when mode flags change so firmware tracks the operator.
        self.bind(retract_enabled=self._on_modes_changed,
                  wizard_enabled=self._on_modes_changed,
                  els_forward=self._on_modes_changed)

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
        log.info("ui controller _on_ui_state_changed()")
        def _apply(_dt):
            self.ui_state = state
            self._apply_policy()
        Clock.schedule_once(_apply, 0)

    def _on_alarm(self, reason):
        Clock.schedule_once(lambda _dt: setattr(self, "alarm_text", reason), 0)

    def _on_connected_changed(self, instance, value):
        Clock.schedule_once(lambda _dt: self._apply_policy(), 0)

    def _on_domain_state_changed(self, state):
        # Mirror domain FSM state into a Kivy property the widget can bind to.
        Clock.schedule_once(lambda _dt: self._sync_engaged(state), 0)

    def _sync_engaged(self, state):
        self.engaged = state != "disabled"
        self._apply_policy()

    def _poll_els_stop_active(self, *args):
        # Bound to board.update_tick. Kivy property writes from the polling
        # thread are an existing project-wide pattern; matching that here.
        active = self._hal.read_active()
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
        self._hal.set_stop_direction(self._els.stop_direction_value(self.els_forward))
        if self.retract_enabled or self.wizard_enabled:
            self._hal.set_hysteresis_tight()
        else:
            self._hal.set_hysteresis_loose()

    def _sync_ui_state_to_modes(self):
        """Align the UI FSM with the current wizard_enabled flag.

        Non-wizard mode has no Start button, so the bar must auto-advance
        into in_cycle.waiting_to_cut whenever the operator toggles wizard
        off (or the app starts up in a non-wizard mode). Toggling wizard
        on cancels back to idle so the wizard can be initiated via Start
        as today. Mid-cycle transitions are left alone — pulling the rug
        out from under a live cut would be surprising.
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

    def _apply_policy(self):
        # X/Z input buttons are usable only when the machine is not moving.
        self.x_z_inputs_enabled = self._els_fsm.state in ["stopped", "disabled"]
        self.start_not_stop = self._ui_fsm.state == "idle"

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
        if not self.engaged:
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

    # ——— Z-position predicates ———
    def _z_safe_for_cut(self) -> bool:
        """True iff Z is on the safe side of stop_z (not past it in the
        cutting direction). Mirrors the domain FSM's is_ready_to_cut
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
            cut_dir = self._els.direction_sign(self.els_forward)
        except Exception:
            log.debug(f"_z_safe_for_cut: failed to read cut_dir → True (don't block)")
            return True
        result = (z_pos - self.stop_z) * cut_dir < 0
        log.debug(
            f"_z_safe_for_cut: z_pos={z_pos} stop_z={self.stop_z} "
            f"cut_dir={cut_dir} diff={z_pos - self.stop_z} → {result}"
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
        """
        x = self._x_position()
        if x is None:
            return True
        return x < self.start_dia if self.is_inner else x > self.start_dia

    def _x_reached_stop_dia(self) -> bool:
        """True iff the last cut has reached/passed the configured stop diameter."""
        x = self._x_position()
        if x is None:
            return False
        return x >= self.stop_dia if self.is_inner else x <= self.stop_dia

    # ——— intents from UI ———
    def toggle_engage(self):
        """Engage/disengage button intent. Drives the domain FSM."""
        if self.engaged:
            self._els_fsm.disable()
        else:
            self._els_fsm.enable()

    def commit_standalone_stop_z(self, stop_z_value: float):
        """Stop-Z entered via the standalone keypad or long-press capture.

        Stashes the value on the controller only — no firmware writes here.
        The action button is the sole initiator of motion: when the cycle
        advances to in_cycle.cutting, ElsFsm.start_cut() pushes stop_z to
        firmware via the HAL.
        """
        self.stop_z = stop_z_value

    def commit_standalone_retract_z(self, retract_z_value: float):
        """Start-Z (retract target) entered outside the wizard. retract_z is
        only consumed by ElsFsm.on_enter_retracting, so we just stash it on
        the controller — no firmware write here.
        """
        self.retract_z = retract_z_value

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
        if self._ui_fsm.state == "set_stop_z":
            self.stop_z = self._els.get_z_axis().scaledPosition
        elif self._ui_fsm.state == "set_retract_z":
            self.retract_z = self._els.get_z_axis().scaledPosition
        elif self._ui_fsm.state == "set_start_dia":
            self.start_dia = self._els.get_x_axis().scaledPosition
        elif self._ui_fsm.state == "set_stop_dia":
            self.stop_dia = self._els.get_x_axis().scaledPosition
        elif self._ui_fsm.state == "confirm":
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

    # ——— validators ———
    def _validate_stop_z(self):
        # No validation criteria
        self.stop_z_valid = True
        self.stop_z_error = ""

    def _validate_retract_z(self):
        self.retract_z_valid = self.retract_z > self.stop_z # TODO: account for direction
        self.retract_z_error = "" if self.retract_z_valid else "Invalid retract Z setting"

    def _validate_start_dia(self):
        # No validation criteria
        self.start_dia_valid = True
        self.start_dia_error = ""

    def _validate_stop_dia(self):
        self.stop_dia_valid = self.stop_dia < self.start_dia # TODO: account for direction
        self.stop_dia_error = "" if self.stop_dia_valid else "Invalid stop diameter setting"

    # ——— UI FSM —> ELS FSM methods ———
    def start_cut(self):
        self._els_fsm.set_stop_z(self.stop_z)
        self._els_fsm.cut()

    def start_retract(self):
        self._els_fsm.retract()
 