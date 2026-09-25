"""Install over an engaged ELS job: the Update screen offers to disengage it.

2026-09-25 08:32, on the lathe: the in-app update to v1.2.0-rc.6 FAILED in
11 s, safely -- the controller refuses to reboot into its bootloader while an
ELS job is engaged (Ramps.c elsBootCommandTick), and the operator had left one
engaged-idle overnight. The screen said FAILED with no reason, and the way out
was to back all the way out, disengage on the home screen, and come back. The
operator's request: offer to disengage it.

What is pinned here, against the REAL ElsUiController over MagicMock'd
hardware (tests/fsms/test_ui_controller.py's rig):

  * not engaged: no dialog, the install starts exactly as before;
  * engaged and allowed: an "ELS job engaged" dialog offers Cancel and
    Disengage and Install;
  * confirming disengages through the controller's disengage() -- the path
    the ADV bar's button takes -- and the install starts only once a board
    tick AFTER the disengage has the controller reporting elsStop.enable 0;
  * the controller never reporting 0 (or not answering) refuses: nothing is
    started;
  * sync armed with the spindle turning, or a cycle running: no Disengage is
    offered, and the dialog names what to do instead;
  * a pre-release asks its own question FIRST, and nothing is disengaged
    until the ELS dialog is answered.

The dialogs are captured by replacing the screen module's Popup, BoxLayout
and Factory with recording fakes -- building the real themed widgets needs a
running app.
"""
import asyncio
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from kivy.event import EventDispatcher
from kivy.properties import NumericProperty

import reflex.components.screens.update_screen as us
from reflex.components.screens.update_screen import UpdateScreen
from reflex.fsms.ui_controller import ElsUiController
from reflex.utils.updater import Release
from tests.fsms.test_ui_controller import (_engage, _make_collaborators,
                                           _make_x_axis, _make_z_axis, _pump)

FINAL = Release(tag="v9.1.0", prerelease=False,
                firmware_url="https://example/v9.1.0/fw.bin",
                firmware_name="reflex-app-9.1.0.bin")
RC = Release(tag="v9.2.0-rc.1", prerelease=True,
             firmware_url="https://example/v9.2.0-rc.1/fw.bin",
             firmware_name="reflex-app-9.2.0-rc.1.bin")


# ─── recording stand-ins for the dialog widgets ───────────────────────────────

class FakeWidget:
    def __init__(self, **kw):
        self.kw = kw
        self.text = kw.get("text", "")
        self.children = []
        self.handlers = {}

    def add_widget(self, w):
        self.children.append(w)

    def bind(self, **kw):
        self.handlers.update(kw)

    def press(self):
        self.handlers["on_release"](self)


class FakePopup(FakeWidget):
    def __init__(self, log, **kw):
        super().__init__(**kw)
        self.title = kw["title"]
        self.is_open = False
        log.append(self)

    def open(self):
        self.is_open = True

    def dismiss(self, *_):
        self.is_open = False

    # what the operator reads and can press
    @property
    def body(self):
        return self.kw["content"].children[0].text

    @property
    def buttons(self):
        return self.kw["content"].children[1].children

    def button(self, text):
        [b] = [b for b in self.buttons if b.text == text]
        return b


class TickBoard(EventDispatcher):
    """The one Board property the wait watches: update_tick, bumped once per
    poll after the elsStop snapshot is refreshed (Board.update)."""
    update_tick = NumericProperty(0)


@pytest.fixture
def dialogs():
    opened = []
    with patch.object(us, "Popup", lambda **kw: FakePopup(opened, **kw)), \
         patch.object(us, "BoxLayout", FakeWidget), \
         patch.object(us, "Factory", SimpleNamespace(ThemedLabel=FakeWidget,
                                                     SetupButton=FakeWidget)):
        yield opened


@pytest.fixture
def ctrl():
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    _pump()
    return c


def engaged_idle(c, *, sync=False, spindle=False):
    _engage(c)
    assert c.engaged and c._els_fsm.state == "stopped", "precondition: engaged-idle"
    c._board.servo.servoMode = 1 if sync else 0
    c._els.spindle_is_running = spindle
    # The controller holds the job: elsStop.enable reads 1 on the snapshot.
    c._board.els_stop_values = {"enable": 1}
    return c


class Rig:
    def __init__(self, screen, ctrl, board):
        self.screen, self.ctrl, self.board = screen, ctrl, board
        self.status = []


