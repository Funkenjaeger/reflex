"""The system suite commissions every other test to the EMULATOR REFERENCE
machine's geometry (reflex-fw emulator/config/lathe.toml: 400 counts/mm
scales, 4000 counts/rev spindle, 127/32000 mm/step leadscrew --
SystemHarness.commission_geometry's defaults). This file instead runs one
full ELS cut/stop/retract cycle at the REAL elspi lathe's commissioned
geometry, read from its live config 2026-08-03:

    Z scale:       200 counts/mm      (reference machine: 400)
    X scale:       400 counts/mm      (matches -- no change)
    Spindle:       6144 counts/rev    (reference machine: 4000)
    Leadscrew:     0.125 in @ 1600 steps/rev -> exactly 127/64000 mm/step
                   (reference machine: 800 steps/rev -> 127/32000)

CRITICAL INSIGHT this test exists to prove out: you must change BOTH sides.
test_els_real_config.py's fidelity-gap note (#1) explains why it deliberately
keeps the emulator-reference 127/32000 leadscrew ratio on the UI side even
while commissioning otherwise-real-machine settings: mirroring the real
127/64000 ratio on the UI ALONE would put the FSM's count-domain step math at
exactly 2x the emulator's own physics, producing a self-inflicted unit
mismatch rather than reproducing anything real. That is only true if the UI
changes alone. This file instead overrides the EMULATOR'S OWN physics config
(via tests/system/wiring.py's make_config, the same TOML-patch mechanism
test_els_x_dimension.py uses) to match the same elspi geometry, so both sides
agree and the mismatch disappears -- see SystemHarness.commission_geometry's
docstring, which now documents this as the intended way to commission a
non-reference machine.

Harness change this test needed (see harness.py): commission_geometry() was
parameterized (z_counts_per_mm, x_counts_per_mm, spindle_ppr,
leadscrew_steps), all keyword-only with the prior hardcoded values as
defaults -- every other system test's call site is unaffected.

Sanity note: under EMU_SCENARIO (which conftest.py always sets, to "serve")
the emulator forces z_initial_mm=100 regardless of the TOML, and widens Z
travel -- so this test never assumes Z boots at 0; every span below is
computed relative to a freshly-read z_start, exactly like every other system
test. At 200 counts/mm the boot offset is 20000 counts, comfortably under the
int16 boot-delta wrap at 32767 (see test_els_x_dimension.py's own note on
this same class of wrap, at 400 counts/mm).

Reference tests this borrows its flow/assertion shape from:
test_els_turning_stop_retract.py (commission -> cut -> stop -> retract, and
its retract-direction/overshoot bounds) and test_els_real_config.py (the
independent first-principles recomputation of threadPitchSteps/
zCountsPerPitch, which this file mirrors at the elspi geometry instead of the
reference geometry).
"""

import os
import tempfile
from fractions import Fraction
from pathlib import Path

import pytest

from reflex.feeds import THREAD_IN
from tests.system.wiring import make_config

pytestmark = pytest.mark.system

# Mirrors conftest.py's REFLEX_FW_DIR default exactly (same pattern as
# test_els_x_dimension.py -- conftest.py is read-only for this file).
_REFLEX_FW_DIR = Path(
    os.environ.get("REFLEX_FW_DIR")
    or Path(__file__).resolve().parents[3] / "fw"
)
_BASE_TOML = _REFLEX_FW_DIR / "emulator" / "config" / "lathe.toml"

# elspi's live-read geometry (2026-08-03) -- ground truth for both the UI
# commissioning below and the emulator TOML patch.
_ELSPI_Z_COUNTS_PER_MM = 200        # [z_axis] encoder_counts_per_mm: 400 -> 200
_ELSPI_SPINDLE_PPR = 6144           # [spindle] counts_per_rev: 4000 -> 6144
_ELSPI_LEADSCREW_STEPS = 1600       # 0.125in @ 1600 steps/rev (was 800)
_ELSPI_MM_PER_STEP = 0.001984375    # [leadscrew] mm_per_step: exactly 127/64000
# (0.001984375 is the exact decimal expansion of 127/64000 -- 64000 = 2^9*5^3
# has only 2 and 5 as prime factors, so the decimal terminates with no
# rounding. This float literal is only what gets written into the emulator's
# TOML text; the harness/test math below uses the exact Fraction, never this
# float, for any assertion -- see harness.py's commission_geometry, which
# asserts Fraction(127, 40*leadscrew_steps) exactly.)

