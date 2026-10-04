"""The real stack on the emulator with the Modbus scan on the owner's thread.

A DELIBERATELY SMALL SUBSET. The rest of the system suite runs on the
synchronous seam, where one pump() is exactly one tick and a cut can be
stepped register by register; that determinism is worth more than running
every case twice. What cannot be checked there is what the thread itself
changes, so this file covers the three places it could plausibly break:

  * the link comes up at all, and keeps coming up, with nobody calling
    Board.update() -- construction pushes its settings through the queue to
    the owner thread while that thread is already scanning;
  * a cut starts and a disengage lands, with the writes crossing the queue
    from the Kivy thread while scans interleave with them;
  * the firmware-flasher handover still genuinely releases the port, which on
    a thread means asking the loop and waiting for it rather than closing the
    port under an exchange.

Everything here goes through wait_until, which pumps the Kivy clock: the
owner thread is what produces snapshots, and Clock.tick() is what applies
them. Nothing asserts on a tick COUNT, because with a real thread the number
of scans between two moments is a race by construction -- the assertions are
on state the machine actually reaches.
"""
import threading
import time

import pytest

# timing: a real owner thread, with waits on snapshots actually arriving.
# Stable so far, marked on principle: contention is the one thing that
# would make these lie, and they are the cases guarding the threading.
pytestmark = [pytest.mark.system, pytest.mark.timing]


def test_the_link_comes_up_with_the_scan_on_its_own_thread(threaded_harness):
    h = threaded_harness
    assert h.connected
    assert h.board._io_threaded
    owner = h.board._port()
    assert owner.running
    assert owner._thread.name == "modbus-owner"
    # The whole real stack still assembled around it.
    assert h.els_fsm is not None and h.ui_fsm is not None
    assert h.els_fsm.state == "disabled"


def test_snapshots_keep_arriving_without_anyone_calling_update(threaded_harness):
    """The property the Clock event used to provide. If this fails the UI
    freezes on its last values while believing the link is up -- which is the
    failure the watchdog exists to name."""
    h = threaded_harness
    applied = h.board.snapshots_applied
    assert h.wait_until(lambda: h.board.snapshots_applied > applied + 5,
                        timeout_s=5), "the owner thread stopped producing"
    assert h.board.t_acq is not None, "no acquisition time was recorded"


def test_the_tick_still_fills_both_blocks(threaded_harness):
    """The merged read's two halves must survive the hand-off to the main
    thread: a snapshot applied from another thread is still one tick's worth
    of consistent registers, not a mixture."""
    h = threaded_harness
    assert h.wait_until(lambda: bool(h.board.fast_data_values)
                        and bool(h.board.els_stop_values), timeout_s=5)
    assert "servoMode" in h.board.fast_data_values
    assert "enable" in h.board.els_stop_values


def test_a_live_read_from_the_kivy_thread_crosses_the_queue(threaded_harness):
    """BaseDevice item access is the blocking proxy now: it submits and waits
    for the owner thread. The value must still be the real register."""
    h = threaded_harness
    assert h.register("servo", "servoDir") in (1, -1)
    assert isinstance(h.carriage_position_counts(), int)


def test_a_write_from_the_kivy_thread_reaches_the_firmware(threaded_harness):
    """The production write path with scans interleaving: the toggle's handler
    runs on the Kivy thread, its write is queued, and the owner thread puts it
    on the wire between two tick reads."""
    h = threaded_harness
    h.set_servo_reverse(True)
    assert h.wait_until(lambda: h.register("servo", "servoDir") == -1, timeout_s=5)
    h.set_servo_reverse(False)
    assert h.wait_until(lambda: h.register("servo", "servoDir") == 1, timeout_s=5)


