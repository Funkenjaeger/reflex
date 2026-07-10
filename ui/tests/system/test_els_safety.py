"""Emulator-backed regressions for the ELS safety guards (see todo.md 'Safety
audit results'). Each test drives the REAL controller + FSM stack against the
emulator and pins a guard that the audit showed was missing.
"""
import pytest

pytestmark = pytest.mark.system

_ENV = {"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}


def _commission(h, *, els_forward=True, retract_enabled=False, is_threading=False):
    h.configure(is_threading=is_threading, retract_enabled=retract_enabled,
                wizard_enabled=False, els_forward=els_forward)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)


# ── H3: a refused cut must not lock the UI in "Cutting…" ──────────────────────
@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_refused_cut_does_not_lock_ui(harness):
    """Audit H3 / TOCTOU: the UI waiting_to_cut→cutting transition is gated on a
    fresh domain-readiness check, so a stale click (action_allowed cached True
    while is_ready_to_cut has flipped False) can't park the UI in in_cycle.cutting
    with Stop disabled and no exit. The UI must stay in waiting_to_cut."""
    h = harness
    _commission(h, els_forward=True, retract_enabled=False)
    z0 = h.z_scaled_position()
    h.set_stop_z(z0 - 300.0)
    h.engage()
    h.enable_sync()
    assert h.ui_fsm.state == "in_cycle.waiting_to_cut"
    assert h.els_fsm.is_ready_to_cut(), "precondition: cut should be ready here"

    # TOCTOU: move stop_z onto the carriage so the domain FSM would refuse the cut.
    h.els_fsm.set_stop_z(z0)
    h.controller.stop_z = z0
    assert not h.els_fsm.is_ready_to_cut(), "domain should now refuse the cut"

    # Simulate the stale-cache click firing the UI action directly (what
    # on_action_button_clicked does once the cached action_allowed gate passes).
    # (queued=True makes the trigger's return value uninformative — assert on the
    # resulting state instead.)
    h.ui_fsm.action()
    h.pump()

    # The gated transition must refuse (no-op) and leave the UI recoverable.
    assert h.ui_fsm.state == "in_cycle.waiting_to_cut", (
        f"UI locked in a bad state after a refused cut: ui={h.ui_fsm.state}"
    )
    assert h.els_fsm.state == "stopped"
    # Sanity: the action button still offers "Cut" and Stop is enabled — not the
    # blank/locked "Cutting…" policy.
    assert h.controller.action_button_text == "Cut"
