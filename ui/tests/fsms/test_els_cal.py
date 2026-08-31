"""Tests for BacklashCalibration (the closed-loop backlash calibration run).

Exercised against a fake HAL that models the firmware's ACTUAL ack semantics:
``calCommand`` is cleared immediately on consume and ``calSeq`` only increments
when the run finishes. A test that let the controller poll ``calCommand`` for
completion would pass against a naive fake and fail against real firmware, so
the fake is deliberately built to punish that.

The properties under test are the ones that make the feature safe rather than
merely present:

  - a firmware refusal must surface as an operator-legible cause, never a
    silent proceed;
  - an INCONSISTENT run must be rejected — the spread is the finding, and
    widening the tolerance to proceed is the exact anti-pattern this guards;
  - only the COMMANDED take-up (measured + margin) reaches the firmware
    register, while the raw measurement is stored separately, because
    ElsStopFsm._safety_margin_display depends on that distinction.

Mutation notes on individual cases record the edit that must break them.
"""
from types import SimpleNamespace

import pytest

from reflex.fsms.els_cal import (
    BacklashCalibration,
    CalState,
    cal_is_consistent,
    cal_mean,
    cal_spread,
    takeup_command_steps,
)
from reflex.utils.devices import (
    ELS_CAL_ERR_ENABLED,
    ELS_CAL_ERR_NO_MOTION,
    ELS_CAL_ERR_SERVOMODE,
    ELS_CAL_OK,
    ELS_PROTOCOL_VERSION,
)


class FakeHal:
    """Models the firmware's command/ack contract, including its traps."""

    def __init__(self, measured=(100, 101, 100), result=ELS_CAL_OK,
                 connected=True, ticks_to_finish=3,
                 protocol_version=ELS_PROTOCOL_VERSION):
        self.connected = connected
        self._protocol_version = protocol_version
        # The machine's own speed settings, which calibration must borrow and
        # give back.
        self.motion = (250.0, 500.0)
        self.motion_writes = []

        # servoMode: 0 off, 1 sync/index, 2 jog. Start at 0 -- the state an
        # operator is actually in when they walk up to calibrate, and the whole
        # reason the FSM has to promote it.
        self.servo_mode = 0
        self.mode_writes = []
        self._fail_next_mode_read = False
        self._measured = list(measured)
        self._result = result
        self._ticks_to_finish = ticks_to_finish

        self.cal_command = 0
        self.cal_seq = 7           # non-zero baseline: a real machine has run before
        self.cal_result = ELS_CAL_OK
        self.limits = None
        self.backlash_written = None
        self.scale_index_written = None
        self.cal_measured_written = None
        self._running = 0

        # Read-failure accounting with the PRODUCTION semantics (real int,
        # real comparison), matching FakeConnectionManager in
        # test_ui_controller.py -- a MagicMock here would make
        # reads_fabricated_since() return a truthy Mock unconditionally, so
        # every guarded poll would discard itself and the guard tests would
        # pass for the wrong reason.
        self.read_failures = 0
        self._fail_next_seq_read = False
        self._fail_next_outcome_reads = False

    def reads_baseline(self) -> int:
        return self.read_failures

    def reads_fabricated_since(self, baseline: int) -> bool:
        return self.read_failures != baseline

    def fail_read(self, n: int = 1):
        """Simulate n failed Modbus reads (checksum / timeout / short frame)."""
        self.read_failures += n

    # -- writes ------------------------------------------------------
    def set_cal_limits(self, ceiling, thresh):
        self.limits = (ceiling, thresh)

    def set_scale_index(self, scale_index):
        self.scale_index_written = scale_index

    def set_cal_measured(self, legs):
        self.cal_measured_written = list(legs)

    def request_calibration(self):
        # The firmware consumes and CLEARS the command in the same ISR pass.
        # Anything polling cal_command for completion sees 0 immediately.
        self.cal_command = 0
        self._running = self._ticks_to_finish

    def set_backlash_steps(self, steps):
        self.backlash_written = steps

    def read_protocol_version(self):
        return self._protocol_version

    def read_servo_motion_params(self):
        return self.motion

    def set_servo_motion_params(self, max_speed, accel):
        self.motion = (max_speed, accel)
        self.motion_writes.append((max_speed, accel))

    def read_servo_mode(self):
        if self._fail_next_mode_read:
            self._fail_next_mode_read = False
            self.fail_read()
            return 0   # fabricated -- a failed read returns 0, a REAL mode here
        return self.servo_mode

    def set_servo_mode(self, mode):
        self.servo_mode = mode
        self.mode_writes.append(mode)

    # -- reads -------------------------------------------------------
    def read_cal_seq(self):
        if self._running > 0:
            self._running -= 1
            if self._running == 0:
                self.cal_result = self._result
                self.cal_seq += 1
        if self._fail_next_seq_read:
            self._fail_next_seq_read = False
            self.fail_read()
            return 0   # fabricated -- a checksum failure returns 0, not the real value
        return self.cal_seq

    def read_cal_result(self):
        if self._fail_next_outcome_reads:
            self.fail_read()
            return 0
        return self.cal_result

    def read_cal_measured(self):
        if self._fail_next_outcome_reads:
            self._fail_next_outcome_reads = False
            self.fail_read()
            return [0, 0, 0]
        return list(self._measured)


