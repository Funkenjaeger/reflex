"""The Setup screen, plus the USB export/import of the commissioning bundle.

WHY THIS SCREEN, NOT A NEW ONE. Export/import act on the whole machine (every
config file, see ``commissioning_bundle``), not one subsystem, and Setup is
already the screen that hosts machine-wide actions (its buttons *navigate* to
the subsystem screens; nothing here is itself a subsystem). A dedicated screen
would need its own nav entry for two buttons and a status line.

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
"""
from pathlib import Path

import yaml
from kivy.logger import Logger
from kivy.properties import StringProperty
from kivy.uix.screenmanager import Screen

from reflex.components.popups.custom_popup import CustomPopup
from reflex.utils import commissioning_bundle, usb
from reflex.utils.kv_loader import load_kv
from reflex.utils.paths import config_dir

log = Logger.getChild(__name__)
load_kv(__file__)


class SetupScreen(Screen):
    #: The last export/import outcome, shown in ``setup_screen.kv``. Mirrors
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

    # ── export ───────────────────────────────────────────────────────────

    def export_to_usb(self):
        """Build the whole-machine bundle and write it to the first stick
        found, or show the refusal message when there isn't one.

        ``fw_rev`` is not passed to :func:`commissioning_bundle.build` here,
        the same as the existing ``snapshot_if_changed("startup")`` call in
        ``app.py`` -- reaching the board for its firmware revision is a HAL
        concern this screen has no seam for today, and a bundle built with
        ``meta.fw: null`` is still a valid bundle (see ``build``'s docstring).
        """
        doc = commissioning_bundle.build()
        text = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
        filename = usb.bundle_filename(
            doc["meta"]["hostname"], commissioning_bundle._file_stamp())
        try:
            path = usb.export_bundle(text, filename)
        except usb.NoRemovableMedia:
            self._status("No USB stick found -- insert one and try again.")
            return
        except OSError as e:
            log.error(f"setup screen: USB export failed ({e})")
            self._status(f"Export failed: {e}")
            return
        self._status(f"Exported commissioning bundle to {path}")

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
            log.error(f"setup screen: cannot read {path} ({e})")
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
            f"Machine: {meta.get('hostname', 'unknown')}\n"
            f"Captured: {meta.get('ts', 'unknown')}\n"
            f"Firmware: {meta.get('fw', 'unknown')}\n\n"
            f"Apply this bundle from {path.name}?\n"
            f"This overwrites the current commissioning configuration."
        )
        self.import_popup = CustomPopup(
            title="Import commissioning bundle",
            message=message,
            button_text="Apply",
            cancel_text="Cancel",
            confirm_callback=self._apply_pending_import,
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

    # ── shared ───────────────────────────────────────────────────────────

    def _status(self, message: str):
        log.info(f"setup screen: {message}")
        self.status_text = message
