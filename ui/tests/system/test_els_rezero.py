"""Encoder-anchored ELS targets vs. an operator DRO re-zero (safety audit H1).

The whole encoder-anchoring feature (reflex/fsms/ui_controller.py:
``_commit_stop_z`` / ``_commit_retract_z`` / ``_poll_reframe_targets``) exists
because operators re-zero the DRO constantly, and the OLD scaled-storage
design let a re-zero silently move the armed physical stop (audit finding
H1). Unit tests cover the anchoring math with identity-mock axes; this file
is the first to re-zero against the REAL stack (real dispatchers/FSMs over
the real emulator, via ``harness.rezero_z()`` — the operator "Zero" button,
``axis.zero_position()``).

tests/system/test_els_safety.py::test_stop_z_survives_dro_rezero already
covers the encoder/firmware-register invariance right after ``engage()``
(never cutting). This file goes further: it drives an ACTUAL cut after a
mid-cycle re-zero and checks where the carriage PHYSICALLY stops (raw scale
counts, bypassing the display layer entirely) — the regression H1 describes
is that the physical stop moves, so the proof has to be physical, not just
"the encoder int didn't change".

Both tests deliberately give the Z axis a NONZERO starting DRO reference
(``_INITIAL_DRO_REFERENCE_MM``, via the production ``set_current_position``
write path — the same one ``zero_position()`` itself calls with target 0)
before committing any target. The reference machine's carriage starts at the
literal raw-count zero, so re-zeroing right there would be a numeric no-op
that could pass every assertion below vacuously; establishing a real nonzero
reference first (exactly what an operator's very first "Zero" press of a
session already does) makes the later re-zero a genuine offset change, and
we still precondition-assert that it took effect.
"""

from fractions import Fraction

import pytest

pytestmark = pytest.mark.system