def _els(**overrides):
    """Just the persisted config the controller reads.

    ElsDispatcher itself needs a running MainApp, and the policy it used to
    carry now lives as module-level functions precisely so neither this test
    nor the controller has to mirror it.
    """
    cfg = dict(
        els_cal_ceiling_steps=400,
        els_cal_motion_thresh_counts=2,
        els_cal_max_spread_steps=12,
        els_takeup_margin_pct=20,
        els_takeup_margin_floor_steps=10,
        els_cal_last_measured_steps=0,
        els_backlash_steps=0,
        els_cal_measured_legs=[0, 0, 0],
    )
    cfg.update(overrides)
    z_input_index = cfg.pop("z_input_index", 1)
    ns = SimpleNamespace(**cfg)
    if not hasattr(ns, "get_z_axis"):
        if z_input_index is None:
            ns.get_z_axis = lambda: None
        else:
            inp = SimpleNamespace(inputIndex=z_input_index)
            ns.get_z_axis = lambda: SimpleNamespace(
                _primary_input=lambda: inp)
    return ns


def _run_to_completion(cal, limit=50):
    for _ in range(limit):
        state = cal.poll()
        if state != CalState.RUNNING:
            return state
    return CalState.RUNNING


# ─── policy layer (pure) ──────────────────────────────────────────────

def test_takeup_command_uses_percentage_at_coarse_lash():
    assert takeup_command_steps(100, 20, 10) == 120


def test_takeup_command_uses_floor_at_fine_lash():
    """At a small lash a flat 20% (5 steps) is about the measurement's own
    blind zone, so the floor is what keeps real margin.

    MUTATION: drop the max(pct, floor) in takeup_command_steps -> 30, fails."""
    assert takeup_command_steps(25, 20, 10) == 35


def test_takeup_command_never_negative_or_from_nothing():
    assert takeup_command_steps(0, 20, 10) == 0
    assert takeup_command_steps(-5, 20, 10) == 0


def test_consistency_rejects_wide_spread():
    assert cal_is_consistent([100, 104, 98], 12)
    assert not cal_is_consistent([100, 220, 98], 12)
    assert cal_spread([100, 104, 98]) == 6
    assert cal_mean([100, 104, 98]) == 100


def test_consistency_rejects_zero_measurements():
    """A zero measurement means a leg never measured anything; the set is not
    usable even though its spread may look small."""
    assert not cal_is_consistent([0, 0, 0], 12)
    assert not cal_is_consistent([100, 0, 100], 12)


# ─── run controller ───────────────────────────────────────────────────

def test_happy_path_passes_and_commits_the_command_not_the_measurement():
    els = _els(els_takeup_margin_pct=20, els_takeup_margin_floor_steps=10,
               els_cal_max_spread_steps=12)
    hal = FakeHal(measured=(100, 101, 100))
    cal = BacklashCalibration(hal, els)

    assert cal.start() is True
    assert hal.limits == (int(els.els_cal_ceiling_steps),
                          int(els.els_cal_motion_thresh_counts))
    assert _run_to_completion(cal) == CalState.PASSED
    assert cal.mean_steps == 100
    assert cal.command_steps == 120

    assert cal.commit() is True
    # The RAW measurement and the COMMANDED take-up are stored separately, and
    # only the command reaches the firmware.
    # MUTATION: write mean_steps to els_backlash_steps -> 100 != 120, fails.
    assert els.els_cal_last_measured_steps == 100
    assert els.els_backlash_steps == 120
    assert hal.backlash_written == 120


