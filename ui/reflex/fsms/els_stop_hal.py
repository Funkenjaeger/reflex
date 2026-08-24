"""Hardware abstraction layer for the elsStop register block.

The HAL is the single place that knows about firmware register names and
encoding. Methods are named in domain terms; callers (the FSM) never see
register keys. Replacing the firmware protocol means writing one new HAL.
"""
from kivy.logger import Logger

log = Logger.getChild(__name__)


class TickReads:
    """elsStop values served from THIS BOARD TICK's snapshot, not from the wire.

    WHAT THIS IS FOR, and the line it draws. Every read on ElsStopHal below is
    live: ``device['elsStop']['active']`` is a Modbus exchange, not a cache
    lookup. That is correct for an on-demand read — an apply, a wizard step, a
    calibration run — where the operator has just acted and the answer must
    describe the machine NOW. It was ruinous for the tick-driven pollers, which
    between them made several such exchanges 30 times a second and lost comms on
    six of six cuts on 2026-08-23 by asking the firmware to answer them at the
    moment its 100 kHz ISR had nothing spare (see Board._refresh_els_stop_snapshot).

    So: the pollers read through here, everything else keeps reading live. The
    split is deliberately visible at the call site — ``hal.tick.active()`` reads
    differently from ``hal.read_active()`` — because the two are not
    interchangeable and a future reader must not be able to reach for the cheap
    one by accident. A poller gets once-per-tick freshness, which is all it ever
    had: it fires once per tick.

    FABRICATION IS REPORTED THE SAME WAY IT ALWAYS WAS. When the board holds no
    snapshot — the refresh failed, or the link is down — these return the same
    fallbacks the live readers return, through the same ElsStopHal._no_link, so
    reads_baseline()/reads_fabricated_since() keep working for the callers that
    discard a poll rather than act on a value they cannot trust.
    """

    def __init__(self, hal: "ElsStopHal"):
        self._hal = hal

    def _get(self, key, fallback):
        snapshot = self._hal._board.els_stop_values
        if not snapshot:
            # No snapshot this tick. Indistinguishable, to a caller, from a
            # per-field read that timed out — which is exactly right, because
            # that is what it replaced.
            return self._hal._no_link(fallback)
        # A missing key is NOT this case and must not be swallowed into it: an
        # empty snapshot is a runtime condition, a name the register map does
        # not have is a bug, and turning the second into a permanent silent
        # "communications failure" would hide it forever. Let it raise.
        return snapshot[key]

    def active(self) -> bool:
        return bool(self._get('active', 0))

    def takeup_seq(self) -> int:
        return int(self._get('takeupSeq', 0))

    def takeup_result(self) -> int:
        return int(self._get('takeupResult', 0))

    def last_takeup_z_delta(self) -> int:
        return int(self._get('lastTakeupZDelta', 0))

    def takeup_thresh_counts(self) -> int:
        return int(self._get('takeupThreshCounts', 0))

    def phase_offset_steps(self) -> int:
        return int(self._get('phaseOffsetSteps', 0))

    def diag_seq(self) -> int:
        return int(self._get('diagSeq', 0))

    def current_mode(self) -> int:
        return int(self._get('machineMode', 0))


