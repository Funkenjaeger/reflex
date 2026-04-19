from kivy.logger import Logger
from kivy.properties import NumericProperty, BooleanProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout

from rcp import feeds
from rcp.components.home.thread_type import ThreadType
from rcp.components.popups.custom_popup import CustomPopup
from rcp.dispatchers.els_state_machine import ElsStateMachine
from rcp.dispatchers.saving_dispatcher import SavingDispatcher
from rcp.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class ElsAdvancedBar(BoxLayout, SavingDispatcher):
    """Unified ELS advanced bar — hosts the ElsStateMachine and supports all
    three operating modes (stop-only, stop+retract, wizard) via the
    enable_stop/enable_retract/enable_wizard flags.
    """

    # ── Mode flags ────────────────────────────────────────────────────────────
    enable_stop = BooleanProperty(True)
    enable_retract = BooleanProperty(True)
    enable_wizard = BooleanProperty(True)

    # ── Per-job thread settings (persisted) ──────────────────────────────────
    metric_mode = BooleanProperty(True)
    selected_pitch = StringProperty("")
    current_feeds_index = NumericProperty(0)
    thread_profile_type = StringProperty("ISO_METRIC")
    shaft_diameter = NumericProperty(1)
    left_hand_thread = BooleanProperty(False)
    inner_thread = BooleanProperty(False)

    # ── ELS Stop hardware state ─────────────────────────────────────────────
    els_stop_engaged = BooleanProperty(False)

    # ── Transient UI state ───────────────────────────────────────────────────
    is_active = BooleanProperty(True)
    is_running = BooleanProperty(False)
    action_button_enabled = BooleanProperty(True)
    label_text = StringProperty("")
    display_value = StringProperty("")
    next_button_text = StringProperty("")
    start_position = NumericProperty(0)
    stop_position = NumericProperty(0)
    material_width = NumericProperty(0)
    cutting_depth = NumericProperty(0)
    last_cutting_depth = NumericProperty(0)
    retract_button_visible = BooleanProperty(False)
    retract_button_enabled = BooleanProperty(True)

    # ── State-machine mirror + per-button display text ───────────────────────
    current_state = StringProperty("idle")
    start_z_text = StringProperty("")
    stop_z_text = StringProperty("")
    major_diameter_text = StringProperty("")
    minor_diameter_text = StringProperty("")

    _skip_save = [
        "is_active",
        "is_running",
        "action_button_enabled",
        "label_text",
        "display_value",
        "next_button_text",
        "start_position",
        "stop_position",
        "material_width",
        "cutting_depth",
        "last_cutting_depth",
        "retract_button_visible",
        "retract_button_enabled",
        "current_state",
        "start_z_text",
        "stop_z_text",
        "major_diameter_text",
        "minor_diameter_text",
        "position",
        "x", "y",
        "minimum_width",
        "minimum_height",
        "width", "height",
    ]

    def __init__(self, **kwargs):
        from rcp.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        self.action_button_condition_fn = None
        self.retract_button_condition_fn = None
        self._on_value_button_release = None
        super().__init__(**kwargs)

        self.current_feeds_table = (
            feeds.table["Thread MM"] if self.metric_mode else feeds.table["Thread IN"]
        )
        if not self.thread_profile_type:
            self.thread_profile_type = ThreadType.ISO_METRIC.value

        self.machine = ElsStateMachine(self)
        self.update_feeds_ratio(self, None)
        self.bind(left_hand_thread=self.update_feeds_ratio)
        self.bind(els_stop_engaged=self._on_els_stop_engaged)
        self.bind(enable_retract=self._update_hysteresis)
        self.bind(enable_wizard=self._update_hysteresis)
        self.app.board.bind(update_tick=self._poll_els_stop_active)

    # ── Engage / disengage ───────────────────────────────────────────────────

    def toggle_engage(self):
        self.els_stop_engaged = not self.els_stop_engaged

    def _on_els_stop_engaged(self, instance, value):
        if self.app.board.connected and value:
            self.app.board.device['elsStop']['active'] = 0
            self.app.board.device['elsStop']['stopDirection'] = -1
            self.app.board.device['elsStop']['enable'] = 1
            self._update_hysteresis()
            log.info("elsStop engaged")

    def _update_hysteresis(self, *args):
        if not self.app.board.connected:
            return
        hysteresis = 0 if (self.enable_retract or self.enable_wizard) else 800
        self.app.board.device['elsStop']['hysteresis'] = hysteresis
        log.info(f"elsStop.hysteresis = {hysteresis}")

    def _poll_els_stop_active(self, *args):
        if not self.app.board.connected:
            return
        active = bool(self.app.board.device['elsStop']['active'])
        if active and self.els_stop_engaged:
            log.info("elsStop.active set by firmware — disengaging indicator")
            self.els_stop_engaged = False
        elif not active and not self.els_stop_engaged and self.app.board.device['elsStop']['enable']:
            log.info("elsStop.active cleared by firmware — re-engaging indicator")
            self.els_stop_engaged = True

    # ── Start / stop ──────────────────────────────────────────────────────────

    def toggle_is_running(self):
        if not self.is_running:
            if not self.app.board.connected:
                CustomPopup(
                    title="Controller Not Connected",
                    message=(
                        "The motion controller board is not connected. "
                        "ELS cannot capture scale positions without live "
                        "hardware. Check the RS-485 connection and try again."
                    ),
                    button_text="OK",
                ).open()
                return
            missing = []
            if self.app.els.get_spindle_axis() is None:
                missing.append("Spindle")
            if self.app.els.get_z_axis() is None:
                missing.append("Saddle (Z)")
            if self.enable_wizard and self.app.els.get_x_axis() is None:
                missing.append("Cross-slide (X)")
            if missing:
                CustomPopup(
                    title="Axes Not Configured",
                    message=(
                        f"The following axes are not set in ELS: "
                        f"{', '.join(missing)}. Please configure them in Settings."
                    ),
                    button_text="OK",
                ).open()
                return
        self.is_running = not self.is_running
        if self.is_running:
            self.machine.start()
        else:
            self.machine.stop()

    # ── Button event handlers ────────────────────────────────────────────────

    def on_action_button_clicked(self):
        if self.is_running:
            self.machine.action()
        else:
            self.open_settings()

    def on_retract_button_pressed(self):
        if not self.retract_button_enabled:
            return
        self.machine.start_retracting()

    def on_retract_button_released(self):
        if not self.retract_button_enabled:
            return
        self.machine.stop_retracting()

    # ── Settings ─────────────────────────────────────────────────────────────

    def on_metric_mode(self, instance, value):
        self.current_feeds_table = (
            feeds.table["Thread MM"] if value else feeds.table["Thread IN"]
        )

    def update_feeds_ratio(self, instance, value):
        if not self.is_active:
            return
        ratio = self.current_feeds_table[self.current_feeds_index].ratio
        spindle_axis = self.app.els.get_spindle_axis()
        if spindle_axis is not None:
            direction = -1 if self.left_hand_thread else 1
            spindle_axis.syncRatioNum = ratio.numerator * direction
            spindle_axis.syncRatioDen = ratio.denominator
        log.info(
            f"Configured ratio is: {ratio.numerator}/{ratio.denominator}, "
            f"left_hand_thread={self.left_hand_thread}"
        )

    def open_settings(self):
        from rcp.components.home.els_settings_popup import ElsSettingsPopup
        popup = ElsSettingsPopup(bar=self)
        popup.open()

    # ── Display binding ──────────────────────────────────────────────────────

    def bind_display_value_to_scale(self, axis, target_prop: str = "display_value"):
        """Bind `target_prop` to an AxisDispatcher's formattedPosition with
        strict keypad override support.

        `target_prop` lets each state target one of the per-button text
        properties (`start_z_text`, `stop_z_text`, `major_diameter_text`)
        instead of the shared `display_value`.
        """
        self.unbind_all_display_value()
        self._bound_scale = axis
        self._bound_target_prop = target_prop
        inp = axis._primary_input() if axis is not None else None

        def on_encoder_update(*_):
            if self.machine and self.machine.manual_stop_length is not None:
                log.info(
                    "Scale encoder moved — discarding manual stop length override"
                )
                self.machine.manual_stop_length = None
            setattr(self, target_prop, axis.formattedPosition)
            self.update_buttons_state()

        def on_format_update(instance, value):
            if not (self.machine and self.machine.manual_stop_length is not None):
                setattr(self, target_prop, value)

        self._on_encoder_update = on_encoder_update
        self._on_format_update = on_format_update

        if inp is not None:
            inp.bind(encoderCurrent=on_encoder_update)
        axis.bind(formattedPosition=on_format_update)

        setattr(self, target_prop, axis.formattedPosition)

    def bind_display_value_to_servo_position(self):
        self.unbind_all_display_value()
        self._bound_servo = self.app.servo

        def on_servo_position_update(instance, value):
            self.display_value = value

        self._on_servo_position_update = on_servo_position_update
        self.app.servo.bind(formattedPosition=on_servo_position_update)

    def bind_btn_value_on_release(self, on_release_fn):
        """No-op kept for backward compatibility.

        The former single `btn_value` control has been replaced by the four
        `TextHeaderButton`s (`btn_start_z`, `btn_stop_z`, `btn_major_dia`,
        `btn_minor_dia`). Keypad entry is now dispatched via
        :meth:`on_value_button_released` based on the current machine state.
        """
        self._on_value_button_release = on_release_fn

    def on_value_button_released(self, which: str):
        """Dispatcher wired to each TextHeaderButton's on_release in the kv.

        For stop_z: always opens keypad (supports standalone stop without wizard).
        For wizard fields: only opens keypad when the machine is prompting.
        """
        if which == "stop_z":
            if hasattr(self, "machine") and self.machine is not None and self.machine.state == "set_stop_z":
                self.machine._open_stop_position_keypad()
            else:
                self._open_standalone_stop_z_keypad()
            return
        if not hasattr(self, "machine") or self.machine is None:
            return
        state = self.machine.state
        if which == "minor_dia" and state == "set_minor_diameter":
            self.machine._open_cutting_depth_keypad()

    def _open_standalone_stop_z_keypad(self):
        """Open keypad for stop Z entry outside the wizard state machine."""
        from rcp.components.popups.keypad import Keypad
        z_axis = self.app.els.get_z_axis()
        if z_axis is None:
            from rcp.components.popups.custom_popup import CustomPopup
            CustomPopup(
                title="Axis Not Configured",
                message="Saddle (Z) axis is not set in ELS settings.",
                button_text="OK",
            ).open()
            return

        is_metric = self.app.formats.current_format == "MM"
        keypad = Keypad(
            title="Enter Stop Z Position (" + ("mm" if is_metric else "in") + ")"
        )
        keypad.integer = False

        def on_done(value):
            try:
                stop_val = float(value)
                self.stop_z_text = (
                    f"{stop_val:.3f}" if is_metric else f"{stop_val:.4f}"
                )
                # Convert display units to encoder counts
                z_input = z_axis._primary_input()
                if z_input is None:
                    return
                factor = float(
                    self.app.formats.MM_FRACTION if is_metric
                    else self.app.formats.INCHES_FRACTION
                )
                encoder_counts = (stop_val / factor) * (
                    float(z_input.ratioDen) / float(z_input.ratioNum)
                )
                stop_encoder = int(round(encoder_counts))

                # Update elsStop registers
                if self.app.board.connected:
                    dev = self.app.board.device
                    dev['elsStop']['stopPosition'] = stop_encoder
                    dev['elsStop']['scaleIndex'] = z_input.inputIndex
                    dev['elsStop']['enable'] = 1 if self.els_stop_engaged else 0
                    log.info(
                        f"Standalone elsStop: stopPosition={stop_encoder}, "
                        f"enable={self.els_stop_engaged}"
                    )
            except ValueError:
                log.warning(f"Invalid stop Z value: {value}")

        keypad.show_with_callback(
            callback_fn=on_done,
            current_value=0.0,
        )

    def unbind_all_display_value(self):
        if hasattr(self, "_bound_scale") and self._bound_scale is not None:
            inp = self._bound_scale._primary_input()
            if inp is not None:
                inp.unbind(encoderCurrent=self._on_encoder_update)
            self._bound_scale.unbind(formattedPosition=self._on_format_update)
            self._bound_scale = None
        if hasattr(self, "_bound_servo") and self._bound_servo is not None:
            self._bound_servo.unbind(formattedPosition=self._on_servo_position_update)
            self._bound_servo = None
        if hasattr(self, "machine") and self.machine is not None:
            self.machine.unbind_progress_display()

    def update_buttons_state(self):
        if self.action_button_condition_fn:
            self.action_button_enabled = self.action_button_condition_fn()
        else:
            self.action_button_enabled = True

        if self.retract_button_condition_fn:
            self.retract_button_enabled = self.retract_button_condition_fn()
        else:
            self.retract_button_enabled = True