def test_does_not_finish_before_the_ack():
    """The controller must wait for calSeq, not for calCommand clearing.

    MUTATION: poll cal_command instead of cal_seq and this passes on tick 1
    with a stale result."""
    hal = FakeHal(ticks_to_finish=5)
    cal = BacklashCalibration(hal, _els())
    cal.start()
    assert hal.cal_command == 0        # already cleared by the firmware
    for _ in range(4):
        assert cal.poll() == CalState.RUNNING
    assert cal.poll() == CalState.PASSED


def test_a_corrupted_seq_read_is_not_taken_for_a_finished_run():
    """A checksum-failed read_cal_seq() fabricates 0, which can look like a
    spurious edge (baseline 7 != fabricated 0) long before the sweep is done.

    Mirrors reflex-ui's take-up outcome guard (947ef4b / e8bbe8c) for the
    elspi 2026-08-21 mechanism -- a CRC-failed frame of zeros read as a
    finished outcome -- applied to calSeq instead of takeupSeq.

    MUTATION: drop the `seq_untrustworthy` check in
    BacklashCalibration.poll() and this fails -- the fabricated 0 (!= the
    baseline of 7) is taken for an edge, motion is restored mid-sweep, and
    the run is judged on stale start()-time defaults instead of waiting for
    the real ack.
    """
    hal = FakeHal(ticks_to_finish=5, measured=(100, 101, 100))
    cal = BacklashCalibration(hal, _els())
    cal.start()
    speed_after_start = hal.motion

    hal._fail_next_seq_read = True
    assert cal.poll() == CalState.RUNNING, (
        "a fabricated seq read was taken for a finished run")
    assert hal.motion == speed_after_start, (
        "motion was restored from a fabricated edge -- mid-sweep speed change"
    )
    assert hal.read_failures == 1

    # The real sweep still completes normally once reads are clean again.
    assert _run_to_completion(cal) == CalState.PASSED


def test_a_corrupted_outcome_read_after_a_real_edge_is_deferred():
    """The edge is genuine (calSeq really did advance); the reads that
    collect the outcome are not. This is the elspi 2026-08-21 failure shape
    itself -- a CRC-failed frame of zeros read as a completed outcome --
    reproduced on calResult/calMeasured, which is not covered by the
    seq/payload field-order argument (6c00072) because this HAL reads them as
    separate live exchanges, not one frame.

    MUTATION: drop the fabricated-read check after read_cal_result() /
    read_cal_measured() and this fails -- a zeroed calMeasured would be
    caught by cal_is_consistent's `any(v <= 0)` guard and refused as
    INCONSISTENT rather than accepted, but that is an accident of a different
    check, not evidence the read was trusted; a fabricated OK result
    (0 == ELS_CAL_OK) paired with a fabricated non-zero-looking measurement
    would not be.
    """
    hal = FakeHal(ticks_to_finish=1, measured=(100, 101, 100))
    cal = BacklashCalibration(hal, _els())
    cal.start()

    hal._fail_next_outcome_reads = True
    assert cal.poll() == CalState.RUNNING, (
        "an outcome assembled from fabricated reads was judged instead of deferred"
    )
    assert cal.result_code == ELS_CAL_OK, (
        "self.result_code was overwritten with a fabricated value on a "
        "deferred poll")
    assert hal.read_failures >= 1

    # Deferred, not lost: the same edge is re-observed and the real outcome
    # reported once reads succeed. calSeq does not advance again (the run
    # already finished on the firmware side), so the SAME poll must resolve
    # it now that reads are clean.
    assert cal.poll() == CalState.PASSED
    assert cal.measured == [100, 101, 100]


@pytest.mark.parametrize("code", [ELS_CAL_ERR_ENABLED,
                                  ELS_CAL_ERR_SERVOMODE,
                                  ELS_CAL_ERR_NO_MOTION])
