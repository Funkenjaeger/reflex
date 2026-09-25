"""Software Update: apply a lockstep fw+ui release from the touchscreen.

REWIRED 2026-09-07, unwired since c7d1f3c (2026-08-30). What changed is not
this screen -- it is that ``fw/scripts/modbus-flash.py`` landed and is
hardware-verified, so the firmware half of a release can be applied over the
RS-485 link the UI already holds, in about thirteen seconds, with no ST-Link
and no power cycle. Until that existed, in-app update could only ever have been
a UI-only update, which breaks the version pairing the release model is FOR.

This module is deliberately thin. Everything that decides anything lives in
:mod:`reflex.utils.updater` -- the release filter, the install path, and above
all the protocol gate -- because that module has no Kivy in it and can be run
end to end in a test. What is here is: a screen, a status log, and the
threading that keeps the UI answering touches while a flash is in progress.

WHAT THE OPERATOR SEES WHILE THE LINK IS DOWN. The flasher owns the serial
port for the whole of the firmware half, so the DRO stops updating and the
board reads as disconnected. That is why ``busy`` exists: the screen says the
link is down on purpose, names the step in progress, warns against powering
off, and disables navigation away and the Install button for the duration.
"""

import asyncio
import tempfile
import threading
from pathlib import Path

from kivy.clock import Clock
from kivy.factory import Factory
from kivy.logger import Logger
from kivy.properties import (ListProperty, StringProperty, BooleanProperty,
                             NumericProperty)
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen

from reflex.components.widgets import facelift_chrome  # noqa: F401 -- defines <SetupButton>/<ThemedLabel>
from reflex.utils import release_version, updater
from reflex.utils.devices import ELS_PROTOCOL_VERSION
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

# How long "Disengage and Install" waits for the controller to report
# elsStop.enable == 0 before refusing to start. The board is polled at 30 Hz
# and the disengage writes are synchronous, so a healthy link answers within a
# tick or two; three seconds is patience for a link that is retrying, not for
# a controller that is holding the job -- that one is refused.
ELS_RELEASE_TIMEOUT_S = 3.0
ELS_RELEASE_POLL_S = 0.1


