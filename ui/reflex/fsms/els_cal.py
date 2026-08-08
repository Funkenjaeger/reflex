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

    # A run is a handful of sub-millimetre moves; if the ack has not arrived in
    # this many polls the link or the firmware is wedged. Purely a UI liveness
    # backstop — the firmware has its own ceiling and cannot run forever.
    TIMEOUT_POLLS = 600      # ~10 s at a 60 Hz UI tick

    def __init__(self, hal, els):
        self._hal = hal
        self._els = els
        self.state = CalState.IDLE
        self.measured = [0, 0, 0]
        self.result_code = ELS_CAL_OK
        self.message = ""
        self._baseline_seq = 0
        self._polls = 0

    # ── lifecycle ────────────────────────────────────────────────────
    def start(self) -> bool:
        """Push limits and request a run. False if it could not be requested."""
        if not self._hal.connected:
            self._fail(ELS_CAL_ERR_CONFIG, "Not connected to the controller.")
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
        """Advance the run. Call from the UI tick; returns the current state."""
        if self.state != CalState.RUNNING:
            return self.state

        self._polls += 1
        if self._hal.read_cal_seq() == self._baseline_seq:
            if self._polls >= self.TIMEOUT_POLLS:
                self._fail(
                    ELS_CAL_ERR_CONFIG,
                    "No response from the controller during calibration.",
                )
            return self.state

        # Ack observed — the run finished, for better or worse.
        self.result_code = self._hal.read_cal_result()
        self.measured = self._hal.read_cal_measured()

        if self.result_code != ELS_CAL_OK:
            self._fail(self.result_code, ELS_CAL_MESSAGES.get(
                self.result_code, "Calibration failed."))
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
        self._els.els_backlash_steps = self.command_steps
        self._hal.set_backlash_steps(self.command_steps)
        log.info("els_cal: committed measured=%d command=%d",
                 self.mean_steps, self.command_steps)
        return True

    def cancel(self) -> None:
        self.state = CalState.IDLE
        self.message = ""

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
    def _fail(self, code: int, message: str) -> None:
        self.state = CalState.REFUSED
        self.result_code = code
        self.message = message
        log.warning("els_cal: refused code=%s %s", code, message)

    def _fmt_measured(self) -> str:
        return ", ".join(str(int(v)) for v in self.measured)
