from fractions import Fraction

from kivy.logger import Logger
from transitions.extensions import HierarchicalMachine

from rcp.components.popups.custom_popup import CustomPopup
from rcp.components.home.thread_type import ThreadType
from rcp.utils.devices import SCALES_COUNT

log = Logger.getChild(__name__)

MM_PER_INCH = 25.4


class ElsStateMachine:
    """
    State machine for ELS Advanced Bar — covers three modes:
      - Stop only   (enable_stop)
      - Stop+retract (enable_stop + enable_retract)
      - Wizard       (enable_stop + enable_retract + enable_wizard)

    The bar passed to __init__ must expose:
      Properties: start_position, stop_position, material_width, cutting_depth,
                  last_cutting_depth, label_text, next_button_text, display_value,
                  action_button_enabled, action_button_condition_fn,
                  retract_button_visible, retract_button_enabled,
                  retract_button_condition_fn, is_running,
                  enable_stop, enable_retract, enable_wizard,
                  selected_pitch, metric_mode, thread_profile_type,
                  inner_thread, els_bar
      Methods:    set_instruction(), bind_display_value_to_scale(),
                  bind_display_value_to_servo_position(),
                  unbind_all_display_value(), update_buttons_state(),
                  bind_btn_value_on_release()
    """

    states = [
        'idle',
        'set_start_z',
        'set_stop_z',
        {
            'name': 'returning',
            'children': ['waiting', 'retracting', 'preloading', 'adjusting'],
            'initial': 'waiting',
        },
        'set_major_diameter',
        'set_minor_diameter',
        'engage_half_nut',
        'cut',
        'depth_reached',
    ]

    def __init__(self, bar):
        from rcp.app import MainApp
        log.info("Initializing ElsStateMachine")
        self.bar = bar
        self.app: MainApp = MainApp.get_running_app()
        self.servo = self.app.servo

        # Captured metric-mode context for unit conversions
        self._is_start_position_metric_mode = False
        self._start_scaled_position = 0.0
        self._is_major_diameter_metric_mode = False
        self._major_diameter_scaled_position = 0.0

        # Manual overrides from keypad
        self.manual_start_length = None
        self.manual_stop_length = None
        self.manual_cutting_depth = None

        # Encoder stability sampling
        self._last_saddle_encoder_value = None
        self._stable_count = 0

        # Cut-pass state (replaces _threading_started/_threading_active_confirmed)
        self._cut_started = False
        self._cut_active_confirmed = False
        self._calculated_cut_delta_steps = 0

        # Firmware poll callbacks
        self._motion_poll_callback = None
        self._cut_poll_callback = None
        self._retracting_stop_callback = None

        HierarchicalMachine(
            model=self,
            states=self.states,
            transitions=self._build_transitions(),
            initial='idle',
            ignore_invalid_triggers=True,
            after_state_change='_publish_state',
        )
        # Seed the initial state into the bar — `after_state_change` only
        # fires on transitions, not on construction.
        self.bar.current_state = self.state

    def _publish_state(self, *_):
        """Mirror the `transitions` model state into a Kivy StringProperty
        so the kv layer can reactively bind blink/visibility to it."""
        self.bar.current_state = self.state

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def saddle_scale(self):
        return self.app.els.get_z_axis()

    @property
    def cross_slide_scale(self):
        return self.app.els.get_x_axis()

    @property
    def saddle_input(self):
        axis = self.saddle_scale
        return axis._primary_input() if axis is not None else None

    @property
    def cross_slide_input(self):
        axis = self.cross_slide_scale
        return axis._primary_input() if axis is not None else None

    # ── Mode guards ───────────────────────────────────────────────────────────

    def is_retract_enabled(self):
        return bool(self.bar.enable_retract)

    def is_not_retract_enabled(self):
        return not bool(self.bar.enable_retract)

    def is_wizard_enabled(self):
        return bool(self.bar.enable_wizard)

    def is_not_wizard_enabled(self):
        return not bool(self.bar.enable_wizard)

    def is_retract_or_wizard_enabled(self):
        return bool(self.bar.enable_retract) or bool(self.bar.enable_wizard)

    # ── Transitions ───────────────────────────────────────────────────────────

    def _build_transitions(self):
        return [
            # ── Start ────────────────────────────────────────────────────────
            {'trigger': 'start', 'source': 'idle', 'dest': 'set_stop_z',
             'conditions': ['is_not_retract_enabled', 'is_not_wizard_enabled'],
             'before': '_on_wizard_start'},
            {'trigger': 'start', 'source': 'idle', 'dest': 'set_start_z',
             'conditions': 'is_retract_or_wizard_enabled',
             'before': '_on_wizard_start'},

            # ── Shared setup ─────────────────────────────────────────────────
            {'trigger': 'action', 'source': 'set_start_z', 'dest': 'set_stop_z',
             'before': '_capture_start_z'},

            # set_stop_z → branch by mode
            {'trigger': 'action', 'source': 'set_stop_z', 'dest': 'cut',
             'conditions': ['is_not_retract_enabled', 'is_not_wizard_enabled',
                            '_is_stop_position_set'],
             'before': '_capture_stop_z'},
            {'trigger': 'action', 'source': 'set_stop_z', 'dest': 'returning',
             'conditions': ['is_retract_enabled', 'is_not_wizard_enabled',
                            '_is_valid_stop_position'],
             'before': '_capture_stop_z'},
            {'trigger': 'action', 'source': 'set_stop_z', 'dest': 'set_major_diameter',
             'conditions': ['is_wizard_enabled', '_is_valid_stop_position'],
             'before': '_capture_stop_z'},

            # ── Wizard-only setup ─────────────────────────────────────────────
            {'trigger': 'action', 'source': 'set_major_diameter',
             'dest': 'set_minor_diameter', 'before': '_capture_major_diameter'},
            {'trigger': 'action', 'source': 'set_minor_diameter',
             'dest': 'engage_half_nut', 'before': '_capture_minor_diameter'},
            {'trigger': 'action', 'source': 'engage_half_nut', 'dest': 'returning'},

            # ── returning sub-phases ────────────────────────────────────────
            # waiting: operator confirms cross slide retracted, presses Go
            {'trigger': 'action', 'source': 'returning_waiting',
             'dest': 'returning_retracting',
             'conditions': '_is_cross_slide_retracted'},
            # motion sub-phases are hardware-driven
            {'trigger': 'motion_complete', 'source': 'returning_retracting',
             'dest': 'returning_preloading'},
            {'trigger': 'motion_complete', 'source': 'returning_preloading',
             'dest': 'returning_adjusting'},
            # adjusting exits: depth_reached check first, fallback to cut
            {'trigger': 'motion_complete', 'source': 'returning_adjusting',
             'dest': 'depth_reached',
             'conditions': ['is_wizard_enabled', '_is_at_final_depth']},
            {'trigger': 'motion_complete', 'source': 'returning_adjusting',
             'dest': 'cut'},

            # ── Cut (all modes) ───────────────────────────────────────────────
            {'trigger': 'action', 'source': ['cut', 'depth_reached'],
             'dest': 'cut',
             'conditions': ['_check_valid_start_position',
                            '_check_spindle_turning_forward',
                            '_check_spindle_speed_for_pitch'],
             'before': '_begin_cutting_pass'},
            # cut_done: stop-only loops directly; retract+wizard retracts first
            {'trigger': 'cut_done', 'source': 'cut', 'dest': 'cut',
             'conditions': 'is_not_retract_enabled'},
            {'trigger': 'cut_done', 'source': 'cut', 'dest': 'returning',
             'conditions': 'is_retract_enabled'},

            # retract_done: operator released retract button and servo stopped
            {'trigger': 'retract_done', 'source': ['cut', 'depth_reached'],
             'dest': 'returning'},

            # ── Global stop ───────────────────────────────────────────────────
            {'trigger': 'stop', 'source': '*', 'dest': 'idle',
             'before': '_do_cleanup'},
        ]

    # ── UI helper (equivalent to old wizard set_instruction) ─────────────────

    def set_instruction(
        self,
        label_text: str,
        next_button_text: str,
        callback=None,          # unused — transitions replace callbacks
        action_button_condition_fn=None,
        retract_button_visible: bool = False,
        retract_button_condition_fn=None,
    ):
        self.bar.label_text = label_text
        self.bar.next_button_text = next_button_text
        self.bar.action_button_condition_fn = action_button_condition_fn
        self.bar.retract_button_visible = retract_button_visible
        self.bar.retract_button_condition_fn = retract_button_condition_fn

    def unbind_progress_display(self):
        """Called by the bar's unbind_all_display_value to clean up progress callback."""
        xi = self.cross_slide_input
        if xi is not None and hasattr(self, '_on_cutting_progress_update'):
            xi.unbind(encoderCurrent=self._on_cutting_progress_update)

    # ── State entry / exit callbacks ──────────────────────────────────────────

    def on_enter_set_start_z(self):
        self.set_instruction("Go to start Z and press Set", "Set")
        self.bar.bind_display_value_to_scale(self.saddle_scale, "start_z_text")

    def on_enter_set_stop_z(self):
        self.bar.action_button_enabled = False
        condition_fn = (
            self._is_valid_stop_position
            if self.is_retract_or_wizard_enabled()
            else self._is_stop_position_set
        )
        self.set_instruction(
            "Go to or input stop Z and press Set", "Set",
            action_button_condition_fn=condition_fn,
        )
        self.bar.bind_display_value_to_scale(self.saddle_scale, "stop_z_text")
        self.bar.update_buttons_state()

    def on_enter_set_major_diameter(self):
        self.set_instruction(
            "Go to workpiece major diameter and press Set", "Set")
        self.bar.bind_display_value_to_scale(
            self.cross_slide_scale, "major_diameter_text"
        )

    def on_enter_set_minor_diameter(self):
        self._clear_bar_display()
        calculated_depth = self._calculate_thread_depth()
        self.manual_cutting_depth = None
        if calculated_depth is not None:
            is_metric = self.app.formats.current_format == "MM"
            self.bar.minor_diameter_text = (
                f"{calculated_depth:.3f}" if is_metric else f"{calculated_depth:.4f}"
            )
        else:
            self.bar.minor_diameter_text = ""
        self.set_instruction(
            "Enter final cutting depth (auto-calculated shown, tap to override)",
            "Set",
        )

    def on_enter_engage_half_nut(self):
        self.set_instruction("Engage half nut and press Next", "Next")
        self._clear_bar_display()

    def on_enter_returning_waiting(self):
        self.bar.action_button_enabled = False
        self.bar.retract_button_enabled = False
        self.set_instruction(
            "Confirm cross slide retracted and press Go to return to start position",
            "Go",
            action_button_condition_fn=self._is_cross_slide_retracted,
            retract_button_visible=True,
            retract_button_condition_fn=self._is_cross_slide_retracted,
        )
        self.bar.bind_display_value_to_scale(self.cross_slide_scale)
        self.bar.update_buttons_state()

    def on_enter_returning_retracting(self):
        if not self.app.board.connected:
            self.stop()
            return
        self.bar.retract_button_enabled = False
        self.bar.action_button_enabled = False
        self._apply_reversing_adjusting_acceleration()
        effective_dir = self._get_saddle_scale_effective_dir()
        retraction = abs(self._get_saddle_backlash_distance_encoder_steps() * 1.5)
        retract_target = self.bar.start_position + (-effective_dir) * retraction
        log.info(f"returning: RETRACTING to {retract_target}")
        self._command_move_to_encoder(retract_target,
                                      speed=self.app.els.at_reversing_speed)
        # Bind single poll callback for the remaining motion phases
        self._motion_poll_callback = self._poll_motion
        self.app.board.bind(update_tick=self._motion_poll_callback)

    def on_enter_returning_preloading(self):
        self._apply_reversing_adjusting_acceleration()
        effective_dir = self._get_saddle_scale_effective_dir()
        preload_steps = int(abs(self._get_saddle_backlash_distance_encoder_steps()) * 1.25)
        preload_target = (self.saddle_input.encoderCurrent
                          + effective_dir * preload_steps)
        log.info(f"returning: PRELOADING to {preload_target}")
        self._command_move_to_encoder(preload_target,
                                      speed=self.app.els.at_preload_adjust_speed)

    def on_enter_returning_adjusting(self):
        self._apply_reversing_adjusting_acceleration()
        log.info(f"returning: ADJUSTING to {self.bar.start_position}")
        self._command_move_to_encoder(self.bar.start_position,
                                      speed=self.app.els.at_preload_adjust_speed)

    def on_exit_returning(self):
        if self._motion_poll_callback:
            self.app.board.unbind(update_tick=self._motion_poll_callback)
            self._motion_poll_callback = None

    def on_enter_cut(self):
        self.bar.action_button_enabled = False
        self.bar.retract_button_enabled = False
        label = (
            "Go to minor diameter and press Cut"
            if self.is_wizard_enabled()
            else "Press Cut to start"
        )
        self.set_instruction(label, "Cut", retract_button_visible=True)
        if self.is_wizard_enabled():
            self._bind_cutting_progress_display()
        self.bar.update_buttons_state()

    def on_enter_depth_reached(self):
        self.bar.action_button_enabled = False
        self.bar.retract_button_enabled = False
        self.set_instruction(
            "Final depth reached. Cut more? Press Stop to quit.",
            "Cut",
            retract_button_visible=True,
        )
        self._bind_cutting_progress_display()
        self.bar.update_buttons_state()

    def on_enter_idle(self):
        pass  # cleanup is handled by _do_cleanup in before= hook on the stop trigger

    # ── Hardware polling ──────────────────────────────────────────────────────

    def _poll_motion(self, *_):
        """Bound to board.update_tick during returning motion phases."""
        if self._motion_complete():
            self.motion_complete()

    def _poll_cut_done(self, *_):
        """Bound to board.update_tick during a cut pass."""
        dev = self.app.board.device
        dev['assistedThreadingData'].refresh()
        thread_phase_active = dev['assistedThreadingData']['threadPhaseActive']
        thread_enabled = dev['assistedThreadingData']['threadEnabled']

        if thread_enabled == 1 or thread_phase_active == 1:
            self._cut_active_confirmed = True

        if self._cut_active_confirmed and thread_enabled == 0 and thread_phase_active == 0:
            log.info("Cut pass complete")
            self.app.board.unbind(update_tick=self._poll_cut_done)
            self._cut_poll_callback = None
            self.cut_done()

    # ── Before= action callbacks ──────────────────────────────────────────────

    def _on_wizard_start(self):
        dev = self.app.board.device
        dev['assistedThreadingData']['spindlePhaseTolerance'] = (
            self.app.els.at_rotary_encoder_sync_tolerance
        )
        spindle_axis = self.app.els.get_spindle_axis()
        if spindle_axis is not None:
            inp = spindle_axis._primary_input()
            if inp is not None:
                dev['assistedThreadingData']['spindleCountsPerRev'] = int(
                    spindle_axis._steps_per_revolution()
                )
                dev['assistedThreadingData']['spindleScaleIndex'] = inp.inputIndex
        self._cut_started = False
        self._cut_active_confirmed = False
        self._calculated_cut_delta_steps = 0

        # Configure elsStop: scaleIndex and threadPitchSteps
        z_input = self.saddle_input
        if z_input is not None:
            dev['elsStop']['scaleIndex'] = z_input.inputIndex
        dev['elsStop']['threadPitchSteps'] = self._calculate_thread_pitch_steps()

    def _capture_start_z(self, *_):
        inp = self.saddle_input
        if inp is None:
            log.error("_capture_start_z: saddle_input is None — cannot capture")
            return
        if self.manual_start_length is not None:
            is_metric = self.app.formats.current_format == "MM"
            factor = float(
                self.app.formats.MM_FRACTION if is_metric
                else self.app.formats.INCHES_FRACTION
            )
            encoder_counts = (self.manual_start_length / factor) * (
                float(inp.ratioDen) / float(inp.ratioNum)
            )
            self.bar.start_position = int(round(encoder_counts))
            self._is_start_position_metric_mode = is_metric
            self._start_scaled_position = self.manual_start_length
            log.info(
                f"Start Z captured from keypad: {self.bar.start_position} "
                f"(manual={self.manual_start_length})"
            )
            self.manual_start_length = None
        else:
            self.bar.start_position = inp.encoderCurrent
            self._is_start_position_metric_mode = self.app.formats.current_format == "MM"
            self._start_scaled_position = self.saddle_scale.scaledPosition
            log.info(f"Start Z captured: {self.bar.start_position}")

    def _capture_stop_z(self, *_):
        inp = self.saddle_input
        if inp is None:
            log.error("_capture_stop_z: saddle_input is None — cannot capture")
            return
        self.bar.stop_position = self._get_stop_position_units()
        self.manual_stop_length = None
        log.info(f"Stop Z captured: {self.bar.stop_position}")

        # Update elsStop registers with captured stop position
        if self.app.board.connected:
            dev = self.app.board.device
            els_forward = self.bar.els_bar.els_forward if self.bar.els_bar is not None else True
            stop_direction = -1 if els_forward else 1
            dev['elsStop']['stopPosition'] = int(self.bar.stop_position)
            dev['elsStop']['stopDirection'] = stop_direction
            dev['elsStop']['enable'] = 1 if self.bar.els_stop_engaged else 0
            log.info(
                f"elsStop configured: stopPosition={int(self.bar.stop_position)}, "
                f"stopDirection={stop_direction}, enable={self.bar.els_stop_engaged}"
            )

    def _capture_major_diameter(self, *_):
        self.bar.material_width = self.cross_slide_input.encoderCurrent
        self.bar.last_cutting_depth = self.bar.material_width
        self._is_major_diameter_metric_mode = self.app.formats.current_format == "MM"
        self._major_diameter_scaled_position = self.cross_slide_scale.scaledPosition
        log.info(f"Major diameter captured: {self.bar.material_width}")

    def _capture_minor_diameter(self, *_):
        is_metric = self.app.formats.current_format == "MM"
        depth = (
            self.manual_cutting_depth
            if self.manual_cutting_depth is not None
            else self._calculate_thread_depth()
        )
        encoder_depth = self._convert_distance_units_to_encoder(
            self.cross_slide_scale, depth, is_metric
        )
        self.bar.cutting_depth = (
            self.cross_slide_input.encoderCurrent
            - encoder_depth * self._get_cross_slide_scale_effective_dir()
        )
        log.info(f"Minor diameter / cutting depth captured: {self.bar.cutting_depth}")
        self.bar.minor_diameter_text = (
            f"{depth:.3f}" if is_metric else f"{depth:.4f}"
        )

    def _begin_cutting_pass(self, *_):
        if not self.app.board.connected:
            self.stop()
            return
        log.info("Beginning cut pass to stop position: %s", self.bar.stop_position)
        self.bar.last_cutting_depth = self.cross_slide_input.encoderCurrent
        self._apply_cutting_acceleration()
        self._apply_cutting_max_speed()
        self.bar.bind_display_value_to_servo_position()
        self.bar.action_button_enabled = False
        self.bar.retract_button_visible = False

        dev = self.app.board.device
        if not self._cut_started:
            self._cut_started = True
            self._cut_active_confirmed = False
            self._calculated_cut_delta_steps = self._get_cutting_servo_delta_steps()
            dev['assistedThreadingData']['threadRemainingSteps'] = (
                self._calculated_cut_delta_steps
            )
            dev['assistedThreadingData']['threadRequest'] = 1
        else:
            self._cut_active_confirmed = False
            dev['assistedThreadingData']['threadRemainingSteps'] = (
                self._calculated_cut_delta_steps
            )
            dev['assistedThreadingData']['threadEnabled'] = 1

        log.info(f"Cut requested: threadRemainingSteps={self._calculated_cut_delta_steps}")
        self._cut_poll_callback = self._poll_cut_done
        self.app.board.bind(update_tick=self._cut_poll_callback)

    def _do_cleanup(self, *_):
        log.info("ElsStateMachine cleanup")
        self._cut_started = False
        self._cut_active_confirmed = False
        self.bar.label_text = ""
        self.bar.display_value = ""
        self.bar.action_button_enabled = True
        self.bar.action_button_condition_fn = None
        self.bar.is_running = False
        self.bar.retract_button_visible = False
        self._clear_bar_display()
        if self._motion_poll_callback:
            self.app.board.unbind(update_tick=self._motion_poll_callback)
            self._motion_poll_callback = None
        if self._cut_poll_callback:
            self.app.board.unbind(update_tick=self._cut_poll_callback)
            self._cut_poll_callback = None
        if self._retracting_stop_callback:
            self.app.board.unbind(update_tick=self._retracting_stop_callback)
            self._retracting_stop_callback = None
        self._reset_encoder_stability_check()
        if self.app.board.connected:
            self.app.board.device['assistedThreadingData']['threadReset'] = 1
            self.app.board.device['elsStop']['enable'] = 0
            self._stop_servo()

    # ── Guard conditions ──────────────────────────────────────────────────────

    def _is_stop_position_set(self):
        return self.bar.stop_position != 0

    def _is_valid_stop_position(self):
        effective_dir = self._get_saddle_scale_effective_dir()
        backlash_cushion = abs(self._get_backlash_cushion_encoder_steps())
        stop = self._get_stop_position_units()
        min_stop = self.bar.start_position + effective_dir * backlash_cushion
        log.info(f"effective dir {effective_dir} backlash_cushion {backlash_cushion} stop {stop} min_stop {min_stop} start_position {self.bar.start_position}")
        return (stop - min_stop) * effective_dir > 0

    def _is_cross_slide_retracted(self):
        if not self.is_wizard_enabled():
            return True  # retract/stop-only: no material_width concept
        saddle_dir = self._get_saddle_scale_effective_dir()
        saddle_delta = self.saddle_input.encoderCurrent - self.bar.start_position
        if saddle_delta * saddle_dir <= 0:
            return True  # saddle not yet past start, no need to check
        retract_dir = -self._get_cross_slide_scale_effective_dir()
        cross_delta = (self.cross_slide_input.encoderCurrent
                       - self.bar.material_width)
        return cross_delta * retract_dir > 0

    def _is_at_final_depth(self):
        effective_dir = self._get_cross_slide_scale_effective_dir()
        return (
            (self.bar.last_cutting_depth - self.bar.cutting_depth) * effective_dir >= 0
        )

    def _check_valid_start_position(self) -> bool:
        if not self.is_retract_or_wizard_enabled():
            return True  # stop-only: no start position concept
        backlash_cushion = abs(self._get_backlash_cushion_encoder_steps())
        delta = abs(self.saddle_input.encoderCurrent - self.bar.start_position)
        if delta > backlash_cushion:
            msg = (
                "Not at valid start position including backlash cushion. "
                "Go back to start position."
            )
            log.warning(msg)
            CustomPopup(
                title="Warning", message=msg, button_text="Got it",
            ).open()
            return False
        return True

    def _check_spindle_turning_forward(self) -> bool:
        if not self.is_wizard_enabled():
            return True
        spindle_axis = self.app.els.get_spindle_axis()
        spindle_inp = (spindle_axis._primary_input()
                       if spindle_axis is not None else None)
        if spindle_inp is None:
            log.warning("No spindle scale configured")
            CustomPopup(
                title="Warning",
                message="No spindle scale configured. Cannot verify spindle is turning.",
                button_text="Got it",
            ).open()
            return False
        spindle_speed = self.app.board.fast_data_values.get(
            'scaleSpeed', [0] * SCALES_COUNT
        )[spindle_inp.inputIndex]
        if spindle_speed <= 0:
            msg = (
                "Spindle is not turning forward. "
                "Ensure the spindle is running before starting the cut."
            )
            log.warning(msg)
            CustomPopup(title="Warning", message=msg, button_text="Got it").open()
            return False
        return True

    def _check_spindle_speed_for_pitch(self) -> bool:
        if not self.is_wizard_enabled():
            return True
        spindle_axis = self.app.els.get_spindle_axis()
        spindle_inp = (spindle_axis._primary_input()
                       if spindle_axis is not None else None)
        if spindle_inp is None:
            return True
        spindle_steps_per_sec = self.app.board.fast_data_values.get(
            'scaleSpeed', [0] * SCALES_COUNT
        )[spindle_inp.inputIndex]
        try:
            pitch_str = self.bar.selected_pitch.strip()
            if not pitch_str:
                return True
            pitch_val = float(pitch_str)
        except (ValueError, AttributeError):
            return True
        pitch_mm = (pitch_val if self.bar.metric_mode
                    else (MM_PER_INCH / pitch_val if pitch_val else 0))
        if pitch_mm <= 0:
            return True
        spindle_rev_per_sec = spindle_steps_per_sec / spindle_inp.ratioDen
        encoder_steps_per_sec = (spindle_rev_per_sec * pitch_mm
                                  * self.saddle_input.stepsPerMM)
        scale_ratio = Fraction(abs(self.saddle_input.ratioNum),
                               abs(self.saddle_input.ratioDen))
        servo_ratio = Fraction(abs(self.servo.ratioNum),
                               abs(self.servo.ratioDen))
        required = float(encoder_steps_per_sec * scale_ratio / servo_ratio)
        if required > self.app.els.at_threading_max_speed:
            steps_per_mm_per_rev = (pitch_mm * self.saddle_input.stepsPerMM
                                    * float(scale_ratio / servo_ratio))
            max_rpm = (
                (self.app.els.at_threading_max_speed / steps_per_mm_per_rev) * 60
                if steps_per_mm_per_rev > 0 else 0
            )
            spindle_rpm = spindle_rev_per_sec * 60
            pitch_label = (
                f"{pitch_mm:.3g} mm" if self.bar.metric_mode
                else f"{self.bar.selected_pitch} TPI"
            )
            msg = (
                f"Spindle speed ({spindle_rpm:.0f} RPM) is too fast for "
                f"{pitch_label} pitch. Required servo speed ({required:.0f} steps/s) "
                f"exceeds limit ({self.app.els.at_threading_max_speed} steps/s). "
                f"Max allowed: {max_rpm:.0f} RPM."
            )
            log.warning(msg)
            CustomPopup(title="Warning", message=msg, button_text="Got it").open()
            return False
        return True

    # ── Motion helpers ────────────────────────────────────────────────────────

    def _motion_complete(self) -> bool:
        if self.app.board.fast_data_values['stepsToGo'] != 0:
            return False
        return self._encoder_is_stable(
            self.app.els.at_saddle_encoder_stability_tolerance,
            self.app.els.at_saddle_encoder_stability_samples,
        )

    def _encoder_is_stable(self, tolerance, samples) -> bool:
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

    def _reset_encoder_stability_check(self):
        self._last_saddle_encoder_value = None
        self._stable_count = 0

    def _command_move_to_encoder(self, target_encoder, speed):
        self._reset_encoder_stability_check()
        current_enc = self.saddle_input.encoderCurrent
        scale_ratio = Fraction(abs(self.saddle_input.ratioNum),
                               abs(self.saddle_input.ratioDen))
        servo_ratio = Fraction(abs(self.servo.ratioNum),
                               abs(self.servo.ratioDen))
        delta = int((target_encoder - current_enc) * scale_ratio / servo_ratio)
        log.info(f"Move to encoder: current={current_enc}, target={target_encoder}, delta={delta}")
        self.bar.bind_display_value_to_servo_position()
        self.servo.set_max_speed(speed)
        self.app.board.device['servo']['stepsToGo'] = delta

    # ── Retract button (direct — not state-machine driven) ────────────────────

    def start_retracting(self):
        log.info("Retract button pressed")
        self.bar.action_button_enabled = False
        if not self.app.board.connected:
            return
        self.bar.bind_display_value_to_servo_position()
        servo_dir = 1 if self.servo.ratioNum * self.servo.ratioDen > 0 else -1
        self.servo.jogSpeed = -servo_dir * self.app.els.at_reversing_speed
        self._apply_reversing_adjusting_acceleration()
        self.servo.set_max_speed(self.app.els.at_reversing_speed)
        self.servo.servoMode = 2

    def stop_retracting(self):
        log.info("Retract button released")
        self.bar.action_button_enabled = True
        self.bar.bind_display_value_to_scale(self.cross_slide_scale)
        self.bar.update_buttons_state()
        if not self.app.board.connected:
            return
        self.servo.jogSpeed = 0
        self._retracting_stop_callback = self._watch_retracting_stopped
        self.app.board.bind(update_tick=self._retracting_stop_callback)

    def _watch_retracting_stopped(self, *_):
        if not self._encoder_is_stable(
            self.app.els.at_saddle_encoder_stability_tolerance,
            self.app.els.at_saddle_encoder_stability_samples,
        ):
            return
        self.app.board.unbind(update_tick=self._retracting_stop_callback)
        self._retracting_stop_callback = None
        self.servo.set_max_speed(self.servo.maxSpeed)
        self.servo.servoMode = 1
        self.retract_done()

    # ── Calculation helpers ───────────────────────────────────────────────────

    def _get_saddle_scale_effective_dir(self) -> int:
        els_bar = getattr(self.bar, 'els_bar', None)
        els_forward = els_bar.els_forward if els_bar is not None else True
        thread_dir = -1 if els_forward else 1
        scale_dir = (1 if self.saddle_input.ratioNum * self.saddle_input.ratioDen > 0
                     else -1)
        return thread_dir * scale_dir

    def _get_cross_slide_scale_effective_dir(self) -> int:
        thread_dir = 1 if getattr(self.bar, 'inner_thread', False) else -1
        scale_dir = (1 if self.cross_slide_input.ratioNum * self.cross_slide_input.ratioDen > 0
                     else -1)
        return thread_dir * scale_dir

    def _get_saddle_backlash_distance_encoder_steps(self) -> int:
        return self._convert_distance_units_to_encoder(
            self.saddle_scale,
            self.app.els.at_saddle_backlash_distance,
            self.app.els.at_metric_distances,
        )

    def _get_backlash_cushion_encoder_steps(self) -> int:
        return self._convert_distance_units_to_encoder(
            self.saddle_scale,
            self.app.els.at_backlash_cushion,
            self.app.els.at_metric_distances,
        )

    def _convert_distance_units_to_encoder(self, scale, distance: float,
                                           is_metric: bool) -> int:
        inp = scale._primary_input()
        encoder_factor = float(
            self.app.formats.MM_FRACTION if is_metric
            else self.app.formats.INCHES_FRACTION
        )
        encoder_counts = (distance / encoder_factor) * (
            float(inp.ratioDen) / float(inp.ratioNum)
        )
        return int(round(encoder_counts))

    def _convert_position_units_to_encoder(
        self, scale, manual_position: float,
        is_original_metric: bool, original_scaled_position,
        start_encoder: int,
    ) -> int:
        current_factor = float(self.app.formats.factor)
        factor_at_start = float(
            self.app.formats.MM_FRACTION if is_original_metric
            else self.app.formats.INCHES_FRACTION
        )
        manual_in_start_units = manual_position * (factor_at_start / current_factor)
        delta_in_start_units = manual_in_start_units - original_scaled_position
        inp = scale._primary_input()
        encoder_counts = (delta_in_start_units / factor_at_start) * (
            float(inp.ratioDen) / float(inp.ratioNum)
        )
        return int(round(start_encoder + encoder_counts))

    def _get_stop_position_units(self):
        if self.manual_stop_length is not None:
            return self._convert_position_units_to_encoder(
                self.saddle_scale,
                self.manual_stop_length,
                self._is_start_position_metric_mode,
                self._start_scaled_position,
                self.bar.start_position,
            )
        return self.saddle_input.encoderCurrent

    def _calculate_thread_pitch_steps(self) -> float:
        """Calculate servo steps per thread pitch for the elsStop register.

        Returns the number of leadscrew servo steps that correspond to one
        full thread pitch of carriage travel.  Returns 0.0 for feed/turning
        modes (no phase-correct correction needed).
        """
        pitch_str = getattr(self.bar, 'selected_pitch', None)
        if not pitch_str:
            return 0.0
        try:
            pitch_str = pitch_str.strip()
            if not pitch_str:
                return 0.0
            if self.bar.metric_mode:
                thread_pitch_mm = float(pitch_str)
            else:
                tpi = float(pitch_str)
                thread_pitch_mm = MM_PER_INCH / tpi if tpi else 0.0
        except (ValueError, TypeError):
            return 0.0
        if thread_pitch_mm <= 0:
            return 0.0

        servo = self.app.servo
        leadscrew_pitch_mm = float(servo.leadScrewPitch)
        if servo.leadScrewPitchIn:
            leadscrew_pitch_mm *= MM_PER_INCH
        if leadscrew_pitch_mm <= 0:
            return 0.0

        steps_per_rev = int(servo.leadScrewPitchSteps)
        return (thread_pitch_mm / leadscrew_pitch_mm) * steps_per_rev

    def _get_cutting_servo_delta_steps(self) -> int:
        effective_dir = self._get_saddle_scale_effective_dir()
        current_encoder = self.saddle_input.encoderCurrent
        target_encoder = self.bar.stop_position
        delta_enc = target_encoder - current_encoder
        if delta_enc * effective_dir <= 0:
            log.warning(
                f"Cut delta opposite to cutting direction "
                f"(current={current_encoder}, stop={target_encoder})"
            )
        scale_ratio = Fraction(abs(self.saddle_input.ratioNum),
                               abs(self.saddle_input.ratioDen))
        servo_ratio = Fraction(abs(self.servo.ratioNum),
                               abs(self.servo.ratioDen))
        return int(delta_enc * scale_ratio / servo_ratio)

    def _calculate_thread_depth(self):
        if not getattr(self.bar, 'selected_pitch', None):
            return None
        try:
            if self.bar.metric_mode:
                pitch = float(self.bar.selected_pitch)
            else:
                tpi = float(self.bar.selected_pitch)
                pitch = MM_PER_INCH / tpi
        except (ValueError, TypeError):
            return None
        if pitch <= 0:
            return None
        thread_type = ThreadType(self.bar.thread_profile_type)
        depth_factors = {
            ThreadType.ISO_METRIC: 0.61343,
            ThreadType.UNIFIED:    0.64952,
            ThreadType.WHITWORTH:  0.6403,
            ThreadType.ACME:       0.5,
        }
        factor = depth_factors.get(thread_type)
        if factor is None:
            return None
        depth = factor * pitch
        if self.app.els.at_cross_slide_diameter_mode:
            depth *= 2
        is_current_metric = self.app.formats.current_format == "MM"
        if self.bar.metric_mode and not is_current_metric:
            depth /= MM_PER_INCH
        elif not self.bar.metric_mode and is_current_metric:
            depth *= MM_PER_INCH
        return depth

    # ── Servo helpers ─────────────────────────────────────────────────────────

    def _stop_servo(self):
        self.servo.set_max_speed(self.servo.maxSpeed)
        self.servo.servoMode = 0
        self._apply_original_servo_acceleration()

    def _apply_original_servo_acceleration(self):
        self.app.board.device['servo']['acceleration'] = self.servo.acceleration

    def _apply_reversing_adjusting_acceleration(self):
        rate = self.app.els.at_reversing_adjusting_acceleration
        if rate and rate > 0:
            self.app.board.device['servo']['acceleration'] = rate
        else:
            self._apply_original_servo_acceleration()

    def _apply_cutting_acceleration(self):
        rate = self.app.els.at_threading_acceleration
        if rate and rate > 0:
            self.app.board.device['servo']['acceleration'] = rate
        else:
            self._apply_original_servo_acceleration()

    def _apply_cutting_max_speed(self):
        target = self.app.els.at_threading_max_speed
        self.servo.set_max_speed(
            target if target and target > 0 else self.servo.maxSpeed
        )

    # ── Display helpers ───────────────────────────────────────────────────────

    def _clear_bar_display(self):
        self.bar.unbind_all_display_value()
        self.bar.display_value = ""

    def _bind_cutting_progress_display(self):
        self.bar.unbind_all_display_value()

        def on_cross_slide_update(instance, value):
            try:
                is_metric = self.app.formats.current_format == "MM"
                current_encoder = self.cross_slide_input.encoderCurrent
                last_depth_encoder = self.bar.last_cutting_depth
                factor = float(self.app.formats.factor)
                scale_ratio = abs(
                    Fraction(self.cross_slide_input.ratioNum,
                             self.cross_slide_input.ratioDen) * factor
                )
                if getattr(self.bar, 'inner_thread', False):
                    incremental = (last_depth_encoder - current_encoder) * scale_ratio
                    remaining = (current_encoder - self.bar.cutting_depth) * scale_ratio
                else:
                    incremental = (current_encoder - last_depth_encoder) * scale_ratio
                    remaining = (self.bar.cutting_depth - current_encoder) * scale_ratio
                fmt = ".3f" if is_metric else ".4f"
                self.bar.display_value = (
                    f"Last: {incremental:{fmt}} | Rem: {remaining:{fmt}}"
                )
            except Exception as e:
                log.error(f"Error updating cut progress display: {e}")

        self._on_cutting_progress_update = on_cross_slide_update
        self.cross_slide_input.bind(encoderCurrent=on_cross_slide_update)
        on_cross_slide_update(
            self.cross_slide_input, self.cross_slide_input.encoderCurrent
        )

    # ── Manual input handlers ─────────────────────────────────────────────────

    def _open_start_position_keypad(self, *_):
        from rcp.components.popups.keypad import Keypad
        is_metric = self.app.formats.current_format == "MM"
        keypad = Keypad(
            title="Enter Start Z (" + ("mm" if is_metric else "in") + ")"
        )
        keypad.integer = False

        def on_done(value):
            try:
                self.manual_start_length = float(value)
                self.bar.start_z_text = (
                    f"{self.manual_start_length:.3f}" if is_metric
                    else f"{self.manual_start_length:.4f}"
                )
            except ValueError:
                log.warning(f"Invalid start Z value: {value}")
            finally:
                self.bar.update_buttons_state()

        keypad.show_with_callback(
            callback_fn=on_done,
            current_value=self.manual_start_length or 0.0,
        )

    def _open_stop_position_keypad(self, *_):
        from rcp.components.popups.keypad import Keypad
        is_metric = self.app.formats.current_format == "MM"
        keypad = Keypad(
            title="Enter Stop Length (" + ("mm" if is_metric else "in") + ")"
        )
        keypad.integer = False

        def on_done(value):
            try:
                self.manual_stop_length = float(value)
                self.bar.stop_z_text = (
                    f"{self.manual_stop_length:.3f}" if is_metric
                    else f"{self.manual_stop_length:.4f}"
                )
            except ValueError:
                log.warning(f"Invalid stop length: {value}")
            finally:
                self.bar.update_buttons_state()

        keypad.show_with_callback(
            callback_fn=on_done,
            current_value=self.manual_stop_length or 0.0,
        )

    def _open_cutting_depth_keypad(self, *_):
        from rcp.components.popups.keypad import Keypad
        is_metric = self.app.formats.current_format == "MM"
        calculated = self._calculate_thread_depth()
        default = calculated if calculated is not None else 0.0
        keypad = Keypad(
            title=f"Enter Final Cutting Depth ({'mm' if is_metric else 'in'})"
        )
        keypad.integer = False

        def on_done(value):
            try:
                self.manual_cutting_depth = abs(float(value))
                self.bar.minor_diameter_text = (
                    f"{self.manual_cutting_depth:.3f}" if is_metric
                    else f"{self.manual_cutting_depth:.4f}"
                )
                self.bar.action_button_enabled = True
            except ValueError:
                log.warning(f"Invalid cutting depth: {value}")
                self.bar.action_button_enabled = False

        keypad.show_with_callback(
            callback_fn=on_done,
            current_value=self.manual_cutting_depth if self.manual_cutting_depth is not None else default,
        )
