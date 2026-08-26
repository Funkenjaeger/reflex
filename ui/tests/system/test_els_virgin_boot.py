"""Virgin boot: the operator's fresh-power-cycle sequence against the
firmware's ACTUAL boot state.

Every other rig in the estate -- unit fakes, the emulator ctest targets, the
rest of this system suite -- pre-configures firmware registers the way the UI
happens to leave them mid-session. None ran the operator's real sequence:
power on (BSS-zeroed rampsSharedData_t), connect, walk straight into a
feature. That shared assumption is how elsStop.scaleIndex = 0 (the SPINDLE)
reached a live calibration on 2026-08-25: the cal drove the carriage its
whole ceiling while watching a stationary spindle and reported NO_MOTION on
two different firmware builds, invisible to 1155 unit tests, 28 emulator
targets and 56 system tests SIMULTANEOUSLY. Every earlier fresh-boot cal had
worked only because some incidental operator action pushed the index first;
the bench sheet's power-cycle-then-calibrate-immediately ordering was the
first time nothing did.

These tests therefore begin by ASSERTING the firmware block is virgin -- if
harness/fixture growth ever starts pre-seeding those registers, the guard
fails before the scenario can pass vacuously.

Regression targets:
  * 97cc3aa -- BacklashCalibration.start() pushes the mapped Z scale index.
    Test 1 runs at EMU_RPM=0 (stationary spindle, the bench-failure
    configuration): without the fix the cal watches scale 0, sees nothing,
    and dies NO_MOTION exactly as it did at the machine.
  * 2744c05 -- the arm path teaches the job's premises (geometry, backlash,
    stop, scale index) and reconcile teaches the session-wide ones (sync
    ratios, calibration legs). Tests 2 and 3.

Future extension noted on the Open Loops task: a generic sweep that diffs the
whole elsStop block after ANY feature runs from virgin boot -- fields still
at zero that the feature depends on are the finding.
"""

from fractions import Fraction

import pytest

pytestmark = pytest.mark.system


def _settle(h, ticks=30):
    """Pump enough for Clock.schedule_once chains (connected bind → reconcile)
    to run. wait_until can't express 'nothing more happens', so pump a fixed,
    generous number of ticks."""
    for _ in range(ticks):
        h.pump()


def _assert_virgin(h):
    """The anti-assumption guard: the firmware block must be BSS-zero fresh.

    If this ever fails, some fixture or connect-path change started
    pre-seeding the registers these scenarios exist to prove the UI
    establishes -- and every assertion below it would pass vacuously.
    (reconcile_firmware_on_connect legitimately writes cal LIMITS and clears
    enable at connect; the fields asserted here are the ones only a feature
    path establishes.)
    """
    assert int(h.register('elsStop', 'scaleIndex')) == 0, (
        "expected the virgin scaleIndex (0, the spindle) -- something "
        "pre-seeded it before the scenario ran")
    assert float(h.register('elsStop', 'threadPitchSteps')) == 0.0
    assert int(h.register('elsStop', 'backlashSteps')) == 0
    assert int(h.register('elsStop', 'enable')) == 0
    assert list(h.register('elsStop', 'calMeasured')) == [0, 0, 0]


# ── 1. the bench sequence: power cycle, connect, calibrate FIRST ──────────
# EMU_RPM=0: the spindle is stationary, as it was at the machine. This is
# load-bearing -- with the spindle RUNNING, a regressed cal watching scale 0
# would see spindle counts as "motion" and pass on garbage instead of dying
# NO_MOTION, and this test would lose its teeth.

@pytest.mark.parametrize(
    "emulator_process", [{"env": {"EMU_RPM": "0"}}], indirect=True)
