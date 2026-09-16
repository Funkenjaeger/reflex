"""SetupScreen's USB export/import buttons.

Driven the same way ``test_update_screen.py`` and
``test_network_screen_nmcli.py`` drive their screens: ``apply_class_lang_rules``
stubbed so construction never touches the real kv tree (this screen has no ids
its ``__init__`` reaches for, so nothing extra needs stubbing), and every real
dependency (``commissioning_bundle``, ``usb``, ``CustomPopup``) replaced at the
module object ``setup_screen`` imported, which is the one seam all of it goes
through.
"""
from unittest.mock import MagicMock, patch

import pytest

import reflex.components.screens.setup_screen as ss


@pytest.fixture
def screen():
    with patch.object(ss.SetupScreen, "apply_class_lang_rules"):
        return ss.SetupScreen()


@pytest.fixture
def bundle(monkeypatch):
    fake = MagicMock(name="commissioning_bundle")
    fake.build.return_value = {
        "meta": {"schema": 1, "ts": "2026-09-13T19:04:11+00:00",
                  "hostname": "elspi", "fw": "1.2.0"},
        "Axis-0": {"axis_name": "C"},
    }
    fake._file_stamp.return_value = "20260913T190411Z"
    monkeypatch.setattr(ss, "commissioning_bundle", fake)
    return fake


@pytest.fixture
def fake_usb(monkeypatch):
    fake = MagicMock(name="usb")
    # Real exception classes, not MagicMock attributes, so `except
    # usb.NoRemovableMedia` in the code under test still works.
    fake.NoRemovableMedia = ss.usb.NoRemovableMedia
    fake.bundle_filename.return_value = "reflex-commissioning-elspi-20260913T190411Z.yaml"
    monkeypatch.setattr(ss, "usb", fake)
    return fake


# ── export ───────────────────────────────────────────────────────────────

def test_export_success_reports_the_written_path(screen, bundle, fake_usb, tmp_path):
    target = tmp_path / "reflex-commissioning-elspi-20260913T190411Z.yaml"
    fake_usb.export_bundle.return_value = target

    screen.export_to_usb()

    assert str(target) in screen.status_text
    bundle.build.assert_called_once_with()


def test_export_with_no_stick_shows_the_refusal_message(screen, bundle, fake_usb):
    fake_usb.export_bundle.side_effect = fake_usb.NoRemovableMedia("no removable media")

    screen.export_to_usb()

    assert "No USB stick found" in screen.status_text


def test_export_os_error_is_reported_not_raised(screen, bundle, fake_usb):
    fake_usb.export_bundle.side_effect = OSError("disk full")

    screen.export_to_usb()  # must not raise

    assert "Export failed" in screen.status_text


# ── import: no bundle found ─────────────────────────────────────────────

def test_import_with_no_bundle_found_shows_the_refusal_message(screen, fake_usb):
    fake_usb.find_bundles.return_value = []

    screen.import_from_usb()

    assert "No commissioning bundle found" in screen.status_text
    assert screen.import_popup is None


# ── import: confirm dialog ───────────────────────────────────────────────

@pytest.fixture
def one_bundle_on_disk(tmp_path, fake_usb):
    path = tmp_path / "reflex-commissioning-elspi-20260913T190411Z.yaml"
    path.write_text(
        "meta:\n"
        "  schema: 1\n"
        "  ts: '2026-09-13T19:04:11+00:00'\n"
        "  hostname: elspi\n"
        "  fw: 1.2.0\n"
        "Axis-0:\n"
        "  axis_name: C\n"
    )
    fake_usb.find_bundles.return_value = [path]
    return path


def test_import_opens_a_confirm_dialog_showing_the_bundle_meta(
        screen, one_bundle_on_disk, monkeypatch):
    captured = {}

    def fake_custom_popup(**kwargs):
        captured.update(kwargs)
        return MagicMock(name="popup")

    monkeypatch.setattr(ss, "CustomPopup", fake_custom_popup)

    screen.import_from_usb()

    assert screen.import_popup is not None
    assert "elspi" in captured["message"]
    assert "2026-09-13T19:04:11+00:00" in captured["message"]
    assert "1.2.0" in captured["message"]
    assert captured["cancel_text"]  # a confirm/cancel, not a bare OK


def test_confirming_the_dialog_applies_the_bundle(
        screen, one_bundle_on_disk, bundle, monkeypatch):
    monkeypatch.setattr(ss, "CustomPopup", lambda **kw: MagicMock(name="popup"))
    bundle.apply.return_value = MagicMock(ok=True, written=["Axis-0"], skipped=[])

    screen.import_from_usb()
    # Simulate the operator pressing the dialog's primary button.
    screen._apply_pending_import()

    bundle.apply.assert_called_once()
    assert "Imported 1 file(s)" in screen.status_text
    assert screen._pending_doc is None, "pending state must clear either way"


def test_a_refused_apply_is_reported_and_writes_nothing(
        screen, one_bundle_on_disk, bundle, monkeypatch):
    monkeypatch.setattr(ss, "CustomPopup", lambda **kw: MagicMock(name="popup"))
    bundle.apply.return_value = MagicMock(ok=False, reason="bundle schema 2 is newer")

    screen.import_from_usb()
    screen._apply_pending_import()

    assert "Import refused" in screen.status_text
    assert "schema 2" in screen.status_text


def test_cancelling_never_calls_apply(screen, one_bundle_on_disk, bundle, monkeypatch):
    monkeypatch.setattr(ss, "CustomPopup", lambda **kw: MagicMock(name="popup"))

    screen.import_from_usb()
    # Cancel just means _apply_pending_import is never invoked; nothing else
    # to drive here since CustomPopup itself is faked out (see
    # test_custom_popup.py for the popup's own cancel-wiring coverage).
    assert screen._pending_doc is not None
    bundle.apply.assert_not_called()


def test_unreadable_bundle_file_is_reported_not_raised(screen, fake_usb, tmp_path):
    path = tmp_path / "reflex-commissioning-elspi-bad.yaml"
    path.write_text(":\n  not: [valid, yaml")
    fake_usb.find_bundles.return_value = [path]

    screen.import_from_usb()  # must not raise

    assert "Cannot read" in screen.status_text
    assert screen.import_popup is None
