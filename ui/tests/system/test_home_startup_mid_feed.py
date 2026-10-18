"""The home screen, built while the firmware is feeding, draws its layout at
once and leaves the feed alone.

When the connect-time reconcile finds FEEDING it leaves the feed running
(rung 3) and tells the operator the app changed nothing. A restart mid-feed
must therefore not reach change_mode(), the operator mode-switch path: it
waits for the servo to stop, so the screen would show no Sync Enable while the
carriage moves, then turn sync off on every scale and write servoMode 0 once
the spindle stops.

Two sessions on one emulator, as in test_warm_firmware.py: session 1 starts a
feed and drops the link without stopping it (a plain restart); session 2 is a
fresh stack that connects mid-feed, and a real HomePage is built against its
live servo and controller (tests/home_page_rig.py). The spindle is then
stopped from the emulator, outside the UI, as the operator did.
"""
import time
from fractions import Fraction

import pytest

from tests.home_page_rig import MODE_DRO, MODE_ELS, HomeApp, build_home_page, shown_layout

pytestmark = pytest.mark.system

MODE_FEEDING = 2   # els_machine_mode.h ELS_MMODE_FEEDING

_RUNNING = {"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}


def _session(pty_path, proc):
    from tests.system.harness import SystemHarness
    h = SystemHarness(pty_path, proc=proc)
    h.connect()
    h.configure(is_threading=False, retract_enabled=False,
                wizard_enabled=False, els_forward=True)
    return h


def _pump_for(h, seconds):
    """Pump in real time, so the screen's 0.1 s Clock.schedule_once chain
    gets every chance to run."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        h.pump()
        time.sleep(0.02)


def _sync_enables(h):
    return [int(h.board.device['scales'][i]['syncEnable']) for i in range(4)]


def _servo_mode_reg(h):
    return int(h.register('fastData', 'servoMode'))


class _NoticeRecorder:
    """Stands in for CustomPopup: the mock GL backend cannot build a real one
    (it segfaults), and the connect notice is the popup this test reaches."""
    shown = []

    def __init__(self, **kw):
        self.kw = kw

    def open(self):
        _NoticeRecorder.shown.append(self.kw)


@pytest.fixture
def mid_feed(emulator_process, tmp_path, monkeypatch):
    """Session 2 connected to firmware that is FEEDING, a HomePage built on
    it, and the emulator handle. Yields (h2, app, page, proc)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from reflex.components.popups import custom_popup
    monkeypatch.setattr(custom_popup, "CustomPopup", _NoticeRecorder)
    _NoticeRecorder.shown = []
    proc, pty_path = emulator_process

    h1 = _session(pty_path, proc)
    h1.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h1.commission_geometry()
    h1.set_feed(Fraction(254, 160))
    h1.enable_sync()
    assert h1.wait_until(
        lambda: int(h1.hal.read_current_mode()) == MODE_FEEDING
        and h1.board.servo.speed != 0, timeout_s=5), (
        "precondition: session 1 never got the firmware FEEDING and moving")
    h1.disconnect()   # writes nothing: the feed keeps running

    h2 = _session(pty_path, proc)
    try:
        _pump_for(h2, 0.5)
        assert int(h2.hal.read_current_mode()) == MODE_FEEDING, (
            "precondition: the connect-time reconcile did not leave the feed "
            "running (rung 3 hands-off), so this test would prove nothing")
        assert h2.board.servo.speed != 0
        assert len(_NoticeRecorder.shown) == 1, (
            "precondition: the connect notice was not presented exactly once")
        app = HomeApp(servo=h2.board.servo, els_uic=h2.controller,
                      current_mode=MODE_ELS)
        page = build_home_page(app)
        yield h2, app, page, proc
    finally:
        h2.disconnect()


@pytest.mark.parametrize("emulator_process", [_RUNNING], indirect=True)
def test_start_mid_feed_draws_layout_and_keeps_sync(mid_feed):
    h, app, page, proc = mid_feed

    assert shown_layout(page) is page.mode_layouts[MODE_ELS], (
        "home screen built mid-feed shows no mode layout: Sync Enable is "
        "unreachable while the carriage moves")
    sync_before = _sync_enables(h)
    assert sync_before[h.SPINDLE_SCALE_INDEX] == 1

    # The operator stops the spindle.
    h.emu_cmd("spindle rpm 0")
    assert h.wait_until(lambda: h.board.servo.speed == 0, timeout_s=10), (
        "the spindle never stopped in the emulator")
    _pump_for(h, 1.0)

    assert _sync_enables(h) == sync_before, (
        "sync changed when motion stopped, after the notice said the app "
        "changed nothing")
    assert _servo_mode_reg(h) == 1
    assert int(h.hal.read_current_mode()) == MODE_FEEDING
    assert shown_layout(page) is page.mode_layouts[MODE_ELS]


@pytest.mark.parametrize("emulator_process", [_RUNNING], indirect=True)
def test_operator_mode_switch_mid_feed_waits_then_tears_down(mid_feed):
    h, app, page, proc = mid_feed

    app.current_mode = MODE_DRO
    _pump_for(h, 1.0)
    # Still moving: the switch waits, nothing torn down.
    assert shown_layout(page) is page.mode_layouts[MODE_ELS]
    assert _sync_enables(h)[h.SPINDLE_SCALE_INDEX] == 1
    assert _servo_mode_reg(h) == 1

    h.emu_cmd("spindle rpm 0")
    assert h.wait_until(lambda: h.board.servo.speed == 0, timeout_s=10)
    assert h.wait_until(lambda: shown_layout(page) is page.mode_layouts[MODE_DRO],
                        timeout_s=2), "mode switch never completed after motion stopped"
    _pump_for(h, 0.3)

    assert _sync_enables(h) == [0, 0, 0, 0]
    assert _servo_mode_reg(h) == 0