def test_a_cut_starts_with_the_scan_on_the_thread(threaded_harness):
    """Cut start is the write burst that matters: about 14 writes, previously
    all synchronous on the Kivy thread. They must still arrive, in order, and
    leave the domain FSM cutting."""
    h = threaded_harness
    h.set_imperial()
    h.configure(is_threading=False, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_geometry()
    h.commission_servo()
    h.rezero_z()

    h.set_stop_z(h.z_scaled_position() - 5.0)
    h.engage()
    h.enable_sync()
    assert h.wait_until(lambda: h.els_fsm.state in ("stopped", "cutting"),
                        timeout_s=5), f"never armed (state {h.els_fsm.state})"

    h.cut()
    assert h.wait_until(lambda: h.els_fsm.state == "cutting", timeout_s=5), \
        f"the cut never started (state {h.els_fsm.state})"
    # active is the firmware's hold; the cut releases it.
    assert h.wait_until(lambda: int(h.board.els_stop_values.get("active", 1)) == 0,
                        timeout_s=5), "the cut never released the hold"


def test_a_disengage_lands_in_order_with_the_scan_on_the_thread(threaded_harness):
    """The disengage sequence (sync off, enable off, feed off) is one ordered
    group. A queue that split or reordered it would leave the firmware fed
    with the sync off, which is the state this must never produce."""
    h = threaded_harness
    h.set_imperial()
    h.configure(is_threading=False, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_geometry()
    h.commission_servo()
    h.rezero_z()
    h.engage()
    h.enable_sync()
    assert h.wait_until(lambda: h.els_fsm.state != "disabled", timeout_s=5)

    h.els_fsm.disable()
    assert h.wait_until(lambda: h.els_fsm.state == "disabled", timeout_s=5), \
        "the disengage never completed"
    assert h.wait_until(
        lambda: int(h.board.els_stop_values.get("enable", 1)) == 0,
        timeout_s=5), "enable was left set after a disengage"


def test_the_flasher_handover_really_releases_the_port(threaded_harness):
    """pause_polling must come back with the port CLOSED, not merely with the
    polling stopped: the flasher opens the same device, and a second handle on
    a port this process still holds interleaves frames with a poll that has
    not actually stopped. On a thread that means the loop acknowledged.
    """
    h = threaded_harness
    assert h.wait_until(lambda: h.board.snapshots_applied > 2, timeout_s=5)
    owner = h.board._port()
    generation_before = owner.generation

    released = h.board.pause_polling()
    assert released is True, "the owner thread did not confirm the port was closed"
    assert h.board.link_paused
    assert h.board.connection_manager.device is None, "the port is still open"
    assert owner.generation == generation_before + 1
    assert h.board.els_stop_values == {} and h.board.fast_data_values == {}

    # Nothing arrives while the port is away.
    settled = h.board.snapshots_applied
    for _ in range(10):
        h.pump()
        time.sleep(0.01)
    assert h.board.snapshots_applied == settled, "a snapshot arrived while paused"

    h.board.resume_polling()
    assert not h.board.link_paused
    assert h.wait_until(lambda: h.board.snapshots_applied > settled, timeout_s=5), \
        "the link did not come back after the handover"
    assert h.wait_until(lambda: bool(h.board.els_stop_values), timeout_s=5)


def test_no_kivy_property_is_written_off_the_main_thread(threaded_harness):
    """The debug guard reports by name the moment it happens, so an empty
    report after real traffic is the evidence that the owner thread kept to
    publishing snapshots and never touched an EventDispatcher."""
    h = threaded_harness
    h.set_servo_reverse(True)
    assert h.wait_until(lambda: h.board.snapshots_applied > 10, timeout_s=5)
    h.set_servo_reverse(False)
    assert h.wait_until(lambda: h.board.snapshots_applied > 20, timeout_s=5)
    assert h.board._off_thread_reported == frozenset(), (
        "Board properties were written off the main thread: "
        f"{sorted(h.board._off_thread_reported)}")


def test_stopping_the_thread_leaves_no_thread_behind(threaded_harness):
    """disconnect() stops it too, but a test that asserts it directly is what
    keeps a leaked owner thread from quietly slowing every later test."""
    h = threaded_harness
    owner = h.board._port()
    thread = owner._thread
    h.board.stop_io_thread()
    assert not owner.running
    assert not thread.is_alive()
    assert not any(t.name == "modbus-owner" and t.is_alive()
                   for t in threading.enumerate())
    # And the synchronous seam is back, so update() works again.
    applied = h.board.snapshots_applied
    h.board.update()
    assert h.board.snapshots_applied > applied
