from kivy.logger import Logger
from kivy.properties import BooleanProperty, NumericProperty

from rcp.dispatchers.saving_dispatcher import SavingDispatcher

log = Logger.getChild(__name__)


class ElsDispatcher(SavingDispatcher):
    """Persists ELS axis role assignments and Assisted Threading machine settings."""

    _save_class_name = "Els"
    _skip_save = ["x", "y", "width", "height", "size_hint_x", "size_hint_y",
                  "pos", "size", "minimum_height", "minimum_width", "padding", "spacing",
                  "spindle_is_running"]

    # ── ELS axis roles ────────────────────────────────────────────────
    spindle_axis_index = NumericProperty(-1)
    z_axis_index = NumericProperty(-1)
    x_axis_index = NumericProperty(-1)

    # ── Reactive spindle state (updated each tick) ────────────────────
    spindle_is_running = BooleanProperty(False)

    # ── Assisted Threading: thread geometry ───────────────────────────
    at_cross_slide_diameter_mode = BooleanProperty(False)

    # ── Assisted Threading: speed & acceleration ──────────────────────
    at_reversing_speed = NumericProperty(500)
    at_preload_adjust_speed = NumericProperty(500)
    at_threading_max_speed = NumericProperty(2000)
    at_reversing_adjusting_acceleration = NumericProperty(1000)
    at_threading_acceleration = NumericProperty(1000)

    # ── Assisted Threading: tolerances & backlash ─────────────────────
    at_rotary_encoder_sync_tolerance = NumericProperty(5)
    at_saddle_encoder_stability_tolerance = NumericProperty(1)
    at_saddle_encoder_stability_samples = NumericProperty(3)
    at_metric_distances = BooleanProperty(True)
    at_saddle_backlash_distance = NumericProperty(10)
    at_backlash_cushion = NumericProperty(2)

    def __init__(self, **kwargs):
        from rcp.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kwargs)
        self.bind(spindle_axis_index=self._apply_spindle_mode)
        # Apply on startup in case a saved value exists
        self._apply_spindle_mode()
        self.app.board.bind(update_tick=self._update_spindle_running)
        self.app.servo.bind(servoMode=self._sync_spindle_to_servo)

    def _update_spindle_running(self, *args):
        running = self.get_spindle_is_running()
        if running != self.spindle_is_running:
            self.spindle_is_running = running

    def _apply_spindle_mode(self, *args):
        """Set spindleMode=True on the selected spindle axis, False on all others."""
        idx = int(self.spindle_axis_index)
        if idx < 0:
            return
        for i, axis in enumerate(self.app.axes):
            axis.spindleMode = (i == idx)

    def get_spindle_axis(self):
        idx = int(self.spindle_axis_index)
        if 0 <= idx < len(self.app.axes):
            return self.app.axes[idx]
        return None

    def get_z_axis(self):
        idx = int(self.z_axis_index)
        if 0 <= idx < len(self.app.axes):
            return self.app.axes[idx]
        return None

    def get_x_axis(self):
        idx = int(self.x_axis_index)
        if 0 <= idx < len(self.app.axes):
            return self.app.axes[idx]
        return None

    def _sync_spindle_to_servo(self, instance, value):
        """Enable/disable spindle syncEnable when ELS servo mode changes."""
        if not self.app.board.connected:
            return
        spindle_axis = self.get_spindle_axis()
        if spindle_axis is None:
            return
        inp = spindle_axis._primary_input()
        if inp is None:
            return
        enable = 1 if value != 0 else 0
        self.app.board.device['scales'][inp.inputIndex]['syncEnable'] = enable
        spindle_axis.syncEnable = bool(enable)
        log.info(f"Spindle syncEnable = {enable} (servoMode={value})")

    def get_spindle_is_running(self, *args):
        speed = self.get_spindle_speed()
        return abs(speed) > 0.5
    
    def get_spindle_speed(self, *args):
        axis = self.get_spindle_axis()
        return axis.speed if axis is not None else 0.0