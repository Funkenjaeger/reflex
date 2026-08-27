"""What ServoDispatcher.on_connected re-pushes to a firmware that just came back.

Firmware RAM does not survive a reset, so every servo register the UI owns has
to be rewritten at connect or the machine runs on zeros. on_connected has always
rewritten maxSpeed, acceleration and servoDir; jogSpeed was missing from that
list until 2026-08-27.

THE EXPOSURE IS NARROW and the test says so rather than overclaiming. jogbar.py
zeroes jogSpeed on every jog release, so an idle machine re-sends it on the next
press and heals itself. The case that does NOT heal is a firmware reset landing
while a jog button is HELD: there is no False->True edge on enable_jog left to
re-trigger the write, so firmware keeps its freshly-zeroed jogSpeed and the jog
appears dead until the operator releases and presses again.

The test is written against the SET of registers rather than jogSpeed alone, so
that dropping any of the four -- not just re-dropping this one -- fails here.
"""
from unittest.mock import MagicMock

import pytest

from reflex.dispatchers.servo import ServoDispatcher
from tests.dispatchers.conftest import MockBoard, MockFormats


class RecordingDevice(dict):
    """A real dict-of-dicts, so the assertions read the values that actually
    landed. A MagicMock would record the calls but let a wrong value through."""

    def __init__(self):
        super().__init__()
        self['servo'] = {}


@pytest.fixture
def servo(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / ".config" / "reflex"))
    board = MockBoard()
    board.device = RecordingDevice()
    board.connected = False
    board.fast_data_values = {
        'servoCurrent': 0, 'servoMode': 0, 'servoSpeed': 0.0, 'stepsToGo': 0,
    }
    return ServoDispatcher(board=board, formats=MockFormats(), id_override="rp")


def _reconnect(servo):
    """Drive the connect edge the way board.connected does in the app."""
    servo.board.device['servo'].clear()
    servo.board.connected = True
    servo.on_connected(servo.board, True)


def test_reconnect_repushes_jogspeed(servo):
    """The regression this file exists for."""
    servo.jogSpeed = 1234.0
    _reconnect(servo)
    assert servo.board.device['servo'].get('jogSpeed') == 1234.0, (
        "jogSpeed was not re-pushed at connect; a firmware reset during a HELD "
        "jog leaves the machine with jogSpeed 0 and no edge left to fix it"
    )


def test_reconnect_repushes_every_owned_servo_register(servo):
    """All four together, so dropping any one of them fails here."""
    servo.maxSpeed = 4321.0
    servo.acceleration = 555.0
    servo.jogSpeed = -900.0          # negative: a jog in the other direction
    servo.reverse = True             # servoDir is derived, not mirrored
    _reconnect(servo)

    pushed = servo.board.device['servo']
    assert pushed.get('maxSpeed') == 4321.0
    assert pushed.get('acceleration') == 555.0
    assert pushed.get('jogSpeed') == -900.0, (
        "a negative jogSpeed must survive the re-push intact -- the sign is the "
        "jog's direction, not a magnitude"
    )
    assert pushed.get('servoDir') == -1


def test_repush_does_not_invent_a_value(servo):
    """An idle machine re-pushes the zero it actually holds. Pinned because
    'push the last nonzero speed' would be a plausible-looking change that
    silently commands motion at connect."""
    servo.jogSpeed = 0
    _reconnect(servo)
    assert servo.board.device['servo'].get('jogSpeed') == 0
