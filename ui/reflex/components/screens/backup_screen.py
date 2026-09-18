"""The Backup screen: USB export/import and the opt-in gist sync of the
commissioning bundle.

WHY ITS OWN SCREEN (2026-09-17). These controls first lived on Setup itself,
on the argument that a dedicated screen was a nav entry for two buttons. By the
time the gist sync joined them it was seven controls, a device code and a
status line, and on the lathe's 1024x600 they overflowed: Import from USB
spilled out of Setup's three-row grid underneath the gist row, and the export
status line ran off the screen. Evan's call on seeing it: backup and restore do
not belong at the top level of the Setup menu. Setup now carries one Backup
button, and everything here has room. previews/preview_setup_screen.py renders
both screens at 1024x600 and asserts nothing overlaps or clips.

THE SEAM. All the real work lives in ``commissioning_bundle`` (build the
document, apply it back) and ``usb`` (find the stick, read/write the file) --
both plain functions with no Kivy dependency, importable and testable without
a widget tree. This class only wires button presses to them and turns their
results into what the operator sees: a status line for a one-line outcome
(mirroring ``NetworkScreen.status_text``, the existing pattern for "the last
thing this screen's actions did"), and a confirm dialog before an import
actually writes anything.

WHAT THE IMPORT DIALOG SHOWS. The bundle's ``meta`` (machine name, capture
date, firmware revision) BEFORE applying it -- an operator who plugs in the
wrong stick, or an old export, sees whose configuration they are about to
overwrite the current one with. There is no way back from a completed apply
that doesn't involve re-provisioning, so the confirm step is not decorative.

THE GIST HALF IS THE SAME SHAPE. The opt-in GitHub sync
(:mod:`reflex.utils.gist_sync`) is a second transport for the same document, so
it gets the same treatment: all its logic lives in that module, and this screen
only wires buttons to it and turns results into ``status_text``. In particular
a RESTORE FROM GIST ends in :meth:`_open_import_confirm` and
:meth:`_apply_pending_import` -- the SAME confirm dialog and the same
``commissioning_bundle.apply`` call the USB import uses. There is one importer
here, with two ways of getting the document to it.

WHY TWO THREADING SEAMS. The device flow polls GitHub for up to fifteen
minutes; a sync is a round trip over whatever the shop wifi is doing. Neither
can happen on the Kivy thread. :meth:`_run_async` puts the work on a daemon
thread and :meth:`_dispatch_to_ui` brings each result back through
``Clock.schedule_once``, which is the only safe way to touch a Kivy property
from off-thread. They are separate one-line methods precisely so a test can
replace both with "just call it" and drive the whole flow deterministically.
"""
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import yaml
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.screenmanager import Screen

from reflex.components.popups.custom_popup import CustomPopup
from reflex.components.screens import setup_screen  # noqa: F401 -- defines <SetupButton>, used by backup_screen.kv
from reflex.utils import commissioning_bundle, gist_sync, updater, usb
from reflex.utils.kv_loader import load_kv
from reflex.utils.paths import config_dir

log = Logger.getChild(__name__)
load_kv(__file__)


