"""Log-only watchdog: firmware reports the servo running after the UI said stop.

The invariant is narrow and strong: nothing an operator can legitimately do makes
the firmware disagree with the last servoMode the UI wrote. A persistent
disagreement is a defect somewhere -- a dropped write, servoEnableTask
re-asserting on its own, a path that cleared elsStop.enable while sync was live.
The watchdog does not care which; it reports the disagreement.

LOG-ONLY on purpose, and not out of caution: faulting to alarm calls
on_enter_alarm -> set_enable(False), which RELEASES the carriage hold. A false
positive would trigger the very hazard this detects.
"""
import logging
from unittest.mock import MagicMock

import pytest

from reflex.dispatchers.servo import ServoDispatcher
from tests.dispatchers.conftest import MockBoard, MockFormats


@pytest.fixture
def servo(tmp_path, monkeypatch):
    """Same construction as the existing ServoDispatcher tests -- a MagicMock
    for `formats` fails, because Kivy's StringProperty rejects a Mock."""
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / ".config" / "reflex"))
    board = MockBoard()
    board.device = MagicMock()
    board.connected = True
    board.fast_data_values = {
        'servoCurrent': 0, 'servoMode': 0, 'servoSpeed': 0.0, 'stepsToGo': 0,
    }
    return ServoDispatcher(board=board, formats=MockFormats(), id_override="wd")


def _poll(servo, firmware_mode, times=1):
    for _ in range(times):
        servo.board.fast_data_values['servoMode'] = firmware_mode
        servo.on_update_tick(None, None)


N = ServoDispatcher.SERVO_MODE_DIVERGENCE_POLLS


def test_fires_when_firmware_runs_after_ui_said_stop(servo, caplog):
    servo.servoMode = 1          # UI commands feed on
    servo.servoMode = 0          # UI commands feed off  <- the command that matters
    with caplog.at_level(logging.WARNING):
        _poll(servo, firmware_mode=1, times=N)
    assert any("DIVERGENCE" in r.message for r in caplog.records), (
        "watchdog stayed silent while firmware reported the servo running "
        "after the UI commanded 0"
    )


def test_silent_below_the_threshold(servo, caplog):
    """A write in flight -- commanded here, not yet acknowledged there -- is a
    normal transient, not a defect. Reporting it would train the operator to
    ignore the message that matters."""
    servo.servoMode = 1
    servo.servoMode = 0
    with caplog.at_level(logging.WARNING):
        _poll(servo, firmware_mode=1, times=N - 1)
    assert not any("DIVERGENCE" in r.message for r in caplog.records)


def test_silent_when_they_agree(servo, caplog):
    servo.servoMode = 1
    servo.servoMode = 0
    with caplog.at_level(logging.WARNING):
        _poll(servo, firmware_mode=0, times=N * 2)
    assert not any("DIVERGENCE" in r.message for r in caplog.records)


def test_silent_before_the_ui_has_commanded_anything(servo, caplog):
    """Firmware retains servoMode across a UI restart, so at connect it can
    legitimately report a running servo the app never asked for. That is the
    reconnect case, handled by the init teardown -- not a divergence, because
    there is no command to diverge FROM. Reporting it here would fire on every
    restart-into-a-live-machine and drown the real signal."""
    with caplog.at_level(logging.WARNING):
        _poll(servo, firmware_mode=1, times=N * 2)
    assert not any("DIVERGENCE" in r.message for r in caplog.records)


def test_firmware_echo_does_not_count_as_a_command(servo, caplog):
    """THE LOAD-BEARING ONE. servoMode is two-way: the polled firmware value is
    assigned into the same property the UI commands with. If that echo were
    recorded as a command, `commanded` would track `observed` forever, they would
    agree by construction, and the watchdog could never fire -- a check
    structurally incapable of failing.

    Here the UI commands 0, then the firmware reports 1 (which the readback
    mirrors into the property). The watchdog must still see commanded == 0."""
    servo.servoMode = 1
    servo.servoMode = 0
    _poll(servo, firmware_mode=1, times=1)
    assert servo.servoMode == 1, "readback should have mirrored into the property"
    assert servo._commanded_servo_mode == 0, (
        "the firmware echo was recorded as a command -- watchdog is now blind"
    )
    with caplog.at_level(logging.WARNING):
        _poll(servo, firmware_mode=1, times=N)
    assert any("DIVERGENCE" in r.message for r in caplog.records)


def test_reconnect_adoption_does_not_blind_the_watchdog(servo, caplog):
    """on_connected adopts the retained firmware servoMode into the property.
    That adoption is an OBSERVATION and must ride the same flag as the poll
    mirror above -- unflagged, on_servoMode records it as a command and the
    baseline moves to match the firmware at exactly the moment a stale
    retained feed is the thing worth catching. If the UI commanded 0 earlier
    in the session and the reconnected firmware reports nonzero, that IS the
    divergence this watchdog exists to see."""
    servo.servoMode = 1
    servo.servoMode = 0                              # UI's last command: stop
    servo.board.connected = False                    # link drops...
    servo.board.fast_data_values['servoMode'] = 1    # ...firmware kept feeding
    servo.board.connected = True                     # reconnect -> on_connected
    assert servo.servoMode == 1, "adoption should have mirrored the readback"
    assert servo._commanded_servo_mode == 0, (
        "connect adoption moved the commanded baseline -- watchdog is blind "
        "at exactly the reconnect it exists for"
    )
    with caplog.at_level(logging.WARNING):
        _poll(servo, firmware_mode=1, times=N)
    assert any("DIVERGENCE" in r.message for r in caplog.records)


def test_reports_once_per_episode_not_once_per_poll(servo, caplog):
    """A watchdog that floods the log is one nobody reads. It needs to be
    findable afterwards, not loud at the time."""
    servo.servoMode = 1
    servo.servoMode = 0
    with caplog.at_level(logging.WARNING):
        _poll(servo, firmware_mode=1, times=N * 4)
    hits = [r for r in caplog.records if "DIVERGENCE" in r.message
            and "CLEARED" not in r.message]
    assert len(hits) == 1, f"expected one report per episode, got {len(hits)}"


def test_clearing_is_reported_too(servo, caplog):
    """The duration is the diagnostic. 'It disagreed for 5 polls' and 'it
    disagreed until I power-cycled' are very different findings."""
    servo.servoMode = 1
    servo.servoMode = 0
    _poll(servo, firmware_mode=1, times=N)
    with caplog.at_level(logging.WARNING):
        _poll(servo, firmware_mode=0, times=1)
    assert any("CLEARED" in r.message for r in caplog.records)