class UpdateScreen(Screen):
    releases = ListProperty([])
    selected_release = StringProperty("")
    # The tag spelling (v1.2.0-rc.7), so it reads like the list below it and
    # compares with it -- see reflex/utils/release_version.py.
    current_release = StringProperty(release_version.installed_tag())
    enable_update_button = BooleanProperty(False)
    allow_experimental = BooleanProperty(False)
    busy = BooleanProperty(False)
    status = StringProperty("")
    protocol_version = NumericProperty(ELS_PROTOCOL_VERSION)

    def __init__(self, **kv):
        super().__init__(**kv)
        self._catalogue: dict[str, updater.Release] = {}
        self.status = ""
        # True while a "Disengage and Install" waits for the controller to
        # report the ELS job released (see _await_firmware_release).
        self._releasing_els = False

    def on_pre_enter(self, *args):
        """Fetch on entry rather than at construction.

        The old screen kicked off a GitHub request from ``__init__``, i.e. at
        app start, for a screen the operator may never open -- on a machine
        that is frequently on a workshop network with no route out. Nothing
        here is needed until the screen is looked at.

        The pre-release toggle is restored first, from ``Device-0.yaml``
        (2026-09-19: it used to reset to off on every visit).
        """
        device = self._device()
        if device is not None:
            self.allow_experimental = bool(device.offer_prereleases)
        if not self.busy and not self._catalogue:
            self.schedule_refresh_releases()

    @staticmethod
    def _device():
        from reflex.app import MainApp
        app = MainApp.get_running_app()
        return getattr(app, "device", None)

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def update_status(self, line: str):
        """Append a status line. Safe to call from the worker thread."""
        Clock.schedule_once(lambda dt: self._append(line))

    def _append(self, line: str):
        self.status = self.status + line + "\n"

    # ------------------------------------------------------------------
    # release list
    # ------------------------------------------------------------------

    def schedule_refresh_releases(self):
        Clock.schedule_once(
            lambda dt: asyncio.ensure_future(self.refresh_releases()))

    async def refresh_releases(self):
        self.update_status("Fetching releases from GitHub.")
        try:
            found = await asyncio.get_running_loop().run_in_executor(
                None, self._fetch_releases)
        except Exception as e:
            self.update_status(f"Could not fetch releases: {e}")
            return
        self._catalogue = {r.tag: r for r in found}
        self._set_releases()

    def _fetch_releases(self):
        """Finals AND pre-releases, whatever the toggle says: the toggle then
        only filters (_set_releases). Until 2026-09-19 the fetch honoured the
        toggle, so turning it on showed nothing new until Refresh was tapped."""
        session = self._session()
        return session.list_release_catalogue()

    def _set_releases(self):
        """Rebuild the dropdown from the catalogue and the experimental flag.

        There is no longer a "dev (experimental)" entry. It tracked the dev
        BRANCH, for which no firmware image is published anywhere -- so it
        could only ever have installed the UI half, which is the UI-only
        update the fw+ui decision rejected. ``allow_experimental`` now means
        pre-release TAGS, which the same lockstep workflow builds and which
        carry both halves.
        """
        tags = [tag for tag, r in self._catalogue.items()
                if self.allow_experimental or not r.prerelease]
        self.releases = tags
        if self.selected_release not in tags:
            self.selected_release = tags[0] if tags else ""

    def on_allow_experimental(self, instance, value):
        self._set_releases()
        device = self._device()
        if device is not None and device.offer_prereleases != value:
            device.offer_prereleases = value

    def on_selected_release(self, instance, value):
        # Canonically, not as strings: until 2026-09-25 current_release was the
        # package spelling (v1.2.0rc7) and never equalled a pre-release tag
        # (v1.2.0-rc.7), so the installed pre-release was always installable.
        self.enable_update_button = (bool(value) and
                                     not release_version.same_release(value, self.current_release))

    # ------------------------------------------------------------------
    # install
    # ------------------------------------------------------------------

    def _session(self) -> updater.UpdateSession:
        from reflex.app import MainApp
        app = MainApp.get_running_app()
        board = getattr(app, "board", None)
        port = getattr(getattr(board, "connection_manager", None),
                       "serial_device", "/dev/serial0")
        return updater.UpdateSession(
            checkout=updater.resolve_checkout(),
            port=port,
            current_protocol=ELS_PROTOCOL_VERSION,
            workdir=Path(tempfile.gettempdir()) / "reflex-update",
            emit=self.update_status,
        )

    @staticmethod
    def _els_controller():
        """The app's ONE ElsUiController (app.els_uic), or None without an app.

        Never a second one: the controller owns the domain FSM and the elsStop
        HAL, and a second instance would be a second writer to the same
        registers with its own idea of whether ELS is engaged."""
        from reflex.app import MainApp
        app = MainApp.get_running_app()
        return getattr(app, "els_uic", None)

    @staticmethod
    def _board():
        from reflex.app import MainApp
        return getattr(MainApp.get_running_app(), "board", None)

    def install_release(self):
        if self._releasing_els:
            # A disengage is waiting on the controller's word; a second tap
            # must not start a second install behind it.
            return
        release = self._catalogue.get(self.selected_release)
        if release is None:
            self.update_status("No release selected.")
            return
        if release.prerelease:
            self._confirm_prerelease(release)
        else:
            self._install_unless_engaged(release)

    def _install_unless_engaged(self, release):
        """Install, unless an ELS job is engaged -- then ask first.

        WHY (2026-09-25 08:32, the lathe): the firmware refuses to reboot into
        its bootloader while elsStop.enable is set (Ramps.c
        elsBootCommandTick), so an update started over an engaged job fails
        at the first step. The operator had left the job engaged-idle
        overnight, and the only way out was to back all the way out to the
        home screen, disengage, and come back. Now the screen offers it.

        ORDER: the pre-release question (if any) comes FIRST and this one
        second, because this one has a side effect. "Disengage and Install"
        disengages and then starts the install with no further question, so
        answering it is always the last thing the operator does; asked the
        other way round, cancelling the pre-release dialog would have
        disengaged the job for nothing. Asked here, after the pre-release
        dialog closes, so it sees the machine as it is now.
        """
        uic = self._els_controller()
        if uic is None or not uic.engaged:
            self._do_install(release)
            return
        self._confirm_disengage(release, uic)

    def _confirm_disengage(self, release, uic):
        """The ELS dialog. Offers "Disengage and Install" only when the SAME
        rules the ADV bar's Disengage button obeys would allow it
        (ElsUiController.disengage_refusal); otherwise it says what to do
        instead and offers only OK.

        THE BLOCKER, NAMED FOR THIS SCREEN (Evan, 2026-09-25). The shared
        sync refusal reads "Turn Sync Enable off before disengaging" -- right
        for the ADV bar mid-cut, where Sync Enable is the escape hatch, but
        wrong here: sync is on almost whenever advanced ELS is engaged, and
        with the spindle STOPPED disengage is allowed with sync on (operator
        decision 2026-08-17). What actually blocks an update is the turning
        spindle, and refusing on it is correct, so the dialog says that."""
        from reflex.fsms.ui_controller import DISENGAGE_REFUSED_SYNC
        refusal = uic.disengage_refusal()
        lead = (f"An ELS job is engaged.\n\n"
                f"Installing {release.tag} reboots the controller, which ends "
                f"the ELS job, so it has to be disengaged first.")
        if refusal is None:
            text = f"{lead}\n\nDisengage ELS and install {release.tag}?"
        else:
            if refusal == DISENGAGE_REFUSED_SYNC:
                reason = "The spindle is turning. Stop the spindle"
            else:
                reason = refusal
            text = (f"{lead} It cannot be disengaged right now:\n\n"
                    f"{reason}.\n\n"
                    f"Then tap Install Selected Release again.")
        content = BoxLayout(orientation="vertical", spacing=10, padding=10)
        content.add_widget(Factory.ThemedLabel(
            text=text, halign="left", valign="top", text_size=(None, None)))

        buttons = BoxLayout(orientation="horizontal", spacing=10,
                            size_hint_y=None, height=60)
        popup = Popup(title="ELS job engaged", content=content,
                      size_hint=(0.7, 0.5), auto_dismiss=False)
        if refusal is None:
            btn_cancel = Factory.SetupButton(text="Cancel", font_size=22)
            btn_confirm = Factory.SetupButton(text="Disengage and Install",
                                              font_size=22)
            buttons.add_widget(btn_cancel)
            buttons.add_widget(btn_confirm)
            btn_cancel.bind(on_release=popup.dismiss)
            btn_confirm.bind(on_release=lambda _: (
                popup.dismiss(), self._disengage_then_install(release, uic)))
        else:
            btn_ok = Factory.SetupButton(text="OK", font_size=22)
            buttons.add_widget(btn_ok)
            btn_ok.bind(on_release=popup.dismiss)
        content.add_widget(buttons)
        popup.open()

    def _disengage_then_install(self, release, uic):
        """Disengage through the ADV bar's own path, then install only once
        the CONTROLLER says the job is released.

        uic.disengage() re-applies the rules (the machine may have changed
        while the dialog was up) and, when they allow it, disables the domain
        FSM -- whose teardown writes sync off, elsStop.enable = 0 and a feed
        stop over Modbus. Those are writes; the flasher's reboot request is
        refused if the BOARD still holds enable = 1, and once the install
        starts this UI pauses polling and hands the port away, so there is no
        second chance to look. Hence the wait on firmware_els_enable().
        """
        refusal = uic.disengage()
        if refusal is not None:
            self.update_status(
                f"Update not started: ELS was not disengaged. {refusal}.")
            return
        self.update_status("ELS disengaged. Waiting for the controller to "
                           "confirm the ELS job is released.")
        self._releasing_els = True
        self.enable_update_button = False
        Clock.schedule_once(lambda dt: asyncio.ensure_future(
            self._install_once_released(release, uic)))

    async def _install_once_released(self, release, uic):
        try:
            released = await self._await_firmware_release(uic)
        finally:
            self._releasing_els = False
        if not released:
            self.update_status(
                f"Update not started: the controller did not confirm the ELS "
                f"job released within {ELS_RELEASE_TIMEOUT_S:.0f} s of "
                f"disengaging, so it would refuse to reboot. ELS is disengaged "
                f"here; the controller firmware and this UI are unchanged. "
                f"Check the controller connection and try again.")
            log.error("update not started: firmware elsStop.enable not seen "
                      "cleared after disengage")
            self.on_selected_release(self, self.selected_release)
            return
        self.update_status("The controller reports the ELS job released.")
        self._do_install(release)

    async def _await_firmware_release(self, uic, timeout=None, poll=None) -> bool:
        """True once a board tick AFTER the disengage has read elsStop.enable
        as 0 from the controller; False if that has not happened by `timeout`.

        "After the disengage" is the tick count, not the clock: the snapshot
        the controller reads is refreshed once per board tick, and one taken
        before the teardown's writes would still say 1 -- or, worse, a stale
        0 from some earlier moment would say "released" about a job that is
        not. Board.update refreshes the snapshot BEFORE bumping update_tick,
        on the Kivy thread the disengage also ran on, so the first tick
        counted here read the registers after the writes. A fabricated read
        (no link, failed refresh) is None from firmware_els_enable and never
        counts as released.
        """
        timeout = ELS_RELEASE_TIMEOUT_S if timeout is None else timeout
        poll = ELS_RELEASE_POLL_S if poll is None else poll
        board = self._board()
        if board is None:
            return False
        ticks = [0]

        def _count(*_):
            ticks[0] += 1

        board.bind(update_tick=_count)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while True:
                if ticks[0] >= 1 and uic.firmware_els_enable() is False:
                    return True
                if loop.time() >= deadline:
                    return False
                await asyncio.sleep(poll)
        finally:
            board.unbind(update_tick=_count)

    def _confirm_prerelease(self, release):
        """One of the screen's two confirmations, and NEITHER is the gate.

        This asks whether the operator meant to install a release candidate;
        the other (_confirm_disengage, 2026-09-25) asks to disengage an
        engaged ELS job, which the controller will not reboot under.
        The protocol check in :func:`reflex.utils.updater.verify_firmware_half`
        has no dialog and no override -- there is deliberately no "install
        anyway" for a mismatched pair, because the whole reason the feature
        was allowed to come back is that it cannot produce one.
        """
        content = BoxLayout(orientation="vertical", spacing=10, padding=10)
        # Themed: a stock Label is white, 1.38:1 on the light popup.
        content.add_widget(Factory.ThemedLabel(
            text=(
                f"{release.tag} is a pre-release.\n\n"
                "- It may be unstable or incomplete\n"
                "- Both the controller firmware and this UI will be updated\n"
                "- The machine must not be powered off during the update"
            ),
            halign="left", valign="top", text_size=(None, None),
        ))

        buttons = BoxLayout(orientation="horizontal", spacing=10,
                            size_hint_y=None, height=60)
        btn_cancel = Factory.SetupButton(text="Cancel", font_size=22)
        btn_confirm = Factory.SetupButton(text="Install Anyway", font_size=22)
        buttons.add_widget(btn_cancel)
        buttons.add_widget(btn_confirm)
        content.add_widget(buttons)

        popup = Popup(title="Pre-release", content=content,
                      size_hint=(0.7, 0.5), auto_dismiss=False)
        btn_cancel.bind(on_release=popup.dismiss)
        btn_confirm.bind(on_release=lambda _: (popup.dismiss(),
                                               self._install_unless_engaged(release)))
        popup.open()

    def _do_install(self, release):
        self.busy = True
        self.enable_update_button = False
        # After the warning line has been laid out (next frame), bring the
        # status box into view: during an update it is what the operator
        # watches, and at rest it sits mostly below the fold.
        Clock.schedule_once(self._scroll_to_status, 0.1)
        Clock.schedule_once(
            lambda dt: asyncio.ensure_future(self.perform_install(release)))

    def _scroll_to_status(self, *_):
        """The status box is the last thing on the screen, so the bottom of
        the scroller shows all of it, with the warning line just above."""
        scroller = getattr(self, "ids", {}).get("scroller")
        if scroller is not None:
            scroller.scroll_y = 0

    async def perform_install(self, release):
        """Run the update off the Kivy thread so the screen keeps drawing.

        A flash is ~13 s of blocking serial I/O and ``uv sync`` on a Pi can be
        minutes; both used to be run with ``Popen`` plus a one-second
        ``asyncio.sleep`` poll, which meant status lines arrived a second late
        and only between commands. An executor gets the real output as the
        tools print it.
        """
        self.update_status(
            f"Updating {self.current_release} -> {release.tag}. "
            "Both the controller firmware and this UI will be replaced.")
        try:
            restarting = await asyncio.get_running_loop().run_in_executor(
                None, self._install_blocking, release)
        except updater.UpdateRefused as e:
            self.update_status(str(e))
            log.error(f"update refused: {e}")
        except Exception as e:
            self.update_status(f"Update failed: {e}")
            log.exception("update failed")
        else:
            if restarting:
                self.update_status("Update applied. Restarting.")
            else:
                self.update_status(
                    f"Update applied, but the UI did not restart. Tap Exit "
                    f"Application to start {release.tag}.")
        finally:
            self.busy = False
            self.on_selected_release(self, self.selected_release)

    def _on_kivy_thread(self, fn, what: str):
        """Run ``fn`` on the Kivy thread and wait for it.

        ``pause_polling``/``resume_polling`` cancel and create Clock events and
        set Kivy properties. Calling those from the update worker would be a
        data race against the main loop -- and the specific thing being raced
        is the object that owns the serial port, so the failure would look like
        a flaky flash rather than like threading. ``Clock.schedule_once`` is
        the thread-safe entry point; the Event turns it back into a call this
        worker can wait on.
        """
        done = threading.Event()
        box = {}

        def _run(dt):
            try:
                fn()
            except BaseException as e:      # noqa: BLE001 - re-raised below
                box["error"] = e
            finally:
                done.set()

        Clock.schedule_once(_run)
        if not done.wait(timeout=30):
            raise updater.UpdateRefused(
                f"Timed out waiting for the UI to {what}; refusing to "
                f"continue. Nothing has been changed.")
        if "error" in box:
            raise box["error"]

    def _install_blocking(self, release):
        from reflex.app import MainApp
        board = getattr(MainApp.get_running_app(), "board", None)
        session = self._session()
        return session.run(
            release,
            pause_link=(lambda: self._on_kivy_thread(
                board.pause_polling, "release the serial port")) if board else None,
            resume_link=(lambda: self._on_kivy_thread(
                board.resume_polling, "reclaim the serial port")) if board else None,
        )
