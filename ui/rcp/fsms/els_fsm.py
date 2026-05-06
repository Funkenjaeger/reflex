from kivy.logger import Logger
from transitions import Machine

from rcp.dispatchers import els, board
from rcp.fsms.els_stop_hal import ElsStopHal
from rcp.fsms.fsm_event_bus import fsm_event_bus as bus

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
        
        self.z_cut_dir = -1 # TODO: move to controller

        self._z_backlash = 0

        self.safe_x = 0 # TODO: move to controller
        self.check_x_retract = False # TODO: move to controller
        self.inside = False # TODO: move to controller


    # ——— transition side effects ———
    def on_enter_retracting(self):
        enc_current = self._saddle_input.encoderCurrent
        enc_target = self.z_axis.position_to_encoder(self.controller.retract_z)
        enc_delta = enc_target - enc_current + self._z_backlash
        step_delta = self._scale_counts_to_steps(enc_delta)
        log.info(f"***retract(): {enc_current} {enc_target} {enc_delta} {self._z_backlash} {step_delta}")
        self.set_scale_index()
        self.hal.set_steps_to_go(step_delta) 
        self.board.bind(update_tick=self._on_board_update)

    def on_exit_retracting(self):
        z_pos_enc = self._saddle_input.encoderCurrent
        axis = self.els.get_z_axis()
        z_tgt_enc = axis.position_to_encoder(self.controller.retract_z)
        z_delta_enc = z_tgt_enc - z_pos_enc
        step_delta = self._scale_counts_to_steps(z_delta_enc)
        log.info(f"***on_exit_retracting: z_pos_enc {z_pos_enc} z_tgt_enc {z_tgt_enc} step_delta {step_delta}")
        if z_delta_enc > 0: # TODO handle sign
            self._z_backlash += z_delta_enc
            log.info(f"Retract was short, updated backlash estimate to {self._z_backlash} counts")

    def on_enter_cutting(self):
        self.hal.set_scale_index(self._saddle_input.inputIndex)
        self.set_thread_pitch_steps() # TODO: only if threading!
        self.hal.set_active(False)
        self.board.bind(update_tick=self._on_board_update)

    def on_enter_stopped(self):
        # Engaged but idle: arm the stop block and configure direction +
        # hysteresis from current operator-mode flags. Idempotent — also
        # runs after the cycle ends or a retract completes.
        self.board.unbind(update_tick=self._on_board_update)
        self.hal.set_stop_direction(forward=self.controller.els_forward)
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
        z_pos = self.z_axis.scaledPosition
        #z_pos = self._saddle_input.encoderCurrent
        log.info(f"DEBUG is_retracted() z_pos {z_pos} retract_z {self.controller.retract_z} z_cut_dir {self.z_cut_dir}")        
        return (self.controller.retract_z - z_pos) * self.z_cut_dir >= 0
    
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

    def set_thread_pitch_steps(self) -> None:
        spindle = self.els.get_spindle_axis()
        spindle_ratio = Fraction(abs(spindle.syncRatioNum), 
                                 abs(spindle.syncRatioDen))
        servo_ratio   = Fraction(abs(self.servo.ratioNum),  
                                 abs(self.servo.ratioDen))
        thread_pitch_steps = float(spindle_ratio / servo_ratio)
        log.info(f"*** set_thread_pitch_steps {spindle_ratio} {servo_ratio} {thread_pitch_steps}")
        self.hal.set_thread_pitch_steps(thread_pitch_steps)    

    # ——— Helpers ———
    def _scale_counts_to_steps(self, scale_counts : int) -> int:
        scale_ratio = Fraction(abs(self._saddle_input.ratioNum),
                               abs(self._saddle_input.ratioDen))
        servo_ratio = Fraction(abs(self.servo.ratioNum),
                               abs(self.servo.ratioDen))        
        step_delta = int(scale_counts * scale_ratio / servo_ratio)
        step_delta = -1 * step_delta # TODO fix this
        return step_delta