def test_firmware_refusals_surface_an_operator_legible_cause(code):
    hal = FakeHal(result=code)
    cal = BacklashCalibration(hal, _els())
    cal.start()
    assert _run_to_completion(cal) == CalState.REFUSED
    assert cal.result_code == code
    assert cal.message                       # non-empty
    assert not cal.commit()                  # nothing committed on refusal
    assert hal.backlash_written is None


def test_open_half_nut_names_the_half_nut():
    """The whole point of the feature: the operator gets a physical check to
    act on, not a register value."""
    hal = FakeHal(result=ELS_CAL_ERR_NO_MOTION)
    cal = BacklashCalibration(hal, _els())
    cal.start()
    _run_to_completion(cal)
    assert "half-nut" in cal.message.lower()


def test_inconsistent_run_is_refused_and_says_why():
    """MUTATION: make cal_is_consistent always True and this becomes PASSED —
    the machine would commit a take-up derived from a measurement it could not
    reproduce."""
    els = _els(els_cal_max_spread_steps=12)
    hal = FakeHal(measured=(100, 240, 98))
    cal = BacklashCalibration(hal, els)
    cal.start()

    assert _run_to_completion(cal) == CalState.INCONSISTENT
    assert not cal.commit()
    assert hal.backlash_written is None
    assert els.els_backlash_steps == 0        # untouched
    assert "spread" in cal.message.lower()


def test_uncommissioned_limits_refuse_locally():
    """A zero motion threshold makes the firmware fail closed, which would look
    like a hardware fault. Refuse before requesting instead."""
    els = _els(els_cal_motion_thresh_counts=0)
    hal = FakeHal()
    cal = BacklashCalibration(hal, els)

    assert cal.start() is False
    assert cal.state == CalState.REFUSED
    assert hal.limits is None                 # never requested

def test_disconnected_board_refuses():
    cal = BacklashCalibration(FakeHal(connected=False), _els())
    assert cal.start() is False
    assert cal.state == CalState.REFUSED


def test_timeout_when_ack_never_arrives():
    hal = FakeHal(ticks_to_finish=10 ** 9)    # effectively never finishes
    cal = BacklashCalibration(hal, _els())
    cal.start()
    for _ in range(BacklashCalibration.TIMEOUT_POLLS + 1):
        cal.poll()
    assert cal.state == CalState.REFUSED
    # The message must NOT blame the link: start() already proved the firmware
    # has these registers, so a stall here is a machine-state problem and the
    # text should point the operator at the servo.
    assert "never reported a result" in cal.message.lower()
    assert "servo" in cal.message.lower()


def test_old_firmware_is_named_not_reported_as_a_dead_link():
    """Firmware without the calibration registers must be diagnosed as such.

    Otherwise calSeq can never increment, the run sits until the liveness
    timeout, and the UI blames the link for a missing firmware update — the
    single most confusing way this can fail.

    MUTATION: drop the version check in start() and this becomes RUNNING."""
    hal = FakeHal(protocol_version=0)
    cal = BacklashCalibration(hal, _els())
    assert cal.start() is False
    assert cal.state == CalState.REFUSED
    assert "firmware" in cal.message.lower()
    assert hal.limits is None          # nothing written to an incapable firmware


def test_calibration_drives_at_a_known_speed_and_restores_it():
    """The sweep must not inherit whatever maxSpeed the machine is set to.

    At a slow setting a ~2000-step sweep takes minutes while looking completely
    stationary, which is exactly how a working run got killed by hand.

    MUTATION: remove the restore in _restore_motion and the machine is left on
    calibration speeds afterwards."""
    hal = FakeHal(measured=(100, 101, 100))
    original = hal.motion
    cal = BacklashCalibration(hal, _els())

    assert cal.start() is True
    assert hal.motion == (BacklashCalibration.CAL_MAX_SPEED,
                          BacklashCalibration.CAL_ACCEL)
    assert hal.motion[1] > 0, "zero acceleration NaNs the firmware ramp"

    _run_to_completion(cal)
    assert hal.motion == original, "machine speed settings must be restored"


def test_motion_params_restored_even_on_a_refusal():
    hal = FakeHal(result=ELS_CAL_ERR_NO_MOTION)
    original = hal.motion
    cal = BacklashCalibration(hal, _els())
    cal.start()
    _run_to_completion(cal)
    assert hal.motion == original


