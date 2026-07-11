from kivy.properties import StringProperty, BooleanProperty, NumericProperty
from kivy.event import EventDispatcher
from kivy.clock import Clock
from kivy.logger import Logger

from reflex.dispatchers import els, board
from reflex.fsms.ui_fsm import ElsUiFsm
from reflex.fsms.els_fsm import ElsFsm
from reflex.fsms.els_stop_hal import ElsStopHal
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

    def __init__(self, els: els, board: board, **kw):
        super().__init__(**kw)
        self._els = els
        self._board = board
        self._hal = ElsStopHal(board)

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

        # 2. Build domain FSM (HAL injected; controller doubles as modes source).
        self._els_fsm = ElsFsm(els, board, self._hal, self)

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

        # 6. Apply initial state policy and connect HW bindings.
        self._apply_policy()
        self._board.bind(connected=self._on_connected_changed)
        self._board.bind(update_tick=self._poll_els_stop_active)
        self._board.bind(update_tick=self._poll_carriage_retracted)
        self._board.bind(update_tick=self._poll_apply_policy)
        self._board.bind(update_tick=self._poll_reframe_targets)

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
        # Clear thread geometry when switching to feed mode so stale values
        # don't trigger firmware takeup/phase-correction on future transitions.
        if not self.is_threading:
            self._hal.set_thread_pitch_steps(0.0)
            self._hal.set_z_counts_per_pitch(0.0)
            self._hal.set_backlash_steps(0)
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
        """Write stopPosition to firmware when domain FSM is in stopped state,
        so ELS uses the operator's latest configured value immediately."""
        if self._els_fsm.state != "stopped":
            return
        try:
            self._els_fsm.push_stop_to_firmware()
        except Exception:
            log.debug("_propagate_stop_z_to_firmware: failed to write stopPosition")

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
            # Guard the trigger: disable() is only valid from stopped/retracting/
            # alarm. The button is disabled mid-cycle, but check anyway so a
            # stray press can never raise MachineError inside a kv handler.
            if self._els_fsm.may_disable():
                self._els_fsm.disable()
            else:
                log.warning(
                    f"Disengage ignored — not valid from '{self._els_fsm.state}'"
                )
        elif self._els.get_z_axis() is None:
            # No Z (leadscrew) axis assigned — engaging would arm ELS against a
            # non-existent axis and crash on_enter_stopped. Refuse instead of
            # entering an invalid state. Happens when the ELS Z axis hasn't been
            # mapped in setup (index defaults to -1) or no board is connected.
            log.warning(
                "Cannot engage ELS: no Z axis assigned "
                "(map the ELS Z axis in setup, or connect the controller)"
            )
        elif self._els_fsm.may_enable():
            self._els_fsm.enable()
        else:
            # `engaged` is synced via Clock.schedule_once, so a double-tap within
            # one frame could otherwise call enable() from 'stopped' → MachineError
            # inside a kv handler. Guard it (mirrors the disable side above).
            log.warning(
                f"Engage ignored — not valid from '{self._els_fsm.state}'"
            )

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
        # ELS settings -- bail with a warning rather than dereferencing None
        # (mirrors the engage-time Z guard in toggle_engage).
        state = self._ui_fsm.state
        if state in ("set_stop_z", "set_retract_z"):
            axis = self._els.get_z_axis()
            if axis is None:
                log.warning(f"action button: no Z (saddle) axis assigned in state '{state}'")
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
        self._els_fsm.push_stop_to_firmware()
        self._els_fsm.cut()

    def start_retract(self):
        self._els_fsm.retract()
 