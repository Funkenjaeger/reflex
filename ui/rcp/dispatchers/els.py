from kivy.logger import Logger
from kivy.properties import BooleanProperty, NumericProperty

from rcp.dispatchers.saving_dispatcher import SavingDispatcher

log = Logger.getChild(__name__)


class ElsDispatcher(SavingDispatcher):
    """Persists ELS axis role assignments and ELS-stop tuning."""

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

    # ── ELS stop tuning ───────────────────────────────────────────────
    # Magnitude in servo steps; 0 disables takeup. Direction is derived by
    # firmware from stopDirection × sign(threadPitchSteps × zCountsPerPitch).
    # User-facing entry happens in mm via the settings popup, which converts
    # using the servo ratio.
    els_backlash_steps = NumericProperty(0)

    # ── Machine direction polarity ────────────────────────────────────
    # Three INDEPENDENT polarity knobs. They have to be independent because
    # they capture three different physical wiring relationships:
    #
    #   cut_polarity_inverted     → flips spindle.syncRatioNum sign.
    #       Captures: operator's "forward" rotation × servo step polarity
    #       × leadscrew lead direction. Determines which way the carriage
    #       moves during the cut.
    #
    #   stop_polarity_inverted    → flips elsStop.stopDirection sign.
    #       Captures: operator's "forward" carriage direction × Z-scale
    #       wiring sign. Determines which side of stopPosition triggers
    #       the stop, and (because firmware derives backlash takeup
    #       direction from stopDirection × sign(threadPitchSteps ×
    #       zCountsPerPitch)) which way takeup moves.
    #
    #   z_scale_step_inverted     → flips the sign that converts Z-scale
    #       count deltas to leadscrew step deltas during non-sync moves
    #       (retract). Captures: servo step polarity × Z-scale wiring sign
    #       (independent of spindle).
    #
    # Default baseline (all False) matches the original hardcoded logic:
    # forward → +syncRatio, -stopDirection, scale-to-step inverted (-1).
    # Flip whichever one(s) at first commissioning so all three motions
    # (cut, stop trigger, retract) go the right way for this machine.
    cut_polarity_inverted  = BooleanProperty(False)
    stop_polarity_inverted = BooleanProperty(False)
    z_scale_step_inverted  = BooleanProperty(False)

    def direction_sign(self, els_forward: bool) -> int:
        """Sign applied to spindle syncRatioNum to drive the carriage in
        the operator's "forward" direction. ±1.
        """
        base = 1 if els_forward else -1
        return -base if self.cut_polarity_inverted else base

    def stop_direction_value(self, els_forward: bool) -> int:
        """Integer value for elsStop.stopDirection register. ±1.
        Independent of direction_sign — different physical wiring.
        """
        base = -1 if els_forward else 1
        return -base if self.stop_polarity_inverted else base

    def scale_to_step_sign(self) -> int:
        """Sign that maps a Z-scale count delta to a servo step delta for
        non-sync moves (retract). ±1. Independent of operator's forward
        toggle — pure mechanical wiring.
        """
        # Default baseline matches the prior hardcoded `step_delta = -1 *
        # step_delta` flip; toggling z_scale_step_inverted makes it +1.
        return 1 if self.z_scale_step_inverted else -1

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