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
    "idle":          {"action_button_text": "\uf013", "can_stop": True, "instruction_text": ""},
    "set_stop_z":    {"action_button_text": "Set", "can_stop": True, "instruction_text": "Go to or enter stop Z position and press Set"},
    "set_retract_z": {"action_button_text": "Set", "can_stop": True, "instruction_text": "Go to or enter start Z position and press Set"},
    "set_start_dia": {"action_button_text": "Set", "can_stop": True, "instruction_text": "Go to or enter start diameter and press Set"},
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

    # ── Derived UI state ───────────────────────────────────────────────
    x_z_inputs_enabled  = BooleanProperty(False)
    start_stop_enabled  = BooleanProperty(False)
    start_not_stop      = BooleanProperty(False)

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

        # 7. Re-arm HAL when mode flags change so firmware tracks the operator.
        self.bind(retract_enabled=self._on_modes_changed,
                  wizard_enabled=self._on_modes_changed,
                  els_forward=self._on_modes_changed)

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

    def _on_modes_changed(self, *args):
        # When engaged-and-idle, push current direction/hysteresis to firmware
        # without forcing an FSM transition.
        if self._els_fsm.state != "stopped":
            return
        self._hal.set_stop_direction(self._els.stop_direction_value(self.els_forward))
        if self.retract_enabled or self.wizard_enabled:
            self._hal.set_hysteresis_tight()
        else:
            self._hal.set_hysteresis_loose()

    def _apply_policy(self):
        # X/Z input buttons are usable only when the machine is not moving.
        self.x_z_inputs_enabled = self._els_fsm.state in ["stopped", "disabled"]
        self.start_not_stop = self._ui_fsm.state == "idle"

        p = UI_POLICY[self._ui_fsm.state]
        self.start_stop_enabled = p["can_stop"] and self._board.connected
        self.instruction_text = p["instruction_text"]
        self.action_button_text = p["action_button_text"]
        self.active_input = BLINK_TARGET.get(self._ui_fsm.state, "")

    # ——— intents from UI ———
    def toggle_engage(self):
        """Engage/disengage button intent. Drives the domain FSM."""
        if self.engaged:
            self._els_fsm.disable()
        else:
            self._els_fsm.enable()

    def commit_standalone_stop_z(self, stop_z_value: float):
        """Stop-Z entered via the standalone keypad (no wizard). Pushes the
        new target through the FSM/HAL and re-arms enable to match the
        current engagement.
        """
        self.stop_z = stop_z_value
        self._els_fsm.set_stop_z(stop_z_value)
        self._hal.set_enable(self.engaged)

    def on_action_button_clicked(self):
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
        self._ui_fsm.action()
        pass

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
 