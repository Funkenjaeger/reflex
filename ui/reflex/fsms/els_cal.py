"""Backlash calibration run controller.

Drives one closed-loop calibration: push the machine limits, request the run,
wait for the firmware's ack, judge the result, and (only if it passes) commit
the take-up command.

WHY THE ACK IS A SEQUENCE COUNTER, NOT A FLAG
---------------------------------------------
``calCommand`` is cleared by the FIRMWARE the instant the ISR consumes it —
long before the run finishes. Polling it for completion reports "done"
immediately and reads a stale result. ``calSeq`` increments once per finished
run, success or refusal, so edge-detecting it against a baseline captured at
request time is the only way a host polling at Modbus rates cannot alias a fast
run. Everything here is built around that.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not widen the acceptance spread to make a run succeed. A wide spread
means the measurement is not reproducible, and that IS the finding — the same
class of fault that would silently corrupt every other ELS operation. Surface
it; do not work around it.

It also never writes the raw measurement to ``els_backlash_steps``. That
property holds the COMMANDED take-up (measured + margin); the raw measurement
lives in ``els_cal_last_measured_steps``. ``ElsStopFsm._safety_margin_display``
depends on that distinction.
"""
from kivy.logger import Logger

from reflex.utils.devices import (
    ELS_CAL_ERR_CONFIG,
    ELS_CAL_MESSAGES,
    ELS_CAL_OK,
    ELS_PROTOCOL_VERSION,
)

log = Logger.getChild(__name__)


# ── Pure policy ──────────────────────────────────────────────────────
# Module-level rather than methods on ElsDispatcher: that class cannot be
# constructed without a running MainApp, so logic living on it can only be
# tested by mirroring it in a stub, and a mirrored rule is a rule that will
# drift. These read their thresholds from the dispatcher's persisted
# properties but hold no state themselves.

def cal_spread(measured) -> int:
    """Max - min across a measurement set, in servo steps.

    Mirrors elsCalSpread() in the firmware's els_backlash_cal.h so both sides
    compute the same number; the acceptance THRESHOLD is host policy and lives
    only here.
    """
    vals = [int(v) for v in measured]
    return (max(vals) - min(vals)) if vals else 0


