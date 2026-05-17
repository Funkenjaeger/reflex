from kivy.logger import Logger
from kivy.uix.popup import Popup
from kivy.properties import ObjectProperty, NumericProperty

from rcp.components.home.thread_type import ThreadType
from rcp.utils.kv_loader import load_kv

log = Logger.getChild(__name__)

load_kv(__file__)


class ElsSettingsPopup(Popup):
    bar = ObjectProperty(None)
    # Backlash takeup magnitude as displayed/entered by the user (always mm).
    # The dispatcher persists the value in steps; this property converts both
    # ways using the servo ratio (mm-per-step).
    backlash_mm = NumericProperty(0.0)

    def __init__(self, **kv):
        super().__init__(**kv)
        from rcp.app import MainApp
        self.app = MainApp.get_running_app()
        self.backlash_mm = self._steps_to_mm(self.app.els.els_backlash_steps)

    def _servo_mm_per_step(self) -> float:
        servo = self.app.servo
        if servo.ratioDen == 0:
            return 0.0
        return float(servo.ratioNum) / float(servo.ratioDen)

    def _steps_to_mm(self, steps: int) -> float:
        mm_per_step = self._servo_mm_per_step()
        if mm_per_step <= 0.0:
            return 0.0
        return abs(int(steps)) * mm_per_step

    def _mm_to_steps(self, mm: float) -> int:
        mm_per_step = self._servo_mm_per_step()
        if mm_per_step <= 0.0 or mm <= 0.0:
            return 0
        return int(round(mm / mm_per_step))

    def on_backlash_mm(self, _instance, value):
        if value < 0:
            self.backlash_mm = 0.0
            return
        steps = self._mm_to_steps(value)
        if steps != int(self.app.els.els_backlash_steps):
            self.app.els.els_backlash_steps = steps
            log.info(f"Backlash takeup: {value} mm → {steps} steps")

    def get_pitches(self):
        if not self.bar:
            return []
        return [f.name for f in self.bar.current_feeds_table]

    def get_thread_types(self):
        """Get available thread types based on metric mode."""
        if self.bar.metric_mode:
            return [ThreadType.ISO_METRIC.value, ThreadType.ACME.value]
        else:
            return [ThreadType.UNIFIED.value, ThreadType.WHITWORTH.value, ThreadType.ACME.value]

    def on_metric_mode_changed(self, value):
        self.bar.metric_mode = value
        pitches_dropdown = self.ids.pitches_dropdown
        pitches = self.get_pitches()
        pitches_dropdown.options = pitches
        first_pitch = pitches[0] if pitches else ""
        pitches_dropdown.value = first_pitch
        self.on_pitch_selected(0, first_pitch)

        # Update thread type options based on metric mode
        thread_type_dropdown = self.ids.thread_type_dropdown
        thread_type_dropdown.options = self.get_thread_types()
        # Reset to first available type
        first_type = self.get_thread_types()[0] if self.get_thread_types() else ThreadType.ISO_METRIC.value
        thread_type_dropdown.value = first_type
        self.bar.thread_profile_type = ThreadType(first_type).value

        log.info(f"Metric mode changed to: {value}")

    def on_pitch_selected(self, index, selected_pitch):
        self.bar.selected_pitch = selected_pitch
        self.bar.current_feeds_index = index
        self.bar.update_feeds_ratio(None, None)
        log.info(f"Selected pitch: {selected_pitch}")

    def on_thread_type_selected(self, value):
        """Handle thread type selection."""
        try:
            thread_type = ThreadType(value)
            self.bar.thread_profile_type = thread_type.value
            log.info(f"Selected thread type: {thread_type}")
        except ValueError:
            log.warning(f"Invalid thread type value: {value}")