def test_timeout_is_sized_against_a_real_sweep():
    """A 20-second backstop killed a healthy run. Guard the sizing, not just
    the mechanism."""
    assert BacklashCalibration.TIMEOUT_POLLS / BacklashCalibration.POLL_HZ >= 60


# ─── take-up refusal text ─────────────────────────────────────────────
# This formatter was written and then never called by anything, so on the first
# hardware run a refused pass produced NO operator-visible output at all: the
# machine simply sat there, indistinguishable from hung. It now drives the
# inline warning strip in the ELS bar, so it is worth pinning.

def test_takeup_text_keeps_the_counts_OFF_the_screen():
    """INVERTED 2026-08-29, and the inversion is the point.

    This test used to be `test_takeup_text_names_the_numbers_when_it_has_them`
    and asserted that "Moved 5 counts, needed 11" reached the operator, on the
    argument that the ratio distinguishes a partially engaged half-nut from one
    that never engaged. Its PREMISE was wrong about the audience: those are raw
    Z-scale counts, a unit this UI exposes nowhere else, so the operator at the
    machine has nothing to judge 5-against-11 by. Evan's call.

    It is inverted rather than deleted because the numbers coming back is now a
    LAYOUT defect, not just noise: the notice strip overlays the status gutter,
    and a longer message lands on top of the phase-offset chip's text. The
    numbers live in the log instead -- pinned by
    tests/fsms/test_ui_controller_takeup_outcome.py, so this is a relocation
    with a guard at each end, not a deletion.
    """
    from reflex.utils.devices import (ELS_TAKEUP_ERR_UNCONFIRMED,
                                      takeup_failure_text)
    msg = takeup_failure_text(ELS_TAKEUP_ERR_UNCONFIRMED, z_delta=5)
    assert "half-nut" in msg.lower()
    assert msg.lower().startswith("cut aborted"), (
        "the message must lead with the machine's STATE -- an operator who is "
        "only told the fault is left asking 'okay, what now?'")
    assert "5" not in msg and "count" not in msg.lower(), (
        "raw Z-scale counts are for the log, not the screen: nothing else in "
        "this UI shows that unit, and the width is needed to keep the notice "
        f"strip off the status chips.\n  {msg}")


def test_takeup_text_calls_out_wrong_way_motion_separately():
    """A carriage moving the WRONG way is a different fault (scale direction,
    or something else driving it) and must not be folded into 'not enough'.

    Still a separate message; only the count it used to quote is gone. The
    SIGN of z_delta is what selects it, which is why that parameter survived
    the 2026-08-29 trim and `thresh_counts` did not.
    """
    from reflex.utils.devices import (ELS_TAKEUP_ERR_UNCONFIRMED,
                                      takeup_failure_text)
    msg = takeup_failure_text(ELS_TAKEUP_ERR_UNCONFIRMED, z_delta=-7)
    plain = takeup_failure_text(ELS_TAKEUP_ERR_UNCONFIRMED, z_delta=7)
    assert "wrong" in msg.lower()
    assert msg != plain, "a wrong-way carriage must not read as 'not enough'"
    assert "7" not in msg, "the count belongs in the log, not on the strip"


def test_takeup_text_degrades_without_a_delta():
    """No motion reading at all -- the plain refusal, never the wrong-way one."""
    from reflex.utils.devices import (ELS_TAKEUP_ERR_UNCONFIRMED,
                                      takeup_failure_text)
    msg = takeup_failure_text(ELS_TAKEUP_ERR_UNCONFIRMED)
    assert "half-nut" in msg.lower()
    assert "wrong" not in msg.lower()


def test_takeup_timeout_has_its_own_text():
    from reflex.utils.devices import ELS_TAKEUP_ERR_TIMEOUT, takeup_failure_text
    msg = takeup_failure_text(ELS_TAKEUP_ERR_TIMEOUT)
    assert "half-nut" not in msg.lower(), "a timeout is not a half-nut diagnosis"
    assert msg.lower().startswith("cut aborted")


