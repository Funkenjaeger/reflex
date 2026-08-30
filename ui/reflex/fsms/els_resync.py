"""Interactive re-sync to an existing thread — run controller.

Walks one manual reference latch: the operator establishes a physically
verified point on an existing thread's helix, and the firmware captures the
same (latchedSpindle, latchedZ) pair the first-trigger auto-latch would have.
Everything downstream of the latch (take-up, phase correction, resume) is
unchanged and unaware of which producer latched the reference.

THE PROCEDURE THIS ENFORCES (order is load-bearing)
---------------------------------------------------
1. Position the carriage BY HAND into the threaded region, close the half
   nut, then move it back BY HAND in the ANTI-CUTTING direction until it seats
   against the leadscrew. That last move is the whole point: it loads the lash
   on the side the leadscrew will push from.

   CORRECTED 2026-08-24. This step used to read "coarse jog, CUTTING DIRECTION
   ONLY", which was wrong twice over. The jog does not exist -- jog is a
   separate screen, disabled in lathe mode, and this runs two modals deep -- so
   the carriage is moved by hand regardless. And hand-moving in direction D
   seats the nut against the OPPOSITE flank from the leadscrew driving it in D,
   so an operator following the old wording loaded the lash backwards. A
   powered cutting-direction jog and a hand pull in the anti-cutting direction
   arrive at the SAME state, which is why everything downstream is unchanged.
2. Fine alignment is NOT a Z operation. The operator rotates the SPINDLE by
   hand and works the CROSS-SLIDE to ease the tool into the groove. Z is HELD.
3. This controller watches the Z scale throughout step 2. Once seated the nut
   sits hard against the flank the leadscrew drives from, so further
   anti-cutting motion is mechanically blocked and the only motion the carriage
   is capable of is drifting in the CUTTING direction through the free play —
   which is why the monitor is not direction-aware: any Z motion IS that
   drift.
4. On confirm, the firmware latches (spindle, Z) in one ISR pass.

THE TOLERANCE IS A FINDING, NOT A KNOB
--------------------------------------
Drift is recoverable: the operator nudges the carriage back BY HAND until it
re-seats against the drive flank — arriving back at a mechanical stop it was
already sitting against, so the Z readout must return to the post-jog baseline
within a couple of encoder counts. A re-seat that misses by more means the Z
chain (scale, counting, mechanics) has lost custody, and the SAME fault would
silently corrupt every other ELS operation. That is a RED FLAG to surface and
stop on. Do NOT widen the tolerance to make the wizard proceed. The corollary:
a clean re-seat within tolerance positively confirms the whole Z chain — the
wizard is incidentally a drivetrain integrity check.

WHY THE ACK IS A SEQUENCE COUNTER, NOT A FLAG
---------------------------------------------
``latchCommand`` is cleared by the FIRMWARE the instant the ISR consumes it.
Edge-detect ``latchSeq`` against a baseline captured at request time, exactly
like ``calSeq`` (see els_cal.py). A latch requested with ``enable == 0`` is
consumed with NO seq increment — the absent ack IS the refusal.
"""
from kivy.logger import Logger

from reflex.utils.devices import ELS_PROTOCOL_VERSION

log = Logger.getChild(__name__)


# ── Pure policy ──────────────────────────────────────────────────────
# Module-level for the same reason as els_cal.py's: testable without a
# running MainApp, and a rule mirrored into a test stub is a rule that
# will drift.

def z_held(baseline_counts, current_counts, tolerance_counts) -> bool:
    """Whether Z still sits at the post-jog baseline, within tolerance.

    Used both for the live drift watch and for judging a re-seat — they are
    deliberately the SAME predicate, because the re-seat claim is precisely
    "the carriage is back where the drive flank held it".
    """
    return abs(int(current_counts) - int(baseline_counts)) <= int(tolerance_counts)


class ResyncState:
    IDLE = "idle"
    ALIGNING = "aligning"          # Z-watch + spindle stillness gating Confirm
    DRIFTED = "drifted"            # Z moved; operator must re-seat by hand
    LATCH_REQUESTED = "latch_requested"
    LATCHED = "latched"
    RED_FLAG = "red_flag"          # re-seat missed the baseline — custody fault
    REFUSED = "refused"
    # Not a refusal: a question. The job already carries a reference, which is
    # worth stopping for but not worth sending the operator back out to the ELS
    # screen to disengage and re-engage (2026-08-24 bench feedback).
    CONFIRM_OVERWRITE = "confirm_overwrite"


