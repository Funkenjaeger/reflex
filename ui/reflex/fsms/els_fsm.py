from kivy.logger import Logger
from transitions import Machine

from reflex.dispatchers import els, board
from reflex.fsms.els_stop_hal import ElsStopHal
from reflex.fsms.fsm_event_bus import fsm_event_bus as bus

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
        # has_retract_target is a CONDITION, not just an on_enter guard: entering
        # 'retracting' and then refusing to move leaves the FSM parked there with
        # no move poller bound and no path out (retract_done is only published by
        # that poller). Refusing the transition keeps it in 'stopped' instead.
        {'trigger': 'retract', 'source': 'stopped', 'dest': 'retracting',
         'conditions': ['is_ready_to_retract', 'has_retract_target']},
        {'trigger': 'retract_done', 'source': 'retracting', 'dest': 'stopped', 'conditions': ['is_retracted']},
        {'trigger': 'retract_done', 'source': 'retracting', 'dest': '='},
        {'trigger': 'cut', 'source': 'stopped', 'dest': 'cutting', 'conditions': ['is_ready_to_cut']}, 
        {'trigger': 'stop_active', 'source': 'cutting', 'dest': 'stopped'},
        {'trigger': 'disable', 'source': ['stopped', 'retracting', 'alarm'], 'dest': 'disabled'},
        {'trigger': 'fault', 'source': '*', 'dest': 'alarm'},
    ]

    def __init__(self, els: els, board: board, hal: ElsStopHal, controller):
        self.els = els
        self.board = board
        self.servo = board.servo
        self.hal = hal
        self.controller = controller

        self.fsm = Machine(
            model=self,
            states=self.STATES,
            transitions=self.TRANSITIONS,
            initial="disabled",
            after_state_change="_broadcast",
            queued=True,
        )
        
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

        # Set by reconcile_firmware_on_connect when it finds the firmware was
        # driving the carriage at connect and stops it. Holds the register
        # snapshot so the UI can tell the operator WHAT it interrupted; None
        # means no pass was interrupted this session. Read once and cleared by
        # whoever presents it.
        self.interrupted_pass = None


    # ——— transition side effects ———

    def _on_prepare_enable(self):
        """Mark that we're entering stopped from disabled so on_enter_stopped
        can arm ELS with a fresh stopPosition."""
        self._engaging = True

    def on_enter_retracting(self):
        # Frozen leadscrew encoder captured when the operator set retract_z
        # (anchored to the physical position, immune to display-frame changes).
        enc_target = self.controller.retract_z_encoder
        if enc_target is None:
            # No committed retract target — REFUSE rather than fabricate a move
            # to the 0.0 display default (which on a shoulder-zeroed setup is the
            # chuck). The UI gates this upstream (retract_z_valid); this is a
            # defensive backstop, symmetric with on_enter_cutting's None-abort.
            log.error("on_enter_retracting: no committed retract_z — refusing to move")
            return
        enc_current = self._saddle_input.encoderCurrent
        # Invert the delta: DRO and servo have opposite polarity on the lathe.
        # Positive servo steps move toward the shoulder (cutting direction), so
        # retracting requires negative steps even when the DRO position is larger
        # at retract_z than at stop_z.
        enc_delta = enc_current - enc_target
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

        # ARM the ELS stop and verify the safety-critical writes are acknowledged
        # BEFORE releasing the cut. stopPosition, scaleIndex and enable are
        # fire-and-forget over Modbus; if any silently failed the cut could run
        # against a stale stop (wrong shoulder) or — on the engage-past-stop path
        # where `enable` is 0 until this point, with a feed possibly already
        # running — with NO armed stop at all (safety audit #4 + review). Every
        # Modbus write is ACK'd by the firmware (minimalmodbus raises on a
        # missing/bad response, which the write helpers turn into
        # connection_manager.connected = False), so accumulate that per-write ACK
        # — a later write recovering the link can't mask an earlier failure.
        cm = self.board.connection_manager
        # Write stopPosition and scaleIndex individually (not via set_stop_z,
        # which bundles both) so the per-write ACK is checked at the right
        # granularity — otherwise the scaleIndex write would recover `connected`
        # and mask a failed stopPosition write. stopPosition is the FROZEN
        # leadscrew encoder captured when the operator set the stop — immune to
        # any DRO re-zero / units switch since (the physical shoulder is fixed).
        enc = self.controller.stop_z_encoder
        armed_ok = enc is not None
        if armed_ok:
            self.hal.set_stop_position(enc)
            armed_ok = bool(cm.connected)
        self.hal.set_scale_index(self._saddle_input.inputIndex)
        armed_ok = armed_ok and bool(cm.connected)
        if self.controller.is_threading:
            self.push_thread_geometry()
            self.hal.set_backlash_steps(int(self.els.els_backlash_steps))
        else:
            # Clear thread geometry so firmware skips takeup/phase-correction
            # on the active→inactive transition below.
            self.hal.set_thread_pitch_steps(0.0)
            self.hal.set_z_counts_per_pitch(0.0)
            self.hal.set_backlash_steps(0)
        # Arm `enable` here (not just post-release) and verify it — this is the
        # first arming on the engage-past-stop path, and the one thing that
        # actually stops the feed. (stopDirection/hysteresis are re-asserted
        # post-release; their values are already correct from engage time.)
        self.hal.set_enable(True)
        armed_ok = armed_ok and bool(cm.connected)

        if not armed_ok:
            # A safety-critical stop write wasn't confirmed — do NOT release the
            # cut. Stop any feed and fault out rather than cut against an
            # unverified / unarmed stop.
            log.error("on_enter_cutting: ELS stop not acknowledged — "
                      "aborting cut (stop not released)")
            self.board.servo.stop_feed()
            bus.publish("alarm_raised",
                        reason="ELS stop not confirmed — cut aborted. Check the "
                               "controller connection and try again.")
            # Defer the fault transition to the top level: triggering it from
            # inside this (nested) cut transition callback doesn't reliably
            # process on a queued machine.
            from kivy.clock import Clock
            Clock.schedule_once(lambda _dt: self._raise_stop_write_fault(), 0)
            return

        self.hal.set_active(False)
        self.hal.set_stop_direction(
            self.els.stop_direction_value(self.controller.els_forward)
        )
        if self.controller.retract_enabled or self.controller.wizard_enabled:
            self.hal.set_hysteresis_tight()
        else:
            self.hal.set_hysteresis_loose()
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
            self.arm_idle_stop()

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
        # Safety: disengaging ELS removes the stop that was gating the feed, so
        # also stop any running sync feed — otherwise a spindle-synced feed keeps
        # driving the carriage with no auto-stop (audit H6). Idempotent.
        #
        # SYNC OFF FIRST, THEN ENABLE, THEN FEED. Not cosmetic ordering.
        # Dropping enable clears elsStop.active in firmware, and the firmware's
        # servoEnableTask turns the feed back ON whenever it sees
        # (any syncEnable) && !active. So between "enable = 0" and "syncEnable =
        # 0" there is a window in which that task re-asserts servoMode = 1 —
        # and nothing in firmware ever clears it again.
        #
        # Previously syncEnable was cleared only as a side effect of the
        # servoMode binding in dispatchers/els.py, which by construction fires
        # AFTER servoMode changes, so the window was always open. Measured at
        # ~1 disengage in 7-10 leaving the carriage feeding — 1.825 mm in 3 s and
        # still going — with the ELS stop simultaneously disarmed, because the
        # firmware gates the stop check on enable. Feed running, backstop gone.
        feed_was_on = self.board.servo.servoMode != 0
        self.hal.stop_sync()
        self.hal.set_enable(False)
        self.board.servo.stop_feed()
        if feed_was_on and not self.board.connection_manager.connected:
            # A stop-feed write was attempted but not acknowledged (link down).
            # Nothing more software can do to stop it here — surface it loudly.
            # (Only when the feed was actually on, so an offline idle disengage
            # doesn't cry wolf.)
            log.error("on_enter_disabled: feed-stop write NOT acknowledged — "
                      "sync feed may still be running (controller link down)")

    def _raise_stop_write_fault(self):
        """Top-level fault after an unacknowledged cut-stop write (scheduled from
        on_enter_cutting). Faults the domain FSM and mirrors it to the UI FSM."""
        if self.state != 'alarm':
            self.fault()
        bus.publish("els_alarm")

    def on_enter_alarm(self):
        # Fault state — drive the machine to a safe idle: stop the feed, disarm
        # the stop, and detach the move poller. Reached e.g. when a cut's stop
        # writes weren't acknowledged (on_enter_cutting).
        self.board.unbind(update_tick=self._on_board_update)
        self.board.servo.stop_feed()
        self.hal.set_enable(False)

    # ——— condition-checking methods ———
    def is_ready_to_retract(self):
        # Domain-level X retract gate — REMOVED. It duplicated, in raw encoder
        # counts with no unit conversion, protection the controller already
        # provides correctly in display units: _x_clear_of_start_dia (surfaced
        # as "Move X clear of start diameter, then retract" via
        # ui_controller._apply_policy's waiting_to_retract branch, and enforced
        # again by may_retract()/has_retract_target at the FSM boundary). The
        # old check_x_retract/safe_x/inside trio here defaulted to a no-op
        # (check_x_retract=False) and, when an earlier version of this
        # one-liner accidentally made it live, refused every retract on a
        # machine whose cross-slide encoder sat below its power-on zero — see
        # git history (is_ready_to_retract, pre-e3cbc5a) for the precedence
        # bug this replaced. This transition condition is now unconditional.
        return True

    def has_retract_target(self):
        """True iff the operator has committed a retract target. Without one
        there is nothing to move to — on_enter_retracting would refuse rather
        than fabricate a move to the 0.0 display default (which on a
        shoulder-zeroed setup is the chuck)."""
        if self.controller.retract_z_encoder is None:
            log.warning("retract refused: no committed retract_z (Start Z not set)")
            return False
        return True

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
    def z_axis(self):
        """Resolve the ELS Z axis live.

        Axis assignment can change after the FSM is built (config load, setup
        edits) and defaults to -1/unassigned until the operator maps it, so we
        never cache it. on_enter_stopped used to dereference a stale None here
        and crash when no Z axis was assigned.
        """
        return self.els.get_z_axis()

    @property
    def x_axis(self):
        """Resolve the ELS X axis live (see :attr:`z_axis`)."""
        return self.els.get_x_axis()

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

    def arm_idle_stop(self):
        """Arm ELS while engaged-and-idle so an unexpected spindle start is
        arrested before "Cut" is ever pressed.

        Called at engage, and again whenever the operator commits a stop while
        already engaged (engaging with no stop set is the normal order of
        operations, so arming has to be retried then or the protection simply
        never appears).

        Refuses when no stop is committed: push_stop_to_firmware() is a no-op in
        that case, so setting `enable` would arm ELS against whatever
        stopPosition the FIRMWARE still holds from a previous session — a
        different shoulder, or a different part. It also made
        ``feed_without_armed_stop()`` report "armed", which silently skipped the
        no-stop feed confirmation. Refuses too when Z is already past the stop,
        where arming would fire ELS immediately → backlash takeup.
        """
        if self.controller.stop_z_encoder is None:
            log.info("arm_idle_stop: no committed stop — leaving ELS disarmed")
            return False
        z_pos = self.z_axis.scaledPosition
        cut_dir = self.els.stop_direction_value(self.controller.els_forward)
        diff = (z_pos - self.controller.stop_z) * cut_dir
        if diff > 0:  # Z is past stop_z — arming would fire ELS on the spot
            log.info(
                f"arm_idle_stop: Z past stop — not arming "
                f"(stop_z={self.controller.stop_z} z_pos={z_pos})"
            )
            return False
        self.push_stop_to_firmware()
        # Set active=1 before enable so ELS arms in STOPPED state — sync motion
        # paused until the operator clicks Cut (which clears active via
        # on_enter_cutting, triggering resume/takeup).
        self.hal.set_active(True)
        self.hal.set_enable(True)
        log.info(
            f"arm_idle_stop: armed ELS stopped with "
            f"stop_z={self.controller.stop_z} z_pos={z_pos}"
        )
        return True

    def reconcile_firmware_on_connect(self):
        """Drive the firmware's retained elsStop block to match THIS session's
        FSM state when a connection is (re)established.

        Firmware retains elsStop.{enable, active, stopPosition} across app
        restarts (observed on the real machine 2026-08-01: after a restart the
        previous session's servoMode was still set). Nothing else clears them
        at startup — set_enable(False) otherwise runs only on the disable/alarm
        transitions, and the FSM's initial 'disabled' state fires no on_enter —
        so a fresh session silently inherits the PREVIOUS session's armed stop,
        and feed_without_armed_stop() (which trusts the firmware enable bit)
        would skip the no-stop feed confirmation against a stale shoulder.

        Policy by state:
        - disabled / alarm → clear enable+active. A cleared enable makes the
          retained stopPosition inert.
        - stopped (engaged-idle) → re-assert direction/hysteresis and re-arm via
          arm_idle_stop() (self-gates on a committed stop + Z on the safe
          side). Covers a firmware reboot mid-session losing our armed stop.
          If arming refuses, clear enable+active — a retained arm must not
          outlive its session either.
        - cutting / retracting → hands off, log only: motion may be live, and
          blindly rewriting could disarm a stop that is actively protecting the
          cut. (Full link-loss-mid-cut recovery is a known separate gap.)
        """
        # Push the calibration limits FIRST, in every state, before any
        # state-specific policy below.
        #
        # calMotionThreshCounts is not only a calibration input — the per-pass
        # take-up confirmation gate reads it on EVERY pass, and it fails CLOSED
        # at 0. It is otherwise written only when a calibration run starts, so a
        # machine that was commissioned in settings but has not run a
        # calibration this session would refuse every take-up with "carriage not
        # moving" while the drivetrain is perfectly healthy. Firmware does not
        # retain it across a power cycle either. Pushing on connect is what
        # makes the setting mean what the settings screen says it means.
        self.hal.set_cal_limits(
            int(self.els.els_cal_ceiling_steps),
            int(self.els.els_cal_motion_thresh_counts),
        )

        # SNAPSHOT BEFORE TEARDOWN. The firmware keeps running across a UI
        # restart, so these registers are the only surviving evidence of what
        # the machine was doing. Read first, because everything below destroys
        # it. Never raises -- a diagnostic must not block a safety teardown.
        flight = self.hal.read_motion_in_flight()

        state = self.state
        if state in ('disabled', 'alarm'):
            # SYNC OFF FIRST, ALWAYS, and before enable/active. Decided
            # 2026-08-16; see the method docstring for the full reasoning.
            #
            # Two distinct hazards, both closed by this one call:
            #
            # 1. RELEASING A HELD STOP. active == 1 is what physically holds the
            #    carriage at the shoulder -- firmware only accumulates sync steps
            #    while active == 0 (reflex-fw Ramps.c:815), and clearing active
            #    1->0 is the RESUME trigger (Ramps.c:826). So clearing active
            #    with sync still live is a resume command, issued while enable is
            #    also being cleared -- carriage off the shoulder with no armed
            #    stop. The dwell at a shoulder is unbounded (operator backing out
            #    a tool, checking a thread), which makes this the WIDE window,
            #    not the narrow one.
            #
            # 2. THE SERVOENABLETASK RACE. Clearing enable clears active, and
            #    while any syncEnable remains set the firmware task re-asserts
            #    servoMode = 1 and nothing ever clears it.
            #
            # ACCEPTED COST: if the previous session died MID-PASS, this stops
            # the carriage with the tool in the cut. In threading that risks a
            # minor crash. Chosen deliberately over the alternative -- leaving a
            # pass running with the stop disarmed -- because that is worse in
            # every case, and this is safest in the large majority. The operator
            # is TOLD when it happens rather than left to infer it (below), so
            # the one time it bites there is no ambiguity about why.
            #
            # Window for that cost: ~10 s (RestartSec=5 plus ~5 s to connect,
            # measured on elspi 2026-08-16) against a pass of comparable length,
            # and it needs a crash to reach at all.
            self.hal.stop_sync()
            self.hal.set_enable(False)
            self.hal.set_active(False)
            log.info(
                f"reconcile_firmware_on_connect: cleared retained ELS stop "
                f"(state={state}, motion_in_flight={flight})"
            )
            if flight.get('moving'):
                # Deliberately loud. This is the accepted-cost case actually
                # occurring, and the operator needs to know the carriage was
                # stopped BY US and not by the machine finishing its pass.
                log.warning(
                    f"reconcile_firmware_on_connect: firmware was driving the "
                    f"carriage at connect ({flight}) — sync disabled and stop "
                    f"cleared; a pass was interrupted"
                )
                self.interrupted_pass = flight
                bus.publish("els_pass_interrupted")
        elif state == 'stopped':
            self.hal.set_stop_direction(
                self.els.stop_direction_value(self.controller.els_forward)
            )
            if self.controller.retract_enabled or self.controller.wizard_enabled:
                self.hal.set_hysteresis_tight()
            else:
                self.hal.set_hysteresis_loose()
            armed = self.arm_idle_stop()
            if not armed:
                self.hal.set_enable(False)
                self.hal.set_active(False)
            log.info(
                f"reconcile_firmware_on_connect: re-asserted engaged-idle "
                f"(armed={armed})"
            )
        else:
            log.warning(
                f"reconcile_firmware_on_connect: state={state} — leaving "
                f"firmware ELS stop untouched (motion may be live)"
            )

    def push_stop_to_firmware(self):
        """Push the operator's frozen stop encoder to firmware + set scaleIndex.

        Used by the engage-arm (on_enter_stopped) and the idle-propagate path.
        The stop is anchored to the encoder captured when the operator set it
        (controller.stop_z_encoder); a no-op when no stop is committed."""
        enc = self.controller.stop_z_encoder
        if enc is None:
            return
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

        INVARIANT THIS DEPENDS ON (closed-loop backlash calibration, 2026-08-08).
        ``els_backlash_steps`` must hold the COMMANDED take-up (measured lash +
        margin), not the raw measured lash. The two are deliberately separate
        properties on ElsDispatcher — ``els_cal_last_measured_steps`` holds the
        measurement, ``els_backlash_steps`` holds the command — and only the
        command is written to the firmware's ``backlashSteps`` register, so this
        margin is computed against the number the firmware will actually drive.
        Storing the raw measurement here instead would under-budget this margin
        by exactly the take-up margin.

        Given that, the formula stays conservative for the right reason: it
        treats the ENTIRE take-up as potential carriage travel, whereas
        physically most of it is absorbed crossing the lash window and only the
        margin moves the carriage. Over-budgeting is the safe direction.

        NOTE: this margin governs starting a CUT. It does not govern a
        calibration run, which moves the carriage bidirectionally by up to
        ``els_cal_ceiling_steps`` per leg. Calibration is gated separately — the
        firmware refuses it unless ``elsStop.enable == 0``, and the operator
        modal owns "tool clear of the work".
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
