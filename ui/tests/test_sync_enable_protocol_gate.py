"""Sync Enable refuses to turn the feed ON against a mismatched protocol.

Board's protocol check is deliberately non-fatal (the DRO and the Update
screen keep working, which is how the operator recovers). Before 2026-09-17
nothing acted on it except calibration and resync, so a feed could be engaged
with this UI's register map disagreeing with the board's (Open Loops
6aaca75b). MainApp.on_servo_enable_pressed is the one entry both Sync Enable
buttons (elsbar.kv, servobar.kv) call, in every mode.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from reflex.app import MODE_ELS, MainApp
from reflex.components.popups import custom_popup


@pytest.fixture
def popups(monkeypatch):
    opened = []

    class FakePopup:
        def __init__(self, **kw):
            self.kw = kw

        def open(self):
            opened.append(self.kw)

    monkeypatch.setattr(custom_popup, "CustomPopup", FakePopup)
    return opened


def _app(*, mismatch, feed_on=False, mode=MODE_ELS):
    servo = MagicMock(servoMode=1 if feed_on else 0)
    els_uic = MagicMock()
    els_uic.request_feed_enable.return_value = True
    board = SimpleNamespace(
        protocol_mismatch=mismatch,
        protocol_message=("Firmware register protocol is version 10; this UI "
                          "only understands 9. Update the UI.") if mismatch else "")
    return SimpleNamespace(servo=servo, board=board, current_mode=mode, els_uic=els_uic)


@pytest.mark.parametrize("mode", [MODE_ELS, 3])
def test_turning_the_feed_on_is_refused_when_mismatched(popups, mode):
    app = _app(mismatch=True, mode=mode)

    MainApp.on_servo_enable_pressed(app)

    app.servo.toggle_enable.assert_not_called()
    app.els_uic.request_feed_enable.assert_not_called()
    assert len(popups) == 1
    assert "only understands 9" in popups[0]["message"], "the board's own words"
    assert not popups[0].get("confirm_callback"), "a refusal, not an override"


def test_turning_the_feed_off_is_never_refused(popups):
    app = _app(mismatch=True, feed_on=True, mode=3)

    MainApp.on_servo_enable_pressed(app)

    app.servo.toggle_enable.assert_called_once()
    assert popups == []


def test_a_matched_protocol_is_not_gated(popups):
    app = _app(mismatch=False)

    MainApp.on_servo_enable_pressed(app)

    app.els_uic.request_feed_enable.assert_called_once_with()
    assert popups == []