@pytest.fixture
def rig(ctrl, dialogs, monkeypatch):
    """The screen, wired to the real controller and a tick board, with the
    install itself and the Kivy scheduling replaced by recorders."""
    with patch.object(UpdateScreen, "apply_class_lang_rules"):
        s = UpdateScreen()
    s._catalogue = {FINAL.tag: FINAL, RC.tag: RC}
    board = TickBoard()
    r = Rig(s, ctrl, board)
    monkeypatch.setattr(UpdateScreen, "_els_controller", staticmethod(lambda: ctrl))
    monkeypatch.setattr(UpdateScreen, "_board", staticmethod(lambda: board))
    monkeypatch.setattr(s, "update_status", r.status.append)
    r.do_install = MagicMock()
    monkeypatch.setattr(s, "_do_install", r.do_install)
    r.clock = MagicMock()
    monkeypatch.setattr(us, "Clock", r.clock)
    # Short, so the refusal cases take a fraction of a second.
    monkeypatch.setattr(us, "ELS_RELEASE_TIMEOUT_S", 0.4)
    monkeypatch.setattr(us, "ELS_RELEASE_POLL_S", 0.01)
    r.dialogs = dialogs
    return r


def install(r, release=FINAL):
    r.screen.allow_experimental = True
    r.screen.selected_release = release.tag
    r.screen.install_release()


def run_the_wait(r, during=None):
    """Fire what "Disengage and Install" scheduled -- inside an event loop, as
    the app's async_run provides -- and let it finish. `during(r)` runs while
    the wait is in progress (it is where a test ticks the board)."""
    [call] = r.clock.schedule_once.call_args_list
    callback = call.args[0]

    async def drive():
        callback(0)
        waiting = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert waiting, "nothing was scheduled"
        if during is not None:
            await during(r)
        await asyncio.gather(*waiting)

    asyncio.run(drive())


# ─── not engaged: unchanged ──────────────────────────────────────────────────

def test_not_engaged_installs_with_no_dialog(rig):
    assert rig.ctrl.engaged is False
    install(rig)
    assert rig.dialogs == []
    rig.do_install.assert_called_once_with(FINAL)


def test_no_controller_installs_with_no_dialog(rig, monkeypatch):
    monkeypatch.setattr(UpdateScreen, "_els_controller", staticmethod(lambda: None))
    install(rig)
    assert rig.dialogs == []
    rig.do_install.assert_called_once_with(FINAL)


# ─── engaged and allowed: the offer ──────────────────────────────────────────

def test_engaged_offers_disengage_and_install(rig):
    engaged_idle(rig.ctrl)
    install(rig)
    [d] = rig.dialogs
    assert d.is_open and d.title == "ELS job engaged"
    assert [b.text for b in d.buttons] == ["Cancel", "Disengage and Install"]
    assert "reboots the controller, which ends the ELS job" in d.body
    assert FINAL.tag in d.body
    rig.do_install.assert_not_called()
    assert rig.ctrl._els_fsm.state == "stopped", "asking changes nothing"


def test_cancel_changes_nothing(rig):
    engaged_idle(rig.ctrl)
    install(rig)
    rig.dialogs[0].button("Cancel").press()
    assert not rig.dialogs[0].is_open
    assert rig.ctrl._els_fsm.state == "stopped"
    rig.do_install.assert_not_called()
    rig.clock.schedule_once.assert_not_called()


# ─── confirming: the shared path, then the controller's word ──────────────────

def test_confirming_disengages_via_the_shared_path_and_waits_for_enable_0(rig):
    engaged_idle(rig.ctrl)
    install(rig)
    with patch.object(ElsUiController, "disengage", autospec=True,
                      side_effect=ElsUiController.disengage) as shared:
        rig.dialogs[0].button("Disengage and Install").press()
    shared.assert_called_once_with(rig.ctrl)
    assert rig.ctrl._els_fsm.state == "disabled", "disengaged by the domain FSM"
    rig.do_install.assert_not_called()

    async def during(r):
        await asyncio.sleep(0.05)
        r.board.update_tick += 1              # a tick: the controller still holds it
        await asyncio.sleep(0.05)
        assert not r.do_install.called, "started while the board still said enable=1"
        # The next poll: Board.update refreshes the snapshot, then bumps the tick.
        r.ctrl._board.els_stop_values = {"enable": 0}
        r.board.update_tick += 1

    run_the_wait(rig, during)
    rig.do_install.assert_called_once_with(FINAL)
    assert "The controller reports the ELS job released." in rig.status


def test_a_snapshot_no_tick_has_refreshed_is_not_trusted(rig):
    """A 0 already sitting in the snapshot when the disengage ran was read
    BEFORE its writes -- it says nothing about the job just released. With no
    board tick after the disengage, the install must not start."""
    engaged_idle(rig.ctrl)
    install(rig)
    rig.dialogs[0].button("Disengage and Install").press()
    rig.ctrl._board.els_stop_values = {"enable": 0}
    run_the_wait(rig)
    rig.do_install.assert_not_called()
    assert any(line.startswith("Update not started") for line in rig.status)