# The character budget is a PROXY for a pixel budget, and the proxy is
# calibrated, not guessed. The notice strip is translucent and pinned across
# the top of the advanced bar -- i.e. over the status gutter -- so a message
# wider than the gap between the two status chips renders ON TOP of the
# phase-offset chip's text.
#
# THE BUDGET IS NOT THE GAP, and the first version of this comment had it
# wrong. The strip's Label is halign 'center' across the FULL bar and the gap
# is not centred on the bar, so a centred string is bounded by twice its
# distance to the NEARER chip. Measured 2026-08-29 at 1024x600, stop-only,
# both chips up (previews/preview_phase_offset.py::measure_takeup_texts):
#
#   chip_reference.right = 197, chip_phase.x = 783, bar centre = 566
#   raw gap                              586 px   <- NOT the constraint
#   centred budget  2 x (783 - 566)  =   435 px   <- the constraint
#   worst observed density               ~6.7 px/char
#
# 65 chars x 6.7 = 435 px. Checking against the 586 px gap passed every string
# and the RENDER then showed the longest one sitting on the phase chip's text
# anyway -- which is why the texts were re-chosen from
# previews/preview_takeup_text_widths.py rather than from a character count.
#
# KNOWN NOT COVERED, and recorded here rather than quietly assumed away: the
# budget is not constant either. The phase chip sizes to its value, and its
# longest possible value is the un-convertible fallback ("+500 leadscrew steps
# thread geometry unavailable"), which pulls chip_phase.x left to ~633 and
# collapses the centred budget to ~135 px. NOTHING fits that; no character
# budget can fix it, and the remedy would be to the strip (align it to the
# gap instead of the bar) or to the fallback text. It needs a
# missing-thread-geometry fault and a take-up refusal at the same time, so it
# is a transient double fault -- but do not read this guard as proof there is
# no collision.
#
# It is a proxy because the honest measurement needs a real GL texture, which
# this suite cannot build (the mock backend segfaults on real textures). The
# rendering check lives in previews/preview_phase_offset.py, which prints
# every message's measured width against the real budget; this guard is what
# fails in CI when someone lengthens a string without running it.
TAKEUP_TEXT_MAX_CHARS = 65


def test_takeup_texts_fit_the_gutter_gap():
    from reflex.utils.devices import (ELS_TAKEUP_MESSAGES,
                                      ELS_TAKEUP_WRONG_WAY,
                                      ELS_TAKEUP_UNKNOWN,
                                      ELS_TAKEUP_TIMEOUT_LATCHED)
    texts = dict(ELS_TAKEUP_MESSAGES)
    texts["WRONG_WAY"] = ELS_TAKEUP_WRONG_WAY
    texts["UNKNOWN"] = ELS_TAKEUP_UNKNOWN
    # Added 2026-08-30 with the string itself. Every message that can reach
    # the notice strip has to be in this dict or it has no width guard at
    # all, and the one added outside it is the one that lands on the chip.
    texts["TIMEOUT_LATCHED"] = ELS_TAKEUP_TIMEOUT_LATCHED
    for key, msg in texts.items():
        assert len(msg) <= TAKEUP_TEXT_MAX_CHARS, (
            f"take-up message {key} is {len(msg)} characters, over the "
            f"{TAKEUP_TEXT_MAX_CHARS} a CENTRED string has room for between "
            f"the status chips. The notice strip is translucent and sits over "
            f"the gutter, so this one will render on top of the phase-offset "
            f"chip's text. Measure, do not count: "
            f"previews/preview_takeup_text_widths.py.\n  {msg}")
    # And the whole point of the trim: nothing in this set may name a count.
    for key, msg in texts.items():
        assert "count" not in msg.lower(), (
            f"take-up message {key} names a count. Raw Z-scale counts are a "
            f"unit this UI shows nowhere else; they belong in the REFUSED log "
            f"line.\n  {msg}")


# ── the run owns its scale index (2026-08-25 bench failure) ────────────────
# elsStop.scaleIndex is firmware RAM: a power cycle resets it to 0, the
# SPINDLE. Nothing between boot and a calibration used to push the real Z
# index, so a fresh-boot cal drove the carriage its whole ceiling while
# watching a stationary spindle and reported NO_MOTION -- on two different
# firmware builds, at the machine, before this was understood. Every earlier
# fresh-boot cal had worked only because some incidental operator action
# (a retract, an engage) pushed the index first.

def test_start_pushes_the_z_scale_index():
    hal = FakeHal()
    cal = BacklashCalibration(hal, _els(z_input_index=1))

    assert cal.start() is True
    assert hal.scale_index_written == 1


