"""The first system test that drives Evan's REAL commissioned lathe
configuration through the real UI FSM: threading mode, the wizard, host-driven
retract, and imperial display -- together, end to end. No prior system test
has cut a thread, driven the wizard, or run imperial.

REAL-MACHINE CONFIG (elspi, the commissioned lathe): threading ("Thread IN"
feeds table -> controller.is_threading=True), wizard_enabled=True,
retract_enabled=True, els_forward=True, servo reverse=True maxSpeed=10000
accel=20000, els_backlash_steps=403, imperial display, spindle=scale0/
Z=scale1/X=scale2.

── Fidelity gaps (deliberate, and load-bearing for the assertions below) ──

1. Leadscrew steps/rev. The real machine's UI-side servo geometry is ratio
   127/64000 mm/step (leadScrewPitch=0.125in, leadScrewPitchIn=True,
   leadScrewPitchSteps=1600 -- a finer microstep count than the emulator
   models). The emulator's OWN physics (reflex-fw emulator/config/lathe.toml
   [leadscrew] mm_per_step=0.00396875) is hardcoded to 127/32000 mm/step
   (leadScrewPitchSteps=800, matching SystemHarness.commission_geometry()).
   Mirroring the real 127/64000 on the UI side would make every FSM-computed
   step count (retract deltas, thread-pitch steps, the safety margin) disagree
   with what the emulator's physics actually produces -- off by exactly 2x --
   which would not reproduce a real bug, just a self-inflicted unit mismatch.
   This test therefore keeps commission_geometry()'s emulator-coherent
   127/32000 ratio (leadScrewPitchSteps=800) instead of the real 127/64000,
   and reports the gap here rather than silently mismatching. leadScrewPitch
   (0.125in) and leadScrewPitchIn (True) themselves ARE real-machine-accurate
   -- only the step count differs.

2. Backlash-compensation magnitude vs. the emulator's simulated nut play.
   els_backlash_steps=403 is a REQUIRED real-machine value (Evan's measured
   compensation) and is set as-is. At the emulator-coherent ratio above, 403
   steps is a commanded ~1.60 mm of compensation motion on the first retract
   after a cut (403 * 127/32000 mm). The emulator's own leadscrew-nut backlash
   window, however, is NOT the lathe.toml default (0.02 mm) in these tests --
   reflex-fw emulator/src/main.cpp forces z_backlash_mm to 0.6 mm whenever
   EMU_SCENARIO is set (which conftest.py always sets, to "serve"), regardless
   of EMU_LASH_MM being unset. Since our compensation (~1.60 mm) exceeds the
   emulator's actual simulated window (0.6 mm) by design (403 was tuned to
   Evan's real machine, not this emulator), the first retract is expected to
   overshoot the naive target by roughly that ~1.0 mm difference. Rather than
   pin a tight bound (which would either mask a real bug or be tuned to this
   incidental mismatch), the retract assertions use a generous, justified
   tolerance and print/report the observed error for calibration.

3. is_threading source. The harness has no ElsBar/ElsAdvancedBar (the Kivy
   widget layer) that would derive is_threading from a feeds-table name via
   feeds.is_threading_table(); harness.configure(is_threading=...) IS the
   closest real-machine-equivalent path exposed to system tests (every
   existing reference test that needs is_threading uses this same
   parameter), so that path is used here directly.

4. Feed selection. harness.set_feed() writes spindle_axis.syncRatioNum/Den
   using exactly the math in ElsBar.update_feeds_ratio (reflex/components/
   home/elsbar.py) -- verified by reading both. THREAD_IN "16" (16 TPI,
   Fraction(254, 160) mm/rev, reflex/feeds.py) is used, consistent with the
   rest of the suite's default feed.

Reference tests this borrows patterns from: test_els_turning_stop_retract.py
(commissioning + cut/retract flow), test_els_safety.py (driving the UI FSM
via the controller), test_emu_x_channel.py (the emulator X stdin channel:
harness.emu_cmd, harness.x_position_counts).
"""

from fractions import Fraction

import pytest

pytestmark = pytest.mark.system

