"""Warm-firmware reconciliation: a new app session must not inherit the
previous session's armed ELS stop.

Regression for the 2026-08-01 on-machine finding: firmware retains
elsStop.{enable, active, stopPosition} across app restarts (the reflex-ui
restart that evening came up with the prior session's servoMode still set).
Nothing at startup cleared them — ``set_enable(False)`` only ran on the
disable/alarm FSM transitions, and the FSM's initial 'disabled' state fires no
on_enter — so a fresh session inherited the previous session's armed stop, and
``feed_without_armed_stop()`` (which trusts the firmware enable bit) would skip
the no-stop feed confirmation against a stale shoulder.

Every other system test runs against a COLD emulator (the emulator_process
fixture is per-test), so this file deliberately builds TWO app-stack sessions
against ONE emulator process: session 1 arms a stop and drops the link without
disengaging (the app-crash / plain-restart case — harness.disconnect() writes
nothing to firmware); session 2 is a fresh stack on the now-warm firmware.
"""

import pytest

pytestmark = pytest.mark.system


def _fresh_session(pty_path):
    from tests.system.harness import SystemHarness
    h = SystemHarness(pty_path)
    h.connect()
    h.configure(is_threading=False, retract_enabled=False,
                wizard_enabled=False, els_forward=True)
    return h


def _settle(h, ticks=30):
    """Pump enough for Clock.schedule_once chains (connected bind → reconcile)
    to run. wait_until can't express 'nothing more happens', so pump a fixed,
    generous number of ticks."""
    for _ in range(ticks):
        h.pump()


def test_new_session_clears_retained_els_stop(emulator_process, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _, pty_path = emulator_process

    # ── Session 1: engage with a committed stop → ELS armed in firmware ──
    h1 = _fresh_session(pty_path)
    z = h1.z_scaled_position()
    # els_forward=True → cut_dir=-1 → safe side is ABOVE the stop; put the stop
    # a margin+1mm below the carriage so the engage-time arm accepts.
    h1.set_stop_z(z - (h1.safety_margin() + 1.0))
    h1.engage()
    assert h1.els_fsm.state == "stopped"
    _settle(h1)
    assert int(h1.register('elsStop', 'enable')) == 1, (
        "precondition failed: session 1 never armed the stop — the rest of "
        "this test would pass vacuously against a disarmed firmware"
    )
    retained_stop = int(h1.register('elsStop', 'stopPosition'))

    # Drop the link WITHOUT disengaging. harness.disconnect() writes nothing
    # to firmware, so the emulator now holds a warm, armed elsStop block.
    h1.disconnect()

    # ── Session 2: fresh app stack on the warm firmware ──────────────────
    h2 = _fresh_session(pty_path)
    _settle(h2)

    # Startup reconciliation must have cleared the retained arm: the new
    # session's FSM is 'disabled', so firmware must be disarmed regardless of
    # what the previous session left.
    assert int(h2.register('elsStop', 'enable')) == 0, (
        f"retained arm survived into a new session "
        f"(stopPosition={retained_stop} from session 1 would be live)"
    )
    assert int(h2.register('elsStop', 'active')) == 0

    # And the feed guard must ask for confirmation again: engaged with no stop
    # committed IN THIS SESSION → disarmed → confirmation required. Before the
    # fix, the stale enable bit made feed_without_armed_stop() report False and
    # the confirmation was silently skipped.
    h2.engage()
    _settle(h2)
    assert h2.els_fsm.state == "stopped"
    assert int(h2.register('elsStop', 'enable')) == 0, (
        "engaging with no committed stop must not resurrect the stale arm"
    )
    assert h2.controller.feed_without_armed_stop() is True

    # Committing a stop NOW arms against THIS session's target, not the old
    # one. The carriage hasn't moved between sessions, so a same-offset stop
    # would coincide with session 1's value and prove nothing — deliberately
    # commit a DIFFERENT offset so old-arm vs new-arm is distinguishable.
    z2 = h2.z_scaled_position()
    h2.set_stop_z(z2 - (h2.safety_margin() + 2.0))
    _settle(h2)
    assert int(h2.register('elsStop', 'enable')) == 1
    assert int(h2.register('elsStop', 'stopPosition')) != retained_stop, (
        "session 2 armed against session 1's stopPosition"
    )
    h2.disconnect()


def test_reconnect_while_disengaged_clears_retained_arm(emulator_process, tmp_path, monkeypatch):
    """Same property through the RECONNECT path (link blip, not app restart):
    if the session is disengaged when the link comes back, a retained arm is
    cleared rather than trusted."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _, pty_path = emulator_process

    h = _fresh_session(pty_path)
    # Arm, then disengage — on_enter_disabled clears enable. Now simulate the
    # firmware being re-armed out from under a disengaged session (stands in
    # for any stale-state divergence during a link gap).
    z = h.z_scaled_position()
    h.set_stop_z(z - (h.safety_margin() + 1.0))
    h.engage()
    _settle(h)
    h.els_fsm.disable()
    _settle(h)
    assert int(h.register('elsStop', 'enable')) == 0
    h.hal.set_enable(True)   # divergence: firmware armed, session disengaged
    _settle(h)
    assert int(h.register('elsStop', 'enable')) == 1

    # Drive the reconcile through the same path a reconnect uses.
    h.controller._on_connected_changed(h.board, True)
    _settle(h)
    assert int(h.register('elsStop', 'enable')) == 0, (
        "reconnect reconcile did not clear a stale arm while disengaged"
    )
    h.disconnect()
