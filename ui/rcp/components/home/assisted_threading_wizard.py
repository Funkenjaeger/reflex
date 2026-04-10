import logging
from fractions import Fraction

from kivy.logger import Logger

from rcp.components.popups.custom_popup import CustomPopup
from rcp.components.home.thread_type import ThreadType
from rcp.utils.devices import SCALES_COUNT

log = Logger.getChild(__name__)

MM_PER_INCH = 25.4


class GoToStartPhase:
    IDLE = 0
    RETRACT = 1
    PRELOAD = 2
    ADJUST = 3


class AssistedThreadingWizard:
    @property
    def saddle_scale(self):
        """Returns the AxisDispatcher for the saddle (Z) axis."""
        return self.app.els.get_z_axis()

    @property
    def cross_slide_scale(self):
        """Returns the AxisDispatcher for the cross-slide (X) axis."""
        return self.app.els.get_x_axis()

    @property
    def saddle_input(self):
        """Returns the InputDispatcher (raw encoder) for the saddle axis."""
        axis = self.saddle_scale
        return axis._primary_input() if axis is not None else None

    @property
    def cross_slide_input(self):
        """Returns the InputDispatcher (raw encoder) for the cross-slide axis."""
        axis = self.cross_slide_scale
        return axis._primary_input() if axis is not None else None

    def __init__(self, bar):
        from rcp.app import MainApp
        log.info("Initializing AssistedThreadingWizard")
        self.bar = bar
        self.app: MainApp = MainApp.get_running_app()
        self.servo = self.app.servo
        self.current_step = 0
        self._threading_started = False
        self._threading_active_confirmed = False
        self._calculated_threading_delta_steps = 0
        self._current_callback = None
        self._servo_watch_callback = None
        self.manual_stop_length = None  
        self.manual_cutting_depth = None
        self._last_saddle_encoder_value = None
        self._start_position_preloaded = False
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
        dev = self.app.board.device
        dev['assistedThreadingData']['spindlePhaseTolerance'] = self.app.els.at_rotary_encoder_sync_tolerance
        
        spindle_axis = self.app.els.get_spindle_axis()
        if spindle_axis is not None:
            inp = spindle_axis._primary_input()
            if inp is not None:
                dev['assistedThreadingData']['spindleCountsPerRev'] = int(spindle_axis._steps_per_revolution())
                dev['assistedThreadingData']['spindleScaleIndex'] = inp.inputIndex
        
        self.goto_step(0)

    def stop(self):
        # Reset wizard_area to default content
        log.info("Wizard finished")
        self._current_callback = None
        self._threading_started = False
        self._threading_active_confirmed = False
        self.bar.label_text = ""
        self.bar.display_value = ""
        self.bar.action_button_enabled = True
        self.bar.action_button_condition_fn = None
        self.bar.is_running = False
        self.bar.retract_button_visible = False
        self._clear_bar_display()
        self._reset_servo_watch_callback()
        self._reset_encoder_stability_check()
        
        if self.app.board.connected:
            self.app.board.device['assistedThreadingData']['threadReset'] = 1
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

    def set_instruction(self, label_text, next_button_text, next_button_callback, value_button_fn=None, action_button_condition_fn=None, retract_button_visible=False, retract_button_condition_fn=None):
        self.bar.label_text = label_text
        self.bar.next_button_text = next_button_text
        self._current_callback = next_button_callback
        self.bar.bind_btn_value_on_release(value_button_fn)
        self.bar.action_button_condition_fn = action_button_condition_fn
        self.bar.retract_button_visible = retract_button_visible
        self.bar.retract_button_condition_fn = retract_button_condition_fn
      
    def start_retracting(self):        
        log.info("Retract button pressed")
        self.bar.action_button_enabled = False  # disable action button while retracting
        
        if not self.app.board.connected:
            return
        self.bar.bind_display_value_to_servo_position() # bind to servo position
        servo_direction = 1 if self.servo.ratioNum * self.servo.ratioDen > 0 else -1
        self.servo.jogSpeed = - servo_direction * self.app.els.at_reversing_speed # set to reversing speed
        self._apply_reversing_adjusting_acceleration()
        self.servo.set_max_speed(self.app.els.at_reversing_speed)  # ensure step rate supports jog speed
        self.servo.servoEnable = 2
    
    def stop_retracting(self):
        log.info("Retract button released")
        self.bar.action_button_enabled = True  # re-enable action button
        self.bar.bind_display_value_to_scale(self.cross_slide_scale)
        self.bar.update_buttons_state()
        
        if not self.app.board.connected:
            return
        self.servo.jogSpeed = 0
        
        self._servo_watch_callback = self._watch_retracting_stopped
        self.app.board.bind(update_tick=self._servo_watch_callback)
    
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
        self._clear_bar_display()
        
        # Calculate thread depth and show immediately
        calculated_depth = self._calculate_thread_depth()
        self.manual_cutting_depth = None  # Reset manual override
        if calculated_depth is not None:
            is_metric = self.app.formats.current_format == "MM"
            self.bar.display_value = f"{calculated_depth:.3f}" if is_metric else f"{calculated_depth:.4f}"
        else:
            self.bar.display_value = ""
            
        self.set_instruction(
            "Enter Final Cutting Depth (auto-calculated shown, tap to override)",
            "Set",
            self._capture_final_cutting_depth_position,
            self._open_final_cutting_depth_position_keypad
        )
    
    #Step 5
    def _step_engage_half_nut(self):
        self.set_instruction("Engage half nut and press Next", "Next", None)
        self._clear_bar_display()
    
    #Step 6       
    def _step_go_to_start(self):
        self.bar.action_button_enabled = False  # Disable until valid
        self.bar.retract_button_enabled = False  # Disable until valid
        self.servo.servoEnable = 1  # Ensure servo enabled
        self.set_instruction("Confirm cross slide retracted and press Go to return to start position", "Go", self._go_to_start, None, self._is_cross_slide_retracted, True, self._is_cross_slide_retracted)
        self.bar.bind_display_value_to_scale(self.cross_slide_scale)
        self.bar.update_buttons_state()
     
    #Step 7
    def _step_cut_thread(self):        
        self.bar.action_button_enabled = False  # Disable until valid
        self.bar.retract_button_enabled = False  # Disable until valid
        self.set_instruction("Go to cutting depth and press Cut to start threading operation", "Cut", self._start_threading_operation, None, None, True)
        self._bind_threading_progress_display()  # Bind to progress display
        self.bar.update_buttons_state()
    
    #Step 8
    def _step_depth_reached(self):
        self.bar.action_button_enabled = False  # Disable until valid
        self.bar.retract_button_enabled = False  # Disable until valid
        self.set_instruction("Final depth reached. Cut more? Press Stop to quit.", "Cut", self._start_threading_operation, None, None, True)
        self._bind_threading_progress_display()  # Bind to progress display
        self.bar.update_buttons_state()
    
    # Step callbacks    
    # Step 1
    def _capture_initial_position(self, *args):
        self.bar.start_position = self.saddle_input.encoderCurrent
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
        self.bar.material_width = self.cross_slide_input.encoderCurrent
        self.bar.last_cutting_depth = self.bar.material_width  # Initialize last_cutting_depth to material_width
        self._isMaterialWidthPositionMetricMode = self.app.formats.current_format == "MM"
        self._materialWidthScaledPosition = self.cross_slide_scale.scaledPosition
        log.info(f"Material width set to: {self.bar.material_width}")
        return True  # advance to next step
    
    #Step 4
    def _capture_final_cutting_depth_position(self, *args):
        # Use manual override if set, otherwise use calculated depth
        is_metric = self.app.formats.current_format == "MM"
        depth = self.manual_cutting_depth if self.manual_cutting_depth is not None else self._calculate_thread_depth()
        encoder_cutting_depth = self._convert_distance_units_to_encoder(self.cross_slide_scale, depth, is_metric)
        
        self.bar.cutting_depth = self.cross_slide_input.encoderCurrent - (encoder_cutting_depth * self._get_cross_slide_scale_effective_dir())
        
        
        log.info(f"Cutting depth set: {depth} (manual_override={self.manual_cutting_depth is not None})")
        self.bar.display_value = f"{depth:.3f}" if is_metric else f"{depth:.4f}"
        return True  # advance to next step
    
    #Step 6
    def _go_to_start(self, *args):
        if not self.app.board.connected:
            self.stop()
            return False
        
        self.bar.retract_button_enabled = False  # Disable retract button during move to start
        self.bar.action_button_enabled = False  # Disable action button during move to start

        self._apply_reversing_adjusting_acceleration()
        self._start_position_preloaded = False
        self._goto_start_phase = GoToStartPhase.RETRACT

        effective_dir = self._get_saddle_scale_effective_dir()

        retraction = abs(self._get_saddle_backlash_distance_encoder_steps() * 1.5)  # retract 1.5x backlash distance
        retraction_dir = -effective_dir  # retract opposite to cutting direction
        log.info(f"Starting retract to go to start: effective_dir={effective_dir}, retraction={retraction}, retraction_dir={retraction_dir}")
        retract_target = self.bar.start_position + retraction_dir * retraction

        self._command_move_to_encoder(retract_target, speed=self.app.els.at_reversing_speed)

        self._servo_watch_callback = self._watch_go_to_start
        self.app.board.bind(update_tick=self._servo_watch_callback)

        return False
     
    #Step 7
    def _start_threading_operation(self, *args):
        if not self.app.board.connected:
            self.stop()
            return False # tell goto_next_step not to advance immediately

        if not self._start_position_preloaded:
            log.warning("Threading requested without start preload")
            self.goto_step(5)
            return False

        if not self._check_valid_start_position():
            return False

        if not self._check_spindle_turning_forward():
            return False

        if not self._check_spindle_speed_for_pitch():
            return False

        log.info("Starting threaded cut to stop position: %s", self.bar.stop_position)
        self.bar.last_cutting_depth = self.cross_slide_input.encoderCurrent  # Update last cutting depth to current position

        self._apply_threading_acceleration()
        self._apply_threading_max_speed()
        self.bar.bind_display_value_to_servo_position() # Bind UI to servo position so progress/pos displays scaledPosition
        self.bar.action_button_enabled = False  # Disable action button during threading
        self.bar.retract_button_visible = False  # Hide retract button during threading
        
        # Write the fields into firmware via modbus/device wrapper
        dev = self.app.board.device
        
        # Request latch+wait. Firmware will latch current spindle phase and wait until matched.
        if (self._threading_started is False):
            # First time starting threading - latch phase and enable
            self._threading_started = True
            self._threading_active_confirmed = False
            self._calculated_threading_delta_steps = self._get_threading_servo_delta_steps() # Calculate threading delta steps - we only calculate it once including backlash
            dev['assistedThreadingData']['threadRemainingSteps'] = self._calculated_threading_delta_steps
            dev['assistedThreadingData']['threadRequest'] = 1
        else:
            self._threading_active_confirmed = False
            dev['assistedThreadingData']['threadRemainingSteps'] = self._calculated_threading_delta_steps
            dev['assistedThreadingData']['threadEnabled'] = 1 # Continue threading from previous state
        
        log.info(f"Threading requested: threadRemainingSteps={dev['assistedThreadingData']['threadRemainingSteps']}, servoCurrent={self.app.board.fast_data_values['servoCurrent']}, calculatedDeltaSteps={self._calculated_threading_delta_steps}")
        
        # Watch until done - then go back to step 6 (Go to start)
        self._servo_watch_callback = lambda *a: self._check_servo_threading_done(5, *a)
        self.app.board.bind(update_tick=self._servo_watch_callback)

        return False  # tell goto_next_step not to advance immediately
    

    #Step Action button condition functions
    #Step 2
    def _is_valid_stop_position(self):
        """Check if the stop position is valid given the start position and thread direction.
         - For right-hand threads, stop must be less than start.
         - For left-hand threads, stop must be greater than start.
         - Stop position must be greater than the backlash cushion distance from start position - if stop is too small, the saddle may not have enough room to cut properly.
         - Depending on sign of the scale ratioNum/ratioDen, this will also affect the calculation"""
        
        effective_dir = self._get_saddle_scale_effective_dir()
        backlash_cushion = abs(self._get_backlash_cusion_encoder_steps())
        stop = self._get_stop_position_units()
        min_stop = self.bar.start_position + effective_dir * backlash_cushion
        return (stop - min_stop) * effective_dir > 0
    

    #Step 6
    def _is_cross_slide_retracted(self):
        """
        Check if the cross slide is safely retracted when the saddle has moved beyond the threading start position.
        """
        log.debug("Checking if cross slide is retracted for threading start...")

        # --- Saddle direction check (Z axis) ---
        saddle_dir = self._get_saddle_scale_effective_dir()

        saddle_delta = self.saddle_input.encoderCurrent - self.bar.start_position
        saddle_beyond_start = saddle_delta * saddle_dir > 0

        if not saddle_beyond_start:
            log.debug("Saddle is not beyond start position, no need to check cross slide")
            return True

        log.debug("Saddle is beyond start position, checking cross slide retraction")

        # --- Cross-slide retraction check (X axis) ---
        retract_dir = -self._get_cross_slide_scale_effective_dir()

        cross_delta = self.cross_slide_input.encoderCurrent - self.bar.material_width
        return cross_delta * retract_dir > 0

    # Manual input handlers
    def _open_stop_position_keypad(self, *args):
        from rcp.components.popups.keypad import Keypad
        
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
                self.bar.update_buttons_state()

        keypad.show_with_callback(callback_fn=on_done,
                                current_value=self.manual_stop_length or 0.0)
    
    def _open_final_cutting_depth_position_keypad(self, *args):
        from rcp.components.popups.keypad import Keypad
        is_metric = self.app.formats.current_format == "MM"
        # Always use calculated depth as default
        calculated_depth = self._calculate_thread_depth()
        default_value = calculated_depth if calculated_depth is not None else 0.0
        depth_unit = "mm" if is_metric else "in"
        keypad = Keypad(title=f"Enter Final Cutting Depth ({depth_unit})")
        keypad.integer = False
        def on_done(value):
            try:
                self.manual_cutting_depth = abs(float(value))
                log.info(f"Manual cutting depth entered: {self.manual_cutting_depth}")
                self.bar.display_value = f"{self.manual_cutting_depth:.3f}" if is_metric else f"{self.manual_cutting_depth:.4f}"
                self.bar.action_button_enabled = True
            except ValueError:
                log.warning(f"Invalid cutting depth input: {value}")                
                self.bar.action_button_enabled = False

        log.info(f"Opening cutting depth keypad with calculated default: {default_value:.4f}")
        keypad.show_with_callback(callback_fn=on_done,
                                current_value=self.manual_cutting_depth if self.manual_cutting_depth is not None else default_value)
        
    # Utilities
    def _convert_position_units_to_encoder(self,
                                                scale,
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

        # delta_in_start_units is already relative to the start position — offsets do not apply
        inp = scale._primary_input()
        encoder_counts = (delta_in_start_units / factor_at_start_position) * (float(inp.ratioDen) / float(inp.ratioNum))

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
        log.info(f"Using live encoder value: {self.saddle_input.encoderCurrent}")
        return self.saddle_input.encoderCurrent

    def _convert_distance_units_to_encoder(self, scale, distance: float, is_metric: bool) -> int:
        """
        Convert a pure distance (mm or inch) into encoder counts.
        scale: AxisDispatcher
        """
        inp = scale._primary_input()
        encoder_factor = float(self.app.formats.MM_FRACTION if is_metric else self.app.formats.INCHES_FRACTION)

        # Pure distance conversion — offsets do not apply (those are DRO zero offsets for positions, not distances)
        encoder_counts = (distance / encoder_factor) * (float(inp.ratioDen) / float(inp.ratioNum))

        final_encoder_distance = int(round(encoder_counts))

        log.info(
            f"Converted distance to encoder counts: {final_encoder_distance} "
            f"(input distance={distance}, encoder delta={encoder_counts})"
        )

        return final_encoder_distance

    def _get_saddle_backlash_distance_encoder_steps(self) -> int:
        """Get the retraction distance in encoder counts."""
        return self._convert_distance_units_to_encoder(self.saddle_scale, self.app.els.at_saddle_backlash_distance, self.app.els.at_metric_distances)
    
    def _get_backlash_cusion_encoder_steps(self) -> int:
        """Get the backlash cushion distance in encoder counts."""
        return self._convert_distance_units_to_encoder(self.saddle_scale, self.app.els.at_backlash_cushion, self.app.els.at_metric_distances)

    def _check_servo_threading_done(self, next_step: int, *args):
        dev = self.app.board.device       
        dev['assistedThreadingData'].refresh() 
        threadPhaseActive = dev['assistedThreadingData']['threadPhaseActive']
        threadEnabled = dev['assistedThreadingData']['threadEnabled']     
        
        if log.isEnabledFor(logging.DEBUG):
            spindleScaleIndex = dev['assistedThreadingData']['spindleScaleIndex']
            log.debug(
                f"Checking servo done: "
                f"spindleScaleIndex={spindleScaleIndex}, "
                f"spindleCountsPerRev={dev['assistedThreadingData']['spindleCountsPerRev']}, "
                f"spindlePhaseTolerance={dev['assistedThreadingData']['spindlePhaseTolerance']}, "
                f"threadRequest={dev['assistedThreadingData']['threadRequest']}, "
                f"threadReset={dev['assistedThreadingData']['threadReset']}, "
                f"threadPhaseActive={threadPhaseActive}, "
                f"threadEnabled={threadEnabled}, "
                f"syncEnable={dev['scales'][spindleScaleIndex]['syncEnable']}, "
                f"threadPhaseRef={dev['assistedThreadingData']['threadPhaseRef']}, "
                f"currentThreadPhase={dev['assistedThreadingData']['currentThreadPhase']}, "
                f"spindleEncoderPosition={dev['scales'][spindleScaleIndex]['position']}, "
                f"threadRemainingSteps={dev['assistedThreadingData']['threadRemainingSteps']}, "
                f"threadStartSteps={dev['assistedThreadingData']['threadStartSteps']}, "
                f"desiredSteps={dev['servo']['desiredSteps']}, "
                f"currentSteps={dev['servo']['currentSteps']}, "
            )
        
        if threadEnabled == 1 or threadPhaseActive == 1:
            self._threading_active_confirmed = True

        if self._threading_active_confirmed and threadEnabled == 0 and threadPhaseActive == 0:
            log.info("Servo reached desired position")
            
            # Stop watching
            self._reset_servo_watch_callback()

            self.goto_step(next_step)
    
    def _get_threading_servo_delta_steps(self) -> int:
        """
        Compute the servo step delta needed to move the saddle
        from the current position to the stop position
        in the cutting direction.
        """

        effective_dir = self._get_saddle_scale_effective_dir()

        current_encoder = self.saddle_input.encoderCurrent
        target_encoder = self.bar.stop_position

        delta_enc = target_encoder - current_encoder
        if delta_enc * effective_dir <= 0:
            log.warning(
                "Threading delta is opposite to effective cutting direction "
                f"(current={current_encoder}, stop={target_encoder}, "
                f"effective_dir={effective_dir})"
            )

        # Convert encoder delta → servo steps
        scale_ratio = Fraction(abs(self.saddle_input.ratioNum), abs(self.saddle_input.ratioDen))
        servo_ratio = Fraction(abs(self.servo.ratioNum), abs(self.servo.ratioDen))

        delta_steps = int(delta_enc * scale_ratio / servo_ratio)

        log.info(
            f"Computed threading servo delta: {delta_steps} steps "
            f"(current_enc={current_encoder}, stop_enc={target_encoder}, "
            f"delta_enc={delta_enc}, "
            f"scale_ratio={scale_ratio}, servo_ratio={servo_ratio}, "
            f"effective_dir={effective_dir})"
        )

        return delta_steps
    
    def _calculate_thread_depth(self):
        """
        Calculate thread depth based on selected pitch and thread profile type.
        
        Uses metric_mode to determine if selected_pitch is in mm or TPI.
        Formulas provided are for radial depth; multiply by 2 if diameter mode is enabled.
        
        Returns:
            Thread depth in the selected units (mm or inches), or None if invalid
        """
        if not self.bar.selected_pitch:
            log.warning("No pitch selected for depth calculation")
            return None
        
        # Determine effective pitch based on metric_mode
        try:
            if self.bar.metric_mode:
                # In metric mode, selected_pitch is the pitch in mm
                pitch = float(self.bar.selected_pitch)
            else:
                # In imperial mode, selected_pitch is TPI (threads per inch)
                # Convert TPI to pitch in inches
                tpi = float(self.bar.selected_pitch)
                pitch = MM_PER_INCH / tpi
        except (ValueError, TypeError):
            log.warning(f"Could not parse pitch from: {self.bar.selected_pitch}")
            return None
        
        if pitch <= 0:
            log.warning(f"Invalid pitch value: {pitch}")
            return None
        
        # Determine thread profile and calculate radial depth
        thread_type = ThreadType(self.bar.thread_profile_type)

        if thread_type == ThreadType.ISO_METRIC:
            depth = 0.61343 * pitch
        elif thread_type == ThreadType.UNIFIED:
            depth = 0.64952 * pitch
        elif thread_type == ThreadType.WHITWORTH:
            depth = 0.6403 * pitch
        elif thread_type == ThreadType.ACME:
            depth = 0.5 * pitch
        else:
            log.warning(f"Unknown thread profile: {thread_type}")
            return None
        
        # Account for cross-slide diameter mode
        # Formulas are for radial depth; in diameter mode multiply by 2
        if self.app.els.at_cross_slide_diameter_mode:
            depth = depth * 2
        
        # Convert depth to match current display format if needed
        is_current_format_metric = self.app.formats.current_format == "MM"
        if self.bar.metric_mode and not is_current_format_metric:
            # Calculated in mm but displaying in inches
            depth = depth / MM_PER_INCH
        elif not self.bar.metric_mode and is_current_format_metric:
            # Calculated in inches but displaying in mm
            depth = depth * MM_PER_INCH
        
        log.info(f"Calculated thread depth: {depth:.4f} (pitch={pitch:.4f}, type={thread_type}, metric_mode={self.bar.metric_mode}, current_format={'MM' if is_current_format_metric else 'IN'}, diameter_mode={self.app.els.at_cross_slide_diameter_mode})")
        return depth
      

    def _is_cross_slide_at_final_cutting_depth(self):
        """Check if the cross slide is at or more than the final cutting depth position."""
        effective_dir = self._get_cross_slide_scale_effective_dir()
        current = self.cross_slide_input.encoderCurrent
        log.info(f"Checking if at cutting depth: last_cutting_depth={self.bar.last_cutting_depth}, cutting_depth={self.bar.cutting_depth}, effective_dir={effective_dir}")
        return (self.bar.last_cutting_depth - self.bar.cutting_depth) * effective_dir >= 0
    
    def _stop_servo(self):
        if not self.app.board.connected:
            return
        self.servo.set_max_speed(self.servo.maxSpeed)  # restore speed
        self.servo.servoEnable = 0  # disable
        self._apply_original_servo_acceleration()  # restore original acceleration if it was changed
    
    def _reset_servo_watch_callback(self):
        if self._servo_watch_callback:
            self.app.board.unbind(update_tick=self._servo_watch_callback)
            self._servo_watch_callback = None
        
    def _clear_bar_display(self):
        self.bar.unbind_all_display_value() 
        self.bar.display_value = ""
        
    def _apply_original_servo_acceleration(self):
        self.app.board.device['servo']['acceleration'] = self.servo.acceleration

    def _apply_reversing_adjusting_acceleration(self):
        rate = self.app.els.at_reversing_adjusting_acceleration
        if rate and rate > 0:
            self.app.board.device['servo']['acceleration'] = rate
        else:
            self._apply_original_servo_acceleration()

    def _apply_threading_acceleration(self):
        rate = self.app.els.at_threading_acceleration
        if rate and rate > 0:
            self.app.board.device['servo']['acceleration'] = rate
        else:
            self._apply_original_servo_acceleration()

    def _apply_threading_max_speed(self):
        target_speed = self.app.els.at_threading_max_speed
        if target_speed and target_speed > 0:
            self.servo.set_max_speed(target_speed)
        else:
            self.servo.set_max_speed(self.servo.maxSpeed)

    def _bind_threading_progress_display(self):
        """
        Bind display to show threading progress: "Last: <incremental_cut> | Rem: <remaining>"
        where:
        - Last = incremental cut since last_cutting_depth
        - Rem = remaining distance until final thread depth
        """
        self.bar.unbind_all_display_value()
        self._progress_display_scale = self.cross_slide_input
        def on_cross_slide_update(instance, value):
            try:
                is_metric = self.app.formats.current_format == "MM"
                current_encoder = self.cross_slide_input.encoderCurrent
                last_cutting_depth_encoder = self.bar.last_cutting_depth
                factor = float(self.app.formats.factor)
                
                scale_ratio = abs(Fraction(self.cross_slide_input.ratioNum, self.cross_slide_input.ratioDen) * factor)
                
                # Calculate incremental cut depth in encoder units
                incremental_cut_encoder = last_cutting_depth_encoder - current_encoder if self.bar.inner_thread else current_encoder - last_cutting_depth_encoder
            
                incremental_cut_display = incremental_cut_encoder * scale_ratio
                # Calculate remaining depth
                final_depth_encoder =  current_encoder - self.bar.cutting_depth if self.bar.inner_thread else self.bar.cutting_depth - current_encoder
                remaining_display = final_depth_encoder * scale_ratio
            
                if is_metric:
                    self.bar.display_value = f"Last: {incremental_cut_display:.3f} | Rem: {remaining_display:.3f}"
                else:
                    self.bar.display_value = f"Last: {incremental_cut_display:.4f} | Rem: {remaining_display:.4f}"
                log.debug(f"Threading progress: incremental_cut={incremental_cut_display:.4f}, remaining={remaining_display:.4f}")
            except Exception as e:
                log.error(f"Error updating threading progress display: {e}")
        self._on_threading_progress_update = on_cross_slide_update
        self.cross_slide_input.bind(encoderCurrent=on_cross_slide_update)
        on_cross_slide_update(self.cross_slide_input, self.cross_slide_input.encoderCurrent)
        
    def _get_cross_slide_scale_effective_dir(self) -> int:
        """Get the cross slide effective direction, considering thread type (internal/external) and scale direction."""
        # Physical cutting direction: internal → outward (+), external → inward (-)
        thread_dir = 1 if self.bar.inner_thread else -1
        
        # Encoder direction: positive if scale ratio is positive, negative if reversed
        scale_dir = 1 if self.cross_slide_input.ratioNum * self.cross_slide_input.ratioDen > 0 else -1

        # Combined effective direction
        return thread_dir * scale_dir
    
    
    def _get_saddle_scale_effective_dir(self) -> int:
        """Get the saddle scale effective direction, considering if it's left/right hand tread and scale direction."""
        # Thread direction: LH → +, RH → -
        thread_dir = 1 if self.bar.left_hand_thread else -1
        
        # Scale direction from ratio sign
        scale_dir = 1 if self.saddle_input.ratioNum * self.saddle_input.ratioDen > 0 else -1
        
        return thread_dir * scale_dir
    
    def _command_move_to_encoder(self, target_encoder, speed):
        self._reset_encoder_stability_check()

        current_enc = self.saddle_input.encoderCurrent

        scale_ratio = Fraction(abs(self.saddle_input.ratioNum), abs(self.saddle_input.ratioDen))
        servo_ratio = Fraction(abs(self.servo.ratioNum), abs(self.servo.ratioDen))

        delta = int((target_encoder - current_enc) * scale_ratio / servo_ratio)

        log.info(
            f"Move to encoder: current={current_enc}, "
            f"target={target_encoder}, delta={delta}"
        )

        self.bar.bind_display_value_to_servo_position()
        self.servo.set_max_speed(speed)
        self.app.board.device['servo']['direction'] = delta
    
    def _watch_retracting_stopped(self, *_):
        if not self._encoder_is_stable(self.app.els.at_saddle_encoder_stability_tolerance, self.app.els.at_saddle_encoder_stability_samples):
            return
        
        self._reset_servo_watch_callback()
        self.servo.set_max_speed(self.servo.maxSpeed)
        self.servo.servoEnable = 1  # back to normal servo mode
        
        self.goto_step(5)  # Go back to step 6 - Go to start position
        
        
    def _watch_go_to_start(self, *_):
        if not self._motion_complete():
            return

        if self._goto_start_phase == GoToStartPhase.RETRACT:
            self._start_preload_move()

        elif self._goto_start_phase == GoToStartPhase.PRELOAD:
            self._start_adjust_move()
             
        elif self._goto_start_phase == GoToStartPhase.ADJUST:
            self._finish_go_to_start()
    
    def _reset_encoder_stability_check(self):
        self._last_saddle_encoder_value = None
        self._stable_count = 0
    
    def _encoder_is_stable(self, tolerance, samples):
        current = self.saddle_input.encoderCurrent

        if self._last_saddle_encoder_value is None:
            self._last_saddle_encoder_value = current
            self._stable_count = 0
            return False

        if abs(current - self._last_saddle_encoder_value) <= tolerance:
            self._stable_count += 1
        else:
            self._stable_count = 0

        self._last_saddle_encoder_value = current

        return self._stable_count >= samples
            
    def _motion_complete(self):
        if self.app.board.fast_data_values['stepsToGo'] != 0:
            return False

        if not self._encoder_is_stable(self.app.els.at_saddle_encoder_stability_tolerance, self.app.els.at_saddle_encoder_stability_samples):
            return False

        return True
        
    def _start_preload_move(self):
        self._reset_servo_watch_callback()
        self._goto_start_phase = GoToStartPhase.PRELOAD

        log.info("Retract complete, starting preload move")
        backlash_preload_steps = int(abs(self._get_saddle_backlash_distance_encoder_steps()) * 1.25) # preload 1.25x backlash distance - before we retracted 1.5x so we have some cushion
        preload_target = self.saddle_input.encoderCurrent + self._get_saddle_scale_effective_dir() * backlash_preload_steps

        self._apply_reversing_adjusting_acceleration()
        self._command_move_to_encoder(
            preload_target,
            speed=self.app.els.at_preload_adjust_speed
        )

        self._servo_watch_callback = self._watch_go_to_start
        self.app.board.bind(update_tick=self._servo_watch_callback)
        
    def _start_adjust_move(self):
        self._reset_servo_watch_callback()
        self._goto_start_phase = GoToStartPhase.ADJUST

        log.info("Preload move complete, starting final adjust move")

        self._apply_reversing_adjusting_acceleration()
        self._command_move_to_encoder(
            self.bar.start_position,
            speed=self.app.els.at_preload_adjust_speed
        )

        self._servo_watch_callback = self._watch_go_to_start
        self.app.board.bind(update_tick=self._servo_watch_callback)

    def _finish_go_to_start(self):
        self._reset_servo_watch_callback()

        log.info("Start position reached with backlash preloaded")

        self._start_position_preloaded = True

        next_step = self.current_step + 1
        if self._is_cross_slide_at_final_cutting_depth():
            next_step += 1

        self.goto_step(next_step)
    
    def _check_valid_start_position(self) -> bool:
        """Return True if the saddle is within the backlash cushion of the start position.
        Shows a warning popup and redirects to step 6 if not. Sanity check in case the
        start_position_preloaded flag was bypassed or the saddle moved after preload."""
        backlash_cushion = abs(self._get_backlash_cusion_encoder_steps())
        log.info(
            f"Validating start position: current={self.saddle_input.encoderCurrent}, "
            f"start={self.bar.start_position}, "
            f"backlash_cushion={backlash_cushion}"
        )
        delta = abs(self.saddle_input.encoderCurrent - self.bar.start_position)
        if delta > backlash_cushion:
            message = (
                "Not at valid start position including backlash cushion. "
                "Aborting threading operation. Go back to start position."
            )
            log.warning(message)
            CustomPopup(
                title="Warning",
                message=message,
                button_text="Got it",
                on_dismiss_callback=lambda: self.goto_step(5),
            ).open()
            return False
        return True

    def _check_spindle_turning_forward(self) -> bool:
        """Return True if the spindle scale exists and is turning in the right/positive/CCW direction.
        Shows a warning popup and redirects to step 6 if not."""
        spindle_axis = self.app.els.get_spindle_axis()
        spindle_inp = spindle_axis._primary_input() if spindle_axis is not None else None
        if spindle_inp is None:
            log.warning("No spindle scale configured — cannot verify spindle direction")
            CustomPopup(
                title="Warning",
                message="No spindle scale configured. Cannot verify spindle is turning.",
                button_text="Got it",
                on_dismiss_callback=lambda: self.goto_step(5),
            ).open()
            return False

        spindle_speed = self.app.board.fast_data_values.get('scaleSpeed', [0] * SCALES_COUNT)[spindle_inp.inputIndex]
        log.info(f"Validating spindle direction: scaleSpeed[{spindle_inp.inputIndex}]={spindle_speed}")

        if spindle_speed <= 0:
            message = (
                "Spindle is not turning in the right/positive/CCW direction. "
                "Ensure the spindle is running forward before starting the threading operation."
            )
            log.warning(message)
            CustomPopup(
                title="Warning",
                message=message,
                button_text="Got it",
                on_dismiss_callback=lambda: self.goto_step(5),
            ).open()
            return False
        return True

    def _check_spindle_speed_for_pitch(self) -> bool:
        """Return True if the current spindle RPM is within the servo's speed limit
        for the selected pitch. Shows a warning popup and redirects to step 6 if not."""
        spindle_axis = self.app.els.get_spindle_axis()
        spindle_inp = spindle_axis._primary_input() if spindle_axis is not None else None
        if spindle_inp is None:
            return True  # already caught by _check_spindle_turning_forward

        spindle_steps_per_sec = self.app.board.fast_data_values.get('scaleSpeed', [0] * SCALES_COUNT)[spindle_inp.inputIndex]

        try:
            pitch_str = self.bar.selected_pitch.strip()
            if not pitch_str:
                return True  # no pitch selected yet — skip
            pitch_val = float(pitch_str)
        except ValueError:
            log.warning(f"Cannot parse selected_pitch={self.bar.selected_pitch!r} — skipping speed check")
            return True

        if self.bar.metric_mode:
            pitch_mm = pitch_val
        else:
            if pitch_val == 0:
                return True
            pitch_mm = MM_PER_INCH / pitch_val  # TPI → mm/rev

        spindle_rev_per_sec = spindle_steps_per_sec / spindle_inp.ratioDen
        feed_mm_per_sec = spindle_rev_per_sec * pitch_mm
        encoder_steps_per_sec = feed_mm_per_sec * self.saddle_input.stepsPerMM

        scale_ratio = Fraction(abs(self.saddle_input.ratioNum), abs(self.saddle_input.ratioDen))
        servo_ratio = Fraction(abs(self.servo.ratioNum), abs(self.servo.ratioDen))
        required = float(encoder_steps_per_sec * scale_ratio / servo_ratio)

        steps_per_mm_per_rev = pitch_mm * self.saddle_input.stepsPerMM * float(scale_ratio / servo_ratio)
        max_rpm = (self.app.els.at_threading_max_speed / steps_per_mm_per_rev) * 60 if steps_per_mm_per_rev > 0 else 0

        log.info(
            f"Spindle speed check: spindle={spindle_steps_per_sec} steps/s, "
            f"pitch={pitch_mm:.4f} mm, feed={feed_mm_per_sec:.4f} mm/s, "
            f"required_servo={required:.1f} steps/s, max={self.app.els.at_threading_max_speed}, "
            f"max_rpm={max_rpm:.1f}, greater={required > self.app.els.at_threading_max_speed}"
        )

        if required > self.app.els.at_threading_max_speed:
            spindle_rpm = spindle_rev_per_sec * 60
            pitch_label = f"{pitch_mm:.3g} mm" if self.bar.metric_mode else f"{self.bar.selected_pitch} TPI"
            message = (
                f"Spindle speed ({spindle_rpm:.0f} RPM) is too fast for {pitch_label} pitch. "
                f"Required servo speed ({required:.0f} steps/s) exceeds the threading limit "
                f"({self.app.els.at_threading_max_speed} steps/s). "
                f"Max allowed spindle speed for this pitch is {max_rpm:.0f} RPM. "
                "Reduce spindle speed or increase the threading max speed limit."
            )
            log.warning(message)
            CustomPopup(
                title="Warning",
                message=message,
                button_text="Got it",
                on_dismiss_callback=lambda: self.goto_step(5),
            ).open()
            return False
        return True