def test_the_index_pushed_is_the_mapped_axis_not_a_constant():
    """A hardcoded 1 would pass the test above on elspi and silently watch
    the wrong scale on any machine mapped differently."""
    hal = FakeHal()
    cal = BacklashCalibration(hal, _els(z_input_index=3))

    cal.start()

    assert hal.scale_index_written == 3


def test_no_z_axis_refuses_before_anything_is_written():
    """The refusal must name the operator's fix, and nothing may reach the
    firmware -- limits included: a half-configured run that then gets its
    index from a later accidental push would calibrate with THESE limits."""
    hal = FakeHal()
    cal = BacklashCalibration(hal, _els(z_input_index=None))

    assert cal.start() is False
    assert cal.state == CalState.REFUSED
    assert "Z axis" in cal.message
    assert hal.scale_index_written is None
    assert hal.limits is None
    assert hal.cal_command == 0 or hal._running == 0


# ── a failed run must not loosen the take-up gate (2026-08-23 finding) ─────
# On EVERY calibration completion, success or failure, the firmware copies its
# working measured[] into elsStop.calMeasured -- the same array the take-up
# confirmation threshold derives from. A run that failed before measuring
# anything copies ZEROS, silently dropping that gate to its 2-count floor for
# every subsequent pass until the next successful calibration. The UI holds
# the accepted record (els_cal_measured_legs), so a finished-but-failed run
# re-teaches it immediately -- the same mechanism reconcile applies at connect.

def test_failed_run_reteaches_the_stored_legs():
    """MUTATION: drop the _reteach_stored_legs call on the result!=OK path and
    the gate stays at the floor until the next connect."""
    els = _els(els_cal_measured_legs=[365, 373, 366])
    hal = FakeHal(result=ELS_CAL_ERR_NO_MOTION, measured=(0, 0, 0))
    cal = BacklashCalibration(hal, els)
    cal.start()

    assert _run_to_completion(cal) == CalState.REFUSED
    assert hal.cal_measured_written == [365, 373, 366]


def test_inconsistent_run_reteaches_the_stored_legs():
    """INCONSISTENT means the firmware run SUCCEEDED -- its wide-spread legs
    are already in calMeasured -- but the host just rejected them, so the gate
    must keep deriving from the ACCEPTED record, not the rejected one."""
    els = _els(els_cal_measured_legs=[365, 373, 366])
    hal = FakeHal(measured=(100, 240, 98))
    cal = BacklashCalibration(hal, els)
    cal.start()

    assert _run_to_completion(cal) == CalState.INCONSISTENT
    assert hal.cal_measured_written == [365, 373, 366]


def test_timeout_reteaches_the_stored_legs():
    els = _els(els_cal_measured_legs=[365, 373, 366])
    hal = FakeHal(ticks_to_finish=10 ** 9)
    cal = BacklashCalibration(hal, els)
    cal.start()

    for _ in range(BacklashCalibration.TIMEOUT_POLLS + 1):
        cal.poll()

    assert cal.state == CalState.REFUSED
    assert hal.cal_measured_written == [365, 373, 366]


def test_failed_run_with_no_stored_record_tells_the_operator():
    """Nothing to restore is a state the operator must hear about: the gate
    sits at its bare motion floor until a calibration passes, and today
    nothing says so. MUTATION: drop the message append and this fails."""
    els = _els()                          # legs [0, 0, 0]: never calibrated
    hal = FakeHal(result=ELS_CAL_ERR_NO_MOTION)
    cal = BacklashCalibration(hal, els)
    cal.start()

    assert _run_to_completion(cal) == CalState.REFUSED
    assert hal.cal_measured_written is None      # zeros are not a record
    assert "until a calibration passes" in cal.message


def test_passed_run_does_not_repush_from_poll():
    """Success needs no restoration: the firmware just wrote REAL legs, and
    commit()/reconcile own persistence. A poll-side push on success would
    overwrite the fresh measurement with the stale stored record."""
    els = _els(els_cal_measured_legs=[365, 373, 366])
    hal = FakeHal(measured=(100, 101, 100))
    cal = BacklashCalibration(hal, els)
    cal.start()

    assert _run_to_completion(cal) == CalState.PASSED
    assert hal.cal_measured_written is None


