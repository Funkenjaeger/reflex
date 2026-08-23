"""The controller half of the transient-notice surface.

tests/utils/test_notices.py owns the POLICY -- expiry, severity, collisions.
What is worth pinning here is the wiring, which is where a correct policy still
fails to reach the operator:

  1. A posted notice reaches the two kv properties the status bar binds to.
  2. Expiry reaches them too. The NoticeCenter can be perfectly right about a
     notice being over and the strip still sit there forever if nothing
     republishes -- which is exactly what happens if the periodic sweep is
     dropped or wired to the wrong tick.
  3. THE SWEEP IS NOT ON THE BOARD TICK. The first migrated notice ("No ELS Z
     axis assigned") fires most often with nothing connected, and board.
     update_tick does not fire when nothing is connected. On that tick the
     notice would appear and never leave.
  4. The migrated caller (toggle_engage) actually says something, at the right
     severity -- the end-to-end proof that the surface works.

Fixture idiom is tests/fsms/test_ui_controller_phase_offset.py's: the real
controller over MagicMock'd hardware, driven by hand.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

from unittest.mock import MagicMock

import pytest

from reflex.fsms.ui_controller import ElsUiController, NOTICE_SWEEP_SECONDS
from reflex.utils.notices import (NoticeCenter, NOTICE_INFO, NOTICE_WARNING,
                                  SEVERITIES)
from tests.fsms.test_ui_controller import (_make_collaborators, _make_x_axis,
                                           _make_z_axis, _pump)

INFO_S = SEVERITIES[NOTICE_INFO].seconds


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return FakeClock()


def _controller(clock, *, z_axis=None, x_axis=None):
    board, els = _make_collaborators(z_axis=z_axis, x_axis=x_axis)
    c = ElsUiController(els=els, board=board)
    _pump()
    # Swap in a controllable clock AFTER construction. The controller builds its
    # own NoticeCenter on the real monotonic clock (correct in production); the
    # expiry rules cannot be exercised against that without sleeping.
    c._notices = NoticeCenter(time_fn=clock)
    return c


@pytest.fixture
def ctrl(clock):
    """Z and X mapped: the default rig, where nothing refuses anything."""
    return _controller(clock, z_axis=_make_z_axis(), x_axis=_make_x_axis())


@pytest.fixture
def ctrl_no_z(clock):
    """No ELS Z axis assigned -- the state a fresh install or a disconnected
    controller is in, and the one where Engage is a button that does nothing."""
    return _controller(clock, x_axis=_make_x_axis())


# ─── republication into kv ────────────────────────────────────────────────────

def test_a_notice_reaches_the_properties_the_status_bar_binds(ctrl):
    assert ctrl.notify("carriage retracted", NOTICE_INFO) is True
    assert ctrl.notice_text == "carriage retracted"
    assert ctrl.notice_severity == NOTICE_INFO


def test_severity_reaches_the_renderer_too(ctrl):
    ctrl.notify("no ELS Z axis assigned", NOTICE_WARNING)
    assert ctrl.notice_severity == NOTICE_WARNING


def test_an_empty_notice_changes_nothing(ctrl):
    ctrl.notify("real message", NOTICE_INFO)
    assert ctrl.notify("", NOTICE_WARNING) is False
    assert ctrl.notice_text == "real message"
    assert ctrl.notice_severity == NOTICE_INFO


# ─── expiry has to reach the screen, not just the queue ───────────────────────

def test_the_sweep_takes_an_expired_notice_off_the_screen(ctrl, clock):
    ctrl.notify("carriage retracted", NOTICE_INFO)

    clock.advance(INFO_S - 0.1)
    ctrl._poll_notices(NOTICE_SWEEP_SECONDS)
    assert ctrl.notice_text == "carriage retracted"

    clock.advance(0.2)
    ctrl._poll_notices(NOTICE_SWEEP_SECONDS)
    assert ctrl.notice_text == ""
    # The severity clears with it: a stale severity would tint the NEXT notice
    # for a frame if it happened to arrive before the renderer caught up.
    assert ctrl.notice_severity == ""


def test_the_sweep_promotes_whatever_was_waiting(ctrl, clock):
    ctrl.notify("second in line", NOTICE_INFO)
    ctrl.notify("takes the screen", NOTICE_WARNING)
    assert ctrl.notice_text == "takes the screen"

    clock.advance(SEVERITIES[NOTICE_WARNING].seconds + 0.1)
    ctrl._poll_notices(NOTICE_SWEEP_SECONDS)
    assert ctrl.notice_text == "second in line"
    assert ctrl.notice_severity == NOTICE_INFO


class RecordingClock:
    """The real Kivy Clock with schedule_interval() calls written down.

    Patched over the NAME the controller module imported, not over the Clock
    object itself: kivy's Clock is a Cython instance and setting attributes on
    it is not something to rely on.
    """

    def __init__(self, real):
        self._real = real
        self.intervals = []

    def schedule_interval(self, callback, timeout):
        self.intervals.append((callback, timeout))
        return self._real.schedule_interval(callback, timeout)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_notice_expiry_is_wired_to_the_clock_not_to_the_board_tick(clock, monkeypatch):
    """THE WIRING MISTAKE THIS EXISTS TO CATCH. Every other poller in this
    controller hangs off board.update_tick, and copying that pattern is the
    obvious thing to do -- but update_tick only fires while a board is talking,
    and the very first notice migrated onto this surface is one you get when
    NOTHING is connected. Wired that way the strip would appear and never come
    down: a permanent banner, which is precisely what this surface is defined
    not to be.

    STRUCTURAL, deliberately. An expiry test that calls _poll_notices by hand
    (like the two above) cannot see which tick would have called it -- it would
    pass just as happily against the broken wiring. So this one asserts where
    the callback was registered, which is the thing that would actually be
    wrong.
    """
    import reflex.fsms.ui_controller as uic_module

    recording = RecordingClock(uic_module.Clock)
    monkeypatch.setattr(uic_module, "Clock", recording)
    c = _controller(clock, z_axis=_make_z_axis(), x_axis=_make_x_axis())

    assert any(cb == c._poll_notices for cb, _t in recording.intervals), \
        "the notice sweep was never scheduled on the Clock"

    bound_kwargs = [kw for _args, kw in c._board.bind.call_args_list]
    assert not any(kw.get("update_tick") == c._poll_notices
                   for kw in bound_kwargs), \
        "the notice sweep hangs off board.update_tick — it will not run, and " \
        "notices will not expire, while the controller is disconnected"


# ─── the migrated caller ──────────────────────────────────────────────────────

def test_engaging_without_a_z_axis_tells_the_operator_why(ctrl_no_z):
    """END TO END, on the path that made this feature worth building.

    With no ELS Z axis mapped, Engage is fully enabled and does nothing -- the
    explanation went to a log file the operator cannot read while standing at
    the lathe. Now it goes to the status bar, and it names the fix rather than
    the fault.
    """
    ctrl_no_z.toggle_engage()

    assert ctrl_no_z.engaged is False, "precondition: the engage was refused"
    assert ctrl_no_z.notice_severity == NOTICE_WARNING
    assert "Z axis" in ctrl_no_z.notice_text
    assert "ELS settings" in ctrl_no_z.notice_text


def test_a_stale_engage_tap_is_reported_as_info_not_as_a_warning(ctrl):
    """The double-tap race guard. Nothing is wrong with the machine and nothing
    needs fixing, so it must not wear the colour reserved for "go change
    something" -- a surface that cries wolf gets ignored."""
    ctrl._els_fsm = MagicMock()
    ctrl._els_fsm.may_enable.return_value = False
    ctrl._els_fsm.state = "cutting"

    ctrl.toggle_engage()

    assert ctrl.notice_severity == NOTICE_INFO
    assert "Engage ignored" in ctrl.notice_text


def test_a_successful_engage_says_nothing(ctrl):
    """A notice for every successful press would train the operator to ignore
    the strip, which costs exactly the refusals it exists to deliver."""
    ctrl.toggle_engage()
    _pump()

    assert ctrl.engaged is True, "precondition: the engage was accepted"
    assert ctrl.notice_text == ""
