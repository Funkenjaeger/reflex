from kivy.logger import Logger
from kivy.properties import NumericProperty, BooleanProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout

from rcp import feeds
from rcp.components.home.thread_type import ThreadType
from rcp.components.popups.custom_popup import CustomPopup
from rcp.dispatchers.saving_dispatcher import SavingDispatcher
from rcp.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class ElsAdvancedBar(BoxLayout, SavingDispatcher):
    """Unified ELS advanced bar — hosts the Els UI controller and supports all
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
    inner_thread = BooleanProperty(False)

    # ── Transient UI state ───────────────────────────────────────────────────
    is_active = BooleanProperty(True)
    is_running = BooleanProperty(False)
    label_text = StringProperty("")
    display_value = StringProperty("")
    next_button_text = StringProperty("")
    start_position = NumericProperty(0)
    stop_position = NumericProperty(0)
    material_width = NumericProperty(0)
    cutting_depth = NumericProperty(0)
    last_cutting_depth = NumericProperty(0)

    # ── State-machine mirror + per-button display text ───────────────────────
    current_state = StringProperty("idle")
    start_z_text = StringProperty("")
    stop_z_text = StringProperty("")
    major_diameter_text = StringProperty("")
    minor_diameter_text = StringProperty("")

    _skip_save = [
        "is_active",
        "is_running",
        "label_text",
        "display_value",
        "next_button_text",
        "start_position",
        "stop_position",
        "material_width",
        "cutting_depth",
        "last_cutting_depth",
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

    def __init__(self, els_bar=None, **kwargs):
        from rcp.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        self.els_bar = els_bar
        self.controller = self.app.els_uic
        super().__init__(**kwargs)

        self.current_feeds_table = (
            feeds.table["Thread MM"] if self.metric_mode else feeds.table["Thread IN"]
        )
        if not self.thread_profile_type:
            self.thread_profile_type = ThreadType.ISO_METRIC.value

        # Mirror persisted widget mode flags into the controller so the
        # FSM can read them via conditions / on_enter callbacks.
        self.controller.wizard_enabled = self.enable_wizard
        self.controller.retract_enabled = self.enable_retract
        self.bind(enable_wizard=self._sync_wizard_to_controller,
                  enable_retract=self._sync_retract_to_controller)

        # ElsBar is the single writer of spindle.syncRatioNum/Den; it has
        # already self-initialized the spindle from its persisted state.
        # We deliberately do NOT call self.update_feeds_ratio() here — that
        # would race with ElsBar at startup and clobber the visible bar's
        # selection with whatever this widget last persisted.
        if self.els_bar is not None:
            self.controller.els_forward = self.els_bar.els_forward
            self.els_bar.bind(els_forward=self._on_els_forward_changed)
            # Mirror thread/feed mode and inner/outer direction so the controller
            # can apply mode-specific safety gates (e.g. block Z-retract when
            # threading and X is still at depth).
            self._sync_is_threading()
            self.els_bar.bind(mode_name=lambda *_: self._sync_is_threading())
        self.controller.is_inner = self.inner_thread
        self.bind(inner_thread=lambda *_: setattr(self.controller, "is_inner", self.inner_thread))

    # ── Mode-flag mirroring (widget persistence → controller) ────────────────

    def _sync_wizard_to_controller(self, instance, value):
        self.controller.wizard_enabled = value

    def _sync_retract_to_controller(self, instance, value):
        self.controller.retract_enabled = value

    def _on_els_forward_changed(self, instance, value):
        self.controller.els_forward = value
        self.update_feeds_ratio(instance, value)

    def _sync_is_threading(self):
        # ElsBar.mode_name uses the "Thread" prefix for threading feed tables
        # (e.g. "Thread MM", "Thread IN"); feed tables don't contain it.
        self.controller.is_threading = "Thread" in (self.els_bar.mode_name or "")

    # ── Engage / disengage (delegates to controller) ─────────────────────────

    def toggle_engage(self):
        self.controller.toggle_engage()

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

    # ── Settings ─────────────────────────────────────────────────────────────

    def on_metric_mode(self, instance, value):
        self.current_feeds_table = (
            feeds.table["Thread MM"] if value else feeds.table["Thread IN"]
        )

    def update_feeds_ratio(self, instance, value):
        # ElsAdvancedBar does NOT write the spindle syncRatio itself. The
        # popup-driven pitch selection (or anything else that calls this)
        # is propagated to ElsBar, which owns the single canonical
        # spindle-write path.
        if not self.is_active:
            return
        if self.els_bar is None:
            return
        mode_name = "Thread MM" if self.metric_mode else "Thread IN"
        idx = int(self.current_feeds_index)
        # Sync ElsBar's selection. set_feed_ratio() doesn't itself trigger
        # ElsBar.update_feeds_ratio (Kivy property bindings don't fire when
        # the value is unchanged), so call it explicitly to guarantee a
        # spindle write reflecting the new pitch.
        self.els_bar.set_feed_ratio(mode_name, idx)
        self.els_bar.update_feeds_ratio(self.els_bar, None)
        log.info(
            f"ElsAdvancedBar pitch change → ElsBar({mode_name}[{idx}])"
        )

    def open_settings(self):
        from rcp.components.home.els_settings_popup import ElsSettingsPopup
        popup = ElsSettingsPopup(bar=self)
        popup.open()

    # ── Display binding ──────────────────────────────────────────────────────

    def bind_display_value_to_scale(self, axis, target_prop: str = "display_value"):
        """Bind `target_prop` to an AxisDispatcher's formattedPosition.

        `target_prop` lets each state target one of the per-button text
        properties (`start_z_text`, `stop_z_text`, `major_diameter_text`)
        instead of the shared `display_value`.
        """
        self.unbind_all_display_value()
        self._bound_scale = axis
        inp = axis._primary_input() if axis is not None else None

        def on_encoder_update(*_):
            setattr(self, target_prop, axis.formattedPosition)

        def on_format_update(instance, value):
            setattr(self, target_prop, value)

        self._on_encoder_update = on_encoder_update
        self._on_format_update = on_format_update

        if inp is not None:
            inp.bind(encoderCurrent=on_encoder_update)
        axis.bind(formattedPosition=on_format_update)

        setattr(self, target_prop, axis.formattedPosition)

    def on_value_button_released(self, which: str):
        """Dispatcher wired to each TextHeaderButton's on_release in the kv.

        Wizard-state-aware fields (start_z, minor_dia) are routed by
        controller.ui_state in a future iteration. For now, only stop_z
        has a concrete handler — the standalone keypad path.
        """
        if which == "stop_z":
            self._open_standalone_stop_z_keypad()
        elif which == "major_dia":
            self._open_standalone_diameter_keypad("major")
        elif which == "minor_dia":
            self._open_standalone_diameter_keypad("minor")

    def _open_standalone_stop_z_keypad(self):
        """Open keypad for stop Z entry outside the wizard state machine.

        Conversion + register writes are delegated to the controller; the
        widget only handles the keypad UX.
        """
        from rcp.components.popups.keypad import Keypad
        if self.app.els.get_z_axis() is None:
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
                self.controller.commit_standalone_stop_z(float(value))
            except ValueError:
                log.warning(f"Invalid stop Z value: {value}")

        keypad.show_with_callback(
            callback_fn=on_done,
            current_value=0.0,
        )

    def _open_standalone_diameter_keypad(self, which: str):
        """Open keypad for manual major/minor diameter entry.

        Bypasses the wizard's "move to position and press Set" flow. Writes
        directly to controller.start_dia / controller.stop_dia; validation
        bindings run automatically.
        """
        from rcp.components.popups.keypad import Keypad
        if self.app.els.get_x_axis() is None:
            from rcp.components.popups.custom_popup import CustomPopup
            CustomPopup(
                title="Axis Not Configured",
                message="Cross-slide (X) axis is not set in ELS settings.",
                button_text="OK",
            ).open()
            return

        is_metric = self.app.formats.current_format == "MM"
        unit_label = "mm" if is_metric else "in"
        target_attr = "start_dia" if which == "major" else "stop_dia"
        title_label = "Major ø" if which == "major" else "Minor ø"
        keypad = Keypad(title=f"Enter {title_label} ({unit_label})")
        keypad.integer = False

        def on_done(value):
            try:
                setattr(self.controller, target_attr, float(value))
            except ValueError:
                log.warning(f"Invalid {target_attr} value: {value}")

        keypad.show_with_callback(
            callback_fn=on_done,
            current_value=getattr(self.controller, target_attr),
        )

    def unbind_all_display_value(self):
        if hasattr(self, "_bound_scale") and self._bound_scale is not None:
            inp = self._bound_scale._primary_input()
            if inp is not None:
                inp.unbind(encoderCurrent=self._on_encoder_update)
            self._bound_scale.unbind(formattedPosition=self._on_format_update)
            self._bound_scale = None