if _BASE_TOML.is_file():
    _CONFIG_DIR = Path(tempfile.mkdtemp(prefix="test_els_elspi_geometry_"))
    _ELSPI_CONFIG = make_config(
        _BASE_TOML,
        {
            "spindle.counts_per_rev": _ELSPI_SPINDLE_PPR,
            "z_axis.encoder_counts_per_mm": _ELSPI_Z_COUNTS_PER_MM,
            "leadscrew.mm_per_step": _ELSPI_MM_PER_STEP,
        },
        _CONFIG_DIR / "elspi_geometry.toml",
    )
else:
    # reflex-fw not checked out: the emulator_binary fixture will
    # pytest.skip() before this path is ever opened -- just needs a valid
    # Path object so parametrize can collect (mirrors test_els_x_dimension.py).
    _ELSPI_CONFIG = _BASE_TOML

# EMU_NO_AUTO_RETRACT: this test drives reflex-ui's OWN (host/FSM) retract,
# so the emulator must not fight it with a simulated hand-retract on ELS
# stop. No EMU_RPM override -- the emulator's serve-mode default (30, a
# forward spindle) already IS the real elspi commissioning's spindle
# direction; the SERVO carries the reversal (commission_servo below), exactly
# as documented in test_els_turning_stop_retract.py.
_ELSPI_PARAM = {"config": _ELSPI_CONFIG, "env": {"EMU_NO_AUTO_RETRACT": "1"}}

# Thread IN "20" (20 TPI, elspi's threading feed) -- reflex/feeds.py.
# Verified against the production table rather than assumed, so a future
# table edit can't silently drift this test's independent math out from
# under it.
_THREAD_IN_20 = next(f for f in THREAD_IN if f.name == "20")
assert _THREAD_IN_20.ratio == Fraction(254, 200), (
    f"reflex/feeds.py THREAD_IN '20' changed: {_THREAD_IN_20.ratio} != 254/200"
)