class ThreadResync:
    """One manual-latch procedure against the firmware."""

    POLL_HZ = 30

    # Spindle stillness dwell gating Confirm. The operator's hand is on the
    # spindle during fine alignment; a confirm racing a still-moving spindle
    # would latch a spindle count the operator never looked at. ~0.7 s of
    # stillness (within the jitter floor of a hand-held spindle) must
    # accumulate before Confirm arms, and any motion resets the dwell.
    SPINDLE_STILL_POLLS = 20
    SPINDLE_JITTER_COUNTS = 1

    # Ack liveness. Consumption is a single ISR pass (~10 us), so this only
    # bounds Modbus round-trips; a timeout means the firmware refused (job not
    # enabled) or the link died, never that the latch is still "in progress".
    LATCH_TIMEOUT_POLLS = 30 * 2

    def __init__(self, hal, els, read_z_counts, read_spindle_counts,
                 format_z=None):
        """``read_z_counts`` / ``read_spindle_counts`` are zero-arg callables
        returning live raw encoder counts (firmware ``scales[i].position`` as
        mirrored into ``fastData.scaleCurrent``) — injected so this class can
        be driven in tests without a running app.

        ``format_z`` renders a Z distance IN COUNTS as operator-facing text.
        Injected for the same reason the readers are: the scale ratio and the
        display units live on the app, and this class is driven headless. When
        it is absent every message falls back to naming counts, which is what
        they all did until 2026-08-30 — counts are a unit that appears nowhere
        else in the UI, so the operator nulling this by hand had nothing to
        judge "+11, tolerance ±3" by.
        """
        self._hal = hal
        self._els = els
        self._read_z = read_z_counts
        self._read_spindle = read_spindle_counts
        self._format_z = format_z

        self.state = ResyncState.IDLE
        self.message = ""
        self.baseline_z = 0
        self.latched_z = 0
        self.latched_spindle = 0
        self._still_polls = 0
        self._last_spindle = 0
        self._baseline_seq = 0
        self._latch_polls = 0

    # ── lifecycle ────────────────────────────────────────────────────
    def begin_alignment(self, force: bool = False) -> bool:
        """Operator finished the coarse jog; capture the Z baseline and start
        the watch. False (state REFUSED) if preconditions fail."""
        if not self._hal.connected:
            # Four words with no next step used to be the whole message here,
            # while its five siblings below — and the phase-offset modal's
            # message for this identical condition — are sentences that say
            # what to do. The refusal CONDITION is unchanged; only what the
            # operator is told about it.
            return self._refuse(
                "Not connected to the controller. The thread reference is "
                "latched by the controller, so there is nothing here to latch "
                "it in — reconnect and start the pick-up again."
            )

        version = self._hal.read_protocol_version()
        if version != ELS_PROTOCOL_VERSION:
            # Same failure mode as calibration: on firmware predating the
            # latch registers the command write lands nowhere and the ack can
            # never come, which would misreport as a link problem.
            return self._refuse(
                f"Controller firmware does not support thread re-sync "
                f"(reports register version {version}, this UI needs "
                f"{ELS_PROTOCOL_VERSION}). Flash the firmware to match this UI."
            )

        if not self._hal.read_enable():
            return self._refuse(
                "No threading job is armed. Engage the ELS stop first — the "
                "reference belongs to a job and is cleared when one starts."
            )

        if self._hal.read_reference_latched() and not force:
            # ASKED, NOT REFUSED (2026-08-24). The concern is real: a job that
            # already has a reference either cut with it -- in which case
            # re-syncing re-anchors every remaining pass -- or something has
            # gone wrong. But this used to be a hard refusal telling the
            # operator to disengage and re-engage, which meant leaving two
            # modals, crossing to the ELS screen, cycling engage, re-enabling
            # sync, and navigating back in. The firmware would happily
            # overwrite; the policy is ours; so make it a question with an
            # abort, not a maze. force=True is that answer coming back.
            self.state = ResyncState.CONFIRM_OVERWRITE
            self.message = (
                "This job already has a thread reference. Overwriting it "
                "re-anchors EVERY remaining pass to the new one — any pass "
                "already cut with the old reference will not match. Overwrite "
                "only if that is what you want."
            )
            log.warning("els_resync: reference already latched, asking to overwrite")
            return False

        self.baseline_z = int(self._read_z())
        self._last_spindle = int(self._read_spindle())
        self._still_polls = 0
        self.message = ""
        self.state = ResyncState.ALIGNING
        log.info("els_resync: aligning, Z baseline = %d counts", self.baseline_z)
        return True

    def poll(self) -> str:
        """Advance the watch. Call at UI cadence; returns the current state."""
        if self.state == ResyncState.ALIGNING:
            self._poll_spindle_stillness()
            if not self._z_ok():
                self._still_polls = 0
                self.state = ResyncState.DRIFTED
                log.warning(
                    "els_resync: Z drifted to %+d counts from baseline",
                    self.z_delta_counts,
                )
        elif self.state == ResyncState.LATCH_REQUESTED:
            self._poll_latch_ack()
        return self.state

    def reseat_check(self) -> bool:
        """Operator says the carriage is re-seated against the drive flank.

        Within tolerance → back to ALIGNING (and the round trip has positively
        confirmed Z-chain custody). Outside → RED_FLAG, no retry: a carriage
        that returned to its mechanical stop but not to its Z reading is the
        fault this tolerance exists to catch.
        """
        if self.state != ResyncState.DRIFTED:
            return False
        if self._z_ok():
            self._still_polls = 0
            self.state = ResyncState.ALIGNING
            log.info("els_resync: re-seat confirmed at %+d counts", self.z_delta_counts)
            return True
        self.state = ResyncState.RED_FLAG
        self.message = (
            f"Carriage re-seated {self.fmt_z(self.z_delta_counts)} from the "
            f"baseline (tolerance ±{self.fmt_z(self.tolerance_counts, False)}). "
            "A re-seat "
            "against the drive flank must return the Z reading almost "
            "exactly — missing it means the Z scale chain has lost custody "
            "of the position, and the same fault would corrupt every ELS "
            "operation. Do not cut. Check the Z scale, its wiring, and the "
            "carriage mechanics."
        )
        log.error("els_resync: RED FLAG — re-seat missed baseline by %+d counts",
                  self.z_delta_counts)
        return False

    def request_latch(self) -> bool:
        """Confirm: ask the firmware to latch the reference at this instant."""
        if self.state != ResyncState.ALIGNING:
            return False
        # TOCTOU guard: re-read Z at the moment of the request. A drift
        # arriving between the last poll and the button press routes to
        # DRIFTED — same as the next poll would — rather than latching a
        # position the operator is no longer looking at. Checked BEFORE the
        # stillness gate so the drift is surfaced immediately either way.
        if not self._z_ok():
            self._still_polls = 0
            self.state = ResyncState.DRIFTED
            return False
        if not self.spindle_still:
            return False
        self._baseline_seq = self._hal.read_latch_seq()
        self._latch_polls = 0
        self.state = ResyncState.LATCH_REQUESTED
        self._hal.request_latch()
        log.info("els_resync: latch requested (seq baseline=%d)", self._baseline_seq)
        return True

    def cancel(self) -> None:
        self.state = ResyncState.IDLE
        self.message = ""

    # ── UI-facing readouts ───────────────────────────────────────────
    def fmt_z(self, counts, signed=True) -> str:
        """A Z distance, in the operator's units when they are available.

        Falls back to counts rather than raising: a headless driver has no
        formats, and a diagnostic that says "+11 counts" is still better than
        a message that fails to render at all.
        """
        if self._format_z is None:
            return f"{int(counts):+d} counts" if signed else f"{int(counts)} counts"
        return self._format_z(counts, signed)

    @property
    def tolerance_counts(self) -> int:
        return int(self._els.els_resync_z_tol_counts)

    @property
    def z_delta_counts(self) -> int:
        """Signed live drift from the baseline. After the cutting-direction
        jog this can physically only grow in the cutting direction (retract is
        blocked by the drive flank), but it is displayed signed anyway — a
        NEGATIVE drift would itself be evidence of a custody problem."""
        return int(self._read_z()) - self.baseline_z

    @property
    def spindle_still(self) -> bool:
        return self._still_polls >= self.SPINDLE_STILL_POLLS

    @property
    def stillness_fraction(self) -> float:
        """0.0–1.0 progress of the stillness dwell, for the Confirm gate UI."""
        return min(1.0, self._still_polls / float(self.SPINDLE_STILL_POLLS))

    @property
    def confirm_allowed(self) -> bool:
        return (self.state == ResyncState.ALIGNING
                and self.spindle_still
                and self._z_ok())

    # ── internals ────────────────────────────────────────────────────
    def _z_ok(self) -> bool:
        return z_held(self.baseline_z, self._read_z(), self.tolerance_counts)

    def _poll_spindle_stillness(self) -> None:
        current = int(self._read_spindle())
        if abs(current - self._last_spindle) <= self.SPINDLE_JITTER_COUNTS:
            self._still_polls += 1
        else:
            self._still_polls = 0
        self._last_spindle = current

    def _poll_latch_ack(self) -> None:
        # A FABRICATED SEQ IS NOT AN ACK -- and here the consequence is the
        # worst in either feature. A failed frame or a dropped link returns 0
        # for latchSeq, which differs from any nonzero baseline, so the wizard
        # would announce "Thread reference latched" for a latch the firmware
        # refused. The cross-check below reads latchedZ/latchedSpindle through
        # the same door, so on a dead link it compares fabricated zeros against
        # the watched baseline and reports either a false success or a false
        # RED FLAG telling the operator their Z scale has lost custody.
        #
        # Treated as "no ack yet" rather than as a failure: the timeout below
        # is already the honest way to give up, and it keeps one exit path.
        baseline = self._hal.reads_baseline()
        latch_seq = self._hal.read_latch_seq()
        if self._hal.reads_fabricated_since(baseline):
            self._latch_polls += 1
            if self._latch_polls >= self.LATCH_TIMEOUT_POLLS:
                self._refuse(
                    "Lost contact with the controller while waiting for the "
                    "latch. Nothing was latched. Check the connection and "
                    "retry."
                )
            return

        if latch_seq == self._baseline_seq:
            self._latch_polls += 1
            if self._latch_polls >= self.LATCH_TIMEOUT_POLLS:
                self._refuse(
                    "The controller never acknowledged the latch. The job may "
                    "have disengaged — check the ELS stop is still engaged and "
                    "retry."
                )
            return

        # Ack observed. Read the reference back and cross-check it against the
        # baseline this controller watched the whole time: the firmware latched
        # its own Z register, and if that disagrees with what the host was
        # monitoring by more than the tolerance, the two sides do not share a
        # coherent view of the axis — same custody fault class as a bad
        # re-seat, surfaced the same way.
        self.latched_z = self._hal.read_latched_z()
        self.latched_spindle = self._hal.read_latched_spindle()
        if not self._hal.read_reference_latched():
            self._refuse("The controller acknowledged the latch but reports no "
                         "reference. Retry, and check the firmware version.")
            return
        if not z_held(self.baseline_z, self.latched_z, self.tolerance_counts):
            self.state = ResyncState.RED_FLAG
            self.message = (
                f"The controller latched the reference "
                f"{self.fmt_z(self.latched_z - self.baseline_z)} from where "
                f"this screen was watching (tolerance ±"
                f"{self.fmt_z(self.tolerance_counts, False)}). The UI and "
                "controller do not agree on the carriage position. Do not "
                "cut. Check the Z scale connection and re-engage the ELS "
                "stop."
            )
            log.error("els_resync: RED FLAG — latched Z %d vs baseline %d",
                      self.latched_z, self.baseline_z)
            return
        self.state = ResyncState.LATCHED
        # THE RAW COUNTS ARE GONE FROM THE CONFIRMATION (2026-08-30). They
        # read as a receipt but there is nothing to do with them: the operator
        # cannot check a spindle count against anything, and both numbers are
        # in the log line below, which is where a diagnostician reads them.
        self.message = "Thread reference latched."
        log.info("els_resync: latched spindle=%d z=%d",
                 self.latched_spindle, self.latched_z)

    def _refuse(self, message: str) -> bool:
        self.state = ResyncState.REFUSED
        self.message = message
        log.warning("els_resync: refused — %s", message)
        return False
