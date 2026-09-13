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
import importlib.metadata
import tempfile
import threading
from pathlib import Path

from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import (ListProperty, StringProperty, BooleanProperty,
                             NumericProperty)
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen

from reflex.utils import updater
from reflex.utils.devices import ELS_PROTOCOL_VERSION
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class UpdateScreen(Screen):
    releases = ListProperty([])
    selected_release = StringProperty("")
    current_release = StringProperty("v" + importlib.metadata.version("reflex"))
    enable_update_button = BooleanProperty(False)
    allow_experimental = BooleanProperty(False)
    busy = BooleanProperty(False)
    status = StringProperty("")
    protocol_version = NumericProperty(ELS_PROTOCOL_VERSION)

    def __init__(self, **kv):
        super().__init__(**kv)
        self._catalogue: dict[str, updater.Release] = {}
        self.status = ""

    def on_pre_enter(self, *args):
        """Fetch on entry rather than at construction.

        The old screen kicked off a GitHub request from ``__init__``, i.e. at
        app start, for a screen the operator may never open -- on a machine
        that is frequently on a workshop network with no route out. Nothing
        here is needed until the screen is looked at.
        """
        if not self.busy and not self.releases:
            self.schedule_refresh_releases()

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
        session = self._session()
        return session.list_releases(allow_prerelease=self.allow_experimental)

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

    def on_selected_release(self, instance, value):
        self.enable_update_button = bool(value) and value != self.current_release

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

    def install_release(self):
        release = self._catalogue.get(self.selected_release)
        if release is None:
            self.update_status("No release selected.")
            return
        if release.prerelease:
            self._confirm_prerelease(release)
        else:
            self._do_install(release)

    def _confirm_prerelease(self, release):
        """The one confirmation in this screen, and it is NOT the gate.

        This asks whether the operator meant to install a release candidate.
        The protocol check in :func:`reflex.utils.updater.verify_firmware_half`
        has no dialog and no override -- there is deliberately no "install
        anyway" for a mismatched pair, because the whole reason the feature
        was allowed to come back is that it cannot produce one.
        """
        content = BoxLayout(orientation="vertical", spacing=10, padding=10)
        content.add_widget(Label(
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
        btn_cancel = Button(text="Cancel", font_size=22)
        btn_confirm = Button(text="Install Anyway", font_size=22)
        buttons.add_widget(btn_cancel)
        buttons.add_widget(btn_confirm)
        content.add_widget(buttons)

        popup = Popup(title="Pre-release", content=content,
                      size_hint=(0.7, 0.5), auto_dismiss=False)
        btn_cancel.bind(on_release=popup.dismiss)
        btn_confirm.bind(on_release=lambda _: (popup.dismiss(),
                                               self._do_install(release)))
        popup.open()

    def _do_install(self, release):
        self.busy = True
        self.enable_update_button = False
        Clock.schedule_once(
            lambda dt: asyncio.ensure_future(self.perform_install(release)))

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
            await asyncio.get_running_loop().run_in_executor(
                None, self._install_blocking, release)
        except updater.UpdateRefused as e:
            self.update_status(str(e))
            log.error(f"update refused: {e}")
        except Exception as e:
            self.update_status(f"Update failed: {e}")
            log.exception("update failed")
        else:
            self.update_status("Update applied. Restarting.")
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
        session.run(
            release,
            pause_link=(lambda: self._on_kivy_thread(
                board.pause_polling, "release the serial port")) if board else None,
            resume_link=(lambda: self._on_kivy_thread(
                board.resume_polling, "reclaim the serial port")) if board else None,
        )
