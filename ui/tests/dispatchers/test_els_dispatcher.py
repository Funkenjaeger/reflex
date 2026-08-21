"""The servoMode -> syncEnable binding (ElsDispatcher._sync_spindle_to_servo).

Sync follows the SYNC-FEED mode, not "any mode" (review 2026-08-16, F5/F6):

  * servoMode == 1 (sync feed)  -> arm the SPINDLE scale's syncEnable, only.
  * servoMode == anything else  -> clear syncEnable on ALL scales, the
    els_stop_hal.stop_sync shape. 2 is jog, 0 is off; neither is a reason for
    any scale to keep feeding the firmware's `anySyncMotionEnabled` term, and
    toggle_sync (Jog/Index coordbars) can have armed a NON-spindle scale that
    must not survive into a feed stop.

Construction mirrors tests/system/harness.py and tests/components/conftest.py:
ElsDispatcher's __init__ reaches MainApp.get_running_app() for exactly three
attributes (board, servo, axes), so the fake app is a 3-attribute shim behind
a patched kivy.app.App.get_running_app. Everything downstream of the binding
is REAL — real ServoDispatcher (so the firmware-originated flag path is the
production one, not a hand-set stub), real AxisDispatchers, real scale dicts.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import kivy.app
import pytest

from reflex.dispatchers.axis import AxisDispatcher
from reflex.dispatchers.axis_transform import AxisTransform
from reflex.dispatchers.input import InputDispatcher
from reflex.dispatchers.servo import ServoDispatcher
from tests.dispatchers.conftest import MockBoard, MockFormats, MockOffsetProvider

N_SCALES = 4
SPINDLE = 0  # axes[0] / scales[0] is the spindle in these tests


@pytest.fixture
def board():
    b = MockBoard()
    b.device = {
        'scales': [{'syncEnable': 0} for _ in range(N_SCALES)],
        'servo': MagicMock(),
        'fastData': MagicMock(),
    }
    # ServoDispatcher's connect/poll paths read these.
    b.fast_data_values = {
        'servoCurrent': 0, 'servoMode': 0, 'servoSpeed': 0.0, 'stepsToGo': 0,
    }
    return b


@pytest.fixture
def els(board, tmp_path, monkeypatch):
    """A real ElsDispatcher over a real servo + axes graph, spindle = axis 0,
    board connected."""
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / ".config" / "reflex"))
    formats = MockFormats()
    offset_provider = MockOffsetProvider()
    servo = ServoDispatcher(board=board, formats=formats, id_override="els_t_servo")
    inputs = [
        InputDispatcher(board=board, inputIndex=i, id_override=f"els_t_input_{i}")
        for i in range(N_SCALES)
    ]
    axes = [
        AxisDispatcher(
            board=board, formats=formats, servo=servo,
            offset_provider=offset_provider, inputs=inputs,
            transform=AxisTransform.identity(i),
            id_override=f"els_t_axis_{i}",
        )
        for i in range(N_SCALES)
    ]
    fake_app = SimpleNamespace(board=board, servo=servo, axes=axes)
    monkeypatch.setattr(kivy.app.App, "get_running_app",
                        staticmethod(lambda: fake_app))

    from reflex.dispatchers.els import ElsDispatcher
    els = ElsDispatcher(id_override="els_t")
    els.spindle_axis_index = SPINDLE
    board.connected = True
    return els


def _sync_states(board):
    return [s['syncEnable'] for s in board.device['scales']]


def test_sync_feed_arms_the_spindle_scale_only(els, board):
    els.app.servo.servoMode = 1
    assert _sync_states(board) == [1, 0, 0, 0]
    assert els.app.axes[SPINDLE].syncEnable is True


def test_jog_mode_does_not_arm_spindle_sync(els, board):
    """THE F5 FIX. The old `1 if value != 0 else 0` treated jog (2) as
    feed-on, arming the spindle scale — which hands the firmware's
    servoEnableTask the term it needs to switch the feed on by itself."""
    els.app.servo.servoMode = 2
    assert board.device['scales'][SPINDLE]['syncEnable'] == 0
    assert _sync_states(board) == [0, 0, 0, 0]
    assert els.app.axes[SPINDLE].syncEnable is False


def test_leaving_feed_clears_a_non_spindle_scale_too(els, board):
    """Arm a non-spindle scale through the production toggle_sync path (the
    Jog/Index coordbar route), then stop the feed: mode 1 -> 0 must clear
    EVERY scale, not just the spindle's — the stop_sync shape."""
    els.app.servo.servoMode = 1
    els.app.axes[2].toggle_sync()          # operator armed axis 2 earlier
    assert _sync_states(board) == [1, 0, 1, 0]

    els.app.servo.servoMode = 0
    assert _sync_states(board) == [0, 0, 0, 0]
    assert els.app.axes[2].syncEnable is False, (
        "the UI mirror on the toggled axis survived the clear — the coordbar "
        "button would show sync armed on a disarmed scale"
    )
    assert els.app.axes[SPINDLE].syncEnable is False


def test_firmware_originated_mode_changes_are_skipped(els, board):
    """OBSERVATION MUST NOT BECOME COMMAND. Drive the mirror through the real
    ServoDispatcher poll path (which flags servoMode_from_firmware around the
    assignment) — not by hand-setting the flag — so this fails if either side
    of that contract drifts."""
    board.fast_data_values['servoMode'] = 1
    els.app.servo.on_update_tick(None, None)
    # Guard the guard: the property really did change (the binding had its
    # chance to fire and declined), we are not asserting on a non-event.
    assert els.app.servo.servoMode == 1
    assert _sync_states(board) == [0, 0, 0, 0], (
        "a polled firmware servoMode armed syncEnable — the disengage-race "
        "latch (see _sync_spindle_to_servo docstring) is back"
    )


def test_clear_path_needs_no_spindle_axis(els, board):
    """A scale armed via toggle_sync with NO spindle role assigned must still
    be cleared on mode -> 0; the clear path is deliberately not gated on
    get_spindle_axis()."""
    els.app.servo.servoMode = 1   # arms spindle while the role is assigned
    els.app.axes[1].toggle_sync()
    els.spindle_axis_index = -1   # role unassigned (e.g. fresh machine config)
    els.app.servo.servoMode = 0
    assert _sync_states(board) == [0, 0, 0, 0]
