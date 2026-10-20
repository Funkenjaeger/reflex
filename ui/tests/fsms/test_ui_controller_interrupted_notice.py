"""The connect-time notice: what the operator is told reconcile found, and did.

Two paths raise it, and they leave the machine in opposite states:

- the state-keyed teardown stopped a moving carriage: sync off, stop cleared;
- the adopt path's live-work rows (els_adopt.py) changed nothing: the work is
  still running with whatever stop it had.

Until 2026-10-17 the notice always said the first, so on the adopt path it
would have stated the opposite of what happened. The tests here pin the text
for each case, and one drives the real reconnect wiring, which resets the
diagnostic recorder just before reconcile runs: the adopt path must not
depend on anything that reset clears.

Fixture idiom is tests/fsms/test_ui_controller_notices.py's: the real
controller over MagicMock'd hardware, driven by hand.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

from unittest.mock import MagicMock

import pytest

from reflex.components.popups import custom_popup
from reflex.fsms.els_mode_watch import (MODE_CAL, MODE_FEEDING, MODE_MOVING,
                                        MODE_TAKEUP)
from reflex.fsms.ui_controller import ElsUiController
from tests.fsms.test_ui_controller import (_make_collaborators, _make_x_axis,
                                           _make_z_axis, _pump)


class FakePopup:
    shown = []

    def __init__(self, *, title, message, button_text, **_):
        self.title, self.message = title, message

    def open(self):
        FakePopup.shown.append(self)


@pytest.fixture
def ctrl(monkeypatch):
    FakePopup.shown = []
    monkeypatch.setattr(custom_popup, "CustomPopup", FakePopup)
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    _pump()
    return c


def _notice(ctrl, flight):
    ctrl._els_fsm.interrupted_pass = flight
    ctrl._on_pass_interrupted()
    assert len(FakePopup.shown) == 1, "exactly one notice"
    assert ctrl._els_fsm.interrupted_pass is None, "the notice is one-shot"
    return FakePopup.shown[0]


SNAPSHOT = {'moving': True, 'sync': True, 'servoMode': 1, 'active': False, 'enable': True}


# ─── the teardown path: unchanged ───────────────────────────────────────────

def test_teardown_notice_says_the_feed_was_stopped(ctrl):
    """The state-keyed teardown really did stop the carriage. Its text is the
    one the app has always shown."""
    p = _notice(ctrl, dict(SNAPSHOT, teardown=True))
    assert p.title == "Feed stopped on startup"
    assert "Sync is now off and the ELS stop is clear" in p.message
    assert "(with an armed stop)" in p.message


def test_a_payload_without_the_key_is_read_as_teardown(ctrl):
    """Any producer that predates the teardown key came from the teardown
    path, so its notice must not claim the work is still running."""
    p = _notice(ctrl, dict(SNAPSHOT))
    assert p.title == "Feed stopped on startup"


# ─── the hands-off path: nothing was changed ────────────────────────────────

@pytest.mark.parametrize("mode, doing", [
    (MODE_FEEDING, "feeding"),
    (MODE_MOVING, "making a commanded move"),
    (MODE_TAKEUP, "taking up backlash"),
    (MODE_CAL, "running a backlash calibration"),
])
def test_hands_off_notice_says_nothing_was_changed(ctrl, mode, doing):
    """Every live-work row: the notice names what the controller was doing and
    says, truthfully, that sync and the stop are as it found them. It must not
    say anything was stopped or cleared."""
    p = _notice(ctrl, dict(SNAPSHOT, mode=mode, mode_name="X", teardown=False))
    assert p.title == "Controller still working"
    assert f"the controller was {doing}" in p.message
    assert "This app changed nothing. Sync is still on and the ELS stop is still armed." in p.message
    assert "ELS shows as disengaged" in p.message
    for wrong in ("stopped", "now off", "is clear", "HERE"):
        assert wrong not in p.message, f"hands-off notice claims {wrong!r}: {p.message}"


@pytest.mark.parametrize("mode, sync_advice", [
    (MODE_FEEDING, True), (MODE_TAKEUP, True), (MODE_MOVING, False), (MODE_CAL, False),
])
def test_sync_off_advice_only_where_sync_off_stops_the_carriage(ctrl, mode, sync_advice):
    """Turning Sync Enable off stops a feed. It does not stop a commanded move
    or a calibration, which end by themselves, so the notice must not offer
    it as the way to stop those."""
    p = _notice(ctrl, dict(SNAPSHOT, mode=mode, mode_name="X", teardown=False))
    assert ("turn Sync Enable off" in p.message) is sync_advice


def test_hands_off_with_sync_on_and_no_armed_stop_says_so(ctrl):
    """The dangerous hands-off case: a feed running with no stop armed. The
    operator must be told nothing will stop it at a shoulder."""
    p = _notice(ctrl, dict(SNAPSHOT, enable=False, mode=MODE_FEEDING,
                           mode_name="FEEDING", teardown=False))
    assert "the ELS stop is still NOT armed" in p.message
    assert "Nothing will stop the carriage at a shoulder." in p.message


def test_hands_off_with_an_unreadable_snapshot_does_not_guess(ctrl):
    """read_motion_in_flight never raises; on a failed read it returns only
    'moving' and 'reason'. The notice then says it could not read sync or the
    stop instead of reporting values it never saw."""
    p = _notice(ctrl, {'moving': False, 'reason': 'read failed: boom',
                       'mode': MODE_FEEDING, 'mode_name': 'FEEDING', 'teardown': False})
    assert "could not read whether sync is on or the ELS stop is armed" in p.message
    assert "still armed" not in p.message and "still on" not in p.message


def test_hands_off_notice_for_an_unknown_mode_names_it(ctrl):
    p = _notice(ctrl, dict(SNAPSHOT, mode=99, mode_name="UNKNOWN(99)", teardown=False))
    assert p.title == "Controller still working"
    assert "in a mode this app does not know (UNKNOWN(99))" in p.message


# ─── through the real reconnect wiring ──────────────────────────────────────

def test_reconnect_adopts_after_the_recorder_reset(ctrl):
    """_on_connected_changed resets the diagnostic recorder (schema back to
    None) and then reconciles in the same callback. When the adopt path was
    gated on the recorder's learned schema, that ordering meant it could not
    fire on a reconnect even with the gate satisfied. It now reads the
    machineMode register directly, so a fresh session that finds the
    controller feeding is left alone and told so."""
    ctrl._hal.read_current_mode = MagicMock(return_value=MODE_FEEDING)
    ctrl._hal.read_motion_in_flight = MagicMock(return_value=dict(SNAPSHOT))
    ctrl._hal.stop_sync = MagicMock()
    ctrl._hal.set_enable = MagicMock()
    ctrl._hal.set_active = MagicMock()
    assert ctrl._els_fsm.state == 'disabled'

    ctrl._on_connected_changed(ctrl._board, True)
    _pump()

    assert ctrl._diag_recorder.schema is None, "the reset ran"
    ctrl._hal.read_current_mode.assert_called_once_with()
    ctrl._hal.stop_sync.assert_not_called()
    ctrl._hal.set_enable.assert_not_called()
    ctrl._hal.set_active.assert_not_called()
    assert len(FakePopup.shown) == 1
    assert FakePopup.shown[0].title == "Controller still working"
