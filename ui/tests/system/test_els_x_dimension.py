"""System-level tests for the X (cross-slide) axis: until this file, X never
moved in ANY test at any level (see tests/system/test_emu_x_channel.py, the
first probe of harness.emu_cmd / harness.x_position_counts()). That gap hid
three real corners with zero coverage:

1. test_retract_works_with_negative_x_encoder -- THE regression for the real
   lathe's power-on X encoder sitting below its zero. ElsFsm.is_ready_to_retract
   once collapsed to a bare `x_encoder >= 0` check on the RAW encoder count
   (see the long comment on that method in reflex/fsms/els_fsm.py) and
   refused every retract on such a machine. The current source already
   carries the fix (`check_x_retract` defaults False, so the gate is a no-op
   today), but this is the first END-TO-END test in which X is ever actually
   negative when a retract is attempted -- i.e. the first test that could
   catch a reintroduction of that bug at the full-stack level.
2. test_threading_x_clear_gate_respects_is_inner -- the "Move X clear of
   start diameter, then retract" threading gate
   (ElsUiController._x_clear_of_start_dia) and its is_inner polarity flip
   (OD clears above start_dia, ID clears below), which had zero system-level
   coverage.
3. test_x_reverse_wiring_cancels -- the x_reverse leg of the physical-wiring
   reversing matrix (test_els_reversing_matrix.py), whose own docstring
   admits it never exercises x_reverse because X doesn't move during a
   stop-only turning cut.

TOML override mechanism (reused, not reinvented): tests/system/wiring.py's
``make_config(base_toml, {"section.key": value, ...}, out_path)`` writes a
byte-for-byte patched copy of the base emulator config, and
tests/system/conftest.py's ``emulator_process`` fixture accepts that patched
path via *indirect* parametrization: ``{"config": <path>, "env": {...}}``
(see conftest.py's ``_resolve_emulator_param`` -- ``wiring_cut_harness`` uses
the exact same two-piece pattern for the reversing matrix). Both custom
configs below are built ONCE at module-import time (pytest collection) into
a throwaway ``tempfile.mkdtemp()`` dir: indirect-parametrize values must be
fixed at decoration time, so a per-test ``tmp_path`` (as ``wiring_cut_harness``
uses) isn't available here -- this hoists the same generation to module
scope instead. Guarded on the base TOML actually existing so collection still
degrades gracefully (mirrors ``emulator_binary``'s clean skip) when reflex-fw
isn't checked out alongside reflex-ui.

Harness/wiring gaps found: NONE. Every method needed already existed --
``emu_cmd``, ``x_position_counts``, ``wait_until``, ``set_input_reverse``,
``commit_standalone_start_dia``, ``trigger_retract`` (harness.py) and
``make_config`` (wiring.py) -- plus the controller's plain Kivy properties
(``is_inner``, ``action_allowed``, ``start_dia``) read directly, the same way
tests/system/test_els_safety.py already reads ``action_button_text`` etc.
"""

import os
import tempfile
from fractions import Fraction
from pathlib import Path

import pytest

from tests.system.wiring import make_config

pytestmark = pytest.mark.system

# Mirrors conftest.py's REFLEX_FW_DIR default exactly. Kept independent here
# (rather than importing conftest.REFLEX_FW_DIR) since conftest.py is
# read-only for this file and this only needs the one derived path.
_REFLEX_FW_DIR = Path(
    os.environ.get("REFLEX_FW_DIR")
    or Path(__file__).resolve().parents[3] / "fw"
)
_BASE_TOML = _REFLEX_FW_DIR / "emulator" / "config" / "lathe.toml"

# Real machine commissioning (forward +30 spindle, servo reversed at
# commission_servo(reverse=True)) -- same env every other stop/retract system
# test uses (test_els_turning_stop_retract.py, test_els_safety.py).
_REAL_COMMISSIONING_ENV = {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}

if _BASE_TOML.is_file():
    _CONFIG_DIR = Path(tempfile.mkdtemp(prefix="test_els_x_dimension_"))

    # Test 1: X boots at 0 (not the default 50mm) so "x move -20" lands well
    # clear of the int16 boot-delta-wrap zone -- |initial_position_mm * 400|
    # must stay well under 32767 (~81.9mm), see Core/Src/Ramps.c's
    # `position += delta * scaleDir` accumulation off the raw HW timer delta.
    # min_position_mm widened to -30 so -20mm is inside travel (default is -5).
    _X_NEGATIVE_CONFIG = make_config(
        _BASE_TOML,
        {"cross_slide.initial_position_mm": 0.0, "cross_slide.min_position_mm": -30.0},
        _CONFIG_DIR / "x_negative.toml",
    )
    # Test 3: a PHYSICALLY miswired X encoder -- only the emulator's physics
    # model (physics.cpp x_scale_sign) is flipped. The firmware's own
    # scaleDir register still boots +1 regardless of this and is changed only
    # by the operator-facing toggle under test (harness.set_input_reverse).
    _X_REVERSED_WIRING_CONFIG = make_config(
        _BASE_TOML,
        {"cross_slide.scale_dir": -1},
        _CONFIG_DIR / "x_reversed_wiring.toml",
    )
