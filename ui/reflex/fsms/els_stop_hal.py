"""Hardware abstraction layer for the elsStop register block.

The HAL is the single place that knows about firmware register names and
encoding. Methods are named in domain terms; callers (the FSM) never see
register keys. Replacing the firmware protocol means writing one new HAL.
"""
from kivy.logger import Logger

log = Logger.getChild(__name__)


class ElsStopHal:
    """Domain-named operations against the elsStop register block."""

    # Hysteresis values used by the firmware to debounce the stop trigger.
    HYSTERESIS_TIGHT = 0      # active retract / wizard — stop on first crossing
    HYSTERESIS_LOOSE = 800    # standalone stop — tolerate small overshoot

    def __init__(self, board):
        self._board = board

    @property
    def connected(self) -> bool:
        return self._board.connected

    # ── enable / active ───────────────────────────────────────────────
    def set_enable(self, enabled: bool) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['enable'] = 1 if enabled else 0

    def set_active(self, active: bool) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['active'] = 1 if active else 0

    def read_enable(self) -> bool:
        if not self._board.connected:
            return False
        return bool(self._board.device['elsStop']['enable'])

    def read_active(self) -> bool:
        if not self._board.connected:
            return False
        return bool(self._board.device['elsStop']['active'])

    # ── direction / hysteresis ────────────────────────────────────────
    def set_stop_direction(self, value: int) -> None:
        # Caller (FSM / controller / UI bar) computes the signed value
        # via ElsDispatcher.stop_direction_value(els_forward). The HAL
        # just writes the int the firmware expects (-1 or +1).
        if not self._board.connected:
            return
        self._board.device['elsStop']['stopDirection'] = int(value)

    def set_hysteresis_tight(self) -> None:
        self._set_hysteresis(self.HYSTERESIS_TIGHT)

    def set_hysteresis_loose(self) -> None:
        self._set_hysteresis(self.HYSTERESIS_LOOSE)

    def _set_hysteresis(self, counts: int) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['hysteresis'] = counts

    # ── stop target / scale source ────────────────────────────────────
    def set_stop_position(self, encoder_counts: int) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['stopPosition'] = encoder_counts

    def set_scale_index(self, scale_index: int) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['scaleIndex'] = scale_index

    def set_steps_to_go(self, steps: int) -> None:
        if not self._board.connected:
            return
        self._board.device['servo']['stepsToGo'] = steps

    # ── thread geometry ───────────────────────────────────────────────
    def set_thread_pitch_steps(self, tps_value: float) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['threadPitchSteps'] = tps_value

    def set_z_counts_per_pitch(self, value: float) -> None:
        # 0.0 disables the firmware's Z-scale-based phase correction.
        if not self._board.connected:
            return
        self._board.device['elsStop']['zCountsPerPitch'] = value

    def set_backlash_steps(self, magnitude: int) -> None:
        # uint32 magnitude in servo steps. The firmware derives the takeup
        # direction from sign(syncRatioNum) × sign(threadPitchSteps × zCountsPerPitch).
        if not self._board.connected:
            return
        self._board.device['elsStop']['backlashSteps'] = max(0, int(magnitude))

    # ── diagnostics / latch readbacks (low frequency) ─────────────────
    def read_reference_latched(self) -> bool:
        if not self._board.connected:
            return False
        return bool(self._board.device['elsStop']['referenceLatched'])

    def read_takeup_pending(self) -> bool:
        if not self._board.connected:
            return False
        return bool(self._board.device['elsStop']['takeupPending'])

    def read_latched_z(self) -> int:
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['latchedZ'])

    def read_latched_spindle(self) -> int:
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['latchedSpindle'])

    def read_last_ideal_advance(self) -> float:
        if not self._board.connected:
            return 0.0
        return float(self._board.device['elsStop']['lastIdealAdvance'])

    def read_last_actual_advance(self) -> float:
        if not self._board.connected:
            return 0.0
        return float(self._board.device['elsStop']['lastActualAdvance'])

    def read_last_phase_error(self) -> float:
        if not self._board.connected:
            return 0.0
        return float(self._board.device['elsStop']['lastPhaseError'])

    def read_last_correction(self) -> float:
        if not self._board.connected:
            return 0.0
        return float(self._board.device['elsStop']['lastCorrection'])

    # ── protocol version ──────────────────────────────────────────────
    def read_protocol_version(self) -> int:
        """Firmware register-layout version.

        Returns 0 when disconnected AND on firmware predating the register
        (an unwritten appended register reads 0), so callers must treat 0 as
        "too old / unknown" rather than as a version number.
        """
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['protocolVersion'])

    # ── backlash calibration ──────────────────────────────────────────
    # The command/ack split matters here: request_calibration() sets calCommand,
    # and the FIRMWARE clears it the instant the ISR consumes it — long before
    # the run finishes. Polling calCommand for completion would therefore report
    # "done" immediately and read a stale result. Edge-detect read_cal_seq().

    def set_cal_limits(self, ceiling_steps: int, motion_thresh_counts: int) -> None:
        """Push the two machine-specific calibration limits.

        A motion threshold of 0 disables detection and makes the firmware fail
        CLOSED (it never confirms), which is deliberate — an unconfigured
        threshold must refuse rather than wave every take-up through. Callers
        should treat 0 as "not commissioned", not as a usable default.
        """
        if not self._board.connected:
            return
        self._board.device['elsStop']['calCeilingSteps'] = max(0, int(ceiling_steps))
        self._board.device['elsStop']['calMotionThreshCounts'] = max(0, int(motion_thresh_counts))

    def read_servo_motion_params(self):
        """(maxSpeed, acceleration) as the firmware currently holds them."""
        if not self._board.connected:
            return (0.0, 0.0)
        return (float(self._board.device['servo']['maxSpeed']),
                float(self._board.device['servo']['acceleration']))

    def set_servo_motion_params(self, max_speed: float, acceleration: float) -> None:
        """Set the ramp parameters the firmware will use for commanded moves.

        Calibration needs this because it is the only feature that commands
        motion from cold: it inherits whatever maxSpeed the machine happens to
        be configured for, and at a slow setting a 2000-step sweep takes minutes
        while looking completely stationary. The caller is responsible for
        restoring the previous values.

        NOTE acceleration must never be 0: updateIndexingPosition computes
        stopDistance as (v*v/acceleration)/2, so a zero acceleration yields NaN,
        every ramp comparison against it is false, and the move hangs forever
        without ever starting.
        """
        if not self._board.connected:
            return
        self._board.device['servo']['maxSpeed'] = float(max_speed)
        self._board.device['servo']['acceleration'] = float(acceleration)

    def request_calibration(self) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['calCommand'] = 1

    def read_cal_seq(self) -> int:
        """Monotonic counter, incremented once per finished run (success OR
        refusal). This is the ack — edge-detect it to know a run completed."""
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['calSeq'])

    def read_cal_result(self) -> int:
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['calResult'])

    def read_cal_measured(self) -> list:
        """The three per-reversal lash measurements, in servo steps.

        Populated on failure too (a run that measured two reversals and then
        lost the carriage is diagnostically richer than a bare error code), so
        only trust these when read_cal_result() is ELS_CAL_OK.
        """
        if not self._board.connected:
            return [0, 0, 0]
        return [int(v) for v in self._board.device['elsStop']['calMeasured']]

    # ── take-up outcome ───────────────────────────────────────────────
    def read_takeup_result(self) -> int:
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['takeupResult'])

    def read_takeup_seq(self) -> int:
        """Increments once per take-up OUTCOME. takeupPending alone cannot
        distinguish completed-normally from host-cleared; this can."""
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['takeupSeq'])

    def read_diag_schema(self) -> int:
        """Which diagnostic probe the firmware was built with; 0 = none.

        This is the ONLY thing that says what the rest of the scratchpad means.
        protocolVersion deliberately does not move when a probe changes -- the
        register layout does not change, which is the whole point of reserving
        the block -- so a reader that skips this check will happily interpret
        one probe's numbers as another's. Check it, and refuse anything you do
        not recognise; never guess.
        """
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['diagSchema'])

    def read_diag_seq(self) -> int:
        """Increments once per COMPLETED capture. Edge-detect this.

        One register, so it is cheap enough to poll. There is deliberately no
        capture-in-progress register: a reader that polled one would race the
        ISR and could read a half-written trace.
        """
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['diagSeq'])

    def read_diag_capture(self) -> dict:
        """Read the whole scratchpad in one refresh. Call only after diag_seq
        has changed -- this is the expensive read the block is designed around
        (64 registers, roughly 12 ms of serial time at 115200 baud), and it has
        no business anywhere near the steady-state poll loop.

        Returns raw firmware values with no unit conversion. Bucket width is
        reported in ISR TICKS, and the tick period is deliberately NOT assumed
        here: reflex-fw's own documentation disagrees with itself about the ISR
        rate by 10x, so a conversion baked in at this layer would be a confident
        wrong answer. The recorder stores executionInterval alongside the trace
        so the time base is derivable from the same capture that needs it.
        """
        if not self._board.connected:
            return {}
        els = self._board.device['elsStop'].refresh()
        return {
            "schema": int(els['diagSchema']),
            "seq": int(els['diagSeq']),
            "bucket_ticks": int(els['diagBucketTicks']),
            "bucket_count": int(els['diagBucketCount']),
            "settle_ticks": int(els['diagSettleTicks']),
            "net_counts": int(els['diagNetCounts']),
            "trace": [int(v) for v in els['diagTrace']],
        }

    def read_takeup_thresh_counts(self) -> int:
        """Z counts the last take-up had to move to be confirmed.

        Firmware-DERIVED, not operator-set: computed from the commanded take-up
        minus the calibrated lash, so it tracks the calibration automatically.
        Falls back to the bare detection floor with no calibration on file or in
        turning mode. Pair with read_last_takeup_z_delta() to tell an operator
        what was wanted versus what happened.
        """
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['takeupThreshCounts'])

    def read_last_takeup_z_delta(self) -> int:
        """Signed Z counts moved across the last take-up, projected onto the
        take-up direction. NEGATIVE means the carriage moved the WRONG way —
        a distinct fault from "didn't move" and worth surfacing as such."""
        if not self._board.connected:
            return 0
        return int(self._board.device['elsStop']['lastTakeupZDelta'])

    def is_move_done(self) -> bool:
        """True only when the firmware's commanded indexing motion has been
        fully *executed*, not just consumed by the planner.

        The firmware's `updateIndexingPosition` decrements `stepsToGo` as
        it accumulates `positionIncrement` into `desiredSteps`. The step
        pulse generator is separately rate-limited by `servoCycles`
        (derived from maxSpeed) and emits one pulse per `servoCycles`
        ticks until `currentSteps` catches up to `desiredSteps`. When
        `stepsToGo == 0` alone the planner is done but pulses are often
        still in flight — declaring the move complete then lets the ELS
        FSM re-issue a follow-up retract on top of the still-pending
        pulses, producing the proportional overshoot we hit in testing.
        Wait for the pulses to actually flush by also requiring
        `currentSteps == desiredSteps`.
        """
        if not self._board.connected:
            return False
        if self._board.device['servo']['stepsToGo'] != 0:
            return False
        return (self._board.device['servo']['currentSteps']
                == self._board.device['servo']['desiredSteps'])
