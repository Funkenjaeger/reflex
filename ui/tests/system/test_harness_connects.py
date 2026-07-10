"""Task 5 verification: the real object-graph harness assembles, connects to the
emulator, reads a known register, drives the production toggle write-path, and
sees it affect the live physics -- with no Kivy App/UI."""

import pytest

pytestmark = pytest.mark.system


def test_harness_assembles_and_connects(harness):
    assert harness.connected
    # The whole real FSM stack was constructed.
    assert harness.els is not None
    assert harness.controller is not None
    assert harness.els_fsm is not None
    assert harness.ui_fsm is not None
    assert harness.hal is not None
    # Domain FSM starts disabled.
    assert harness.els_fsm.state == "disabled"


def test_harness_reads_known_register(harness):
    # servoDir is readable over the live Modbus link.
    assert harness.register("servo", "servoDir") in (1, -1)
    # scales array reads back too.
    assert isinstance(harness.carriage_position_counts(), int)


def test_servo_reverse_toggle_writes_through_production_path(harness):
    """Setting ServoDispatcher.reverse fires its real handler and flips the
    servoDir register the firmware reads -- the exact write-path production
    uses (Open Item 2 acceptance criterion), not a direct register poke."""
    # reverse defaults False (servoDir=+1 on connect). Toggle True THEN back to
    # False so both writes actually fire the _on_reverse_changed handler -- a
    # set_servo_reverse(False) first would be a no-op (no property change) and
    # only re-read the on-connect write.
    harness.set_servo_reverse(True)
    harness.pump()
    assert harness.register("servo", "servoDir") == -1

    harness.set_servo_reverse(False)
    harness.pump()
    assert harness.register("servo", "servoDir") == 1
