from fractions import Fraction
from kivy.logger import Logger

from rcp.components.home.coordbar import CoordBar

log = Logger.getChild(__name__)

class AssistedThreadingWizard:
    @property
    def saddle_scale(self) -> CoordBar:
        return self.app.scales[self.bar.selected_saddle_scale_id]
    
    @property
    def cross_slide_scale(self) -> CoordBar:
        return self.app.scales[self.bar.selected_cross_slide_scale_id]
    
    def __init__(self, bar):
        self.bar = bar
        self.app = bar.app
        self.servo = self.app.servo
        self.current_step = 0
        self._current_callback = None
        self.manual_stop_length = None  
        self.manual_cutting_depth = None
        self._steps = [
            self._step_set_initial_position,
            self._step_set_stop_position,
            self._step_set_material_width_position,
            self._step_set_final_cutting_depth_position,
            self._step_go_to_start,
            # ... same steps as before ...
        ]
        

    def start(self):
        self.goto_step(0)

    def reset_ui(self):
        # Reset wizard_area to default content
        self.bar.label_text = ""
        self.bar.display_value = ""
        self.bar.action_button_enabled = True
        self.bar.action_button_condition_fn = None
        

    def goto_step(self, index):
        self.current_step = index
        if 0 <= index < len(self._steps):
            self._steps[index]()
        else:
            log.info("Wizard finished")
            self.bar.is_running = False
    
    def goto_next_step(self, *args):
        if self._current_callback:
            self._current_callback(*args)  
        self.goto_step(self.current_step + 1)

    def set_instruction(self, label_text, next_button_text, next_button_callback, value_button_fn=None, action_button_condition_fn=None):
        self.bar.label_text = label_text
        self.bar.next_button_text = next_button_text
        self._current_callback = next_button_callback
        self.bar.bind_btn_value_on_release(value_button_fn)
        self.bar.action_button_condition_fn = action_button_condition_fn
    
    # Instruction steps
    #Step 1
    def _step_set_initial_position(self):
        self.set_instruction("Go to initial Z and press Set", "Set", self._capture_initial_position)
        self.bar.bind_display_value_to_scale(self.saddle_scale)

    #Step 2
    def _step_set_stop_position(self):
        self.bar.action_button_enabled = False  # Disable until valid
        self.set_instruction("Go to stop Z and press Set", "Set", self._capture_stop_position, self._open_stop_position_keypad, self._is_valid_stop_position)
        self.bar.bind_display_value_to_scale(self.saddle_scale)
    
    #Step 3
    def _step_set_material_width_position(self):
        self.set_instruction("Set material width and press Set", "Set", self._capture_material_width_position)
        self.bar.bind_display_value_to_scale(self.cross_slide_scale)
        
    #Step 4
    def _step_set_final_cutting_depth_position(self):
        self.bar.action_button_enabled = False  # Disable until valid
        self.set_instruction("Enter Final Cutting Depth", "Set", self._capture_final_cutting_depth_position, self._open_final_cutting_depth_position_keypad, self._is_valid_cutting_depth_position)
        self.bar.unbind_all_display_value() 
        self.bar.display_value = ""  # Clear display value since not bound to scale
    
    #Step 5        
    def _step_go_to_start(self):
        self.set_instruction("Engage half nut and press Go to return to start position", "Go", self._go_to_start)
     
    
    # Step callbacks    
    # Step 1
    def _capture_initial_position(self, *args):
        self.bar.start_position = self.saddle_scale.encoderCurrent
        self._isStartPositionMetricMode = self.app.formats.current_format == "MM"
        self._startScaledPosition = self.saddle_scale.scaledPosition
        log.info(f"Initial position set to: {self.bar.start_position}")
        
    #Step 2
    def _capture_stop_position(self, *args):         
        self.bar.stop_position = self._get_stop_position_units()
        self.manual_stop_length = None  # reset for next run
        log.info(f"Stop position set - (start={self.bar.start_position}, stop={self.bar.stop_position})")
            
    #Step 3
    def _capture_material_width_position(self, *args):
        self.bar.material_width = self.cross_slide_scale.encoderCurrent
        self._isMaterialWidthPositionMetricMode = self.app.formats.current_format == "MM"
        self._materialWidthScaledPosition = self.cross_slide_scale.scaledPosition
        log.info(f"Material width set to: {self.bar.material_width}")
    
    #Step 4
    def _capture_final_cutting_depth_position(self, *args):         
        # convert length into encoder stop position
        self.bar.cutting_depth = self._convert_position_units_to_encoder(self.cross_slide_scale,
                                                                         self.manual_cutting_depth,
                                                                         self._isMaterialWidthPositionMetricMode,
                                                                         self._materialWidthScaledPosition,
                                                                         self.bar.material_width)
        
        log.info(f"Cutting depth set manually: {self.manual_cutting_depth} "
                f"(start={self.bar.material_width}, stop={self.bar.cutting_depth})")        
    
    #Step 5    
    def _go_to_start(self, *args):
        log.info(f"Moving to start position: {self.bar.start_position} + retraction")
        
        ratio = Fraction(self.servo.ratioNum, self.servo.ratioDen)
        
        if self.bar.left_hand_thread:
            target_scaled = self.bar.start_position - self._get_retraction_distance_encoder_steps()  # subtract retraction
        else:        
            target_scaled = self.bar.start_position + self._get_retraction_distance_encoder_steps()  # add retraction
            
        current_scaled = self.saddle_scale.encoderCurrent
        delta_steps = int((target_scaled - current_scaled) / ratio)
        log.info(f"Computed move delta: {delta_steps} steps (target_scaled={target_scaled}, current_scaled={current_scaled}, ratio={ratio})")
        
        if delta_steps == 0:
            log.info("Already at start position")
            self.goto_step(self.current_step + 1)
            return
        
        self.bar.bind_display_value_to_servo_position() # bind to servo position
        self.servo.set_max_speed(self.bar.reversing_speed)  # set to reversing speed
        self.servo.servoEnable = 1  # enable
        self.app.device['servo']['direction'] = delta_steps  # trigger move
        self.app.bind(update_tick=self._check_servo_done)  # watch until done    
            
    #Step Action button condition functions
    #Step 2
    def _is_valid_stop_position(self):
        """Check if the stop position is valid given the start position and thread direction.
         - For right-hand threads, stop must be less than start.
         - For left-hand threads, stop must be greater than start."""
        if self.bar.left_hand_thread:
            return self.bar.start_position < self._get_stop_position_units()
        return self.bar.start_position > self._get_stop_position_units()
    
    #Step 4
    def _is_valid_cutting_depth_position(self):
        """Check if the cutting depth is valid given the material width position and if it's internal/external thread.
        - For internal threads, cutting depth must be greater than material width.
        - For external threads, cutting depth must be less than material width."""
        if self.bar.inner_thread:
            return self.bar.material_width < self._convert_position_units_to_encoder(self.cross_slide_scale,
                                                                                     self.manual_cutting_depth,
                                                                                     self._isMaterialWidthPositionMetricMode,
                                                                                     self._materialWidthScaledPosition,
                                                                                     self.bar.material_width)
        return self.bar.material_width > self._convert_position_units_to_encoder(self.cross_slide_scale,
                                                                                 self.manual_cutting_depth,
                                                                                 self._isMaterialWidthPositionMetricMode,
                                                                                 self._materialWidthScaledPosition,
                                                                                 self.bar.material_width)

    # Manual input handlers
    def _open_stop_position_keypad(self, *args):
        from rcp.components.keypad import Keypad
        
        is_metric = self.app.formats.current_format == "MM"
        
        keypad = Keypad(title="Enter Stop Length (" + ("mm" if is_metric else "in") + ")")
        keypad.integer = False

        def on_done(value):
            try:
                self.manual_stop_length = float(value)
                log.info(f"Manual stop length entered: {self.manual_stop_length}")
                # Display this override until user moves scale again
                self.bar.display_value = f"{self.manual_stop_length:.3f}" if is_metric else f"{self.manual_stop_length:.4f}"
            except ValueError:
                log.warning(f"Invalid stop length input: {value}")
            finally:
                self.bar.update_action_button_state()

        keypad.show_with_callback(callback_fn=on_done,
                                current_value=self.manual_stop_length or 0.0)
    
    def _open_final_cutting_depth_position_keypad(self, *args):
        from rcp.components.keypad import Keypad
        
        is_metric = self.app.formats.current_format == "MM"
        
        keypad = Keypad(title="Enter Final Cutting Depth (" + ("mm" if is_metric else "in") + ")")
        keypad.integer = False

        def on_done(value):
            try:
                self.manual_cutting_depth = float(value)
                log.info(f"Manual cutting depth entered: {self.manual_cutting_depth}")
                self.bar.display_value = f"{self.manual_cutting_depth:.3f}" if is_metric else f"{self.manual_cutting_depth:.4f}"
            except ValueError:
                log.warning(f"Invalid cutting depth input: {value}")
            finally:
                self.bar.update_action_button_state()

        keypad.show_with_callback(callback_fn=on_done,
                                current_value=self.manual_cutting_depth or 0.0)
        
    # Utilities
    def _convert_position_units_to_encoder(self, 
                                                scale: CoordBar, 
                                                manual_position: float, 
                                                is_original_position_metric_mode: bool,
                                                original_scaled_position, 
                                                start_encoder_units: int) -> int:
        """
        Convert a user-entered stop position (MM/IN) into encoder counts.
        Handles:
            - unit changes (MM ↔ IN)
            - offsets
            - zero start positions
        """

        # Determine factors
        current_factor = float(self.app.formats.factor)
        factor_at_start_position = float(self.app.formats.MM_FRACTION if is_original_position_metric_mode else self.app.formats.INCHES_FRACTION)

        # Normalize manual input to the units used at start
        manual_in_start_units = manual_position * (factor_at_start_position / current_factor)

        # Compute delta relative to start scaled position
        delta_in_start_units = manual_in_start_units - original_scaled_position

        log.info(
            f"Manual input: {manual_position} "
            f"(converted to start units: {manual_in_start_units}, "
            f"delta from start: {delta_in_start_units})"
        )

        # Compute encoder counts using inverse of CoordBar.scaledPosition
        encoder_counts = (
            (delta_in_start_units / factor_at_start_position) - scale.offsets[self.app.currentOffset]
        ) * (float(scale.ratioDen) / float(scale.ratioNum))

        # Offset by the captured start position
        final_encoder_position = int(round(start_encoder_units + encoder_counts))

        log.info(
            f"Computed encoder counts: {final_encoder_position} "
            f"(start_position={start_encoder_units}, encoder delta={encoder_counts})"
        )

        return final_encoder_position

    def _get_stop_position_units(self) -> float:
        scale = self.saddle_scale
        if self.manual_stop_length is not None:
            log.info(f"Using manual stop length: {self.manual_stop_length}")
            result = self._convert_position_units_to_encoder(
                scale,
                self.manual_stop_length,
                self._isStartPositionMetricMode,
                self._startScaledPosition,
                self.bar.start_position
            )
            log.info(f"Converted manual stop length to encoder units: {result}")
            return result
        log.info(f"Using live encoder value: {scale.encoderCurrent}")
        return scale.encoderCurrent
    
    def _convert_distance_units_to_encoder(self, scale: CoordBar, distance: float, is_metric: bool) -> int:
        """
        Convert a pure distance (mm or inch) into encoder counts.
        """
        encoder_factor = float(self.app.formats.MM_FRACTION if is_metric else self.app.formats.INCHES_FRACTION)

        # Compute encoder counts using inverse of CoordBar.scaledPosition
        encoder_counts = (
            (distance / encoder_factor) - scale.offsets[self.app.currentOffset]
        ) * (float(scale.ratioDen) / float(scale.ratioNum))

        final_encoder_distance = int(round(encoder_counts))

        log.info(
            f"Converted distance to encoder counts: {final_encoder_distance} "
            f"(input distance={distance}, encoder delta={encoder_counts})"
        )

        return final_encoder_distance

    def _get_retraction_distance_encoder_steps(self) -> int:
        """Get the retraction distance in encoder counts based on thread pitch and direction."""
        return self._convert_distance_units_to_encoder(self.saddle_scale, self.bar.backlash_retraction_distance, self.bar.metric_distances)

    def _check_servo_done(self, *args):
        if self.app.fast_data_values['stepsToGo'] == 0:
            log.info("Servo reached start position")
            self.servo.servoEnable = 0  # disable
            self.servo.set_max_speed(self.servo.maxSpeed)  # restore speed
            
            # Stop watching
            self.app.unbind(update_tick=self._check_servo_done)

            # Advance workflow (skip callback loop!)
            self.goto_step(self.current_step + 1)
