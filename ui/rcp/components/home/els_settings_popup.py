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

    def get_thread_types(self):
        """Get available thread types based on global format setting."""
        if self.bar.app.formats.current_format == "MM":
            return [ThreadType.ISO_METRIC.value, ThreadType.ACME.value]
        else:
            return [ThreadType.UNIFIED.value, ThreadType.WHITWORTH.value, ThreadType.ACME.value]

    def on_thread_type_selected(self, value):
        """Handle thread type selection."""
        try:
            thread_type = ThreadType(value)
            self.bar.thread_profile_type = thread_type.value
            log.info(f"Selected thread type: {thread_type}")
        except ValueError:
            log.warning(f"Invalid thread type value: {value}")