class ElsStopHal:
    """Domain-named operations against the elsStop register block."""

    # Hysteresis values used by the firmware to debounce the stop trigger.
    HYSTERESIS_TIGHT = 0      # active retract / wizard — stop on first crossing
    HYSTERESIS_LOOSE = 800    # standalone stop — tolerate small overshoot

    def __init__(self, board):
        self._board = board
        # Built once. It holds no state of its own -- it reads whatever the
        # board is holding at the moment it is asked -- so a fresh one per tick
        # would be pure allocation.
        self.tick = TickReads(self)

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

    def stop_sync(self) -> None:
        """Clear syncEnable on every scale. The MOTION SOURCE off-switch.

        ORDER MATTERS AT DISENGAGE, and this is the whole point of the method.
        The firmware's servoEnableTask (reflex-fw Ramps.c, 100 ms period) does:

            if (anySyncMotionEnabled && !elsStop.active && servoMode != 2)
                servoMode = 1;

        -- it only ever turns the feed ON, and the `!active` term is a condition
        that DISENGAGE ITSELF CREATES, because dropping enable clears active. So
        for as long as any syncEnable is still set after enable has gone to 0,
        that task is entitled to switch the feed back on, and nothing in the
        firmware will ever switch it off again.

        Clearing sync FIRST removes the `anySyncMotionEnabled` term before the
        window can open. Relying on the servoMode->syncEnable binding to do it
        (dispatchers/els.py) is what left the window: the binding fires as a
        REACTION to servoMode, so syncEnable was necessarily written LAST.

        Measured before this existed: ~1 disengage in 7-10 left the carriage
        feeding, 1.825 mm in 3 s and still going, with the ELS stop disarmed
        (enable == 0 gates the stop check off) -- i.e. no backstop left.
        """
        if not self._board.connected:
            return
        for scale in self._board.device['scales']:
            scale['syncEnable'] = 0

    def read_motion_in_flight(self) -> dict:
        """Snapshot of whether the FIRMWARE is driving the carriage right now.

        Read BEFORE init tears anything down. The firmware keeps running across a
        UI restart -- observed on the real machine 2026-08-01 -- so these
        registers are the only surviving evidence of what the machine was doing
        when the previous session ended. The FSM's own state does not survive the
        process and always comes up 'disabled', which is why it cannot be used to
        make this call.

        moving := a live motion SOURCE (some syncEnable) and a commanded servo
        (servoMode != 0) and NOT held at a shoulder (active == 0). The active
        term is what separates 'mid-pass' from 'parked at the stop waiting for
        the operator' -- both have sync on, only one is moving.
        """
        if not self._board.connected:
            return self._no_link({'moving': False, 'reason': 'not connected'})
        try:
            stop = self._board.device['elsStop'].refresh()
            sync = any(
                self._board.device['scales'][i]['syncEnable']
                for i in range(len(self._board.device['scales']))
            )
            servo_mode = self._board.servo.servoMode
            active = bool(stop.get('active'))
            enable = bool(stop.get('enable'))
            return {
                'moving': bool(sync) and servo_mode != 0 and not active,
                'sync': bool(sync),
                'servoMode': servo_mode,
                'active': active,
                'enable': enable,
            }
        except Exception as e:
            # Never let a diagnostic read block the teardown that follows it.
            return {'moving': False, 'reason': f'read failed: {e}'}

    def read_enable(self) -> bool:
        if not self._board.connected:
            return self._no_link(False)
        return bool(self._board.device['elsStop']['enable'])

    def read_active(self) -> bool:
        if not self._board.connected:
            return self._no_link(False)
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
            return self._no_link(False)
        return bool(self._board.device['elsStop']['referenceLatched'])

    def read_takeup_pending(self) -> bool:
        if not self._board.connected:
            return self._no_link(False)
        return bool(self._board.device['elsStop']['takeupPending'])

    def read_latched_z(self) -> int:
        if not self._board.connected:
            return self._no_link(0)
        return int(self._board.device['elsStop']['latchedZ'])

    def read_latched_spindle(self) -> int:
        if not self._board.connected:
            return self._no_link(0)
        return int(self._board.device['elsStop']['latchedSpindle'])

    def read_last_ideal_advance(self) -> float:
        if not self._board.connected:
            return self._no_link(0.0)
        return float(self._board.device['elsStop']['lastIdealAdvance'])

    def read_last_actual_advance(self) -> float:
        if not self._board.connected:
            return self._no_link(0.0)
        return float(self._board.device['elsStop']['lastActualAdvance'])

    def read_last_phase_error(self) -> float:
        if not self._board.connected:
            return self._no_link(0.0)
        return float(self._board.device['elsStop']['lastPhaseError'])

    def read_last_correction(self) -> float:
        if not self._board.connected:
            return self._no_link(0.0)
        return float(self._board.device['elsStop']['lastCorrection'])

    # ── protocol version ──────────────────────────────────────────────
    def read_protocol_version(self) -> int:
        """Firmware register-layout version.

        Returns 0 when disconnected AND on firmware predating the register
        (an unwritten appended register reads 0), so callers must treat 0 as
        "too old / unknown" rather than as a version number.
        """
        if not self._board.connected:
            return self._no_link(0)
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
            return self._no_link((0.0, 0.0))
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
            return self._no_link(0)
        return int(self._board.device['elsStop']['calSeq'])

    def read_cal_result(self) -> int:
        if not self._board.connected:
            return self._no_link(0)
        return int(self._board.device['elsStop']['calResult'])

    def read_cal_measured(self) -> list:
        """The three per-reversal lash measurements, in servo steps.

        Populated on failure too (a run that measured two reversals and then
        lost the carriage is diagnostically richer than a bare error code), so
        only trust these when read_cal_result() is ELS_CAL_OK.
        """
        if not self._board.connected:
            return self._no_link([0, 0, 0])
        return [int(v) for v in self._board.device['elsStop']['calMeasured']]

    def reads_baseline(self) -> int:
        """Snapshot to detect fabricated reads across a group of reads.

        Pair with :meth:`reads_fabricated_since`. Callers whose DECISION
        depends on a value need both; callers that merely display one do not.
        """
        return self._board.connection_manager.read_failures

    def reads_fabricated_since(self, baseline: int) -> bool:
        """Did any read since `baseline` return a value the controller never
        sent -- a failed frame, or a read attempted with no link?"""
        return self._board.connection_manager.reads_failed_since(baseline)

    def _no_link(self, fallback):
        """Record a read that could not happen, and hand back the fallback.

        Every read below short-circuits through here when the link is down, and
        so does every snapshot read in TickReads when the board holds no
        snapshot for this tick — the two are the same event to a consumer, and
        counting them the same way is what keeps one guard sufficient.
        The fallback is usually 0, and in this register map 0 is never neutral
        -- it reads as "no offset", "not enabled", "sequence reset". Counting
        it is what lets a caller whose decision depends on the value tell a
        real zero from a fabricated one; callers that merely display something
        ignore the counter and are unaffected.

        Shares ConnectionManager.read_failures with genuine frame failures on
        purpose: to a consumer the two are the same event, and giving them
        separate counters would mean every guard had to remember to check both.
        """
        self._board.connection_manager.read_failures += 1
        return fallback

    # ── manual reference latch (interactive re-sync) ──────────────────
    # Same command/ack split as calibration: request_latch() sets latchCommand,
    # the FIRMWARE clears it the instant the ISR consumes it, and latchSeq is
    # the ack — edge-detect it against a baseline captured at request time.
    # A latch requested with enable == 0 is consumed with NO seq increment;
    # the absent ack IS the refusal.

    def request_latch(self) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['latchCommand'] = 1

    def read_latch_seq(self) -> int:
        """Monotonic counter, incremented once per ACCEPTED manual latch."""
        if not self._board.connected:
            return self._no_link(0)
        return int(self._board.device['elsStop']['latchSeq'])

    # ── ISR headroom ──────────────────────────────────────────────────
    # The worst SynchroRefreshTimerIsr duration seen, in CPU cycles, against a
    # budget of 1000 (100 MHz core, 10 us tick). Everything else on the chip --
    # the Modbus task, the USART RX interrupt -- runs in whatever the ISR
    # leaves, so this is the number that says whether a comms timeout was a
    # load problem or a peripheral one.
    #
    # Read ON DEMAND, deliberately not on the tick: it is a diagnostic, a peak
    # does not go stale, and putting it on the tick would add traffic to the
    # very thing it exists to diagnose.

    def read_execution_cycles_peak(self) -> int:
        """Worst ISR duration since the last reset, in CPU cycles."""
        if not self._board.connected:
            return self._no_link(0)
        return int(self._board.device['elsStop']['executionCyclesPeak'])

    def reset_execution_cycles_peak(self) -> None:
        """Arm a fresh measurement.

        The ISR only ever RAISES the value, so a host write of zero that lands
        between its compare and its store is simply lost -- write again. That
        costs one repeat and nothing else, which is a better trade than a
        command/ack pair for a diagnostic counter.
        """
        if not self._board.connected:
            return
        self._board.device['elsStop']['executionCyclesPeak'] = 0

    # ── thread-phase offset (widening a groove past the cutter) ───────
    # The command/ack split once more, with one addition that matters: the
    # 32-bit Pending MUST be written before the command, never after. The ISR
    # reads Pending only under a nonzero command, and that ordering is the only
    # thing standing between a two-register write and a torn read of a value
    # that displaces the thread.
    #
    # The firmware holds ONE absolute total and does not accumulate. Running
    # totals are built HERE: read the total, add the entry, write the sum. That
    # is also what makes Clear trivial -- it is an apply of zero, and the
    # firmware acks it like any other.
    #
    # Applied only while enable == 1; an offset outside a job would be wiped by
    # the next enable edge, so the absent ack IS the refusal.

    def request_phase_offset(self, total_steps: int) -> None:
        """Apply an ABSOLUTE total, in leadscrew steps. Not a delta.

        Pending is written first and the command second, in that order, for
        the torn-read reason above. Callers wanting a cumulative shift pass
        read_phase_offset_steps() + entry.
        """
        if not self._board.connected:
            return
        self._board.device['elsStop']['phaseOffsetPending'] = int(total_steps)
        self._board.device['elsStop']['phaseOffsetCommand'] = 1

    def read_phase_offset_steps(self) -> int:
        """The live cumulative total the firmware is applying, leadscrew steps.

        Firmware-owned and cleared on the enable 0->1 edge, so this is the only
        honest source for the running total -- a UI-side copy would survive a
        job change the firmware just discarded.
        """
        if not self._board.connected:
            return self._no_link(0)
        return int(self._board.device['elsStop']['phaseOffsetSteps'])

    def read_phase_offset_seq(self) -> int:
        """Monotonic counter, incremented once per ACCEPTED apply."""
        if not self._board.connected:
            return self._no_link(0)
        return int(self._board.device['elsStop']['phaseOffsetSeq'])

    # ── take-up outcome ───────────────────────────────────────────────
    def read_takeup_result(self) -> int:
        if not self._board.connected:
            return self._no_link(0)
        return int(self._board.device['elsStop']['takeupResult'])

    def read_takeup_seq(self) -> int:
        """Increments once per take-up OUTCOME. takeupPending alone cannot
        distinguish completed-normally from host-cleared; this can."""
        if not self._board.connected:
            return self._no_link(0)
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
            return self._no_link(0)
        return int(self._board.device['elsStop']['diagSchema'])

    def read_current_mode(self) -> int:
        """Firmware-derived machine mode (ELS_MMODE_*), live.

        Reads the PERMANENT `machineMode` register, which every build
        publishes every ~100 ms — release firmware included. No schema gate,
        and callers must not add one.

        Until 2026-08-22 this read `diagCaptureTicks`, where a mode-watch
        probe republished the mode; it was meaningful only while such a probe
        was flashed, and probes are one-at-a-time. That made the rung-2 census
        silently uncollectable during any session using a different probe —
        which was both lathe sessions on 2026-08-21/22.
        """
        if not self._board.connected:
            return self._no_link(0)
        return int(self._board.device['elsStop']['machineMode'])

    def read_diag_seq(self) -> int:
        """Increments once per COMPLETED capture. Edge-detect this.

        One register, so it is cheap enough to poll. There is deliberately no
        capture-in-progress register: a reader that polled one would race the
        ISR and could read a half-written trace.
        """
        if not self._board.connected:
            return self._no_link(0)
        return int(self._board.device['elsStop']['diagSeq'])

    def read_diag_capture(self) -> dict:
        """Read the whole scratchpad in one refresh. Call only after diag_seq
        has changed -- this is the expensive read the block is designed around
        (64 registers, roughly 12 ms of serial time at 115200 baud), and it has
        no business anywhere near the steady-state poll loop.

        DELIBERATELY STILL LIVE, even though the board now holds a per-tick
        snapshot of this whole block. The recorder's contract is "the seq edge
        says a capture COMPLETED; then read the block", and a snapshot cannot
        promise that ordering: it is assembled from two FC3 responses, so a
        capture completing between the two would pair one capture's seq with
        the next capture's trace -- and the recorder would then write the same
        trace twice under two different sequence numbers. Re-reading after the
        edge keeps the two phases genuinely separate. The cost is two exchanges
        on the rare tick a capture lands, against nothing in steady state,
        which is exactly the split the diagSeq/scratchpad design asks for.

        Returns raw firmware values with no unit conversion. Bucket width is
        reported in ISR TICKS, and the tick period is deliberately NOT assumed
        here: reflex-fw's own documentation disagrees with itself about the ISR
        rate by 10x, so a conversion baked in at this layer would be a confident
        wrong answer. The recorder stores executionInterval alongside the trace
        so the time base is derivable from the same capture that needs it.
        """
        if not self._board.connected:
            return self._no_link({})
        els = self._board.device['elsStop'].refresh()
        return {
            "schema": int(els['diagSchema']),
            "seq": int(els['diagSeq']),
            "bucket_ticks": int(els['diagBucketTicks']),
            "bucket_count": int(els['diagBucketCount']),
            "settle_ticks": int(els['diagSettleTicks']),
            "net_counts": int(els['diagNetCounts']),
            # How long the servo stayed silent, versus when Z last MOVED. Two
            # different questions: the first bounds the measurement window, the
            # second is the settle time itself.
            "capture_ticks": int(els['diagCaptureTicks']),
            "end_reason": int(els['diagEndReason']),
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
            return self._no_link(0)
        return int(self._board.device['elsStop']['takeupThreshCounts'])

    def read_last_takeup_z_delta(self) -> int:
        """Signed Z counts moved across the last take-up, projected onto the
        take-up direction. NEGATIVE means the carriage moved the WRONG way —
        a distinct fault from "didn't move" and worth surfacing as such."""
        if not self._board.connected:
            return self._no_link(0)
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

        A FABRICATED ZERO IS NOT "DONE". The three reads below are live -- the
        servo block has no per-tick snapshot -- and every read helper returns 0
        on a failed frame. Unguarded, those zeros compose into precisely the
        false positive the paragraph above exists to prevent: stepsToGo
        fabricates 0 and clears the planner test, then currentSteps and
        desiredSteps both fabricate 0 and compare equal, so a retract in
        mid-flight reports COMPLETE and the FSM issues its follow-up on top of
        the pulses still draining.

        THE DISCONNECT CHECK DOES NOT COVER THIS. A timeout is not a
        disconnect, and a timeout is the failure this machine actually has: six
        comms losses in six cuts on 2026-08-23, every one a timeout rather than
        a CRC error, on the very tick path this method sits in.

        So answer "not yet" instead. The caller polls this every tick, so a
        fabricated poll costs one tick and is re-read cleanly on the next --
        the same trade ElsUiController makes on the take-up outcome edge.
        """
        if not self._board.connected:
            return self._no_link(False)
        baseline = self.reads_baseline()
        if self._board.device['servo']['stepsToGo'] != 0:
            # No fabrication check needed on this branch: a NONZERO stepsToGo
            # is a value the controller really sent (a fabricated read is 0),
            # and "not done" is the conservative answer either way.
            return False
        done = (self._board.device['servo']['currentSteps']
                == self._board.device['servo']['desiredSteps'])
        return False if self.reads_fabricated_since(baseline) else done
