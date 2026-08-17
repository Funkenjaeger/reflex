from kivy.logger import Logger
from kivy.properties import (
    NumericProperty,
    BooleanProperty,
    StringProperty,
    AliasProperty,
)
from kivy.uix.boxlayout import BoxLayout

from reflex.components.home.thread_type import ThreadType
from reflex.components.popups.custom_popup import CustomPopup
from reflex.dispatchers.saving_dispatcher import SavingDispatcher
from reflex.utils.kv_loader import load_kv

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

    # Height this bar WANTS, i.e. its base plus whichever collapsible notice
    # strips are currently showing. Computed in the kv rule.
    #
    # It is a separate property from `height` because the two have different
    # owners and conflating them broke the bar on 2026-08-16. ElsModeLayout owns
    # `height`, because it is what decides whether the advanced bar is shown at
    # all — and in Kivy, assigning to a property REPLACES any kv binding on it.
    # So the moment the layout wrote `height`, the kv expression stopped driving
    # it, and the bar was frozen at whatever it measured at construction time:
    # base height, with no strip showing. A strip appearing then made the
    # children taller than the parent and the warning rendered outside the bar,
    # up over the DRO rows.
    #
    # With the two split, kv owns what the bar needs and the layout owns whether
    # it gets it, and neither silently overwrites the other.
    natural_height = NumericProperty(128)

    # ── One-hot tri-state operating mode (derived from the flags above) ───────
    # The single mode button in the advanced bar cycles through these three:
    #   "wizard"        -> guided multi-step cut (enable_wizard)
    #   "stop_retract"  -> stop target + auto-retract
    #   "stop"          -> stop target only
    def _get_mode(self):
        if self.enable_wizard:
            return "wizard"
        if self.enable_retract:
            return "stop_retract"
        return "stop"

    mode = AliasProperty(_get_mode, None, bind=["enable_wizard", "enable_retract"])

    def apply_mode(self, value):
        """Set the operating mode from the one-hot mode tab's selected value."""
        if value == "wizard":
            self.enable_wizard = True
            self.enable_retract = True
        elif value == "stop_retract":
            self.enable_wizard = False
            self.enable_retract = True
        else:  # "stop"
            self.enable_wizard = False
            self.enable_retract = False

    # ── Per-job thread settings (persisted) ──────────────────────────────────
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
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        self.els_bar = els_bar
        self.controller = self.app.els_uic
        super().__init__(**kwargs)

        if not self.thread_profile_type:
            self.thread_profile_type = ThreadType.ISO_METRIC.value

        # Mirror persisted widget mode flags into the controller so the
        # FSM can read them via conditions / on_enter callbacks.
        self.controller.wizard_enabled = self.enable_wizard
        self.controller.retract_enabled = self.enable_retract
        self.bind(enable_wizard=self._sync_wizard_to_controller,
                  enable_retract=self._sync_retract_to_controller)

        # Mirror in_cycle flag from controller so engage/disengage and mode
        # toggles are disabled while the FSM is inside the cut/retract cycle.
        self.is_running = self.controller.in_cycle
        self.controller.bind(in_cycle=lambda _, v: setattr(self, "is_running", v))

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

    def _sync_is_threading(self):
        # Classify via the feeds table's structured mode field (see
        # feeds.is_threading_table). is_threading gates SAFETY behavior (thread
        # geometry push, the X-clear-of-start-dia retract gate), so it must not
        # hang off a display string — the old `"Thread" in mode_name` check
        # silently flipped ELS into feed mode if a table was ever renamed.
        from reflex import feeds
        self.controller.is_threading = feeds.is_threading_table(
            self.els_bar.mode_name or "")

    # ── Engage / disengage (delegates to controller) ─────────────────────────

    def toggle_engage(self):
        self.controller.toggle_engage()

    # ── Settings ─────────────────────────────────────────────────────────────

    def open_settings(self):
        from reflex.components.home.els_settings_popup import ElsSettingsPopup
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
        """Dispatcher wired to each TextHeaderButton's on_short_press in the kv.

        Each field routes to the keypad popup matching its semantics
        (stop_z / retract_z target a Z axis; diameters target the X axis).
        """
        if which == "stop_z":
            self._open_standalone_stop_z_keypad()
        elif which == "start_z":
            self._open_standalone_start_z_keypad()
        elif which == "major_dia":
            self._open_standalone_diameter_keypad("major")
        elif which == "minor_dia":
            self._open_standalone_diameter_keypad("minor")

    def on_value_long_press(self, which: str):
        """Long-press on a bar button captures the live axis position
        directly, skipping the keypad. Used for quick "set this to where
        the carriage is right now" moves in non-wizard modes.
        """
        if which in ("stop_z", "start_z"):
            axis = self.app.els.get_z_axis()
            axis_label = "Saddle (Z)"
        else:
            axis = self.app.els.get_x_axis()
            axis_label = "Cross-slide (X)"
        if axis is None:
            # Same guard as the keypad-entry paths — and the same FEEDBACK.
            # Silently returning here left a long-press indistinguishable from
            # a dead button when the axis wasn't mapped in ELS settings.
            from reflex.components.popups.custom_popup import CustomPopup
            CustomPopup(
                title="Axis Not Configured",
                message=f"{axis_label} axis is not set in ELS settings.",
                button_text="OK",
            ).open()
            return
        position = float(axis.scaledPosition)
        if which == "stop_z":
            self.controller.commit_standalone_stop_z(position)
        elif which == "start_z":
            self.controller.commit_standalone_retract_z(position)
        elif which == "major_dia":
            self.controller.commit_standalone_start_dia(position)
        elif which == "minor_dia":
            self.controller.commit_standalone_stop_dia(position)
        self.controller.try_advance_wizard()

    def _open_standalone_stop_z_keypad(self):
        """Open keypad for stop Z entry outside the wizard state machine.

        Conversion + register writes are delegated to the controller; the
        widget only handles the keypad UX.
        """
        from reflex.components.popups.keypad import Keypad
        z_axis = self.app.els.get_z_axis()
        if z_axis is None:
            from reflex.components.popups.custom_popup import CustomPopup
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
                return
            # If invoked from within the matching wizard step, advance the FSM
            # so the operator doesn't have to also press Set (which would
            # capture the live axis position and clobber what they just typed).
            self.controller.try_advance_wizard()

        def use_current():
            self.controller.commit_standalone_stop_z(float(z_axis.scaledPosition))
            self.controller.try_advance_wizard()

        keypad.show_with_callback(
            callback_fn=on_done,
            current_value=self.controller.stop_z,
            use_current_fn=use_current,
        )

    def _open_standalone_start_z_keypad(self):
        """Open keypad for Start Z (retract target) entry.

        Mirrors the stop-Z keypad: routes through commit_standalone_retract_z
        so the controller stays the chokepoint for value writes.
        """
        from reflex.components.popups.keypad import Keypad
        z_axis = self.app.els.get_z_axis()
        if z_axis is None:
            from reflex.components.popups.custom_popup import CustomPopup
            CustomPopup(
                title="Axis Not Configured",
                message="Saddle (Z) axis is not set in ELS settings.",
                button_text="OK",
            ).open()
            return

        is_metric = self.app.formats.current_format == "MM"
        keypad = Keypad(
            title="Enter Start Z Position (" + ("mm" if is_metric else "in") + ")"
        )
        keypad.integer = False

        def on_done(value):
            try:
                self.controller.commit_standalone_retract_z(float(value))
            except ValueError:
                log.warning(f"Invalid start Z value: {value}")
                return
            self.controller.try_advance_wizard()

        def use_current():
            self.controller.commit_standalone_retract_z(float(z_axis.scaledPosition))
            self.controller.try_advance_wizard()

        keypad.show_with_callback(
            callback_fn=on_done,
            current_value=self.controller.retract_z,
            use_current_fn=use_current,
        )

    def _open_standalone_diameter_keypad(self, which: str):
        """Open keypad for manual major/minor diameter entry.

        Bypasses the wizard's "move to position and press Set" flow. Writes
        directly to controller.start_dia / controller.stop_dia; validation
        bindings run automatically.
        """
        from reflex.components.popups.keypad import Keypad
        if self.app.els.get_x_axis() is None:
            from reflex.components.popups.custom_popup import CustomPopup
            CustomPopup(
                title="Axis Not Configured",
                message="Cross-slide (X) axis is not set in ELS settings.",
                button_text="OK",
            ).open()
            return

        is_metric = self.app.formats.current_format == "MM"
        unit_label = "mm" if is_metric else "in"
        target_attr = "start_dia" if which == "major" else "stop_dia"
        commit = (self.controller.commit_standalone_start_dia if which == "major"
                  else self.controller.commit_standalone_stop_dia)
        title_label = "Major ø" if which == "major" else "Minor ø"
        keypad = Keypad(title=f"Enter {title_label} ({unit_label})")
        keypad.integer = False

        def on_done(value):
            try:
                commit(float(value))
            except ValueError:
                log.warning(f"Invalid {target_attr} value: {value}")
                return
            # If invoked from within the matching wizard step, advance the FSM
            # so the operator doesn't have to also press Set (which would
            # capture the live axis position and clobber what they just typed).
            self.controller.try_advance_wizard()

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
