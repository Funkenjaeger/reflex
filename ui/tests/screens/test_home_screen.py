"""Mode-switch teardown (HomePage.change_mode_speed_check), and construction.

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

The switch tests build HomePage via __new__: __init__ assembles the full
widget tree (StatusBar, four mode layouts, kv rules), none of which
change_mode_speed_check touches beyond the attributes stubbed here.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kivy.event import EventDispatcher
from kivy.properties import NumericProperty

from reflex.fsms.els_stop_registers import ElsStopRegisters

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
        els_uic=SimpleNamespace(hal=ElsStopRegisters(board)),
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
    """SYNC OFF FIRST, the ordering rule from els_stop_registers.stop_sync: at the
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


# ── Construction vs. an operator mode switch ──────────────────────────────────
#
# These build the REAL HomePage through __init__ (tests/home_page_rig.py), which
# the tests above deliberately skip. Built mid-feed, the screen draws its layout
# at once and writes nothing; only an operator switch waits for the servo and
# tears down.

from tests.home_page_rig import (  # noqa: E402
    MODE_DRO, MODE_ELS, HomeApp, build_home_page, shown_layout,
)


class _FakeClock:
    """Stands in for home_screen.Clock: runs scheduled callbacks on demand."""

    def __init__(self):
        self.pending = []

    def schedule_once(self, callback, timeout=0):
        self.pending.append(callback)

    def run(self, rounds=5):
        for _ in range(rounds):
            due, self.pending = self.pending, []
            for callback in due:
                callback(0)


class _RecordingServo:
    """Records EVERY servo write, same-value ones included (a Kivy binding
    would miss those). speed is read only."""

    def __init__(self, events, speed, servoMode):
        object.__setattr__(self, "_events", events)
        object.__setattr__(self, "speed", speed)
        object.__setattr__(self, "servoMode", servoMode)
        object.__setattr__(self, "jogSpeed", 0)

    def __setattr__(self, name, value):
        if name != "speed":
            self._events.append((name, value))
        object.__setattr__(self, name, value)


@pytest.fixture
def startup(monkeypatch):
    from reflex.components.screens import home_screen
    clock = _FakeClock()
    monkeypatch.setattr(home_screen, "Clock", clock)
    events = []
    hal = SimpleNamespace(stop_sync=lambda: events.append(("stop_sync",)))
    # Firmware FEEDING, as the first snapshot mirrors it: servoMode 1, moving.
    servo = _RecordingServo(events, speed=120.0, servoMode=1)
    app = HomeApp(servo=servo, els_uic=SimpleNamespace(hal=hal),
                  current_mode=MODE_ELS)
    return SimpleNamespace(clock=clock, events=events, servo=servo, app=app)


def test_construction_mid_feed_draws_the_layout_and_touches_nothing(startup):
    page = build_home_page(startup.app)

    assert shown_layout(page) is page.mode_layouts[MODE_ELS], (
        "home screen built while the servo moves shows no mode layout, so "
        "Sync Enable is unreachable")
    startup.clock.run()
    assert startup.events == []

    # Motion stops (the operator stops the spindle): still nothing written.
    object.__setattr__(startup.servo, "speed", 0)
    startup.clock.run()
    assert startup.events == [], (
        "construction tore the job down once motion stopped: "
        f"{startup.events}")
    assert shown_layout(page) is page.mode_layouts[MODE_ELS]


def test_operator_mode_switch_while_moving_still_waits_then_tears_down(startup):
    page = build_home_page(startup.app)
    startup.clock.run()
    assert startup.events == []

    startup.app.current_mode = MODE_DRO
    startup.clock.run()
    # Waiting for the servo: the old layout stays, nothing torn down yet.
    # jogSpeed 0 is change_mode's own first write while servoMode != 0.
    assert shown_layout(page) is page.mode_layouts[MODE_ELS]
    assert startup.events == [("jogSpeed", 0)]

    object.__setattr__(startup.servo, "speed", 0)
    startup.clock.run()
    assert startup.events == [("jogSpeed", 0), ("stop_sync",), ("servoMode", 0)], (
        "mode switch teardown order changed: SYNC OFF FIRST, then servoMode 0")
    assert shown_layout(page) is page.mode_layouts[MODE_DRO]
    assert page.mode_layouts[3].jog_bar.enable_jog is False