@pytest.mark.parametrize("emulator_process", [_ELSPI_PARAM], indirect=True)
def test_els_cycle_at_elspi_geometry(harness):
    """One full cut -> ELS stop -> retract cycle at elspi's real geometry,
    with the UI (harness.commission_geometry overrides) and the emulator's
    own physics (module-level TOML patch above) commissioned to match.
    """
    h = harness
    h.configure(is_threading=True, retract_enabled=True, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry(
        z_counts_per_mm=_ELSPI_Z_COUNTS_PER_MM,
        # x_counts_per_mm left at the shared default (400) -- elspi's X scale
        # matches the reference machine, no override needed.
        spindle_ppr=_ELSPI_SPINDLE_PPR,
        leadscrew_steps=_ELSPI_LEADSCREW_STEPS,
    )
    h.set_feed(_THREAD_IN_20.ratio)
    h.els.els_backlash_steps = 403   # real-machine value (Evan's measured compensation)
    h.pump()

    assert h.controller.is_threading is True
    assert h.controller.retract_enabled is True
    assert h.els.els_backlash_steps == 403

    z_start = h.z_scaled_position()
    margin = h.safety_margin()
    assert margin > 0
    span = margin + 1.0
    stop_z = z_start - span          # els_forward=True cuts -Z toward the stop
    retract_z = z_start

    h.set_stop_z(stop_z)
    h.set_retract_z(retract_z)

    h.engage()
    h.enable_sync()
    h.cut()
    assert h.ui_fsm.state == "in_cycle.cutting"
    assert h.els_fsm.state == "cutting"

    # ── Independent first-principles recomputation of the count-domain
    # registers push_thread_geometry() writes (reflex/fsms/els_fsm.py) --
    # mirror the arithmetic here, do NOT call the method under test. ────────
    spindle_axis = h.board.get_spindle_axis()
    servo = h.board.servo
    z_axis = h.els.get_z_axis()
    saddle_input = z_axis._primary_input()

    spindle_pitch_mm = Fraction(abs(spindle_axis.syncRatioNum), abs(spindle_axis.syncRatioDen))
    servo_ratio = Fraction(abs(servo.ratioNum), abs(servo.ratioDen))
    z_scale_mm_per_count = Fraction(saddle_input.ratioNum, saddle_input.ratioDen)

    expected_thread_pitch_steps = float(spindle_pitch_mm / servo_ratio)
    expected_z_counts_per_pitch = float(spindle_pitch_mm / z_scale_mm_per_count)

    # 20 TPI (254/200 mm/rev) over elspi's 127/64000 mm/step servo ratio and
    # 1/200 mm/count Z scale reduces to exact integers: 640 steps/pitch, 254
    # counts/pitch. Pin both the independent recomputation AND these
    # first-principles constants.
    assert expected_thread_pitch_steps == pytest.approx(640.0, abs=1e-6)
    assert expected_z_counts_per_pitch == pytest.approx(254.0, abs=1e-6)

    # THE point of this test: these must differ from what the REFERENCE
    # machine's geometry produces (400 steps/pitch, 635 counts/pitch at its
    # own 16 TPI default feed -- test_els_real_config.py pins those same two
    # constants). If the elspi numbers ever coincidentally matched, this test
    # would prove nothing about the geometry override actually taking effect.
    assert expected_thread_pitch_steps != pytest.approx(400.0), (
        "elspi threadPitchSteps coincides with the reference machine's -- "
        "the geometry override is not proven to have taken effect"
    )
    assert expected_z_counts_per_pitch != pytest.approx(635.0), (
        "elspi zCountsPerPitch coincides with the reference machine's -- "
        "the geometry override is not proven to have taken effect"
    )

    assert h.register("elsStop", "threadPitchSteps") == pytest.approx(
        expected_thread_pitch_steps, abs=1e-3)
    assert h.register("elsStop", "zCountsPerPitch") == pytest.approx(
        expected_z_counts_per_pitch, abs=1e-3)
    assert h.register("elsStop", "backlashSteps") == 403
    assert h.register("elsStop", "stopDirection") == -1   # els_forward=True

    # ── Cut runs to the ELS stop ────────────────────────────────────────────
    reached = h.wait_until(
        lambda: h.ui_fsm.state == "in_cycle.waiting_to_retract", timeout_s=20)
    assert reached, (
        f"did not reach waiting_to_retract: ui={h.ui_fsm.state} els={h.els_fsm.state}"
    )
    z_at_stop = h.z_scaled_position()
    assert (z_at_stop - z_start) < 0, (
        f"cut fed the wrong way: start={z_start} at_stop={z_at_stop}"
    )
    assert abs(z_at_stop - z_start) > span * 0.4, (
        f"cut barely moved: start={z_start} at_stop={z_at_stop}"
    )

    # ── Retract (host-driven, retract_enabled) ──────────────────────────────
    h.trigger_retract()
    done = h.wait_until(lambda: h.els_fsm.state == "stopped", timeout_s=20)
    assert done, (
        f"retract never completed: els={h.els_fsm.state} z={h.z_scaled_position()}"
    )
    z_after = h.z_scaled_position()

    retract_error = z_after - retract_z
    print(
        f"[test_els_elspi_geometry] retract error: {retract_error:+.5f} mm "
        f"(target={retract_z:.5f} actual={z_after:.5f} z_at_stop={z_at_stop:.5f} "
        f"span={span:.5f} margin={margin:.5f})"
    )

    # Direction check (the c69b02a regression class -- see
    # test_els_turning_stop_retract.py): retract must move AWAY from the
    # stop (opposite the cut), never toward it.
    assert (z_after - z_at_stop) > 0, (
        f"retract moved the WRONG way (toward the stop): "
        f"at_stop={z_at_stop} after={z_after}"
    )
    # Same asymmetric bound test_els_turning_stop_retract.py uses: modest
    # servo/backlash overshoot past retract_z is expected and fine, a
    # runaway or a short-stop is not.
    assert z_after >= retract_z - 0.25, (
        f"retract stopped short of retract_z: after={z_after} retract_z={retract_z}"
    )
    assert z_after <= retract_z + 2.0, (
        f"retract overshot far past retract_z (runaway?): "
        f"after={z_after} retract_z={retract_z} span={span}"
    )