# EMU_RPM=30 (real forward-spindle commissioning; the SERVO carries the
# reversal -- see commission_servo below and the Task 9 retract test).
# EMU_NO_AUTO_RETRACT: this test drives its OWN (host/FSM) retract, so the
# emulator must not fight it with a simulated hand-retract on ELS stop.
_ENV = {"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}

# Both the commissioned UI ratio (SystemHarness.commission_geometry, 1/400
# mm/count) and the emulator's own [z_axis]/[cross_slide] encoder_counts_per_mm
# are exactly 400 -- unlike the leadscrew ratio (fidelity gap #1 above), the
# scale ratios have no real-vs-emulator mismatch to report.
COUNTS_PER_MM = 400


def _move_x_and_wait(h, target_mm: float, tol_counts: int = 80, timeout_s: float = 15.0):
    """Issue harness.emu_cmd("x move <mm>") and wait for the emulator's X
    (cross-slide) physics to settle near target_mm, reading back the raw
    firmware count (harness.x_position_counts(), unit- and display-frame-
    independent). Mirrors test_emu_x_channel.py's pattern; not exposed by
    harness.py, so built locally here as instructed."""
    h.emu_cmd(f"x move {target_mm}")
    target_counts = target_mm * COUNTS_PER_MM
    reached = h.wait_until(
        lambda: abs(h.x_position_counts() - target_counts) <= tol_counts,
        timeout_s=timeout_s,
    )
    return reached, h.x_position_counts()


@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_real_machine_config_wizard_threading_retract(harness):
    h = harness

    # ── Commission: the real machine's configuration (see module docstring
    # for the two fidelity gaps this necessarily keeps) ─────────────────────
    h.configure(is_threading=True, retract_enabled=True, wizard_enabled=True,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()
    h.set_feed(Fraction(254, 160))   # Thread IN "16" (16 TPI) -- reflex/feeds.py
    h.els.els_backlash_steps = 403   # real-machine value; REQUIRED as-is
    h.pump()
    h.set_imperial()                 # real machine runs imperial ("Thread IN")

    assert h.controller.is_threading is True
    assert h.controller.retract_enabled is True
    assert h.controller.wizard_enabled is True
    assert h.els.els_backlash_steps == 403
    assert h.board.formats.current_format == "IN"

    # ── Engage BEFORE walking the wizard: action_allowed is gated on
    # `engaged` for every non-alarm UI state (ui_controller._apply_policy),
    # so engaging first isolates the "refuses with a missing value" check
    # below to the value gate, not the engage gate. ──────────────────────────
    h.engage()
    assert h.els_fsm.state == "stopped"
    assert h.controller.engaged is True

    z_start = h.z_scaled_position()      # inches (imperial), current Z
    margin = h.safety_margin()           # inches, from the commissioned geometry
    assert margin > 0

    # Cut span: forward convention (els_forward=True => cut_dir=-1) feeds Z
    # DOWN (more negative) toward stop_z. 1.3x the safety margin -- same
    # proportional buffer the reference tests use (margin + 1.0mm on a
    # ~3.49mm margin) -- clears it comfortably while keeping the wait short.
    span = margin * 1.3
    stop_z_target = z_start - span
    # Retract target: current Z, nudged slightly toward the stop (-0.01 in).
    # Required for is_retracted() (used as is_ready_to_cut() in retract mode)
    # to read True BEFORE the cut has moved Z at all: it requires
    # z_pos >= retract_z, and commit_standalone_retract_z round-trips the
    # value through position_to_encoder (int truncation) then back, so an
    # exact retract_z == z_start risks landing a hair above z_start and
    # failing that comparison. -0.01in is far larger than that quantization
    # (~1/400 mm ~= 0.0001in) and keeps span_retract >> margin.
    retract_z_target = z_start - 0.01

    # ── Wizard walk via the real UI FSM ──────────────────────────────────────
    assert h.ui_fsm.state == "idle"
    h.controller.on_start_stop_button_clicked()
    assert h.ui_fsm.state == "set_stop_z", "wizard_enabled should route idle -> set_stop_z"

    # Refuses to advance with nothing committed yet (stop_z_valid False).
    assert h.controller.stop_z_valid is False
    h.controller.try_advance_wizard()
    assert h.ui_fsm.state == "set_stop_z", "must not advance past an unset Stop Z"

    h.controller.commit_standalone_stop_z(stop_z_target)
    assert h.controller.stop_z_valid is True
    h.controller.try_advance_wizard()
    assert h.ui_fsm.state == "set_retract_z"

    h.controller.commit_standalone_retract_z(retract_z_target)
    assert h.controller.retract_z_valid is True
    h.controller.try_advance_wizard()
    assert h.ui_fsm.state == "set_start_dia"

    # Diameter steps: live-capture path (move X, then the action button
    # captures the live position AND advances in one call).
    reached, x_counts = _move_x_and_wait(h, 40.0)
    assert reached, f"X did not settle near 40mm: {x_counts} counts"
    h.pump()
    h.controller.on_action_button_clicked()
    assert h.ui_fsm.state == "set_stop_dia"
    start_dia_captured = h.controller.start_dia
    assert start_dia_captured == pytest.approx(40.0 / 25.4, abs=0.02)

    reached, x_counts = _move_x_and_wait(h, 35.0)
    assert reached, f"X did not settle near 35mm: {x_counts} counts"
    h.pump()
    h.controller.on_action_button_clicked()
    assert h.ui_fsm.state == "confirm"
    stop_dia_captured = h.controller.stop_dia
    assert stop_dia_captured == pytest.approx(35.0 / 25.4, abs=0.02)
    assert h.controller.stop_dia_valid is True

    h.controller.on_action_button_clicked()
    assert h.ui_fsm.state == "in_cycle.waiting_to_cut"
    # action_allowed/instruction_text are refreshed by _apply_policy() (via a
    # Clock.schedule_once'd bus callback on the state change, and via
    # _poll_apply_policy on every board tick) -- poll for the settled value
    # rather than trusting a single bare pump (see the longer note at the
    # X-clear gate below, which hit this race directly in development).
    settled = h.wait_until(lambda: h.controller.instruction_text == "Ready to cut",
                            timeout_s=3.0)
    assert settled, f"policy did not settle: instruction_text={h.controller.instruction_text!r}"
    assert h.controller.action_allowed is True

    # ── Start the cut: sync feed on, then the action button ─────────────────
    h.enable_sync()
    assert h.els_fsm.is_ready_to_cut(), "retract mode: Z should read as retracted pre-cut"
    h.cut()
    assert h.ui_fsm.state == "in_cycle.cutting"
    assert h.els_fsm.state == "cutting"

    # ── Pre-cut register assertions (independent thread-geometry math) ──────
    assert h.register("elsStop", "backlashSteps") == 403
    assert h.register("elsStop", "hysteresis") == 0          # tight (wizard/retract)
    assert h.register("elsStop", "stopDirection") == -1      # els_forward=True

    spindle_axis = h.board.get_spindle_axis()
    servo = h.board.servo
    z_axis = h.els.get_z_axis()
    saddle_input = z_axis._primary_input()

    spindle_pitch_mm = Fraction(abs(spindle_axis.syncRatioNum), abs(spindle_axis.syncRatioDen))
    servo_ratio = Fraction(abs(servo.ratioNum), abs(servo.ratioDen))
    z_scale_mm_per_count = Fraction(saddle_input.ratioNum, saddle_input.ratioDen)

    expected_thread_pitch_steps = float(spindle_pitch_mm / servo_ratio)
    expected_z_counts_per_pitch = float(spindle_pitch_mm / z_scale_mm_per_count)

    # 16 TPI (254/160 mm/rev) over the commissioned 127/32000 mm/step servo
    # and 1/400 mm/count Z scale reduces to exact integers: 400 steps/pitch,
    # 635 counts/pitch. Pin both the independent recomputation AND these
    # first-principles constants.
    assert expected_thread_pitch_steps == pytest.approx(400.0, abs=1e-6)
    assert expected_z_counts_per_pitch == pytest.approx(635.0, abs=1e-6)
    assert h.register("elsStop", "threadPitchSteps") == pytest.approx(
        expected_thread_pitch_steps, abs=1e-3)
    assert h.register("elsStop", "zCountsPerPitch") == pytest.approx(
        expected_z_counts_per_pitch, abs=1e-3)

    # ── Cut runs to the ELS stop ──────────────────────────────────────────
    # Expected duration: feed = pitch(1.5875mm) * RPM(30)/60 ~= 0.79mm/s;
    # span(in) * 25.4 / 0.79 ~= a few seconds, plus pre-cut backlash takeup
    # (fidelity gap #2) -- comfortably under the 20s timeout used throughout
    # the suite.
    reached = h.wait_until(lambda: h.ui_fsm.state == "in_cycle.waiting_to_retract",
                            timeout_s=20)
    assert reached, (
        f"did not reach waiting_to_retract: ui={h.ui_fsm.state} els={h.els_fsm.state}"
    )
    assert h.els_fsm.state == "stopped"
    z_at_stop = h.z_scaled_position()
    assert (z_at_stop - z_start) < 0, "cut fed the wrong way"
    assert abs(z_at_stop - z_start) > span * 0.4, "cut barely moved"

    # ── Threading X-clear gate: never exercised by any prior system test ────
    # X is parked at stop_dia (35mm) < start_dia (40mm), is_inner=False (OD
    # work) -- the gate must BLOCK retract until X is backed clear. Assert
    # blocked BEFORE moving X (the whole point of the sensitivity check).
    #
    # action_allowed/instruction_text are refreshed by _apply_policy(), which
    # runs both via a Clock.schedule_once'd bus callback (on the ui_state
    # transition) and via _poll_apply_policy (bound to board.update_tick, so
    # every pump()) -- but a single pump right after the waiting_to_retract
    # transition can race the *order* handlers bound to that same tick fire
    # in, so poll (wait_until) for the settled value instead of trusting one
    # bare pump -- self-healing if it's just a tick behind, and still a
    # correct failure (via the timeout) if the gate genuinely doesn't block.
    assert h.controller.is_threading is True
    assert h.controller.is_inner is False
    gate_blocked = h.wait_until(lambda: h.controller.action_allowed is False, timeout_s=3.0)
    assert gate_blocked, (
        f"gate should block with X still at stop_dia: x={h.els.get_x_axis().scaledPosition} "
        f"start_dia={h.controller.start_dia} action_allowed={h.controller.action_allowed}"
    )
    assert "clear" in h.controller.instruction_text.lower(), (
        f"instruction_text should mention moving X clear: {h.controller.instruction_text!r}"
    )
    pre_gate_state = h.ui_fsm.state
    h.controller.on_action_button_clicked()   # must be a no-op (gated)
    h.pump()
    assert h.ui_fsm.state == pre_gate_state, "gated action button must not advance the UI FSM"

    # Now move X clear (> start_dia for OD work) and re-poll the gate.
    reached, x_counts = _move_x_and_wait(h, 45.0)
    assert reached, f"X did not settle near 45mm: {x_counts} counts"
    gate_cleared = h.wait_until(lambda: h.controller.action_allowed is True, timeout_s=3.0)
    assert gate_cleared, (
        f"gate should clear once X is past start_dia: x={h.els.get_x_axis().scaledPosition} "
        f"start_dia={h.controller.start_dia} action_allowed={h.controller.action_allowed}"
    )
    assert h.controller.instruction_text == "Ready to retract"

    # ── Retract: host-driven (retract_enabled), first-ever exercise of the
    # 403-step backlash compensation path (see fidelity gap #2) ─────────────
    h.trigger_retract()
    assert h.ui_fsm.state == "in_cycle.retracting"
    done = h.wait_until(lambda: h.els_fsm.state == "stopped", timeout_s=20)
    assert done, (
        f"retract never completed: els={h.els_fsm.state} z={h.z_scaled_position()}"
    )
    assert h.ui_fsm.state == "in_cycle.waiting_to_cut"

    z_after = h.z_scaled_position()
    retract_error_in = z_after - retract_z_target
    retract_error_mm = retract_error_in * 25.4
    print(
        f"[test_els_real_config] retract error: {retract_error_in:+.5f} in "
        f"({retract_error_mm:+.4f} mm); target={retract_z_target:.5f} "
        f"actual={z_after:.5f} z_at_stop={z_at_stop:.5f} span={span:.5f}in "
        f"margin={margin:.5f}in"
    )

    # Direction check (the c69b02a regression class): retract must move AWAY
    # from the stop (opposite the cut), never toward it.
    assert (z_after - z_at_stop) > 0, (
        f"retract moved the WRONG way (toward the stop): "
        f"at_stop={z_at_stop} after={z_after}"
    )
    # No gross under/overshoot. Generous, justified tolerance: the 403-step
    # compensation implies ~1.60mm (~0.063in) of commanded motion versus the
    # emulator's ~0.6mm (~0.024in) simulated nut-play window (fidelity gap
    # #2) -- an expected excess of roughly 0.04in, plus rounding/servo-step
    # granularity. 0.15in (~3.8mm) comfortably bounds "a real bug" (a
    # runaway, or a no-op) while tolerating the documented mismatch; the
    # print above reports the exact observed value regardless of pass/fail.
    assert abs(retract_error_in) < 0.15, (
        f"retract error outside the justified tolerance: {retract_error_in:+.5f} in "
        f"(target={retract_z_target}, actual={z_after})"
    )