# EMU_RPM=30: real forward spindle. EMU_NO_AUTO_RETRACT: the carriage stays put
# after the ELS stop fires (no simulated hand-retract), so the resting-position
# read in test 1 isn't racing the emulator's own retract. Matches every other
# stop-only system test (test_els_turning_stop_only.py, test_els_safety.py).
_ENV = {"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}

# Reference geometry commissioned by harness.commission_geometry(): 400 Z-scale
# counts/mm. Used only to translate the reversing matrix's proven 0.25 mm
# display-unit stop tolerance into the raw-counts domain for the physical-stop
# assertion below (100 counts) — not a re-derivation, just a unit conversion of
# an already-empirically-validated bound (test_els_reversing_matrix.py /
# test_els_turning_stop_only.py use the same feed/hysteresis/geometry).
_COUNTS_PER_MM = 400
_STOP_TOLERANCE_COUNTS = int(0.25 * _COUNTS_PER_MM)  # 100 counts

# Arbitrary nonzero starting DRO value (mm) — see module docstring. Well clear
# of the reference machine's +/-5 mm physical travel window: this is a SOFTWARE
# display offset (AxisDispatcher.offsets), so it never touches the physical
# raw position the emulator's travel limit is checked against.
_INITIAL_DRO_REFERENCE_MM = 15.0


def _settle(h, ticks=10):
    """Pump repeatedly so the reframe poll (board.update_tick-bound
    ElsUiController._poll_reframe_targets) and any Clock.schedule_once chains
    have had a chance to run. wait_until can't express "nothing more
    happens", so pump a fixed, generous number of ticks — mirrors
    test_warm_firmware.py's local _settle helper (this file can't import it;
    conftest.py/harness.py are read-only and there's no shared test-support
    module)."""
    for _ in range(ticks):
        h.pump()


def _commission(h, *, retract_enabled):
    """Shared setup: real-machine commissioning + reference geometry, mirrors
    test_els_turning_stop_only.py / test_els_turning_stop_retract.py /
    test_els_safety.py's _commission. Needed even for test 2 (which never
    cuts) because _safety_margin_display() — and therefore retract_z_valid —
    depends on the commissioned servo leadscrew pitch."""
    h.configure(is_threading=False, retract_enabled=retract_enabled,
                wizard_enabled=False, els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()
    h.set_feed(Fraction(254, 160))   # 16 TPI = 1.5875 mm/rev -> ~0.79 mm/s at EMU_RPM=30


def _establish_nonzero_dro_reference(h):
    """Relabel the CURRENT physical Z position as _INITIAL_DRO_REFERENCE_MM,
    through the same production write path zero_position() uses
    (AxisDispatcher.set_current_position) — just with a nonzero target. Purely
    a software display-offset change; the physical raw position is untouched.
    Returns the resulting z_scaled_position() for the caller to precondition-
    assert against."""
    z_axis = h.els.get_z_axis()
    z_axis.set_current_position(_INITIAL_DRO_REFERENCE_MM)
    _settle(h)
    return h.z_scaled_position()


# ─────────────────────────────────────────────────────────────────────────
# Test 1: the H1 regression at system level — a re-zero while engaged/armed
# (before the cut even starts) must not move where the carriage physically
# stops.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_rezero_does_not_move_the_armed_stop(harness):
    """Commission stop-only mode, commit + arm a stop on the cutting side,
    re-zero the DRO while engaged-and-idle (armed, not mid-motion — the
    everyday case: operator touches off a new part before cutting it), then
    run the cut and confirm the carriage halts at the PHYSICAL location the
    operator originally pointed at.

    Sensitivity: under the pre-anchoring design (stop_z stored as a scaled
    display value, re-derived to an encoder count only at cut time), this
    re-zero would have shifted the effective stop by the re-zero delta —
    here, ~15 mm of display shift -> ~6000 counts at the commissioned 400
    counts/mm, versus the ~100-count tolerance below. The final physical-stop
    assertion is what would have caught that regression.
    """
    h = harness
    _commission(h, retract_enabled=False)

    z_start = _establish_nonzero_dro_reference(h)
    assert z_start == pytest.approx(_INITIAL_DRO_REFERENCE_MM, abs=0.01), (
        f"failed to establish a nonzero starting DRO reference: z_start={z_start}"
    )

    # Stop target on the cutting side (els_forward=True => cut moves -Z).
    # Same span formula as test_els_turning_stop_only.py: clears the safety
    # margin, stays well inside the 5 mm of physical -Z travel (the offset
    # above is a display relabeling only, so this is exactly the same
    # physical span those tests already exercise successfully).
    margin = h.safety_margin()
    span = margin + 1.0
    stop_z = z_start - span

    h.set_stop_z(stop_z)
    assert h.els_fsm.is_ready_to_cut(), (
        f"not ready to cut: z_start={z_start} stop_z={stop_z} margin={margin}"
    )
    h.engage()
    assert h.els_fsm.state == "stopped"

    # ── Preconditions (engage-time arm must have actually happened, or
    #    everything below passes vacuously against a disarmed stop) ──────
    assert int(h.register('elsStop', 'enable')) == 1, (
        "precondition failed: engage() never armed the stop — the rest of "
        "this test would pass vacuously"
    )
    S = int(h.register('elsStop', 'stopPosition'))
    E = h.controller.stop_z_encoder
    D0 = h.controller.stop_z
    assert E is not None, "precondition failed: stop_z_encoder never committed"
    assert S == E, (
        f"precondition failed: firmware stopPosition {S} != committed "
        f"encoder {E} even before any re-zero"
    )

    # ── The re-zero (operator 'Zero' button) ──────────────────────────────
    h.rezero_z()
    _settle(h)

    # Precondition: the re-zero must have actually moved the DRO to 0, or
    # every assertion below (encoder unchanged, display changed) is vacuous.
    z_after_rezero = h.z_scaled_position()
    assert z_after_rezero == pytest.approx(0.0, abs=0.01), (
        f"rezero_z() did not zero the DRO at the current position — "
        f"precondition for this whole test failed: z_after_rezero={z_after_rezero}"
    )

    D1 = h.controller.stop_z
    assert D1 != D0, (
        f"controller.stop_z did not change after rezero_z() (D0={D0} D1={D1}) "
        f"— either the reframe poll never ran or zero_position() no-op'd; "
        f"this test cannot validate anything if the display never re-rendered"
    )
    # The new display mirror must read relative to the new zero: new(x) =
    # old(x) - z_start for every x on this axis (a uniform additive shift —
    # see AxisDispatcher.scaled_from_encoder / set_current_position).
    expected_D1 = D0 - z_start
    assert D1 == pytest.approx(expected_D1, abs=0.01), (
        f"stop_z display did not re-reference by the re-zero delta: "
        f"D0={D0} z_start={z_start} expected~={expected_D1} got={D1}"
    )

    # The PHYSICAL target must be untouched by the display-frame change.
    assert h.controller.stop_z_encoder == E, (
        f"rezero moved the FROZEN stop encoder: before={E} "
        f"after={h.controller.stop_z_encoder}"
    )
    assert int(h.register('elsStop', 'stopPosition')) == S, (
        f"rezero pushed a re-referenced stopPosition to firmware: before={S} "
        f"after={h.register('elsStop', 'stopPosition')}"
    )
    assert h.controller.stop_z_valid is True, (
        "stop_z_valid dropped after a re-zero — a re-zero must only "
        "re-render, never invalidate, a committed target"
    )

    # ── Run the cut: the carriage must stop at the PHYSICAL location ─────
    h.enable_sync()
    h.cut()
    assert h.els_fsm.state == "cutting", f"cut did not start: {h.els_fsm.state}"

    reached = h.wait_until(lambda: h.els_fsm.state == "stopped", timeout_s=20)
    assert reached, (
        f"cut never stopped; state={h.els_fsm.state} "
        f"z_now={h.z_scaled_position()} stop_z_display={h.controller.stop_z}"
    )

    resting_counts = h.carriage_position_counts()
    assert resting_counts == pytest.approx(E, abs=_STOP_TOLERANCE_COUNTS), (
        f"carriage stopped at the WRONG physical location after a mid-cycle "
        f"re-zero: resting_counts={resting_counts} armed_encoder(E)={E} "
        f"stopPosition(S)={S} tolerance=+/-{_STOP_TOLERANCE_COUNTS} counts. "
        f"(A pre-anchoring / scaled-storage bug would land ~{int(z_start * _COUNTS_PER_MM)} "
        f"counts off, at the re-zeroed-display's naive target instead.)"
    )


# ─────────────────────────────────────────────────────────────────────────
# Test 2: mid-cycle re-zero in stop+retract mode — both targets re-render,
# both stay physically anchored, retract_z_valid survives (commit 1cd8acc),
# and the reframe-notify plumbing fires end-to-end.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_rezero_mid_cycle_keeps_retract_target_and_warns(harness):
    """Stop+retract mode, both targets committed, stop_z_reframe_notify set to
    'warn'. A re-zero must re-render BOTH display mirrors from their frozen
    encoders, leave both encoders untouched, and — the commit 1cd8acc
    regression — must not durably flip retract_z_valid False just because
    _poll_reframe_targets briefly evaluates the two mirrors mid-update: the
    physical span between stop_z and retract_z is frame-invariant, so
    validity must be unaffected. Also exercises the reframe-notify plumbing
    end-to-end (targets_reframed_warn + reframe_message set, then cleared by
    clear_reframe_notice()) — the only user-visible surface of this whole
    anchoring mechanism.
    """
    h = harness
    _commission(h, retract_enabled=True)

    z_start = _establish_nonzero_dro_reference(h)
    assert z_start == pytest.approx(_INITIAL_DRO_REFERENCE_MM, abs=0.01), (
        f"failed to establish a nonzero starting DRO reference: z_start={z_start}"
    )

    margin = h.safety_margin()
    span = margin + 1.0
    stop_z = z_start - span
    retract_z = z_start   # retract above stop, per convention (see
                           # test_els_turning_stop_retract.py: "retract back
                           # to the cut-start position (+Z)")

    h.set_stop_z(stop_z)
    h.set_retract_z(retract_z)

    h.els.stop_z_reframe_notify = "warn"
    h.pump()

    # ── Preconditions ──────────────────────────────────────────────────
    E_stop = h.controller.stop_z_encoder
    E_retract = h.controller.retract_z_encoder
    assert E_stop is not None and E_retract is not None, (
        "precondition failed: stop_z/retract_z never committed"
    )
    D0_stop = h.controller.stop_z
    D0_retract = h.controller.retract_z
    assert h.controller.retract_z_valid is True, (
        "precondition failed: retract_z already invalid before any re-zero — "
        "the 'STILL True' assertion below would pass vacuously"
    )
    assert h.controller.targets_reframed_warn is False, (
        "precondition failed: warn flag already set before the re-zero"
    )
    assert h.els.stop_z_reframe_notify == "warn", (
        "precondition failed: notify-mode write didn't stick"
    )

    # ── The re-zero (operator 'Zero' button), mid-cycle (engaged-idle,
    #    both targets already committed) ──────────────────────────────────
    h.rezero_z()
    _settle(h)

    z_after_rezero = h.z_scaled_position()
    assert z_after_rezero == pytest.approx(0.0, abs=0.01), (
        f"rezero_z() did not zero the DRO — precondition for this whole "
        f"test failed: z_after_rezero={z_after_rezero}"
    )

    D1_stop = h.controller.stop_z
    D1_retract = h.controller.retract_z
    assert D1_stop != D0_stop, (
        f"stop_z display did not re-reference after rezero (D0={D0_stop} D1={D1_stop})"
    )
    assert D1_retract != D0_retract, (
        f"retract_z display did not re-reference after rezero "
        f"(D0={D0_retract} D1={D1_retract})"
    )
    expected_delta = -z_start
    assert D1_stop == pytest.approx(D0_stop + expected_delta, abs=0.01), (
        f"stop_z re-referenced by the wrong amount: D0={D0_stop} "
        f"z_start={z_start} got={D1_stop}"
    )
    assert D1_retract == pytest.approx(D0_retract + expected_delta, abs=0.01), (
        f"retract_z re-referenced by the wrong amount: D0={D0_retract} "
        f"z_start={z_start} got={D1_retract}"
    )

    # Physical targets untouched.
    assert h.controller.stop_z_encoder == E_stop, (
        f"rezero moved the frozen stop encoder: before={E_stop} "
        f"after={h.controller.stop_z_encoder}"
    )
    assert h.controller.retract_z_encoder == E_retract, (
        f"rezero moved the frozen retract encoder: before={E_retract} "
        f"after={h.controller.retract_z_encoder}"
    )

    # commit 1cd8acc regression: the physical span between stop_z and
    # retract_z is frame-invariant, so validity must survive a same-axis
    # re-zero even though both mirrors just changed.
    assert h.controller.retract_z_valid is True, (
        "retract_z_valid flipped False after a same-axis re-zero — the "
        "mixed-frame validation bug (fixed in commit 1cd8acc) is back"
    )

    # Reframe-notify plumbing, end-to-end.
    assert h.controller.targets_reframed_warn is True, (
        "stop_z_reframe_notify='warn' did not raise the warn flag on a Z "
        "re-reference"
    )
    assert h.controller.reframe_message, (
        "warn flag set with no human-readable reframe_message"
    )

    h.controller.clear_reframe_notice()
    assert h.controller.targets_reframed_warn is False, (
        "clear_reframe_notice() did not drop the warn flag"
    )
    assert h.controller.reframe_confirm_pending is False
    assert h.controller.reframe_message == ""
