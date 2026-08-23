from kivy.logger import Logger
from kivy.uix.popup import Popup
from kivy.properties import ObjectProperty, NumericProperty

from reflex.components.home.thread_type import ThreadType
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)

load_kv(__file__)


class ElsSettingsPopup(Popup):
    bar = ObjectProperty(None)
    # Backlash takeup magnitude as displayed/entered by the user (always mm).
    # The dispatcher persists the value in steps; this property converts both
    # ways using the servo ratio (mm-per-step).
    backlash_mm = NumericProperty(0.0)

    # Calibration limits. Both are MACHINE-SPECIFIC with no meaningful default,
    # and the take-up gate fails CLOSED until they are set — so they must be
    # settable from the touchscreen. There is no register browser at the lathe.
    #
    # The ceiling is a distance, so it is entered in mm and converted through the
    # servo ratio like the take-up above. The motion threshold is NOT converted:
    # it is a scale-resolution quantity where 1 count is the meaningful
    # granularity, and rounding it through mm would quantize away the only values
    # that matter (1-3 counts).
    cal_ceiling_mm = NumericProperty(0.0)
    cal_motion_thresh_counts = NumericProperty(0)

    def __init__(self, **kv):
        super().__init__(**kv)
        from reflex.app import MainApp
        self.app = MainApp.get_running_app()
        self.backlash_mm = self._steps_to_mm(self.app.els.els_backlash_steps)
        self.cal_ceiling_mm = self._steps_to_mm(self.app.els.els_cal_ceiling_steps)
        self.cal_motion_thresh_counts = int(self.app.els.els_cal_motion_thresh_counts)

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

    def on_cal_ceiling_mm(self, _instance, value):
        if value < 0:
            self.cal_ceiling_mm = 0.0
            return
        steps = self._mm_to_steps(value)
        if steps != int(self.app.els.els_cal_ceiling_steps):
            self.app.els.els_cal_ceiling_steps = steps
            self._push_cal_limits()
            log.info(f"Calibration ceiling: {value} mm -> {steps} steps")

    def on_cal_motion_thresh_counts(self, _instance, value):
        counts = max(0, int(value))
        if counts != int(value):
            self.cal_motion_thresh_counts = counts
            return
        if counts != int(self.app.els.els_cal_motion_thresh_counts):
            self.app.els.els_cal_motion_thresh_counts = counts
            self._push_cal_limits()
            log.info(f"Calibration motion threshold: {counts} Z counts")

    def _push_cal_limits(self):
        """Send the limits to firmware as soon as they change.

        The take-up gate reads calMotionThreshCounts on EVERY pass and fails
        closed at 0, so a value that only reached firmware on the next connect
        or the next calibration run would leave the machine refusing take-ups
        with the setting visibly "set" on screen. Also pushed on connect (see
        ElsStopFsm.reconcile_firmware_on_connect) because firmware does not
        retain these across a power cycle.
        """
        self.app.els_uic.hal.set_cal_limits(
            int(self.app.els.els_cal_ceiling_steps),
            int(self.app.els.els_cal_motion_thresh_counts),
        )

    def open_backlash_calibration(self):
        """Open the closed-loop backlash calibration wizard.

        Imported lazily, matching how els_advbar opens this popup — keeps the
        component import graph acyclic.
        """
        from reflex.components.home.els_backlash_cal_popup import BacklashCalPopup
        BacklashCalPopup(bar=self).open()

    def open_thread_resync(self):
        """Open the pick-up-existing-thread (manual reference latch) wizard.

        Lazily imported like the calibration popup. The wizard itself refuses
        with a clear message when no job is armed, so this button does not need
        its own gating.
        """
        from reflex.components.home.els_resync_popup import ThreadResyncPopup
        ThreadResyncPopup().open()

    def open_phase_offset(self):
        """Open the thread-phase offset (groove-widening) entry surface.

        Lazily imported like the wizards above. It needs no gating here either:
        the FSM refuses an offset with a stated reason when there is no job, no
        pitch or no connection, and a button greyed out on this screen could
        not say which of those it was.
        """
        from reflex.components.home.els_phase_offset_popup import PhaseOffsetPopup
        PhaseOffsetPopup().open()

    def refresh_backlash(self):
        """Re-read the stored take-up after a calibration commits.

        The wizard writes els_backlash_steps (the COMMANDED take-up: measured +
        margin), so this pulls that back into the mm field and the operator sees
        the number that will actually be driven rather than a stale one.
        """
        self.backlash_mm = self._steps_to_mm(self.app.els.els_backlash_steps)

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