def test_start_refusal_does_not_repush():
    """start() refusals never ran the firmware, so calMeasured was never
    zeroed -- and on some refusals the hal is not even reachable."""
    els = _els(els_cal_measured_legs=[365, 373, 366], z_input_index=None)
    hal = FakeHal()
    cal = BacklashCalibration(hal, els)

    assert cal.start() is False
    assert hal.cal_measured_written is None


def test_commit_stores_the_three_legs_for_reconcile():
    """The mean was always saved; the LEGS are what reconcile re-teaches to
    firmware RAM after a power cycle, so the take-up gate keeps its derived
    threshold. Verbatim, not a fabricated [mean, mean, mean]."""
    hal = FakeHal(measured=(365, 373, 366))
    els = _els()
    cal = BacklashCalibration(hal, els)
    cal.start()
    _run_to_completion(cal)
    assert cal.state == CalState.PASSED

    cal.commit()

    assert els.els_cal_measured_legs == [365, 373, 366]


# ── servoMode promotion: the operator should not have to arm sync ──────────
#
# The firmware refuses a calibration unless servoMode == 1
# (ELS_CAL_ERR_SERVOMODE), and its only automatic route to mode 1 is
# servoEnableTask's promotion on `anySyncMotionEnabled && !active && mode != 2`.
# A calibration enables no sync, so that never fires. Before this, the operator
# had to arm spindle-following by hand -- a state in which a turning spindle
# drives the carriage -- purely to satisfy a precondition for an operation that
# does not use the spindle at all.


def test_start_promotes_servo_mode_and_finish_restores_it():
    """MUTATION: drop the set_servo_mode(1) in start() and the first assert
    fails; drop the restore in _restore_motion() and the last one does."""
    hal = FakeHal(ticks_to_finish=3, measured=(100, 101, 100))
    assert hal.servo_mode == 0
    cal = BacklashCalibration(hal, _els())

    cal.start()
    assert hal.servo_mode == 1, (
        "calibration did not put the servo in sync/index mode; the firmware "
        "would refuse it with ELS_CAL_ERR_SERVOMODE")

    assert _run_to_completion(cal) == CalState.PASSED
    assert hal.servo_mode == 0, (
        "servo mode was left promoted after the run -- the operator's machine "
        "is in a different state than they left it")


def test_cancel_restores_servo_mode():
    """Every terminal path restores, not just the happy one."""
    hal = FakeHal(ticks_to_finish=50, measured=(100, 101, 100))
    cal = BacklashCalibration(hal, _els())
    cal.start()
    assert hal.servo_mode == 1
    cal.cancel()
    assert hal.servo_mode == 0, "cancel left the servo promoted"


def test_jog_mode_is_restored_not_clobbered():
    """An operator in jog gets jog back. Mode 2 is a deliberate state, and
    silently leaving them in sync/index afterwards would be a surprise."""
    hal = FakeHal(ticks_to_finish=3, measured=(100, 101, 100))
    hal.servo_mode = 2
    cal = BacklashCalibration(hal, _els())
    cal.start()
    assert hal.servo_mode == 1, "did not promote out of jog to run"
    assert _run_to_completion(cal) == CalState.PASSED
    assert hal.servo_mode == 2, "did not put the operator back in jog"


def test_a_fabricated_mode_read_is_not_restored_over_the_real_mode():
    """A checksum-failed read_servo_mode() fabricates 0, which is a REAL mode
    here (servo off). Restoring it over a genuine mode 1 would disable the
    servo after a successful calibration -- the same fabricated-read shape as
    the calSeq/calResult guards above, applied to the mode.

    MUTATION: drop the reads_fabricated_since() check around the save and this
    fails -- the fabricated 0 is saved and then written back over the real 1.
    """
    hal = FakeHal(ticks_to_finish=3, measured=(100, 101, 100))
    hal.servo_mode = 1                 # operator was already in sync/index
    hal._fail_next_mode_read = True    # ...but the read of it is corrupted
    cal = BacklashCalibration(hal, _els())

    cal.start()
    assert hal.servo_mode == 1
    assert _run_to_completion(cal) == CalState.PASSED
    assert hal.servo_mode == 1, (
        "a fabricated mode read was restored, disabling the servo after a "
        "calibration that succeeded")
    assert hal.read_failures >= 1
