from fractions import Fraction

from kivy.logger import Logger

from rcp.components.home.assisted_threading.thread_type import ThreadType

log = Logger.getChild(__name__)

MM_PER_INCH = 25.4


class AssistedThreadingCalculationsMixin:
    # ---------------------------------------------------------------------------
    # Direction helpers
    # ---------------------------------------------------------------------------

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

    # ---------------------------------------------------------------------------
    # Unit conversion
    # ---------------------------------------------------------------------------

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

    # ---------------------------------------------------------------------------
    # Backlash distances
    # ---------------------------------------------------------------------------

    def _get_saddle_backlash_distance_encoder_steps(self) -> int:
        """Get the retraction distance in encoder counts."""
        return self._convert_distance_units_to_encoder(self.saddle_scale, self.app.els.at_saddle_backlash_distance, self.app.els.at_metric_distances)

    def _get_backlash_cusion_encoder_steps(self) -> int:
        """Get the backlash cushion distance in encoder counts."""
        return self._convert_distance_units_to_encoder(self.saddle_scale, self.app.els.at_backlash_cushion, self.app.els.at_metric_distances)

    # ---------------------------------------------------------------------------
    # Threading servo delta
    # ---------------------------------------------------------------------------

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

    # ---------------------------------------------------------------------------
    # Thread depth calculation
    # ---------------------------------------------------------------------------

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
