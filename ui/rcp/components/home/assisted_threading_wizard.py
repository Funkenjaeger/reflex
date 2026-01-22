from fractions import Fraction
from kivy.logger import Logger

from rcp.components.forms.custom_popup import CustomPopup
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
        log.info("Initializing AssistedThreadingWizard")
        self.bar = bar
        self.app = bar.app
        self.servo = self.app.servo
        self.current_step = 0
        self._threading_started = False
        self._calculated_threading_delta_steps = 0
        self._current_callback = None
        self._servo_watch_callback = None
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
        dev = self.app.device
        dev['assistedThreadingData']['spindlePhaseTolerance'] = self.bar.encoder_sync_tolerance
        
        # Pick spindle index using get_spindle_scale
        spindle_scale = self.app.get_spindle_scale()
        if spindle_scale is not None:
            dev['assistedThreadingData']['spindleCountsPerRev'] = spindle_scale.ratioDen
            dev['assistedThreadingData']['spindleScaleIndex'] = spindle_scale.inputIndex
        
        self.goto_step(0)

    def stop(self):
        # Reset wizard_area to default content
        log.info("Wizard finished")
        self._current_callback = None
        self._threading_started = False
        self.bar.label_text = ""
        self.bar.display_value = ""
        self.bar.action_button_enabled = True
        self.bar.action_button_condition_fn = None
        self.bar.is_running = False
        self.bar.retract_button_visible = False
        self._clear_bar_display()
        self._reset_servo_watch_callback()
        
        if self.app.connected:
            self.app.device['assistedThreadingData']['threadReset'] = 1
        self._stop_servo()
        

    def goto_step(self, index):
        self.current_step = index
        if 0 <= index < len(self._steps):
            self._steps[index]()
        else:
            self.stop()
    
    def goto_next_step(self, *args):
        # call the callback; it may return False to tell us "do not auto-advance"
        result = None
        if self._current_callback:
            result = self._current_callback(*args)

        # If callback returned exactly False => callback will handle advancement later
        if result is False:
            return

        if self.bar.is_running:  # check to ensure still running and we didn't stop in the callback
            self.goto_step(self.current_step + 1)

    def set_instruction(self, label_text, next_button_text, next_button_callback, value_button_fn=None, action_button_condition_fn=None, retract_button_visible=False):
        self.bar.label_text = label_text
        self.bar.next_button_text = next_button_text
        self._current_callback = next_button_callback
        self.bar.bind_btn_value_on_release(value_button_fn)
        self.bar.action_button_condition_fn = action_button_condition_fn
        self.bar.retract_button_visible = retract_button_visible
        
    def start_retracting(self):        
        log.info("Retract button pressed")
        self.bar.action_button_enabled = False  # disable action button while retracting
        
        if not self.app.connected:
            return
        self.bar.bind_display_value_to_servo_position() # bind to servo position
        servo_direction = 1 if self.servo.ratioNum * self.servo.ratioDen > 0 else -1
        self.servo.jogSpeed = - servo_direction * self.bar.reversing_speed # set to reversing speed
        self.servo.servoEnable = 2
    
    def stop_retracting(self):
        log.info("Retract button released")
        self.bar.action_button_enabled = True  # re-enable action button
        self.bar.bind_display_value_to_scale(self.cross_slide_scale)
        self.bar.update_action_button_state()
        
        if not self.app.connected:
            return
        self.servo.jogSpeed = 0
        self.servo.set_max_speed(self.servo.maxSpeed)
        self.servo.servoEnable = 1  # back to normal servo mode
    
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
        self._clear_bar_display()
    
    #Step 5
    def _step_engage_half_nut(self):
        self.set_instruction("Engage half nut and press Next", "Next", None)
        self._clear_bar_display()
    
    #Step 6       
    def _step_go_to_start(self):
        self.bar.action_button_enabled = False  # Disable until valid
        self.servo.servoEnable = 1  # Ensure servo enabled
        self.set_instruction("Confirm cross slide retracted and press Go to return to start position", "Go", self._go_to_start, None, self._is_cross_slide_retracted)
        self.bar.bind_display_value_to_scale(self.cross_slide_scale)
        self.bar.update_action_button_state()
     
    #Step 7
    def _step_cut_thread(self):
        self.bar.action_button_enabled = False  # Disable until valid
        self.set_instruction("Go to cutting depth and press Cut to start threading operation", "Cut", self._start_threading_operation, None, self._is_cross_slide_at_cutting_depth, True)
        self.bar.bind_display_value_to_scale(self.cross_slide_scale)
        self.bar.update_action_button_state()
    
    #Step 8
    def _step_depth_reached(self):
        self.bar.action_button_enabled = False  # Disable until valid
        self.set_instruction("Final depth reached. Cut more? Press Stop to quit.", "Cut", self._start_threading_operation, None, self._is_cross_slide_at_cutting_depth, True)
        self.bar.bind_display_value_to_scale(self.cross_slide_scale)
        self.bar.update_action_button_state()
    
    # Step callbacks    
    # Step 1
    def _capture_initial_position(self, *args):
        self.bar.start_position = self.saddle_scale.encoderCurrent
        self._isStartPositionMetricMode = self.app.formats.current_format == "MM"
        self._startScaledPosition = self.saddle_scale.scaledPosition
        log.info(f"Initial position set to: {self.bar.start_position}")
        return True  # advance to next step
        
    #Step 2
    def _capture_stop_position(self, *args):         
        self.bar.stop_position = self._get_stop_position_units()
        self.manual_stop_length = None  # reset for next run
        log.info(f"Stop position set - (start={self.bar.start_position}, stop={self.bar.stop_position})")
        return True  # advance to next step
            
    #Step 3
    def _capture_material_width_position(self, *args):
        self.bar.material_width = self.cross_slide_scale.encoderCurrent
        self.bar.last_cutting_depth = self.bar.material_width  # Initialize last_cutting_depth to material_width
        self._isMaterialWidthPositionMetricMode = self.app.formats.current_format == "MM"
        self._materialWidthScaledPosition = self.cross_slide_scale.scaledPosition
        log.info(f"Material width set to: {self.bar.material_width}")
        return True  # advance to next step
    
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
        return True  # advance to next step      
    
    #Step 6
    def _go_to_start(self, *args):
        if not self.app.connected:
            self.stop()
            return False # tell goto_next_step not to advance immediately

        log.info(f"Moving to start position: {self.bar.start_position} + retraction")

        # --- Delta to move the servo ---
        delta_steps = self._get_start_position_servo_delta_steps()
        log.info(f"Calculated delta steps to start position: {delta_steps}")
        
        _next_step: int
        # --- Direction model (same as servo delta) ---
        effective_dir = self._get_saddle_scale_effective_dir()
        retraction_dir = - effective_dir  # retraction is opposite to cutting direction

        retraction = abs(self._get_retraction_distance_encoder_steps())

        # Are we already beyond the retracted start?
        already_retracted = (
            (self.saddle_scale.encoderCurrent - self.bar.start_position)
            * retraction_dir >= retraction
        )
        log.info(f"Already retracted check: current={self.saddle_scale.encoderCurrent}, start={self.bar.start_position}, retraction={retraction}, effective_dir={effective_dir}, retraction_dir={retraction_dir} => {already_retracted}")

        if already_retracted:
            log.info("Saddle already beyond retracted start; taking out backlash")

            # Move further in SAME effective direction
            delta_steps += int(
                effective_dir * retraction
                * Fraction(self.saddle_scale.ratioNum, self.saddle_scale.ratioDen)
                / Fraction(self.servo.ratioNum, self.servo.ratioDen)
            )

            _next_step = self.current_step # Use this same step again
        else:
            _next_step = self.current_step + 1 # Step 7
            # Check if at cutting depth
            if self._is_cross_slide_at_final_cutting_depth():
                _next_step += 1  # skip cutting step and go to step 8 (depth reached)
        
        # --- Issue servo move ---
        self.bar.bind_display_value_to_servo_position()
        self.servo.set_max_speed(self.bar.reversing_speed)
        self.app.device['servo']['direction'] = delta_steps

        self._servo_watch_callback = lambda *a: self._check_servo_retract_done(_next_step, *a)
        self.app.bind(update_tick=self._servo_watch_callback)

        return False # tell goto_next_step not to advance immediately
     
    #Step 7 - TODO test this
    def _start_threading_operation(self, *args):
        if not self.app.connected:
            self.stop()
            return False # tell goto_next_step not to advance immediately

        # --- Determine effective direction ---
        effective_dir = self._get_saddle_scale_effective_dir()
        retraction_dir = - effective_dir  # retraction is opposite to cutting direction
        retraction_distance = abs(self._get_retraction_distance_encoder_steps())
        backlash_cushion = abs(self._get_backlash_cusion_encoder_steps())

        # Compute the desired encoder position at start including retraction
        desired_position = self.bar.start_position + retraction_dir * retraction_distance

        log.info(
            f"Validating start position: current={self.saddle_scale.encoderCurrent}, "
            f"desired={desired_position} (start={self.bar.start_position}, retraction={retraction_distance}, "
            f"backlash_cushion={backlash_cushion}, retraction_dir={retraction_dir})"
        )

        lower_bound = desired_position - backlash_cushion
        upper_bound = desired_position + backlash_cushion
        # Check if we are within backlash cushion
        if not (lower_bound <= self.saddle_scale.encoderCurrent <= upper_bound):
            _warning = (
                "Not at valid start position including backlash cushion. "
                "Aborting threading operation. Go back to start position."
            )
            log.warning(_warning)

            def _acknowledge_warning():
                self.goto_step(5)  # go back to Step 6 - Go to start

            popup = CustomPopup(
                title="Warning",
                message=_warning,
                button_text="Got it",
                on_dismiss_callback=_acknowledge_warning
            )
            popup.open()
            return False  # tell goto_next_step not to advance immediately

        log.info("Starting threaded cut to stop position: %s", self.bar.stop_position)
        self.bar.last_cutting_depth = self.cross_slide_scale.encoderCurrent  # Update last cutting depth to current position

        self.bar.bind_display_value_to_servo_position() # Bind UI to servo position so progress/pos displays scaledPosition
        self.bar.action_button_enabled = False  # Disable action button during threading
        self.bar.retract_button_visible = False  # Hide retract button during threading
        
        # Write the fields into firmware via modbus/device wrapper
        dev = self.app.device
        
        # Request latch+wait. Firmware will latch current spindle phase and wait until matched.
        if (self._threading_started is False):
            # First time starting threading - latch phase and enable
            self._threading_started = True
            self._calculated_threading_delta_steps = self._get_threading_servo_delta_steps() # Calculate threading delta steps - we only calculate it once including backlash
            dev['assistedThreadingData']['threadRemainingSteps'] = self._calculated_threading_delta_steps
            dev['assistedThreadingData']['threadRequest'] = 1
        else:
            dev['assistedThreadingData']['threadRemainingSteps'] = self._calculated_threading_delta_steps
            dev['assistedThreadingData']['threadEnabled'] = 1 # Continue threading from previous state
        
        log.info(f"Threading requested: threadRemainingSteps={dev['assistedThreadingData']['threadRemainingSteps']}, servoCurrent={self.app.fast_data_values['servoCurrent']}, calculatedDeltaSteps={self._calculated_threading_delta_steps}")
        
        # Watch until done - then go back to step 6 (Go to start)
        self._servo_watch_callback = lambda *a: self._check_servo_threading_done(5, *a)
        self.app.bind(update_tick=self._servo_watch_callback)

        return False  # tell goto_next_step not to advance immediately
        
            
    #Step Action button condition functions
    #Step 2
    def _is_valid_stop_position(self):
        """Check if the stop position is valid given the start position and thread direction.
         - For right-hand threads, stop must be less than start.
         - For left-hand threads, stop must be greater than start.
         - Stop position must be greater than the backlash retraction distance from start position so as to take out backlash when retracted further than start position
         - Depending on sign of the scale ratioNum/ratioDen, this will also affect the calculation"""
        
        effective_dir = self._get_saddle_scale_effective_dir()
        retraction = abs(self._get_retraction_distance_encoder_steps())
        stop = self._get_stop_position_units()
        min_stop = self.bar.start_position + effective_dir * retraction
        return (stop - min_stop) * effective_dir > 0
    
    #Step 4
    def _is_valid_cutting_depth_position(self):
        """Check if the cutting depth is valid given the material width position and if it's internal/external thread.
        - For internal threads, cutting depth must be greater than material width.
        - For external threads, cutting depth must be less than material width."""
        # Physical cutting direction
        effective_dir = self._get_cross_slide_scale_effective_dir()
        target_depth = self._convert_position_units_to_encoder(
            self.cross_slide_scale,
            self.manual_cutting_depth,
            self._isMaterialWidthPositionMetricMode,
            self._materialWidthScaledPosition,
            self.bar.material_width
        )
        return (target_depth - self.bar.material_width) * effective_dir > 0

    #Step 6
    def _is_cross_slide_retracted(self):
        """
        Check if the cross slide is safely retracted when the saddle has moved beyond the threading start position.
        """
        log.info("Checking if cross slide is retracted for threading start...")

        # --- Saddle direction check (Z axis) ---
        saddle_dir = self._get_saddle_scale_effective_dir()

        saddle_delta = self.saddle_scale.encoderCurrent - self.bar.start_position
        saddle_beyond_start = saddle_delta * saddle_dir > 0

        if not saddle_beyond_start:
            log.info("Saddle is not beyond start position, no need to check cross slide")
            return True

        log.info("Saddle is beyond start position, checking cross slide retraction")

        # --- Cross-slide retraction check (X axis) ---
        retract_dir = self._get_cross_slide_scale_effective_dir()

        cross_delta = self.cross_slide_scale.encoderCurrent - self.bar.material_width
        return cross_delta * retract_dir > 0

    
    #Step 7
    def _is_cross_slide_at_cutting_depth(self):
        """Check if the cross slide is at the cutting depth position, considering thread type and scale direction."""
        effective_dir = self._get_cross_slide_scale_effective_dir()
        log.info(f"Checking if cross slide reached cutting depth: current={self.cross_slide_scale.encoderCurrent}, target={self.bar.cutting_depth}, effective_dir={effective_dir}")
        
        # Check: has cross slide reached or passed the target depth?
        return (self.cross_slide_scale.encoderCurrent - self.bar.last_cutting_depth) * effective_dir >= 0

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
        """Get the retraction distance in encoder counts."""
        return self._convert_distance_units_to_encoder(self.saddle_scale, self.bar.backlash_retraction_distance, self.bar.metric_distances)
    
    def _get_backlash_cusion_encoder_steps(self) -> int:
        """Get the backlash cushion distance in encoder counts."""
        return self._convert_distance_units_to_encoder(self.saddle_scale, self.bar.backlash_cushion, self.bar.metric_distances)

    def _check_servo_retract_done(self, next_step: int, *args):
        dev = self.app.device
        desiredSteps = dev['servo']['desiredSteps']
        currentSteps = dev['servo']['currentSteps']
        servoCurrent = self.app.fast_data_values['servoCurrent']
        stepsToGo = dev['servo']['direction']
        log.info(f"Checking servo retract done: stepsToGo={stepsToGo}, desiredSteps={desiredSteps}, currentSteps={currentSteps}, servoCurrent={servoCurrent}")
        
        if self.app.fast_data_values['stepsToGo'] == 0:
            log.info("Servo reached desired start position")
            self.servo.set_max_speed(self.servo.maxSpeed)
            
            # Stop watching
            self._reset_servo_watch_callback()

            self.goto_step(next_step)

    def _check_servo_threading_done(self, next_step: int, *args):
        #TODO remove debug logs when done testing
        dev = self.app.device
        dev['assistedThreadingData'].refresh()
        threadRequest = dev['assistedThreadingData']['threadRequest']
        threadReset = dev['assistedThreadingData']['threadReset']
        threadPhaseActive = dev['assistedThreadingData']['threadPhaseActive']
        threadEnabled = dev['assistedThreadingData']['threadEnabled']        
        spindleScaleIndex = dev['assistedThreadingData']['spindleScaleIndex']
        spindleCountsPerRev = dev['assistedThreadingData']['spindleCountsPerRev']
        spindlePhaseTolerance = dev['assistedThreadingData']['spindlePhaseTolerance']        
        threadRemainingSteps = dev['assistedThreadingData']['threadRemainingSteps']
        threadStartSteps = dev['assistedThreadingData']['threadStartSteps']
        threadPhaseRef = dev['assistedThreadingData']['threadPhaseRef']
        currentThreadPhase = dev['assistedThreadingData']['currentThreadPhase']
        desiredSteps = dev['servo']['desiredSteps']
        currentSteps = dev['servo']['currentSteps']
        stepsToGo = dev['servo']['direction']
        syncEnable = dev['scales'][spindleScaleIndex]['syncEnable']
        position = dev['scales'][spindleScaleIndex]['position']
        
        log.info(
            f"Checking servo done: "
            f"spindleScaleIndex={spindleScaleIndex}, "
            f"spindleCountsPerRev={spindleCountsPerRev}, "
            f"spindlePhaseTolerance={spindlePhaseTolerance}, "
            
            f"threadRequest={threadRequest}, "
            f"threadReset={threadReset}, "
            f"threadPhaseActive={threadPhaseActive}, "
            f"threadEnabled={threadEnabled}, "
            f"syncEnable={syncEnable}, "
            
            f"threadPhaseRef={threadPhaseRef}, "
            f"currentThreadPhase={currentThreadPhase}, "
            f"spindleEncoderposition={position}, "
            
            f"threadRemainingSteps={threadRemainingSteps}, "
            f"threadStartSteps={threadStartSteps}, "
            f"desiredSteps={desiredSteps}, "
            f"currentSteps={currentSteps}, "
            f"stepsToGo={stepsToGo}, "
        )
        
        if threadEnabled == 0 and threadPhaseActive == 0:
            log.info("Servo reached desired position")
            
            # Stop watching
            self._reset_servo_watch_callback()

            self.goto_step(next_step)
    
    def _get_start_position_servo_delta_steps(self) -> int:
        """
        Compute the servo step delta needed to move the saddle
        to the retracted start position, accounting for:
        - thread handedness
        - encoder direction
        - backlash retraction
        """

        effective_dir = self._get_saddle_scale_effective_dir()
        retraction_dir = - effective_dir  # retraction is opposite to cutting direction

        retraction = abs(self._get_retraction_distance_encoder_steps())

        target_encoder = self.bar.start_position + retraction_dir * retraction
        current_encoder = self.saddle_scale.encoderCurrent

        # Convert encoder delta → servo steps
        scale_ratio = Fraction(abs(self.saddle_scale.ratioNum), abs(self.saddle_scale.ratioDen))
        servo_ratio = Fraction(self.servo.ratioNum, self.servo.ratioDen)

        delta_steps = int((target_encoder - current_encoder) * effective_dir * scale_ratio / servo_ratio)

        log.info(
            f"Computed servo delta: {delta_steps} steps "
            f"(target_enc={target_encoder}, current_enc={current_encoder}, "
            f"scale_ratio={scale_ratio}, servo_ratio={servo_ratio}, "
            f"effective_dir={effective_dir}, retraction_dir={retraction_dir})"
        )

        return delta_steps
    
    def _get_threading_servo_delta_steps(self) -> int:
        """
        Compute the servo step delta needed to move the saddle
        from the current position to the stop position
        in the cutting direction.
        """

        effective_dir = self._get_saddle_scale_effective_dir()

        current_encoder = self.saddle_scale.encoderCurrent
        target_encoder = self.bar.stop_position

        delta_enc = (target_encoder - current_encoder) * effective_dir
        if delta_enc <= 0:
            log.warning(
                "Threading delta is opposite to effective cutting direction "
                f"(current={current_encoder}, stop={target_encoder}, "
                f"effective_dir={effective_dir})"
            )

        # Convert encoder delta → servo steps
        scale_ratio = Fraction(abs(self.saddle_scale.ratioNum), abs(self.saddle_scale.ratioDen))
        servo_ratio = Fraction(self.servo.ratioNum, self.servo.ratioDen)

        delta_steps = int(delta_enc * scale_ratio / servo_ratio)

        log.info(
            f"Computed threading servo delta: {delta_steps} steps "
            f"(current_enc={current_encoder}, stop_enc={target_encoder}, "
            f"delta_enc={delta_enc}, "
            f"scale_ratio={scale_ratio}, servo_ratio={servo_ratio}, "
            f"effective_dir={effective_dir})"
        )

        return delta_steps
    
    def _is_cross_slide_at_final_cutting_depth(self):
        """Check if the cross slide is at or more than the final cutting depth position."""
        if self.bar.inner_thread:
            return self.cross_slide_scale.encoderCurrent >= self.bar.cutting_depth
        return self.cross_slide_scale.encoderCurrent <= self.bar.cutting_depth
    
    def _stop_servo(self):
        if not self.app.connected:
            return
        self.servo.set_max_speed(self.servo.maxSpeed)  # restore speed
        self.servo.servoEnable = 0  # disable
    
    def _reset_servo_watch_callback(self):
        if self._servo_watch_callback:
            self.app.unbind(update_tick=self._servo_watch_callback)
            self._servo_watch_callback = None
        
    def _clear_bar_display(self):
        self.bar.unbind_all_display_value() 
        self.bar.display_value = ""
        
    def _get_cross_slide_scale_effective_dir(self) -> int:
        """Get the cross slide effective direction, considering thread type (internal/external) and scale direction."""
        # Physical cutting direction: internal → outward (+), external → inward (-)
        thread_dir = 1 if self.bar.inner_thread else -1
        
        # Encoder direction: positive if scale ratio is positive, negative if reversed
        scale_dir = 1 if self.cross_slide_scale.ratioNum * self.cross_slide_scale.ratioDen > 0 else -1

        # Combined effective direction
        return thread_dir * scale_dir
    
    
    def _get_saddle_scale_effective_dir(self) -> int:
        """Get the saddle scale effective direction, considering if it's left/right hand tread and scale direction."""
        # Thread direction: LH → +, RH → -
        thread_dir = 1 if self.bar.left_hand_thread else -1
        
        # Scale direction from ratio sign
        scale_dir = 1 if self.saddle_scale.ratioNum * self.saddle_scale.ratioDen > 0 else -1
        
        return thread_dir * scale_dir