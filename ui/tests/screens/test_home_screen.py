"""Mode-switch teardown (HomePage.change_mode_speed_check).

The requirement pinned here: a mode switch clears syncEnable on ALL scales
EXPLICITLY -- through the controller's HAL (the single stop_sync
implementation) -- and not as an inherited side effect of the servoMode write
firing the binding in dispatchers/els.py (review 2026-08-16, F6, mode-switch
half; the UI-FSM cancel half is a pending design decision, deliberately not
covered here).

THE MUTATION THESE TESTS EXIST TO SURVIVE: the els.py binding not running.
The fake servo below has a real Kivy servoMode property with NO binding
attached -- exactly the world where teardown-by-side-effect silently stops
tearing down. Before the explicit stop_sync call, that mutation left every
armed scale feeding the firmware's `anySyncMotionEnabled` term across a mode
switch (and a same-value servoMode write, 0 -> 0, fires no binding at all,
so the hole was reachable without mutating anything).

HomePage is built via __new__: its __init__ assembles the full widget tree
(StatusBar, four mode layouts, kv rules), none of which
change_mode_speed_check touches beyond the attributes stubbed here.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kivy.event import EventDispatcher
from kivy.properties import NumericProperty

from reflex.fsms.els_stop_hal import ElsStopHal

N_SCALES = 4


class _UnboundServo(EventDispatcher):
    """Real Kivy properties, NO dispatchers/els.py binding attached -- the
    binding's absence is the mutation under test, not a shortcut."""
    servoMode = NumericProperty(0)
    speed = NumericProperty(0)
    jogSpeed = NumericProperty(0)


def _make_page(app):
    from reflex.components.screens.home_screen import HomePage
    page = HomePage.__new__(HomePage)  # skip __init__: no widget tree needed
    page.app = app
    page.mode_layouts = {3: MagicMock(name="jog_layout"),
                         4: MagicMock(name="dro_layout")}
    page.bars_container = MagicMock(name="bars_container")
    page.current_layout = None
    page.next_mode = 4
    return page


@pytest.fixture
def rig():
    scales = [{'syncEnable': 0} for _ in range(N_SCALES)]
    board = SimpleNamespace(connected=True, device={'scales': scales})
    servo = _UnboundServo()
    app = SimpleNamespace(
        board=board,
        servo=servo,
        els_uic=SimpleNamespace(hal=ElsStopHal(board)),
    )
    return SimpleNamespace(page=_make_page(app), scales=scales, servo=servo)


def test_mode_switch_clears_all_scales_without_the_els_binding(rig):
    rig.scales[0]['syncEnable'] = 1   # spindle, armed by a running feed
    rig.scales[2]['syncEnable'] = 1   # non-spindle, armed via toggle_sync
    rig.servo.servoMode = 1

    rig.page.change_mode_speed_check(None)

    assert [s['syncEnable'] for s in rig.scales] == [0, 0, 0, 0], (
        "mode-switch sync teardown still depends on the dispatchers/els.py "
        "binding being attached"
    )
    assert rig.servo.servoMode == 0


def test_mode_switch_clears_sync_even_when_servo_mode_is_already_0(rig):
    """A same-value write (0 -> 0) fires no Kivy binding, so even WITH the
    els.py binding attached the old side-effect teardown never ran here.
    Reachable without any mutation: scale armed via toggle_sync while
    servoMode sat at 0, then a mode switch."""
    rig.scales[1]['syncEnable'] = 1
    assert rig.servo.servoMode == 0

    rig.page.change_mode_speed_check(None)

    assert [s['syncEnable'] for s in rig.scales] == [0, 0, 0, 0]


def test_sync_is_cleared_before_the_servo_mode_write(rig):
    """SYNC OFF FIRST, the ordering rule from els_stop_hal.stop_sync: at the
    moment the servoMode write lands, every scale must already be clear, so
    there is no window where the mode drops with a motion source still armed."""
    rig.scales[0]['syncEnable'] = 1
    rig.servo.servoMode = 1

    seen_at_write = []
    rig.servo.bind(servoMode=lambda *_: seen_at_write.append(
        [s['syncEnable'] for s in rig.scales]))

    rig.page.change_mode_speed_check(None)

    assert seen_at_write == [[0, 0, 0, 0]], (
        "servoMode was written before the scales were cleared -- the "
        "SYNC OFF FIRST ordering regressed"
    )
