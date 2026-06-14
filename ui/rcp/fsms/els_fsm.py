from kivy.logger import Logger
from transitions import Machine

from rcp.dispatchers import els, board
from rcp.fsms.els_stop_hal import ElsStopHal
from rcp.fsms.fsm_event_bus import fsm_event_bus as bus

import math
from fractions import Fraction

log = Logger.getChild(__name__)


class ElsFsm:

    # TODO: Consider putting STATES and TRANSITIONS in their own module
    STATES = ['disabled',
              'stopped', 
              'retracting',
              'cutting',
              'alarm'
    ]

    # Note - Fields: trigger, source, dest, conditions, unless, before, after, prepare
    TRANSITIONS = [
        {'trigger': 'enable', 'source': 'disabled', 'dest': 'stopped',
         'prepare': '_on_prepare_enable'},
        {'trigger': 'retract', 'source': 'stopped', 'dest': 'retracting', 'conditions': ['is_ready_to_retract']},
        {'trigger': 'retract_done', 'source': 'retracting', 'dest': 'stopped', 'conditions': ['is_retracted']},
        {'trigger': 'retract_done', 'source': 'retracting', 'dest': '='},
        {'trigger': 'cut', 'source': 'stopped', 'dest': 'cutting', 'conditions': ['is_ready_to_cut']}, 
        {'trigger': 'stop_active', 'source': 'cutting', 'dest': 'stopped'},
        {'trigger': 'disable', 'source': ['stopped', 'retracting'], 'dest': 'disabled'},
        {'trigger': 'fault', 'source': '*', 'dest': 'alarm'},       
    ]

    def __init__(self, els: els, board: board, hal: ElsStopHal, controller):
        self.els = els
        self.board = board
        self.servo = board.servo
        self.hal = hal
        self.controller = controller
        self.z_axis = self.els.get_z_axis()
        self.x_axis = self.els.get_x_axis()

        self.fsm = Machine(
            model=self,
            states=self.STATES,
            transitions=self.TRANSITIONS,
            initial="disabled",
            after_state_change="_broadcast",
            queued=True,
        )
        
        self.safe_x = 0 # TODO: move to controller
        self.check_x_retract = False # TODO: move to controller
        self.inside = False # TODO: move to controller

        # Whether the leadscrew nut is already at the retract-side wall.
        # The firmware does a backlash takeup in the cutting direction at
        # cut start, leaving the nut against the cut-side wall. The first
        # retract after that has to traverse the full play window before
        # the carriage starts moving — see on_enter_retracting for the
        # compensation. Subsequent same-direction retract self-loops do
        # not need the compensation.
        self._retract_backlash_applied = False

        # True when entering stopped from disabled (enable trigger). Lets
        # on_enter_stopped know to arm ELS with a fresh stopPosition.
        self._engaging = False


    # ——— transition side effects ———

    def _on_prepare_enable(self):
        """Mark that we're entering stopped from disabled so on_enter_stopped
        can arm ELS with a fresh stopPosition."""
        self._engaging = True

    def on_enter_retracting(self):
        enc_current = self._saddle_input.encoderCurrent
        enc_target = self.z_axis.position_to_encoder(self.controller.retract_z)
        enc_delta = enc_target - enc_current
        step_delta = self._scale_counts_to_steps(enc_delta)

        # On the FIRST retract after a cut, the leadscrew nut is at the
        # cut-side wall (the firmware's pre-cut takeup pinned it there).
        # The first `backlash_steps` of retract motion just walk the nut
        # across the play window without moving the carriage, so add
        # them to the commanded move. Self-loop corrections within the
        # same retracting episode don't reverse direction, so the flag
        # stays True until the next cut (or disable) resets it.
        backlash_added = 0
        if step_delta != 0 and not self._retract_backlash_applied:
            backlash_steps = int(self.els.els_backlash_steps or 0)
            if backlash_steps:
                sign = 1 if step_delta > 0 else -1
                backlash_added = sign * backlash_steps
                step_delta += backlash_added
            self._retract_backlash_applied = True

        log.info(
            f"retract: z_pos={self.z_axis.scaledPosition} "
            f"retract_z={self.controller.retract_z} "
            f"enc_d={enc_delta} step_d={step_delta} backlash_added={backlash_added}"
        )
        self.set_scale_index()
        self.hal.set_steps_to_go(step_delta)
        self.board.bind(update_tick=self._on_board_update)

    def on_enter_cutting(self):
        # Starting a new cut reverses direction, so the next retract will
        # again need to traverse the play window.
        self._retract_backlash_applied = False
        self.set_stop_z(self.controller.stop_z)
        self.hal.set_scale_index(self._saddle_input.inputIndex)
        self.push_thread_geometry() # TODO: only if threading!
        self.hal.set_backlash_steps(int(self.els.els_backlash_steps))
        self.hal.set_active(False)
        self.hal.set_stop_direction(
            self.els.stop_direction_value(self.controller.els_forward)
        )
        if self.controller.retract_enabled or self.controller.wizard_enabled:
            self.hal.set_hysteresis_tight()
        else:
            self.hal.set_hysteresis_loose()
        self.hal.set_enable(True)
        log.info(
            f"on_enter_cutting: els_forward={self.controller.els_forward} "
            f"backlash_steps={int(self.els.els_backlash_steps)}"
        )
        self.board.bind(update_tick=self._on_board_update)

    def on_enter_stopped(self):
        # Engaged but idle: configure direction + hysteresis from current
        # operator-mode flags. When entering from disabled (operator clicked
        # "Engage"), arm ELS immediately with a fresh stopPosition so there's
        # protection against unexpected spindle starts before "Cut" is pressed.
        # Only arm if Z is on the safe side of stop_z — arming when past
        # stop_z would cause immediate ELS fire → backlash takeup.
        self.board.unbind(update_tick=self._on_board_update)

        if self._engaging:
            self._engaging = False
            z_pos = self.z_axis.scaledPosition
            cut_dir = self.els.stop_direction_value(self.controller.els_forward)
            diff = (z_pos - self.controller.stop_z) * cut_dir
            if diff <= 0:  # Z is on the safe side or at stop_z
                self.set_stop_z(self.controller.stop_z)
                # Set active=1 before enable so ELS arms in STOPPED state —
                # sync motion paused until operator clicks Cut (which clears
                # active via on_enter_cutting, triggering resume/takeup).
                self.hal.set_active(True)
                self.hal.set_enable(True)
                log.info(
                    f"on_enter_stopped (engage): armed ELS stopped with "
                    f"stop_z={self.controller.stop_z} z_pos={z_pos}"
                )

        self.hal.set_stop_direction(
            self.els.stop_direction_value(self.controller.els_forward)
        )
        if self.controller.retract_enabled or self.controller.wizard_enabled:
            self.hal.set_hysteresis_tight()
        else:
            self.hal.set_hysteresis_loose()

    def on_enter_disabled(self):
        # Nut position is unknown after disable + re-enable + manual jogs,
        # so conservatively assume the next retract needs full takeup.
        self._retract_backlash_applied = False
        self.board.unbind(update_tick=self._on_board_update)
        self.hal.set_enable(False)

    # ——— condition-checking methods ———    
    def is_ready_to_retract(self):
        x_pos = self._cross_slide_input.encoderCurrent
        # TODO: need to align units (encoder counts vs in/mm)
        return not self.check_x_retract or x_pos <= self.safe_x if self.inside else x_pos >= self.safe_x 
    
    def is_retracted(self):
        # Retract direction is implicit in the user-entered values:
        # retract_z is the destination away from stop_z, so sign(retract_z -
        # stop_z) IS the retract direction in scale units — no polarity
        # config needed. True when z_pos has reached or overshot retract_z
        # in that direction (the retract path rounds away from zero, so
        # any non-zero gap is always closed by ≥1 servo step).
        z_pos = self.z_axis.scaledPosition
        stop_z = self.controller.stop_z
        retract_z = self.controller.retract_z
        span = retract_z - stop_z
        if span == 0:
            retracted = (z_pos == retract_z)
        else:
            retract_dir = 1 if span > 0 else -1
            retracted = (z_pos - retract_z) * retract_dir >= 0
        log.debug(
            f"is_retracted() z_pos={z_pos} stop_z={stop_z} "
            f"retract_z={retract_z} span={span} retracted={retracted}"
        )
        return retracted
    
    def is_ready_to_cut(self):
        if self.controller.retract_enabled:
            return self.is_retracted()
        # In stop-only mode, verify Z is on the safe side of stop_z AND at
        # least _safety_margin_display away. This prevents starting a cut when
        # too close to the workpiece — even with deferred ELS arming, a
        # backlash takeup + thread re-sync could push past stop_z before
        # ELS fires if Z is within that distance.
        z_pos = self.z_axis.scaledPosition
        cut_dir = self.els.stop_direction_value(self.controller.els_forward)
        margin = self._safety_margin_display()
        diff = (z_pos - self.controller.stop_z) * cut_dir
        result = diff < -margin if margin > 0 else diff < 0
        log.debug(
            f"is_ready_to_cut: z_pos={z_pos} stop_z={self.controller.stop_z} "
            f"cut_dir={cut_dir} margin={margin:.4f} diff={diff:.4f} → {result}"
        )
        return result

    # ——— after any state change ———
    def _broadcast(self):
        log.info(f"els_fsm new state = {self.state}")
        bus.publish("state_changed", state=self.state)

    # ——— board interface ———

    # bound to update_tick during moves
    def _on_board_update(self, *args, **kv):
        if self.hal.read_active():
            if self.state == 'cutting':
                bus.publish("els_stop_activated")
                self.stop_active()
            if self.state == 'retracting':
                if self.hal.is_move_done():
                    bus.publish("els_retract_done")
                    self.retract_done()

    # ——— convenience properties ———

    @property
    def _saddle_input(self):
        axis = self.els.get_z_axis()
        return axis._primary_input() if axis is not None else None

    @property
    def _cross_slide_input(self):
        axis = self.els.get_x_axis()
        return axis._primary_input() if axis is not None else None
    
    # ——— Firmware interactions ———

    def set_scale_index(self):
        z_input = self.z_axis._primary_input()
        if z_input is not None:
            self.hal.set_scale_index(z_input.inputIndex)

    def set_stop_z(self, stop_z_position: float):
        """Push stop_z (in scale units) to firmware via the HAL.

        Used by the wizard cycle and the standalone keypad path. Also
        sets scaleIndex so a subsequent enable arms against the right
        encoder.
        """
        enc = self.z_axis.position_to_encoder(stop_z_position)
        self.hal.set_stop_position(enc)
        self.set_scale_index()

    def push_thread_geometry(self) -> None:
        # Writes both threadPitchSteps and zCountsPerPitch. The firmware uses
        # both to perform Z-scale-based phase re-sync after a retract/resume,
        # and combines sign(threadPitchSteps × zCountsPerPitch) with
        # stopDirection to derive the backlash takeup direction.
        spindle = self.els.get_spindle_axis()
        saddle = self._saddle_input

        # spindle.syncRatioNum/Den is mm-per-rev (= mm-per-thread-pitch),
        # populated from feeds.table by els_advbar.update_feeds_ratio.
        spindle_pitch_mm = Fraction(abs(spindle.syncRatioNum),
                                    abs(spindle.syncRatioDen))
        # servo.ratioNum/Den is mm-per-step (linear leadscrew).
        servo_ratio = Fraction(abs(self.servo.ratioNum),
                               abs(self.servo.ratioDen))

        thread_pitch_steps = float(spindle_pitch_mm / servo_ratio)

        # saddle.ratioNum/Den is mm-per-count (the raw input ratio, before
        # formats.factor display conversion). z_counts_per_pitch is then
        # mm-per-pitch / mm-per-count = counts-per-pitch.
        if saddle is not None and saddle.ratioDen != 0 and saddle.ratioNum != 0:
            z_scale_mm_per_count = Fraction(saddle.ratioNum, saddle.ratioDen)
            z_counts_per_pitch = float(spindle_pitch_mm / z_scale_mm_per_count)
        else:
            z_counts_per_pitch = 0.0

        log.info(
            f"*** push_thread_geometry: pitch_mm={float(spindle_pitch_mm)} "
            f"servo_ratio={float(servo_ratio)} thread_pitch_steps={thread_pitch_steps} "
            f"z_counts_per_pitch={z_counts_per_pitch}"
        )
        self.hal.set_thread_pitch_steps(thread_pitch_steps)
        self.hal.set_z_counts_per_pitch(z_counts_per_pitch)

    # ——— Safety margin ———
    def _safety_margin_display(self) -> float:
        """Minimum distance (in display units) Z must be from stop_z before cutting is allowed.

        Leadscrew pitch + backlash distance × 1.1 ensures that even a worst-case
        backlash takeup followed by thread re-sync cannot push the carriage
        past the workpiece before ELS can fire. Result is converted to
        display units (mm or inches) so it matches scaledPosition/stop_z.

        Uses the servo leadscrew pitch, not the thread pitch being cut — the
        safety margin is about how far the servo can move before ELS fires,
        independent of what thread is being cut.
        """
        # Leadscrew pitch in mm (not thread pitch)
        try:
            pitch_mm = float(Fraction(self.servo.leadScrewPitch))
            if self.servo.leadScrewPitchIn:
                pitch_mm *= 25.4  # convert inches to mm
        except (ValueError, ZeroDivisionError):
            return 0.0

        try:
            servo_ratio = Fraction(abs(self.servo.ratioNum), abs(self.servo.ratioDen))
        except (ValueError, ZeroDivisionError):
            return 0.0

        backlash_steps = int(self.els.els_backlash_steps or 0)
        backlash_mm = float(backlash_steps * float(servo_ratio))

        margin_mm = (pitch_mm + backlash_mm) * 1.1
        # Convert mm to display units so comparison with scaledPosition is valid
        factor = self.board.formats.factor
        margin_display = margin_mm * float(factor)
        log.debug(
            f"_safety_margin: pitch={pitch_mm:.4f}mm backlash={backlash_mm:.4f}mm "
            f"margin={margin_mm:.4f}mm → {margin_display:.6f} display units (factor={float(factor):.6f})"
        )
        return margin_display

    # ——— Helpers ———
    def _scale_counts_to_steps(self, scale_counts : int) -> int:
        scale_ratio = Fraction(abs(self._saddle_input.ratioNum),
                               abs(self._saddle_input.ratioDen))
        servo_ratio = Fraction(abs(self.servo.ratioNum),
                               abs(self.servo.ratioDen))
        # Round magnitude away from zero so any non-zero scale gap always
        # commands at least one servo step. Truncating toward zero would
        # leave the retract short when the scale's resolution is finer
        # than the servo's (1 count converts to < 1 step → int() → 0),
        # which would visibly undershoot retract_z on the DRO.
        raw = Fraction(scale_counts) * scale_ratio / servo_ratio
        if raw == 0:
            magnitude = 0
        else:
            magnitude = math.ceil(abs(raw))  # Fraction supports __ceil__
        sign = 1 if raw > 0 else (-1 if raw < 0 else 0)
        return sign * magnitude