def cal_mean(measured) -> int:
    vals = [int(v) for v in measured]
    return (sum(vals) // len(vals)) if vals else 0


def cal_is_consistent(measured, max_spread_steps) -> bool:
    """Whether a completed run's measurements agree closely enough to use.

    A zero measurement means some leg never measured anything, so the set is
    unusable even when its spread looks small.
    """
    vals = [int(v) for v in measured]
    if not vals or any(v <= 0 for v in vals):
        return False
    return cal_spread(vals) <= int(max_spread_steps)


def takeup_command_steps(measured_steps, margin_pct, margin_floor_steps) -> int:
    """Take-up command derived from a measured lash: measured + margin.

    Always measured + max(pct, floor), never trimmed toward the minimum. The
    floor exists because at a small lash a flat percentage collapses into the
    measurement's own quantization uncertainty (~5 steps at a 2-count threshold
    on elspi) and stops being margin at all.

    Integer math end to end, mirroring elsCalTakeupCommand() in the firmware
    header — this feeds a step count, and the ELS's no-drift guarantee rests on
    not introducing float rounding into step-domain arithmetic.
    """
    measured = int(measured_steps)
    if measured <= 0:
        return 0
    pct_margin = (measured * int(margin_pct)) // 100
    margin = max(pct_margin, int(margin_floor_steps))
    return measured + margin


class CalState:
    IDLE = "idle"
    RUNNING = "running"
    PASSED = "passed"
    REFUSED = "refused"      # firmware declined or the run failed
    INCONSISTENT = "inconsistent"   # ran, but the numbers do not agree


class BacklashCalibration:
    """One calibration run against the firmware, judged against host policy."""

    # Liveness backstop, in polls at POLL_HZ. Sized against how long the sweep
    # can ACTUALLY take, not against how long it feels like it should.
    #
    # The sweep is five legs (seat + 3 measured + re-seat) of up to
    # els_cal_ceiling_steps each, so ~2000 steps at the default ceiling. The
    # firmware ramp reaches maxSpeed in about a second, so the run is
    # maxSpeed-dominated: at 1000 steps/s that is ~2 s, but at a slow machine
    # setting it is minutes. The first version of this used 20 s and killed a
    # run that was working — 0.8 mm creeping past over a minute looks exactly
    # like a dead machine, so there was nothing to contradict it.
    #
    # Calibration now drives at a known speed (see CAL_MAX_SPEED), which bounds
    # this properly; the backstop stays generous because a false timeout on a
    # healthy machine is far more expensive than waiting a few extra seconds.
    POLL_HZ = 30
    TIMEOUT_POLLS = 30 * 120     # 2 minutes

    # Speed the calibration commands, in steps/s, and its acceleration.
    # Deliberately modest — this is unattended bidirectional carriage motion —
    # but fast enough that a 2000-step sweep completes in seconds and visibly
    # moves. Acceleration must be > 0 or the firmware ramp NaNs and never
    # starts.
    CAL_MAX_SPEED = 800.0
    CAL_ACCEL = 4000.0

    def __init__(self, hal, els):
        self._hal = hal
        self._els = els
        self.state = CalState.IDLE
        self.measured = [0, 0, 0]
        self.result_code = ELS_CAL_OK
        self.message = ""
        self._baseline_seq = 0
        self._polls = 0
        self._saved_motion = None

    # ── lifecycle ────────────────────────────────────────────────────
    def start(self) -> bool:
        """Push limits and request a run. False if it could not be requested."""
        if not self._hal.connected:
            self._fail(ELS_CAL_ERR_CONFIG, "Not connected to the controller.")
            return False

        # Firmware capability check, BEFORE anything is written.
        #
        # On firmware predating this feature the calibration registers do not
        # exist: the calCommand write lands nowhere and calSeq can never
        # increment, so the run would sit until the liveness timeout and then
        # report "no response from the controller" — which blames the link for
        # what is actually a missing firmware update, and is the single most
        # confusing way this can fail. A failed register read returns 0 (see
        # communication.read_long), so 0 means "too old", not "unknown".
        version = self._hal.read_protocol_version()
        if version != ELS_PROTOCOL_VERSION:
            self._fail(
                ELS_CAL_ERR_CONFIG,
                f"Controller firmware does not support backlash calibration "
                f"(reports register version {version}, this UI needs "
                f"{ELS_PROTOCOL_VERSION}). Flash the firmware to match this UI.",
            )
            return False

        ceiling = int(self._els.els_cal_ceiling_steps)
        thresh = int(self._els.els_cal_motion_thresh_counts)
        if ceiling <= 0 or thresh <= 0:
            # Refuse locally rather than shipping a config the firmware will
            # only bounce. A zero threshold in particular makes the firmware
            # fail closed, which would look like a hardware fault.
            self._fail(
                ELS_CAL_ERR_CONFIG,
                "Calibration limits are not commissioned for this machine.",
            )
            return False

        # THE SCALE THE GATE WATCHES IS A PRECONDITION OF THIS RUN, so it is
        # pushed here alongside the limits rather than inherited from ambient
        # state. elsStop.scaleIndex lives in firmware RAM: a power cycle
        # resets it to 0 -- the SPINDLE -- and the only other writers are the
        # retract/arm/cutting paths, none of which a fresh-boot calibration
        # traverses. Found 2026-08-25: the cal drove the carriage its whole
        # ceiling while watching a stationary spindle and reported NO_MOTION
        # on two different firmware builds; every earlier fresh-boot cal had
        # worked only because some incidental operator action pushed the
        # index first. Same resolution ElsFsm.set_scale_index uses.
        z_axis = self._els.get_z_axis()
        z_input = z_axis._primary_input() if z_axis is not None else None
        if z_input is None:
            self._fail(
                ELS_CAL_ERR_CONFIG,
                "No Z axis is assigned, so there is no scale to watch the "
                "carriage with. Map the Z axis in setup, then calibrate.",
            )
            return False
        self._hal.set_scale_index(z_input.inputIndex)

        # Drive at a known speed rather than whatever the machine is set to,
        # and restore afterwards. See CAL_MAX_SPEED.
        self._saved_motion = self._hal.read_servo_motion_params()
        self._hal.set_servo_motion_params(self.CAL_MAX_SPEED, self.CAL_ACCEL)

        self._hal.set_cal_limits(ceiling, thresh)
        self._baseline_seq = self._hal.read_cal_seq()
        self.measured = [0, 0, 0]
        self.result_code = ELS_CAL_OK
        self.message = ""
        self._polls = 0
        self.state = CalState.RUNNING
        self._hal.request_calibration()
        log.info(
            "els_cal: requested (ceiling=%d steps, thresh=%d counts, seq baseline=%d)",
            ceiling, thresh, self._baseline_seq,
        )
        return True

    def poll(self) -> str:
        """Advance the run. Call from the UI tick; returns the current state.

        GUARDED against a fabricated read the same way ui_controller's take-up
        outcome poller is (reflex-ui 947ef4b / e8bbe8c): BaseDevice.__getitem__
        is a live per-field Modbus read, and a checksum/timeout/short-frame
        failure returns 0 rather than raising past this HAL — see
        communication.py's read_* helpers and ConnectionManager.read_failures.
        Zero is not neutral in this register map (0 == ELS_CAL_OK, 0 == "no
        edge"), so two DISTINCT reads here need the guard, not one:

          1. read_cal_seq() itself. A corrupted read fabricates 0, which can
             misread as either "still running" (if baseline != 0, harmless) or
             a SPURIOUS EDGE (if baseline == 0, or the fabricated value simply
             differs from baseline) -- reporting a run "finished" that has not,
             and restoring the calibration motion speed mid-sweep.
          2. read_cal_result() / read_cal_measured(), once a genuine edge is
             seen. This is the exact elspi 2026-08-21 mechanism (takeupSeq 2
             -> 0 from a CRC-failed frame, read as a finished outcome) applied
             to calSeq/calResult/calMeasured instead of takeupSeq/takeupResult.
             calMeasured feeding elsTakeupConfirmThreshold means a poisoned
             value here lowers the take-up bar for every later cut, not just
             this one calibration.

        Neither branch needs the two-poll torn-snapshot guard 6c00072 proved
        unnecessary for calSeq/diagSeq (they are read seq-first, so a torn
        FRAME cannot pair a new seq with a stale payload) -- this HAL reads
        calSeq, calResult and each calMeasured element as up to five SEPARATE
        live Modbus exchanges, not one frame, so that argument does not cover
        this call site at all. What both branches need, and now have, is the
        same fabricated-read counter every other action-gating consumer uses.

        No rollback bookkeeping is needed (unlike the take-up poller's
        _pending_takeup_seq): _baseline_seq is never advanced here, so a
        deferred poll simply re-observes the same edge next tick once reads
        are clean again.
        """
        if self.state != CalState.RUNNING:
            return self.state

        self._polls += 1
        reads_baseline = self._hal.reads_baseline()
        seq = self._hal.read_cal_seq()
        seq_untrustworthy = self._hal.reads_fabricated_since(reads_baseline)

        if seq == self._baseline_seq or seq_untrustworthy:
            if self._polls >= self.TIMEOUT_POLLS:
                self._restore_motion()
                # start() already proved the firmware has these registers, so a
                # timeout here is a genuine stall rather than a version problem.
                self._fail(
                    ELS_CAL_ERR_CONFIG,
                    "The controller accepted the calibration but never reported "
                    "a result. Check the servo is enabled and in sync/index "
                    "mode, then retry.",
                )
                # A stalled run that eventually finishes will overwrite this;
                # reconcile at the next connect is the backstop for that race.
                self._reteach_stored_legs()
            return self.state

        # Ack observed, and the read that produced it was clean -- only now is
        # it safe to stop driving at the calibration speed.
        self._restore_motion()
        reads_baseline = self._hal.reads_baseline()
        result_code = self._hal.read_cal_result()
        measured = self._hal.read_cal_measured()
        if self._hal.reads_fabricated_since(reads_baseline):
            # The edge was real; the outcome was not. Defer rather than judge
            # a result assembled from zeros -- self.result_code/self.measured
            # are deliberately left untouched (not overwritten with the
            # fabricated values) and _baseline_seq is untouched, so the same
            # edge is re-observed and the real outcome reported once reads
            # succeed.
            return self.state
        self.result_code = result_code
        self.measured = measured

        if self.result_code != ELS_CAL_OK:
            self._fail(self.result_code, ELS_CAL_MESSAGES.get(
                self.result_code, "Calibration failed."))
            self._reteach_stored_legs()
            return self.state

        if not cal_is_consistent(self.measured, self._els.els_cal_max_spread_steps):
            spread = cal_spread(self.measured)
            self.state = CalState.INCONSISTENT
            self.message = (
                f"Measurements disagree ({self._fmt_measured()}; spread "
                f"{spread} steps, limit {int(self._els.els_cal_max_spread_steps)}). "
                "Not accepted. Check for a loose leadscrew coupling, a slipping "
                "half-nut, or a Z scale problem before retrying."
            )
            log.warning("els_cal: inconsistent %s spread=%d",
                        self.measured, spread)
            # The firmware accepted this run, so calMeasured now holds legs
            # the HOST just rejected; the gate must keep deriving from the
            # accepted record, not the rejected one.
            self._reteach_stored_legs()
            return self.state

        self.state = CalState.PASSED
        self.message = (
            f"Measured {self.mean_steps} steps ({self._fmt_measured()}). "
            f"Take-up will be commanded at {self.command_steps} steps."
        )
        log.info("els_cal: passed measured=%s mean=%d command=%d",
                 self.measured, self.mean_steps, self.command_steps)
        return self.state

    def commit(self) -> bool:
        """Accept a passed run: store the measurement and the command.

        Only the COMMAND goes to the firmware register. Keeping the raw
        measurement separate is what lets a later run detect drift, and what
        keeps the cut-start safety margin honest.
        """
        if self.state != CalState.PASSED:
            return False
        self._els.els_cal_last_measured_steps = self.mean_steps
        # The legs themselves, not just the mean: reconcile re-teaches them to
        # firmware RAM after a power cycle so the take-up gate keeps its
        # derived threshold instead of falling to the floor (see
        # ElsStopHal.set_cal_measured).
        self._els.els_cal_measured_legs = [int(v) for v in self.measured]
        self._els.els_backlash_steps = self.command_steps
        self._hal.set_backlash_steps(self.command_steps)
        log.info("els_cal: committed measured=%d command=%d",
                 self.mean_steps, self.command_steps)
        return True

    def cancel(self) -> None:
        self._restore_motion()
        self.state = CalState.IDLE
        self.message = ""

    def _restore_motion(self):
        """Put the machine's own speed settings back. Idempotent."""
        if self._saved_motion is None:
            return
        max_speed, accel = self._saved_motion
        self._saved_motion = None
        if max_speed > 0 and accel > 0:
            self._hal.set_servo_motion_params(max_speed, accel)

    @property
    def progress_text(self) -> str:
        """Something that visibly changes while the run is in flight.

        A silent modal during a slow sweep is indistinguishable from a hung one,
        which is how the first version got killed by hand.
        """
        secs = self._polls // self.POLL_HZ
        return f"Measuring… {secs}s"

    # ── derived values ───────────────────────────────────────────────
    @property
    def mean_steps(self) -> int:
        return cal_mean(self.measured)

    @property
    def command_steps(self) -> int:
        return takeup_command_steps(self.mean_steps,
                                    self._els.els_takeup_margin_pct,
                                    self._els.els_takeup_margin_floor_steps)

    @property
    def drift_steps(self) -> int:
        """Change against the previously stored measurement, in servo steps.

        Non-zero is normal (the measurement carries a detection-distance bias
        and real quantization); a LARGE change between commissioning runs is
        worth an operator's attention, which is why it is surfaced rather than
        silently overwritten.
        """
        previous = int(self._els.els_cal_last_measured_steps or 0)
        return (self.mean_steps - previous) if previous else 0

    # ── internals ────────────────────────────────────────────────────
    def _reteach_stored_legs(self) -> None:
        """Restore the take-up gate's basis after a finished-but-failed run.

        The firmware copies its working measured[] into elsStop.calMeasured on
        EVERY completion, success or failure — deliberate, so a partial run
        leaves diagnostics — but the take-up confirmation threshold derives
        from that same array, so a failed run's zeros silently drop the gate
        to its bare motion floor for every subsequent pass (found 2026-08-23).
        The partial values are already captured in self.measured and the log
        by the time this runs, so restoring the last ACCEPTED record costs no
        diagnostics; it is the same re-teaching reconcile does after a power
        cycle, applied at the moment the corruption happens instead of at the
        next connect.
        """
        legs = [int(v) for v in (self._els.els_cal_measured_legs or [])]
        if len(legs) == 3 and all(v > 0 for v in legs):
            self._hal.set_cal_measured(legs)
            log.warning(
                "els_cal: failed run zeroed the firmware's calMeasured; "
                "re-taught stored legs %s so the take-up gate keeps its "
                "derived threshold", legs)
        else:
            # No accepted record exists (fresh machine, or never passed): the
            # gate genuinely sits at its floor, and silently is the one way
            # that must not happen.
            self.message += (
                " Note: the take-up confirmation gate is at its minimum "
                "threshold until a calibration passes."
            )
            log.warning(
                "els_cal: failed run with no stored calibration; take-up "
                "confirmation gate is at its motion floor until a run passes")

    def _fail(self, code: int, message: str) -> None:
        self.state = CalState.REFUSED
        self.result_code = code
        self.message = message
        log.warning("els_cal: refused code=%s %s", code, message)

    def _fmt_measured(self) -> str:
        return ", ".join(str(int(v)) for v in self.measured)
