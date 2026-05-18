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
        {'trigger': 'enable', 'source': 'disabled', 'dest': 'stopped'},
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


    # ——— transition side effects ———
    def on_enter_retracting(self):
        enc_current = self._saddle_input.encoderCurrent
        enc_target = self.z_axis.position_to_encoder(self.controller.retract_z)
        enc_delta = enc_target - enc_current
        step_delta = self._scale_counts_to_steps(enc_delta)
        log.info(f"***retract(): {enc_current} {enc_target} {enc_delta} {step_delta}")
        self.set_scale_index()
        self.hal.set_steps_to_go(step_delta)
        self.board.bind(update_tick=self._on_board_update)

    def on_enter_cutting(self):
        self.hal.set_scale_index(self._saddle_input.inputIndex)
        self.push_thread_geometry() # TODO: only if threading!
        self.hal.set_backlash_steps(int(self.els.els_backlash_steps))
        self.hal.set_active(False)
        log.info(
            f"on_enter_cutting: els_forward={self.controller.els_forward} "
            f"cut_inv={self.els.cut_polarity_inverted} "
            f"stop_inv={self.els.stop_polarity_inverted} "
            f"z_step_inv={self.els.z_scale_step_inverted} "
            f"backlash_steps={int(self.els.els_backlash_steps)}"
        )
        self.board.bind(update_tick=self._on_board_update)

    def on_enter_stopped(self):
        # Engaged but idle: arm the stop block and configure direction +
        # hysteresis from current operator-mode flags. Idempotent — also
        # runs after the cycle ends or a retract completes.
        self.board.unbind(update_tick=self._on_board_update)
        self.hal.set_stop_direction(
            self.els.stop_direction_value(self.controller.els_forward)
        )
        if self.controller.retract_enabled or self.controller.wizard_enabled:
            self.hal.set_hysteresis_tight()
        else:
            self.hal.set_hysteresis_loose()
        self.hal.set_enable(True)

    def on_enter_disabled(self):
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
        return self.is_retracted()

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
        # scale_to_step_sign captures the machine's Z-scale-count ↔
        # leadscrew-step wiring relationship (independent of the cut /
        # stop polarities). User can flip it in the settings popup.
        return sign * magnitude * self.els.scale_to_step_sign()