else:
    # reflex-fw not checked out: the emulator_binary fixture will
    # pytest.skip() every test below before either path is ever opened --
    # these just need to be valid Path objects so parametrize can collect.
    _X_NEGATIVE_CONFIG = _BASE_TOML
    _X_REVERSED_WIRING_CONFIG = _BASE_TOML


def _commission(h):
    """Real-machine geometry + servo commissioning, shared by tests 1 and 2
    (mirrors test_els_safety.py's ``_commission`` / the stop_retract test)."""
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()
    h.set_feed(Fraction(254, 160))  # 16 TPI = 1.5875 mm/rev, ~0.79 mm/s at EMU_RPM=30


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE regression: retract must not refuse forever with X below zero.
# ═══════════════════════════════════════════════════════════════════════════

_X_NEGATIVE_PARAM = {"config": _X_NEGATIVE_CONFIG, "env": _REAL_COMMISSIONING_ENV}


@pytest.mark.parametrize("emulator_process", [_X_NEGATIVE_PARAM], indirect=True)
def test_retract_works_with_negative_x_encoder(harness):
    """Regression for the real-machine bug: ElsFsm.is_ready_to_retract once
    reduced to a bare `x_pos >= 0` check on the RAW cross-slide encoder count
    (a Python conditional-expression precedence bug -- see the long comment
    on that method in reflex/fsms/els_fsm.py). ANY machine whose cross-slide
    encoder sits below its power-on zero -- which is exactly the real lathe's
    commissioned state -- would refuse EVERY retract under that old gate: the
    domain FSM stuck forever in 'stopped', the UI stuck forever showing
    "Retracting...".

    Today's code disables that gate by default (`check_x_retract` stays
    False, so `is_ready_to_retract` is unconditionally True), but nothing at
    the system level had ever actually driven X negative before requesting a
    retract to prove the full stack survives it end-to-end.

    Sensitivity: reintroduce the old bare `x_pos >= 0` gate (or otherwise flip
    `check_x_retract` on with the vestigial `safe_x=0`, `inside=False`
    defaults) and this exact test hangs at the final wait_until -- the
    'retracting' transition's `is_ready_to_retract` condition refuses, so
    ElsFsm never leaves 'stopped' and the UI's `may_retract()` gate keeps the
    UI parked in waiting_to_retract, forever, exactly as it would on the real
    machine.
    """
    h = harness
    h.configure(is_threading=False, retract_enabled=True, wizard_enabled=False,
                els_forward=True)
    _commission(h)

    # Precondition: boots at X=0 (the custom config's initial_position_mm),
    # then drive it NEGATIVE before anything else -- the regression only
    # manifests when x_encoder < 0.
    boot_x = h.x_position_counts()
    assert abs(boot_x) <= 80, f"unexpected boot X position: {boot_x} counts (want ~0)"

    h.emu_cmd("x move -20")
    reached_x = h.wait_until(
        lambda: abs(h.x_position_counts() - (-8000)) <= 80, timeout_s=15.0)
    assert reached_x, f"X never reached -20mm: {h.x_position_counts()} counts"
    x_before_cut = h.x_position_counts()
    assert x_before_cut < 0, (
        f"precondition failed: X encoder is not negative ({x_before_cut} counts) "
        "-- the regression this test targets requires it"
    )

    z_start = h.z_scaled_position()
    margin = h.safety_margin()
    span = margin + 1.0
    stop_z = z_start - span          # cut moves -Z toward the shoulder
    retract_z = z_start              # retract back to the cut-start position

    h.set_stop_z(stop_z)
    h.set_retract_z(retract_z)

    h.engage()
    h.enable_sync()
    h.cut()
    reached_stop = h.wait_until(
        lambda: h.ui_fsm.state == "in_cycle.waiting_to_retract", timeout_s=20)
    assert reached_stop, (
        f"did not reach waiting_to_retract: ui={h.ui_fsm.state} els={h.els_fsm.state}"
    )

    # X never moves during a Z-only turning cut -- confirm directly (rather
    # than assume) so a future change that DOES move X mid-cut can't silently
    # void the precondition the rest of this test depends on.
    x_at_stop = h.x_position_counts()
    assert x_at_stop < 0, (
        f"precondition failed: X drifted non-negative before retract "
        f"({x_at_stop} counts, was {x_before_cut} before the cut)"
    )

    # THE regression: under the old gate this refuses forever. It must now
    # proceed and complete.
    h.trigger_retract()
    done = h.wait_until(lambda: h.els_fsm.state == "stopped", timeout_s=20)
    assert done, (
        f"retract never completed with a negative X encoder "
        f"(x={h.x_position_counts()}): els={h.els_fsm.state} ui={h.ui_fsm.state} "
        f"z={h.z_scaled_position()}"
    )
    z_after = h.z_scaled_position()
    assert z_after == pytest.approx(retract_z, abs=0.5), (
        f"retract completed but Z did not reach retract_z: after={z_after} "
        f"retract_z={retract_z}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2. Threading's X-clear-of-start-dia retract gate, both is_inner polarities.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("emulator_process", [{"env": _REAL_COMMISSIONING_ENV}],
                          indirect=True)