@pytest.mark.parametrize("snapshot", [
    {"enable": 1},     # the controller keeps the job
    {},                # no snapshot: a fabricated 0 must not read as released
])
def test_the_controller_never_releasing_refuses_and_starts_nothing(rig, snapshot):
    engaged_idle(rig.ctrl)
    install(rig)
    rig.dialogs[0].button("Disengage and Install").press()

    async def during(r):
        for _ in range(5):
            r.ctrl._board.els_stop_values = dict(snapshot)
            r.board.update_tick += 1
            await asyncio.sleep(0.02)

    run_the_wait(rig, during)
    rig.do_install.assert_not_called()
    assert any(line.startswith("Update not started") and "did not confirm" in line
               for line in rig.status), rig.status
    assert rig.screen._releasing_els is False


def test_a_second_tap_while_waiting_starts_nothing(rig):
    engaged_idle(rig.ctrl)
    install(rig)
    rig.dialogs[0].button("Disengage and Install").press()
    install(rig)
    assert len(rig.dialogs) == 1
    rig.do_install.assert_not_called()


# ─── engaged and NOT allowed: no offer, say what to do ───────────────────────

def test_sync_armed_and_spindle_turning_offers_no_disengage(rig):
    engaged_idle(rig.ctrl, sync=True, spindle=True)
    install(rig)
    [d] = rig.dialogs
    assert d.title == "ELS job engaged"
    assert [b.text for b in d.buttons] == ["OK"]
    d.button("OK").press()
    assert rig.ctrl._els_fsm.state == "stopped"
    rig.do_install.assert_not_called()


def test_the_turning_spindle_is_named_as_the_blocker(rig):
    """Evan 2026-09-25: refusing while the spindle turns is right, but the
    message must name the actual blocker. Sync is on almost whenever advanced
    ELS is engaged, and with the spindle stopped disengage is allowed with
    sync on -- so "Turn Sync Enable off" pointed at the wrong thing."""
    engaged_idle(rig.ctrl, sync=True, spindle=True)
    install(rig)
    [d] = rig.dialogs
    assert "The spindle is turning. Stop the spindle." in d.body
    assert "Sync Enable" not in d.body


def test_sync_on_with_the_spindle_stopped_is_offered(rig):
    """The common case: sync on, spindle stopped. The offer is made and
    confirming disengages (the teardown clears sync)."""
    engaged_idle(rig.ctrl, sync=True, spindle=False)
    install(rig)
    [d] = rig.dialogs
    assert [b.text for b in d.buttons] == ["Cancel", "Disengage and Install"]
    d.button("Disengage and Install").press()
    assert rig.ctrl._els_fsm.state == "disabled"


def test_a_running_cycle_offers_no_disengage(rig):
    engaged_idle(rig.ctrl)
    rig.ctrl._ui_fsm.fsm.set_state("in_cycle.retracting")
    rig.ctrl._apply_policy()
    install(rig)
    [d] = rig.dialogs
    assert [b.text for b in d.buttons] == ["OK"]
    assert "Stop the cycle" in d.body
    d.button("OK").press()
    assert rig.ctrl._els_fsm.state == "stopped"
    rig.do_install.assert_not_called()


# ─── order with the pre-release question ─────────────────────────────────────

def test_the_pre_release_question_comes_first_and_disengages_nothing(rig):
    engaged_idle(rig.ctrl)
    install(rig, RC)
    [pre] = rig.dialogs
    assert pre.title == "Pre-release"
    pre.button("Install Anyway").press()
    assert rig.ctrl._els_fsm.state == "stopped", "nothing disengaged yet"
    assert [d.title for d in rig.dialogs] == ["Pre-release", "ELS job engaged"]
    rig.do_install.assert_not_called()


def test_cancelling_the_pre_release_question_leaves_the_job_engaged(rig):
    engaged_idle(rig.ctrl)
    install(rig, RC)
    rig.dialogs[0].button("Cancel").press()
    assert rig.ctrl._els_fsm.state == "stopped"
    assert len(rig.dialogs) == 1
    rig.do_install.assert_not_called()


# ─── the flasher's refusal reaches the status box ────────────────────────────

def test_a_refused_reboot_is_what_the_status_box_says(rig):
    """perform_install puts the session's refusal on the screen verbatim; the
    updater leads it with the refusal (tests/utils/test_updater.py). This is
    the last hop: the words the operator reads."""
    from reflex.utils.updater import FLASHER_REFUSED_ELS, UpdateRefused
    refusal = UpdateRefused(
        "The update did not start: the controller REFUSED to reboot into its "
        "bootloader because an ELS job is engaged. ...\n"
        f"flashing the controller failed (exit 1).\n{FLASHER_REFUSED_ELS}.")
    with patch.object(rig.screen, "_install_blocking", side_effect=refusal):
        asyncio.run(rig.screen.perform_install(FINAL))
    assert any(line.startswith("The update did not start") and FLASHER_REFUSED_ELS in line
               for line in rig.status), rig.status
    assert rig.screen.busy is False
