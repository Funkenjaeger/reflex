from kivy.logger import Logger

from rcp.components.home.coordbar import CoordBar

log = Logger.getChild(__name__)

class AssistedThreadingWizard:
    def __init__(self, bar):
        self.bar = bar
        self.app = bar.app
        self.current_step = 0
        self._current_callback = None
        self.manual_stop_length = None  
        self._steps = [
            self._step_1_initial_position,
            self._step_2_stop_position,
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
        self.bar.bind_to_value_button(value_button_fn)
        self.bar.action_button_condition_fn = action_button_condition_fn
    
    # Instruction steps
    def _step_1_initial_position(self):
        self.set_instruction("Go to initial Z and press Set", "Set", self._capture_initial_position)
        self.bar.bind_to_scale(self.app.scales[self.bar.selected_saddle_scale_id])

    def _step_2_stop_position(self):
        self.bar.action_button_enabled = False  # Disable until valid
        self.set_instruction("Go to stop Z and press Set", "Set", self._capture_stop_position, self._open_stop_position_keypad, self._is_valid_stop_position)
        self.bar.bind_to_scale(self.app.scales[self.bar.selected_saddle_scale_id])
    
    # Step callbacks    
    # Step 1
    def _capture_initial_position(self, *args):
        self.bar.start_position = self.app.scales[self.bar.selected_saddle_scale_id].encoderCurrent
        self._isStartPositionMetricMode = self.app.formats.current_format == "MM"
        self._startScaledPosition = self.app.scales[self.bar.selected_saddle_scale_id].scaledPosition
        log.info(f"Initial position set to: {self.bar.start_position}")
        
    #Step 2
    def _capture_stop_position(self, *args): 
        scale = self.app.scales[self.bar.selected_saddle_scale_id]

        if self.manual_stop_length is not None:
            # convert length into encoder stop position
            self.bar.stop_position = self._convert_stop_position_units_to_encoder(scale, self.manual_stop_length)
            log.info(f"Stop position set manually: {self.manual_stop_length} "
                    f"(start={self.bar.start_position}, stop={self.bar.stop_position})")
            self.manual_stop_length = None  # reset for next run
        else:
            # default: take live encoder value
            self.bar.stop_position = scale.encoderCurrent
            log.info(f"Stop position set from scale: {self.bar.stop_position}"
                    f"(start={self.bar.start_position}, stop={self.bar.stop_position})")
            
    #Step Action button condition functions
    #Step 2
    def _is_valid_stop_position(self):
        """Check if the stop position is valid given the start position and thread direction.
         - For right-hand threads, stop must be less than start.
         - For left-hand threads, stop must be greater than start."""
        if self.bar.left_hand_thread:
            return self.bar.start_position < self._get_stop_position_units(self.app.scales[self.bar.selected_saddle_scale_id])
        return self.bar.start_position > self._get_stop_position_units(self.app.scales[self.bar.selected_saddle_scale_id])

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
        
    # Utilities
    def _convert_stop_position_units_to_encoder(self, scale: CoordBar, manual_position: float) -> int:
        """
        Convert a user-entered stop position (MM/IN) into encoder counts.
        Handles:
            - unit changes (MM ↔ IN)
            - offsets
            - zero start positions
        """

        # Determine factors
        current_factor = float(self.app.formats.factor)
        factor_at_start_position = float(self.app.formats.MM_FRACTION if self._isStartPositionMetricMode else self.app.formats.INCHES_FRACTION)

        # Normalize manual input to the units used at start
        manual_in_start_units = manual_position * (factor_at_start_position / current_factor)

        # Compute delta relative to start scaled position
        delta_in_start_units = manual_in_start_units - self._startScaledPosition

        log.info(
            f"Manual stop input: {manual_position} "
            f"(converted to start units: {manual_in_start_units}, "
            f"delta from start: {delta_in_start_units})"
        )

        # Compute encoder counts using inverse of CoordBar.scaledPosition
        encoder_counts = (
            (delta_in_start_units / factor_at_start_position) - scale.offsets[self.app.currentOffset]
        ) * (float(scale.ratioDen) / float(scale.ratioNum))

        # Offset by the captured start position
        final_encoder_position = int(round(self.bar.start_position + encoder_counts))

        log.info(
            f"Computed encoder counts: {final_encoder_position} "
            f"(start_position={self.bar.start_position}, encoder delta={encoder_counts})"
        )

        return final_encoder_position

    def _get_stop_position_units(self, scale: CoordBar) -> float:
        scale = self.app.scales[self.bar.selected_saddle_scale_id]
        if self.manual_stop_length is not None:
            return self._convert_stop_position_units_to_encoder(scale, self.manual_stop_length)
        return scale.encoderCurrent