@pytest.mark.parametrize("is_inner", [False, True], ids=["OD", "ID"])
def test_threading_x_clear_gate_respects_is_inner(harness, is_inner):
    """ElsUiController._x_clear_of_start_dia gates the waiting_to_retract
    action button in threading mode ("Move X clear of start diameter, then
    retract"). OD work (is_inner=False) clears when X is ABOVE start_dia; ID
    work (is_inner=True) INVERTS that comparison -- clear is BELOW start_dia.
    Exercises all four gate states (two polarities x two X positions each);
    had zero system-level coverage before this file.
    """
    h = harness
    h.configure(is_threading=True, retract_enabled=True, wizard_enabled=False,
                els_forward=True)
    _commission(h)
    h.controller.is_inner = is_inner

    # start_dia at the X axis's scaled value for 40mm. Under commission_geometry
    # (X input ratioNum/ratioDen = 1/400, MM display factor=1, zero abs_offset/
    # offsets fresh from the tmp HOME) AxisDispatcher.scaledPosition ==
    # encoder_counts / 400 == physical mm directly (see
    # AxisDispatcher._update_position / position_to_encoder in
    # reflex/dispatchers/axis.py) -- so the scaled value FOR 40mm is just 40.0,
    # no separate unit conversion needed.
    h.controller.commit_standalone_start_dia(40.0)
    assert h.controller.start_dia == pytest.approx(40.0, abs=0.01), (
        f"precondition failed: start_dia commit did not land at 40mm "
        f"(got {h.controller.start_dia})"
    )

    z_start = h.z_scaled_position()
    margin = h.safety_margin()
    span = margin + 1.0
    stop_z = z_start - span
    retract_z = z_start
    h.set_stop_z(stop_z)
    h.set_retract_z(retract_z)

    h.engage()
    h.enable_sync()
    h.cut()
    reached = h.wait_until(
        lambda: h.ui_fsm.state == "in_cycle.waiting_to_retract", timeout_s=20)
    assert reached, (
        f"did not reach waiting_to_retract: ui={h.ui_fsm.state} els={h.els_fsm.state}"
    )

    def move_x_to(target_mm: float):
        h.emu_cmd(f"x move {int(target_mm)}")
        target_counts = target_mm * 400
        ok = h.wait_until(
            lambda: abs(h.x_position_counts() - target_counts) <= 80, timeout_s=15.0)
        # Precondition: X must actually have reached the commanded position
        # before the gate assertion that follows means anything.
        assert ok, (
            f"precondition failed: X never reached {target_mm}mm "
            f"({h.x_position_counts()} counts)"
        )

    below, above = 30.0, 50.0  # start_dia is 40.0

    if not is_inner:
        # OD: below start_dia -> blocked; above -> allowed.
        move_x_to(below)
        blocked = h.wait_until(lambda: h.controller.action_allowed is False, timeout_s=5)
        assert blocked, (
            f"OD, X below start_dia should block retract: "
            f"action_allowed={h.controller.action_allowed} "
            f"text={h.controller.instruction_text!r}"
        )
        move_x_to(above)
        allowed = h.wait_until(lambda: h.controller.action_allowed is True, timeout_s=5)
        assert allowed, (
            f"OD, X above start_dia should allow retract: "
            f"action_allowed={h.controller.action_allowed} "
            f"text={h.controller.instruction_text!r}"
        )
    else:
        # ID: inverted -- above start_dia -> blocked; below -> allowed.
        move_x_to(above)
        blocked = h.wait_until(lambda: h.controller.action_allowed is False, timeout_s=5)
        assert blocked, (
            f"ID, X above start_dia should block retract: "
            f"action_allowed={h.controller.action_allowed} "
            f"text={h.controller.instruction_text!r}"
        )
        move_x_to(below)
        allowed = h.wait_until(lambda: h.controller.action_allowed is True, timeout_s=5)
        assert allowed, (
            f"ID, X below start_dia should allow retract: "
            f"action_allowed={h.controller.action_allowed} "
            f"text={h.controller.instruction_text!r}"
        )

    assert h.ui_fsm.state == "in_cycle.waiting_to_retract", (
        f"moving X alone should not leave waiting_to_retract: ui={h.ui_fsm.state}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 3. x_reverse: the reversing-matrix leg the matrix itself never exercises.
# ═══════════════════════════════════════════════════════════════════════════

_TARGET_MM = 70.0  # default boot is 50mm -- a +20mm physical move
_EXPECTED_DELTA_COUNTS = 20 * 400  # +8000 counts, in a VALID (canceled) configuration
_COUNTS_TOL = 80


def _drive_and_measure_x_delta(h, *, reverse_toggle: bool) -> int:
    h.set_input_reverse(h.X_SCALE_INDEX, reverse_toggle)
    h.pump()
    # Precondition: the toggle write actually reached the firmware register
    # this test's invariant depends on -- a check that structurally can't
    # fail can't prove anything about what follows (confirmed live/writable
    # for this exact register in test_task3_smoke.py's scaleDir probe).
    want_scale_dir = -1 if reverse_toggle else 1
    got_scale_dir = h.board.device['scales'][h.X_SCALE_INDEX]['scaleDir']
    assert got_scale_dir == want_scale_dir, (
        f"precondition failed: X scaleDir register is {got_scale_dir}, "
        f"want {want_scale_dir} after set_input_reverse(reverse={reverse_toggle})"
    )

    start = h.x_position_counts()
    h.emu_cmd(f"x move {int(_TARGET_MM)}")
    ok = h.wait_until(
        lambda: abs((h.x_position_counts() - start) - _EXPECTED_DELTA_COUNTS)
        <= _COUNTS_TOL,
        timeout_s=15.0,
    )
    end = h.x_position_counts()
    delta = end - start
    assert ok, (
        f"X delta did not settle near +{_EXPECTED_DELTA_COUNTS} counts: "
        f"start={start} end={end} delta={delta}"
    )
    return delta


@pytest.mark.parametrize(
    "toggle_x_reverse,emulator_process",
    [
        pytest.param(False, None, id="baseline"),
        pytest.param(True, {"config": _X_REVERSED_WIRING_CONFIG}, id="reversed_wiring_cancelled"),
    ],
    indirect=["emulator_process"],
)
def test_x_reverse_wiring_cancels(harness, toggle_x_reverse):
    """The x_reverse leg of the physical-wiring reversing matrix
    (test_els_reversing_matrix.py) that the matrix's own module docstring
    admits it never exercises -- X doesn't move during a stop-only turning
    cut, so the matrix's stop-only cuts never touch x_reverse.

    The two parametrized runs share ONE assertion: a +20mm physical move must
    produce a +8000-count DRO delta. "baseline" (all-normal wiring, no UI
    toggle) proves that's the CORRECT reading. "reversed_wiring_cancelled"
    proves a PHYSICALLY miswired X encoder (cross_slide.scale_dir=-1 in the
    emulator config, affecting only the emulator's physics model -- see
    physics.cpp's x_scale_sign) reads IDENTICALLY once the operator's
    x_reverse toggle cancels it (harness.set_input_reverse) -- i.e. the same
    +20mm command produces the SAME +8000-count delta as the baseline,
    matching the reversing matrix's "valid configuration" invariant already
    proven for Z (test_els_reversing_matrix.py) and now for X too.

    Note the two runs' ABSOLUTE X counts differ (the reversed config's raw
    boot reading is negative -- firmware's scaleDir register boots +1 and the
    toggle only corrects readings from the moment it's applied onward, not
    retroactively; see Core/Src/Ramps.c's `position += delta * scaleDir`), so
    the shared assertion is deliberately on the DELTA, not the absolute
    position -- which is also why "the same sign/magnitude as baseline" is
    encoded as one shared literal expectation rather than a live
    cross-run comparison.
    """
    h = harness
    delta = _drive_and_measure_x_delta(h, reverse_toggle=toggle_x_reverse)
    assert delta > 0, (
        f"X moved the WRONG direction for a positive-mm command "
        f"(toggle_x_reverse={toggle_x_reverse}): delta={delta}"
    )