class BackupScreen(Screen):
    #: The last export/import outcome, shown in ``backup_screen.kv``. Mirrors
    #: ``NetworkScreen.status_text`` -- the existing pattern for surfacing a
    #: transient, operator-visible result of a button press.
    status_text = StringProperty("")

    #: The confirm/cancel dialog for an in-progress import, held so it is not
    #: garbage-collected before the operator answers it, and so a test can
    #: inspect it without a real widget tree. ``None`` otherwise.
    import_popup = None

    #: The parsed document (and its source path) awaiting the operator's
    #: answer to ``import_popup``. Cleared as soon as that dialog resolves,
    #: either way.
    _pending_doc = None
    _pending_path = None

    #: Mirrors ``gist_sync.is_enabled()`` for the kv toggle. NOT the source of
    #: truth -- the marker file is (``gist_sync.enabled_path``), so a restart
    #: comes back in the state the operator left it in.
    gist_enabled = BooleanProperty(False)

    #: The device-flow code and URL, shown as LARGE TEXT while a flow is in
    #: progress and blank otherwise. THERE IS NO QR CODE: ``pyproject.toml``
    #: carries no QR library and this feature added no dependency, so the
    #: operator reads the eight characters and the short URL off the screen.
    gist_code_text = StringProperty("")

    #: Where to revoke the grant, shown beside the toggle whenever the feature
    #: is configured. A grant the operator cannot find is one they cannot undo.
    gist_revoke_text = StringProperty(gist_sync.REVOKE_URL)

    #: Mirrors ``gist_sync.is_configured()``: False disables the gist buttons.
    gist_configured = BooleanProperty(False)

    #: The small line under the gist buttons: the revoke hint when the feature
    #: is configured, the "not configured in this build" notice when it is not.
    #: That notice used to go in ``gist_code_text`` at 1.8x and overflowed.
    gist_note_text = StringProperty("")

    #: The restore picker, held for the same reason as ``import_popup``.
    restore_popup = None

    #: Candidates from the last :meth:`restore_from_gist`, newest first. A list
    #: of ``gist_sync.GistRef``; empty when nothing was found. A class-level
    #: default that is only ever REPLACED per instance, never mutated in place.
    restore_choices: list = []

    #: True while a device flow is polling, so a second press of the toggle
    #: does not start a second flow against the first one's code.
    _flow_running = False

    # ── export ───────────────────────────────────────────────────────────

    def export_to_usb(self):
        """Build the whole-machine bundle and write it to the first stick
        found, or show the refusal message when there isn't one.

        ``meta.fw`` is the revision from the flash manifest (see
        :func:`_recorded_fw_rev`), not a live read of the board: the identity
        window is only readable through ``modbus-flash.py``, which needs the
        serial port this running UI owns. It was ``null`` on every export until
        2026-09-17, which the import dialog then showed as "Firmware: None".
        """
        doc = commissioning_bundle.build(fw_rev=_recorded_fw_rev())
        text = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
        filename = usb.bundle_filename(
            doc["meta"]["hostname"], commissioning_bundle._file_stamp())
        try:
            path = usb.export_bundle(text, filename)
        except usb.NoRemovableMedia:
            self._status("No USB stick found -- insert one and try again.")
            return
        except OSError as e:
            log.error(f"backup screen: USB export failed ({e})")
            self._status(f"Export failed: {e}")
            return
        # The file name, not the mount path: /media/sda1-3/ tells the operator
        # nothing, and the full path ran off the lathe's screen.
        self._status(f"Exported {Path(path).name} to the USB stick.")

    # ── import ───────────────────────────────────────────────────────────

    def import_from_usb(self):
        """Find a bundle on a stick and open the confirm dialog for the
        newest one, or show the refusal message when none is found."""
        bundles = usb.find_bundles()
        if not bundles:
            self._status("No commissioning bundle found on a USB stick.")
            return

        path = bundles[-1]  # newest by name; see find_bundles' docstring
        try:
            doc = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError) as e:
            log.error(f"backup screen: cannot read {path} ({e})")
            self._status(f"Cannot read {path.name}: {e}")
            return
        if not isinstance(doc, dict):
            self._status(f"{path.name} is not a commissioning bundle.")
            return

        self._pending_doc, self._pending_path = doc, path
        self._open_import_confirm(doc, path)

    def _open_import_confirm(self, doc: dict, path: Path):
        meta = doc.get("meta") or {}
        message = (
            f"Machine: {meta.get('hostname') or 'unknown'}\n"
            f"Captured: {_local_time(meta.get('ts'))}\n"
            f"Firmware: {meta.get('fw') or 'not recorded'}\n\n"
            f"Apply this bundle from {path.name}?\n"
            f"This overwrites the current commissioning configuration."
        )
        self.import_popup = CustomPopup(
            title="Import commissioning bundle",
            message=message,
            button_text="Apply",
            cancel_text="Cancel",
            confirm_callback=self._apply_pending_import,
            popup_size_hint=[0.8, 0.8],
        )
        self.import_popup.open()

    def _apply_pending_import(self):
        doc, path = self._pending_doc, self._pending_path
        self._pending_doc, self._pending_path = None, None
        if doc is None:
            return  # dialog fired with nothing pending; nothing to do

        report = commissioning_bundle.apply(doc, config_dir())
        if not report.ok:
            self._status(f"Import refused: {report.reason}")
            return
        message = f"Imported {len(report.written)} file(s) from {path.name}"
        if report.skipped:
            message += f" ({len(report.skipped)} skipped -- see log)"
        self._status(message)

    # ── gist sync: threading seams ───────────────────────────────────────

    def _run_async(self, work):
        """Run ``work()`` off the Kivy thread. Replaced in tests by a call.

        A daemon thread: if the app is closing, an in-flight device poll is not
        a reason to keep the process alive, and nothing it would have done
        matters more than the shutdown.
        """
        threading.Thread(target=work, daemon=True, name="gist-sync").start()

    def _dispatch_to_ui(self, work):
        """Run ``work()`` ON the Kivy thread. Replaced in tests by a call.

        Every status/property write from a worker goes through here. Touching a
        Kivy property from another thread is the classic way to get a graphics
        crash that reproduces once a week on the lathe and never in CI.
        """
        Clock.schedule_once(lambda _dt: work(), 0)

    def _post_status(self, message: str):
        self._dispatch_to_ui(lambda: self._status(message))

    # ── gist sync: the toggle ────────────────────────────────────────────

    def on_pre_enter(self, *args):
        """Re-read the opt-in marker every time the screen is shown, so the
        toggle reflects the file rather than whatever this instance last saw."""
        self.refresh_gist_state()

    def refresh_gist_state(self):
        self.gist_configured = bool(gist_sync.is_configured())
        self.gist_enabled = self.gist_configured and bool(gist_sync.is_enabled())
        self.gist_note_text = (
            f"Revoke access at {self.gist_revoke_text}" if self.gist_configured
            else gist_sync.NOT_CONFIGURED_MESSAGE)

    def toggle_gist_sync(self, enabled: bool):
        """The "Sync to GitHub gist" switch.

        ON starts a device flow (unless a token is already stored, in which
        case the grant is still good and there is nothing to authorize). OFF
        forgets the local token and drops the marker -- it CANNOT revoke the
        grant, only the operator can, at :data:`gist_sync.REVOKE_URL`, which is
        why the message says so.
        """
        if not gist_sync.is_configured():
            self.gist_enabled = False
            self._status(gist_sync.NOT_CONFIGURED_MESSAGE)
            return

        if not enabled:
            gist_sync.set_enabled(False)
            gist_sync.forget_token()
            self.gist_enabled = False
            self.gist_code_text = ""
            self._status("Gist sync off. Revoke the GitHub grant at "
                         f"{gist_sync.REVOKE_URL}")
            return

        if gist_sync.load_token():
            self._enable_sync_with_token()
            return
        self.start_device_flow()

    def _enable_sync_with_token(self):
        """Everything "on" means once a token exists: remember the opt-in, hook
        the ledger so a future change syncs itself, and push once now so the
        operator sees a result instead of waiting for their next calibration."""
        gist_sync.set_enabled(True)
        gist_sync.install_ledger_hook()
        self.gist_enabled = True
        self.gist_code_text = ""
        self.sync_now()

    def start_device_flow(self):
        """Show a user code, then poll until GitHub says yes, no, or expired.

        The whole flow runs on the worker thread; only the two ``_post_*``
        callbacks come back. Guarded by ``_flow_running`` so a double tap on
        the toggle cannot start a second flow whose polls would race the first.
        """
        if self._flow_running:
            return
        self._flow_running = True
        self.gist_code_text = "Contacting GitHub..."

        def work():
            try:
                gist_sync.authorize(sleep=time.sleep, on_code=self._post_code)
            except gist_sync.GistSyncError as e:
                self._post_code_cleared()
                self._post_status(str(e))
                return
            except OSError as e:
                log.error(f"backup screen: device flow transport failed ({e})")
                self._post_code_cleared()
                self._post_status("Could not reach GitHub. Gist sync stays off.")
                return
            finally:
                self._flow_running = False
            self._dispatch_to_ui(self._enable_sync_with_token)

        self._run_async(work)

    def _post_code(self, code):
        """Put the device code on screen. LARGE TEXT, no QR -- see the class
        docstring for ``gist_code_text``."""
        def show():
            self.gist_code_text = (
                f"{code.user_code}\n"
                f"Enter this at {code.verification_uri}")
        self._dispatch_to_ui(show)

    def _post_code_cleared(self):
        self._dispatch_to_ui(lambda: setattr(self, "gist_code_text", ""))

    def open_revoke_page(self):
        """Best effort only. The lathe has no browser; this is for a developer
        running the UI on a desktop, and a failure is a log line."""
        try:
            webbrowser.open(gist_sync.REVOKE_URL)
        except Exception as e:
            log.warning(f"backup screen: cannot open {gist_sync.REVOKE_URL} ({e})")

    # ── gist sync: push ──────────────────────────────────────────────────

    def sync_now(self):
        """The "Sync now" button, and the tail of turning the toggle on.

        ``gist_sync.sync_now`` never raises, so there is nothing to catch here;
        a ``None`` return is a failure that has already been logged and will be
        retried at the next commissioning change.
        """
        def work():
            gist_id = gist_sync.sync_now()
            if gist_id:
                self._post_status(f"Synced to gist {gist_id}")
            else:
                self._post_status(
                    "Gist sync failed -- will retry at the next change.")

        self._run_async(work)

    # ── gist sync: restore (the SAME importer as USB) ────────────────────

    def restore_from_gist(self):
        """List this account's reflex bundles and let the operator pick one.

        No machine-id filter: restoring onto a REPLACEMENT card means reading
        the dead machine's gist, and the new card's ``/etc/machine-id`` is a
        different string by definition. The description (which carries the old
        machine-id) is shown so they can tell them apart.
        """
        if not gist_sync.is_configured():
            self._status(gist_sync.NOT_CONFIGURED_MESSAGE)
            return

        def work():
            try:
                refs = gist_sync.list_machine_gists()
            except gist_sync.GistSyncError as e:
                self._post_status(str(e))
                return
            except OSError as e:
                log.error(f"backup screen: gist list failed ({e})")
                self._post_status("Could not reach GitHub.")
                return
            self._dispatch_to_ui(lambda: self._offer_restore(refs))

        self._run_async(work)

    def _offer_restore(self, refs):
        self.restore_choices = list(refs)
        if not self.restore_choices:
            self._status("No commissioning bundles found in your gists.")
            return
        if len(self.restore_choices) == 1:
            self.select_restore_gist(self.restore_choices[0].id)
            return
        self._open_restore_picker(self.restore_choices)

    def _open_restore_picker(self, refs):
        """One button per candidate, newest first. Built in Python rather than
        in kv because the rows are data, not layout."""
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.modalview import ModalView

        content = BoxLayout(orientation="vertical", padding=10, spacing=6)
        view = ModalView(size_hint=(0.8, 0.7))
        for ref in refs:
            label = f"{ref.machine_id}  {ref.updated_at}".strip()
            content.add_widget(Button(
                text=label,
                on_release=lambda _b, gid=ref.id: (
                    view.dismiss(), self.select_restore_gist(gid))))
        view.add_widget(content)
        self.restore_popup = view
        view.open()

    def select_restore_gist(self, gist_id: str):
        """Fetch one gist and hand the parsed document to the SHARED confirm
        dialog -- :meth:`_open_import_confirm`, the same one the USB import
        opens, which resolves into the same :meth:`_apply_pending_import` and
        the same ``commissioning_bundle.apply``. This method deliberately does
        no applying of its own; a second importer is how the two paths drift."""
        def work():
            try:
                doc = gist_sync.fetch_bundle(gist_id)
            except gist_sync.GistSyncError as e:
                self._post_status(str(e))
                return
            except OSError as e:
                log.error(f"backup screen: gist fetch failed ({e})")
                self._post_status("Could not reach GitHub.")
                return

            def offer():
                path = Path(f"gist {gist_id}")
                self._pending_doc, self._pending_path = doc, path
                self._open_import_confirm(doc, path)

            self._dispatch_to_ui(offer)

        self._run_async(work)

    # ── shared ───────────────────────────────────────────────────────────

    def _status(self, message: str):
        log.info(f"backup screen: {message}")
        self.status_text = message


def _local_time(ts) -> str:
    """``meta.ts`` as the operator's wall-clock time.

    The bundle stores UTC ISO-8601 (``2026-09-18T00:39:09+00:00``) and the
    dialog used to print it raw, so a capture made at 20:39 in the shop read
    as "the 18th, 00:39" -- a time that looked like it belonged to some other
    machine. Anything unparseable is shown as-is rather than hidden.
    """
    if not ts:
        return "unknown"
    try:
        when = datetime.fromisoformat(str(ts))
    except ValueError:
        return str(ts)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone().strftime("%Y-%m-%d %H:%M")


def _recorded_fw_rev() -> str | None:
    """The firmware revision the flash manifest last recorded, or ``None``.

    Never raises: a missing manifest (a dev desktop, a card nobody flashed
    from) makes an export say "not recorded", it does not stop the export.
    """
    try:
        manifest = updater.manifest_path_for(updater.resolve_checkout())
        return updater.last_flashed_rev(manifest)
    except Exception as e:
        log.info(f"backup screen: no firmware revision for the bundle ({e})")
        return None
