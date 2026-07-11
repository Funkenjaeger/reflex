"""Emulator-backed regressions for the ELS safety guards (see todo.md 'Safety
audit results'). Each test drives the REAL controller + FSM stack against the
emulator and pins a guard that the audit showed was missing.
"""
import time

import pytest

pytestmark = pytest.mark.system

_ENV = {"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}


def test_stop_z_survives_dro_rezero(harness):
    """The stop is anchored to the PHYSICAL leadscrew encoder, so re-zeroing the
    Z DRO (an offset change) must NOT move where the tool actually stops — the
    displayed value re-references but the firmware target (encoder) is unchanged.
    This is the whole point of encoder-backing (the old scaled storage would have
    silently moved the physical stop)."""
    h = harness
    h.configure(is_threading=False, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    z0 = h.z_scaled_position()
    h.set_stop_z(z0 - 100.0)

    enc_before = h.controller.stop_z_encoder
    scaled_before = h.controller.stop_z
    assert enc_before is not None

    # Re-zero the Z DRO at the current carriage position (changes the offset).
    z_axis = h.els.get_z_axis()
    z_axis.set_current_position(0.0)
    for _ in range(3):
        h.pump()

    # Physical stop (encoder) unchanged; the displayed value re-referenced.
    assert h.controller.stop_z_encoder == enc_before, (
        "re-zero moved the physical stop encoder!"
    )
    assert h.controller.stop_z != scaled_before, (
        "displayed stop_z should have re-referenced to the new frame"
    )
    # Arming writes the FROZEN encoder to firmware, not the re-referenced scaled.
    h.engage()
    assert h.board.device['elsStop']['stopPosition'] == enc_before, (
        "firmware stop was re-referenced instead of held at the physical target"
    )


def _max_travel(h, watch_s):
    """Pump for watch_s, return the max |z - z_at_call| excursion."""
    z0 = h.z_scaled_position()
    mx = 0.0
    deadline = time.monotonic() + watch_s
    while time.monotonic() < deadline:
        h.pump()
        mx = max(mx, abs(h.z_scaled_position() - z0))
        time.sleep(0.01)
    return mx


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
    h.controller.commit_standalone_stop_z(z0)
    h.pump()
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


# ── H2b: enabling feed with no armed ELS stop must require confirmation ───────
@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_feed_enable_requires_confirm_without_armed_stop(harness):
    """Audit H2b: enabling the sync feed with no ELS stop armed would feed the
    carriage freely toward the chuck. request_feed_enable must refuse (return
    False, feed NOT enabled) until confirmed; then it enables."""
    h = harness
    h.configure(is_threading=False, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)

    # Not engaged → no armed stop.
    assert h.controller.feed_without_armed_stop() is True
    assert h.board.servo.servoMode == 0

    # First request refuses and does NOT start the feed.
    enabled = h.controller.request_feed_enable(confirmed=False)
    h.pump()
    assert enabled is False
    assert h.board.servo.servoMode == 0, "feed must not start without confirmation"
    # And nothing moved.
    assert _max_travel(h, 1.5) < 50.0, "carriage moved despite feed being refused"

    # Confirming enables the feed.
    enabled = h.controller.request_feed_enable(confirmed=True)
    h.pump()
    assert enabled is True
    assert h.board.servo.servoMode == 1


@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_feed_enable_allowed_with_armed_stop(harness):
    """With ELS engaged and a stop armed, enabling the feed needs no confirm."""
    h = harness
    h.configure(is_threading=False, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    z0 = h.z_scaled_position()
    h.set_stop_z(z0 - 300.0)
    h.engage()                      # arms the stop (Z on safe side)
    assert h.controller.feed_without_armed_stop() is False
    enabled = h.controller.request_feed_enable(confirmed=False)
    h.pump()
    assert enabled is True
    assert h.board.servo.servoMode == 1


# ── H6: disengaging ELS while a feed is running must stop the feed ────────────
@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_disengage_stops_feed(harness):
    """Audit H6: engage + feed, then disengage ELS. The feed must stop
    (servoMode 0) and the carriage must not keep marching toward the chuck."""
    h = harness
    h.configure(is_threading=False, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    z0 = h.z_scaled_position()
    h.set_stop_z(z0 - 5000.0)       # far stop so the feed is genuinely running
    h.engage()
    h.enable_sync()
    # Let the feed run briefly, then disengage.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        h.pump(); time.sleep(0.01)
    assert h.board.servo.servoMode == 1, "precondition: feed should be running"

    h.controller.toggle_engage()    # disengage
    h.pump()
    assert h.els_fsm.state == "disabled"
    assert h.board.servo.servoMode == 0, "disengage must stop the feed"
    # After disengage the carriage must settle quickly, not keep marching.
    settle = _max_travel(h, 3.0)
    assert settle < 500.0, (
        f"carriage kept feeding after disengage: {settle:.0f} counts"
    )


# ── #4: a cut whose stop write isn't ACK'd must abort, not release ────────────
@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_cut_aborts_when_stop_write_not_acked(harness):
    """Audit #4: the elsStop writes are ACK'd over Modbus. Simulate the
    stopPosition write NOT being acknowledged (link drops) but recovering on the
    next write — the accumulated ACK check must still catch it, refuse to release
    the cut, stop the feed, and fault (so the cut can't run against an unverified
    / previous stop)."""
    h = harness
    h.configure(is_threading=False, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    z0 = h.z_scaled_position()
    h.set_stop_z(z0 - 300.0)
    h.engage()

    # Inject: the stopPosition write "fails to ACK" (connected drops); the very
    # next write recovers the link. A naive end-of-sequence check would miss this.
    cm = h.board.connection_manager
    orig_set_stop_position = h.hal.set_stop_position

    def failing_set_stop_position(counts):
        orig_set_stop_position(counts)
        cm.connected = False        # write went out but was not acknowledged

    h.hal.set_stop_position = failing_set_stop_position

    h.enable_sync()
    h.cut()
    h.pump()

    # The cut must have aborted: domain + UI faulted to alarm, the feed is off,
    # and — the safety invariant — no cut motion happened.
    assert h.els_fsm.state == "alarm", (
        f"cut did not abort on an unacknowledged stop write: els={h.els_fsm.state}"
    )
    assert h.ui_fsm.state == "alarm", f"UI didn't mirror the fault: ui={h.ui_fsm.state}"
    assert h.board.servo.servoMode == 0, "feed not stopped on abort"
    # And the carriage did not run a cut (no release → no feed).
    assert _max_travel(h, 2.0) < 300.0, "carriage fed despite the aborted cut"

    # Recovery: the operator can clear the alarm with Disengage — BOTH FSMs must
    # leave alarm (domain → disabled AND the UI FSM out of its alarm state), or
    # ELS is soft-locked until app restart.
    h.hal.set_stop_position = orig_set_stop_position  # link "recovers"
    h.controller.toggle_engage()
    h.pump()
    assert h.els_fsm.state == "disabled", (
        f"could not clear the alarm via disengage: els={h.els_fsm.state}"
    )
    assert h.ui_fsm.state != "alarm", (
        f"UI FSM stuck in alarm after disengage (ELS soft-locked): ui={h.ui_fsm.state}"
    )
    # And ELS is usable again: re-engage + set a stop → ready to cut.
    h.controller.toggle_engage()
    h.set_stop_z(h.z_scaled_position() - 200.0)
    h.pump()
    assert h.els_fsm.state == "stopped", f"re-engage after alarm failed: {h.els_fsm.state}"


@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_cut_aborts_when_enable_write_not_acked(harness):
    """Review finding 2: the enable arming write is verified too. On the
    engage-past-stop path enable is the first/only thing that arms the stop, so a
    failed enable write must also abort the cut (not feed with nothing armed)."""
    h = harness
    h.configure(is_threading=False, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    z0 = h.z_scaled_position()
    h.set_stop_z(z0 - 300.0)
    h.engage()

    cm = h.board.connection_manager
    orig_set_enable = h.hal.set_enable

    def failing_set_enable(enabled):
        orig_set_enable(enabled)
        cm.connected = False        # enable write went out but was not acknowledged

    h.hal.set_enable = failing_set_enable

    h.enable_sync()
    h.cut()
    h.pump()

    assert h.els_fsm.state == "alarm", (
        f"cut did not abort on an unacknowledged enable write: els={h.els_fsm.state}"
    )
    assert h.board.servo.servoMode == 0, "feed not stopped on abort"
    assert _max_travel(h, 2.0) < 300.0, "carriage fed despite the aborted cut"
