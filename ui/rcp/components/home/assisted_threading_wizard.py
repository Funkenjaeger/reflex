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
            self._step_set_initial_position,                # Step 1
            self._step_set_stop_position,                   # Step 2
            self._step_set_material_width_position,         # Step 3
            self._step_set_final_cutting_depth_position,    # Step 4
            self._step_engage_half_nut,                     # Step 5
            self._step_go_to_start,                         # Step 6
            self._step_cut_thread,                          # Step 7
            self._step_depth_reached                        # Step 8
        ]
        

    def start(self):
        self.goto_step(0)

    def reset_ui(self):
        # Reset wizard_area to default content
        self.bar.label_text = ""
        self.bar.display_value = ""
        self.bar.action_button_enabled = True
        self.bar.action_button_condition_fn = None
        self.app.device['fastData']['threadReset'] = 1
        

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

    def set_instruction(self, label_text, next_button_text, next_button_callback, value_button_fn=None, action_button_condition_fn=None, retract_button_visible=False):
        self.bar.label_text = label_text
        self.bar.next_button_text = next_button_text
        self._current_callback = next_button_callback
        self.bar.bind_btn_value_on_release(value_button_fn)
        self.bar.action_button_condition_fn = action_button_condition_fn
        self.bar.retract_button_visible = retract_button_visible
        
    def start_retracting(self):
        if not self.app.connected:
            return
        
        log.info("Retract button pressed")
        #TODO implement retract logic
    
    def stop_retracting(self):
        if not self.app.connected:
            return
        
        log.info("Retract button released")
        #TODO implement retract logic
    
    # Instruction steps
    #Step 1
    def _step_set_initial_position(self):
        self.set_instruction("Go to initial Z and press Set", "Set", self._capture_initial_position)
        self.bar.bind_display_value_to_scale(self.saddle_scale)

    #Step 2
    def _step_set_stop_position(self):
        self.bar.action_button_enabled = False  # Disable until valid
        self.set_instruction("Go to or input stop Z and press Set", "Set", self._capture_stop_position, self._open_stop_position_keypad, self._is_valid_stop_position)
        self.bar.bind_display_value_to_scale(self.saddle_scale)
    
    #Step 3
    def _step_set_material_width_position(self):
        self.set_instruction("Go to material width and press Set", "Set", self._capture_material_width_position)
        self.bar.bind_display_value_to_scale(self.cross_slide_scale)
        
    #Step 4
    def _step_set_final_cutting_depth_position(self):
        self.bar.action_button_enabled = False  # Disable until valid
        self.set_instruction("Enter Final Cutting Depth", "Set", self._capture_final_cutting_depth_position, self._open_final_cutting_depth_position_keypad, self._is_valid_cutting_depth_position)
        self.bar.unbind_all_display_value() 
        self.bar.display_value = ""  # Clear display value since not bound to scale
    
    #Step 5
    def _step_engage_half_nut(self):
        self.set_instruction("Engage half nut and press Next", "Next", None)
        self.bar.unbind_all_display_value() 
        self.bar.display_value = "" 
    
    #Step 6       
    def _step_go_to_start(self):
        self.set_instruction("Confirm cross slide retracted and press Go to return to start position", "Go", self._go_to_start, None, self._is_cross_slide_retracted)
     
    #Step 7
    def _step_cut_thread(self):
        self.set_instruction("Go to cutting depth and press Cut to start threading operation", "Cut", self._start_threading_operation, None, self._is_cross_slide_at_cutting_depth, True)
    
    #Step 8
    def _step_depth_reached(self):
        self.set_instruction("Final depth reached. Cut more? Press Stop to quit.", "Cut", None, self._start_threading_operation, None, self._is_cross_slide_at_cutting_depth, True)
    
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
        self.bar.last_cutting_depth = self.bar.material_width  # Initialize last_cutting_depth to material_width
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
    
    #Step 6 - TODO test this   
    def _go_to_start(self, *args):
        if not self.app.connected:
            return
        
        log.info(f"Moving to start position: {self.bar.start_position} + retraction")

        # --- Delta to move the servo ---
        delta_steps = self._get_servo_delta_steps()
        
        _next_step: int
        # Check if saddle is retracted further than or already at  start position including the backlash - if so, move further than the actual retracted backlash position to take out backlash and go back
        if (delta_steps >= 0 and self.bar.left_hand_thread) or (delta_steps <= 0 and not self.bar.left_hand_thread):
            log.info("Saddle retracted further than or at start position")
            delta_steps += (self._get_retraction_distance_encoder_steps() * (1 if self.bar.left_hand_thread else -1))
            log.info("Taking out backlash by moving further than retracted start position")
            # If taking out backlash, we need to wait until the first move is done, then issue another move to go back to start position
            _next_step = self.current_step # watch until done - then go to next step (which is this same step again)
        else:
            _next_step = self.current_step + 1 # Step 7
            # Check if at cutting depth
            if (self._is_cross_slide_at_final_cutting_depth()):
                _next_step += 1  # skip cutting step and go to step 8 (depth reached)
        
        # --- Issue servo move ---
        self.bar.bind_display_value_to_servo_position() # bind to servo position
        self.servo.set_max_speed(self.bar.reversing_speed) # set to reversing speed
        self.servo.servoEnable = 1
        self.app.device['servo']['direction'] = delta_steps # trigger move        
             
        self.app.bind(update_tick=lambda *a: self._check_servo_done(_next_step, *a)) # watch until done - then go to next step  
     
    #Step 7 - TODO test
    def _start_threading_operation(self, *args):
        if not self.app.connected:
            return
        
        #check that current position is at proper start position including the backlash retraction distance within the bar.backlash_cushion
        retraction_distance = self._get_retraction_distance_encoder_steps()
        if self.bar.left_hand_thread:
            desired_position = self.bar.start_position + retraction_distance
        else:
            desired_position = self.bar.start_position - retraction_distance
        
        if (abs(self.saddle_scale.encoderCurrent - desired_position) > self.bar.backlash_cushion):
            log.warning("Not at valid start position including backlash cushion. Aborting threading operation.")
            #TODO show error message in UI
            return
        
        log.info("Starting threaded cut to stop position: %s", self.bar.stop_position)
        
        target_servo_counts = self._get_servo_delta_steps()
        
        # Pick spindle index using get_spindle_scale
        spindle_scale = self.app.get_spindle_scale()
        spindle_index = spindle_scale.inputIndex if spindle_scale is not None else 0

        tolerance = self.bar.encoder_sync_tolerance 

        # Bind UI to servo position so the progress/pos displays servo scaledPosition
        self.bar.bind_display_value_to_servo_position()

        # Write the fields into firmware via modbus/device wrapper
        dev = self.app.device
        dev['fastData']['threadDesiredSteps'] = target_servo_counts
        dev['fastData']['threadSpindleIndex'] = spindle_index
        dev['fastData']['threadTolerance'] = tolerance

        # Request latch+wait. Firmware will latch current spindle phase and wait until matched.
        dev['fastData']['threadRequest'] = 1

        self.app.bind(update_tick=lambda *a: self._check_servo_done(5, *a)) # watch until done - then go to step 6 (go to start)
            
    #Step Action button condition functions
    #Step 2
    def _is_valid_stop_position(self):
        """Check if the stop position is valid given the start position and thread direction.
         - For right-hand threads, stop must be less than start.
         - For left-hand threads, stop must be greater than start.
         - Stop position must be greater than the backlash retraction distance from start position so as to take out backlash when retracted further than start position"""
        retraction_distance = self._get_retraction_distance_encoder_steps()
         # Ensure stop position is beyond retraction distance from start
        if self.bar.left_hand_thread:
            return self.bar.start_position + retraction_distance < self._get_stop_position_units() 
        return self.bar.start_position - retraction_distance > self._get_stop_position_units()
    
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

    #Step 6 - TODO test this
    def _is_cross_slide_retracted(self):
        """Check if the cross slide is retracted further than the material width if saddle is further than stop position."""
        check_saddle = False
        if self.bar.left_hand_thread:
            check_saddle =  self.saddle_scale.encoderCurrent > self.bar.start_position
        else:
            check_saddle = self.saddle_scale.encoderCurrent < self.bar.start_position
        
        if not check_saddle:
            return True  # saddle is at a safe position, no need to check cross slide
        
        if self.bar.inner_thread:
            return self.cross_slide_scale.encoderCurrent < self.bar.material_width
        return self.cross_slide_scale.encoderCurrent > self.bar.material_width
    
    #Step 7 - TODO test this
    def _is_cross_slide_at_cutting_depth(self):
        """Check if the cross slide is at the cutting depth position."""
        if self.bar.inner_thread:
            return self.cross_slide_scale.encoderCurrent >= self.bar.last_cutting_depth
        return self.cross_slide_scale.encoderCurrent <= self.bar.last_cutting_depth

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

    def _check_servo_done(self, next_step: int, *args):
        if self.app.fast_data_values['stepsToGo'] == 0:
            log.info("Servo reached desired position")
            self.servo.servoEnable = 0  # disable
            self.servo.set_max_speed(self.servo.maxSpeed)  # restore speed
            
            # Stop watching
            self.app.unbind(update_tick=self._check_servo_done)

            # Advance workflow (skip callback loop!)
            self.goto_step(next_step)
        
    def _get_servo_delta_steps(self) -> int:
        """Get current servo position in absolute counts."""
        # --- Convert scale encoder counts -> machine units ---
        scale_ratio = Fraction(self.saddle_scale.ratioNum, self.saddle_scale.ratioDen)
        current_machine_units = self.saddle_scale.encoderCurrent * scale_ratio
        start_machine_units = self.bar.start_position * scale_ratio

        # --- Apply backlash retraction in machine units ---
        retraction = self._get_retraction_distance_encoder_steps() * scale_ratio
        if self.bar.left_hand_thread:
            target_machine_units = start_machine_units - retraction
        else:
            target_machine_units = start_machine_units + retraction

        # --- Convert machine units -> servo steps ---
        servo_ratio = Fraction(self.servo.ratioNum, self.servo.ratioDen)
        target_servo_counts = int(target_machine_units / servo_ratio)
        current_servo_counts = int(current_machine_units / servo_ratio)

        # --- Delta to move the servo ---
        delta_steps = target_servo_counts - current_servo_counts
        log.info(
            f"Computed move delta: {delta_steps} steps "
            f"(target={target_machine_units:.4f}, current={current_machine_units:.4f} {self.app.formats.current_format}, "
            f"scale_ratio={scale_ratio}, servo_ratio={servo_ratio})"
        )
        return delta_steps
    
    def _is_cross_slide_at_final_cutting_depth(self):
        """Check if the cross slide is at or more than the final cutting depth position."""
        if self.bar.inner_thread:
            return self.cross_slide_scale.encoderCurrent >= self.bar.cutting_depth
        return self.cross_slide_scale.encoderCurrent <= self.bar.cutting_depth