def test_fresh_boot_calibration_watches_the_mapped_scale(harness):
    from reflex.fsms.els_cal import BacklashCalibration, CalState

    h = harness
    _settle(h)
    _assert_virgin(h)

    # The operator's sequence, nothing more: axis mapping and servo
    # commissioning are persisted UI config (a real fresh boot HAS them --
    # they live in yaml, not firmware RAM), servo enable is the cal screen's
    # stated precondition (fastData.servoMode must be 1), then straight into
    # the run -- no retract, no arm, no engage first.
    h.configure(is_threading=False, retract_enabled=False,
                wizard_enabled=False, els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.enable_sync()

    # EMULATOR ISR-RATE ACCOMMODATION (probe-verified 2026-08-25): the cal
    # drives at CAL_MAX_SPEED=800, which makes the firmware derive
    # servoCycles = 100000/800 = 125 -- correct pulse pacing on 100 kHz
    # hardware, but the emulator ISR runs at 10 kHz, so pulses emit at ~80/s
    # while stepsToGo drains at the ramp's wall-clock 800/s. The drain-based
    # leg then exhausts its ceiling having EMITTED only ceiling/10 steps
    # (~100 at the default 1008), under the 151-step serve-mode lash --
    # NO_MOTION as an artifact. Widening the ceiling (persisted UI config)
    # keeps emitted-steps-at-drain (~400) safely past the lash. The legs
    # themselves measure EMITTED steps, so the measurement is unaffected.
    # A regressed spindle-watching cal still fails: at EMU_RPM=0 nothing
    # moves scale 0, and each leg now just drains longer before NO_MOTION.
    h.els.els_cal_ceiling_steps = 4000

    cal = BacklashCalibration(h.hal, h.els)
    assert cal.start() is True, f"cal refused to start: {cal.message}"

    # 97cc3aa, asserted at the REGISTER: the run owns its precondition.
    assert int(h.register('elsStop', 'scaleIndex')) == h.Z_SCALE_INDEX, (
        "calibration did not push the mapped Z scale index -- a fresh-boot "
        "cal is watching the spindle again")

    assert h.wait_until(lambda: cal.poll() != CalState.RUNNING, timeout_s=60)
    assert cal.state == CalState.PASSED, (
        f"fresh-boot calibration did not pass: state={cal.state} "
        f"result={cal.result_code} measured={cal.measured} -- {cal.message}")

    # A plausible measurement of the emulator's ACTUAL mechanics: serve mode
    # forces z_backlash_mm = 0.6 regardless of the TOML (reflex-fw main.cpp;
    # see test_els_takeup_attribution.py), ≈ 151 servo steps at 127/32000
    # mm/step, plus a couple of steps of detection distance at the 2-count
    # threshold. This magnitude bound is also what keeps the test honest if
    # it ever runs with a turning spindle: a regressed cal watching the
    # SPINDLE scale sees "motion" within a step or two and would report
    # legs ≈ 1 -- implausible here, so it still fails.
    assert all(100 <= int(v) <= 250 for v in cal.measured), (
        f"implausible legs for the serve-mode 0.6 mm lash (~151 steps): "
        f"{cal.measured}")

    # Committing lands the take-up COMMAND in the virgin register.
    assert cal.commit() is True
    _settle(h)
    assert int(h.register('elsStop', 'backlashSteps')) == cal.command_steps


# ── 2. the armed job describes itself: premises reach the firmware ────────
# The 15-minute silent bench test of 2026-08-25 was this defect: engage after
# a power cycle and the job ran with pitch 0 / backlash 0 -- geometry used to
# arrive only at first Cut, backlash only from calibration paths.

@pytest.mark.parametrize(
    "emulator_process", [{"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}],
    indirect=True)
def test_fresh_boot_armed_job_teaches_its_premises_then_cuts(harness):
    h = harness
    _settle(h)
    _assert_virgin(h)

    # Persisted commissioning a real machine carries across power cycles:
    # mapping, servo polarity/speeds, geometry, feed -- and a previously
    # committed take-up. The firmware knows NONE of it at boot.
    h.configure(is_threading=False, retract_enabled=False,
                wizard_enabled=False, els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()
    h.set_feed(Fraction(254, 160))          # 16 TPI, ~0.79 mm/s at EMU_RPM=30
    # Committed take-up from a prior session. 300 steps ≈ 1.19 mm: it must
    # CLEAR the serve-forced 0.6 mm lash (see test_els_takeup_attribution.py)
    # or the take-up confirmation legitimately refuses the pass -- 16 steps
    # here dies inside the lash zone with "moved 0 counts".
    h.els.els_backlash_steps = 300

    z_start = h.z_scaled_position()
    span = h.safety_margin() + 1.0
    h.set_stop_z(z_start - span)
    h.engage()
    assert h.els_fsm.state == "stopped"
    _settle(h)

    # 2744c05, asserted at the registers: arming alone fully describes the
    # job. Before it, every one of these still held its boot zero here.
    assert int(h.register('elsStop', 'enable')) == 1
    assert int(h.register('elsStop', 'scaleIndex')) == h.Z_SCALE_INDEX
    assert int(h.register('elsStop', 'backlashSteps')) == 300, (
        "the persisted take-up did not reach the firmware at arm")
    assert float(h.register('elsStop', 'threadPitchSteps')) == 0.0, (
        "turning mode must push pitch 0 (no thread phase), not leave the "
        "register unwritten -- same value, different meaning")
    assert float(h.register('elsStop', 'zCountsPerPitch')) != 0.0, (
        "turning geometry must still carry the SIGNED zCountsPerPitch -- "
        "the take-up direction derives from its sign")
    assert int(h.register('elsStop', 'stopDirection')) != 0

    # And the job actually runs: feed to the stop and halt there.
    h.enable_sync()
    h.cut()
    assert h.els_fsm.state == "cutting", f"cut did not start: {h.els_fsm.state}"
    assert h.wait_until(lambda: h.els_fsm.state == "stopped", timeout_s=20), (
        f"cut never stopped; z_now={h.z_scaled_position()}")
    z_final = h.z_scaled_position()
    assert (z_final - z_start) < 0, "cut fed away from the stop"
    assert z_final == pytest.approx(z_start - span, abs=0.25)


# ── 3. reconcile re-teaches the session-wide premises ─────────────────────
# The take-up confirmation threshold derives from elsStop.calMeasured, which
# a power cycle zeroes: the gate silently fell to its 2-count motion floor
# every boot, mitigated only by a bench-sheet instruction to recalibrate.
# Reconcile now re-teaches the persisted legs; this pins the round-trip end
# to end -- persisted UI record, through the device layer's element-wise
# array write, over real Modbus, into the virgin register block.

def test_fresh_boot_reconcile_reteaches_the_calibration_legs(harness):
    h = harness
    _settle(h)
    _assert_virgin(h)

    # The persisted record a previously calibrated machine carries.
    h.els.els_cal_measured_legs = [365, 373, 366]

    # Drive the same path a (re)connect uses -- the warm-firmware test's
    # idiom for exercising reconcile on demand.
    h.controller._on_connected_changed(h.board, True)
    _settle(h)

    assert list(h.register('elsStop', 'calMeasured')) == [365, 373, 366], (
        "the persisted calibration legs did not reach the firmware -- the "
        "take-up gate is deriving its threshold from the motion floor")
