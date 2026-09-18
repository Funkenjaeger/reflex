"""BackupScreen's USB export/import buttons.

Driven the same way ``test_update_screen.py`` and
``test_network_screen_nmcli.py`` drive their screens: ``apply_class_lang_rules``
stubbed so construction never touches the real kv tree (this screen has no ids
its ``__init__`` reaches for, so nothing extra needs stubbing), and every real
dependency (``commissioning_bundle``, ``usb``, ``CustomPopup``) replaced at the
module object ``backup_screen`` imported, which is the one seam all of it goes
through.
"""
from unittest.mock import MagicMock, patch

import pytest

import reflex.components.screens.backup_screen as ss


@pytest.fixture
def screen():
    with patch.object(ss.BackupScreen, "apply_class_lang_rules"):
        return ss.BackupScreen()


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

def test_export_success_reports_the_file_name_not_the_mount_path(
        screen, bundle, fake_usb, monkeypatch):
    # The path the lathe actually wrote on 2026-09-17; in full it ran off the
    # 1024-px screen, and /media/sda1-3/ means nothing to the operator.
    target = ss.Path("/media/sda1-3/reflex-commissioning-elspi-20260918T003909Z.yaml")
    fake_usb.export_bundle.return_value = target
    monkeypatch.setattr(ss, "_recorded_fw_rev", lambda: "43ac7c5 (v1.2.0-rc.3)")

    screen.export_to_usb()

    assert target.name in screen.status_text
    assert "/media/" not in screen.status_text


def test_export_stamps_the_recorded_firmware_revision(screen, bundle, fake_usb, monkeypatch):
    # meta.fw was null on every export until 2026-09-17 ("Firmware: None").
    fake_usb.export_bundle.return_value = ss.Path("/media/x/b.yaml")
    monkeypatch.setattr(ss, "_recorded_fw_rev", lambda: "43ac7c5 (v1.2.0-rc.3)")

    screen.export_to_usb()

    bundle.build.assert_called_once_with(fw_rev="43ac7c5 (v1.2.0-rc.3)")


def test_the_recorded_fw_rev_never_raises(monkeypatch):
    def boom(*a, **k):
        raise ss.updater.UpdateRefused("not a checkout")
    monkeypatch.setattr(ss.updater, "resolve_checkout", boom)

    assert ss._recorded_fw_rev() is None


def test_local_time_converts_the_utc_stamp(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    ss.time.tzset()
    try:
        # The lathe's real export: 20:39 EDT, stored as 00:39 UTC the next day.
        assert ss._local_time("2026-09-18T00:39:09+00:00") == "2026-09-17 20:39"
    finally:
        monkeypatch.delenv("TZ")
        ss.time.tzset()


@pytest.mark.parametrize("ts, shown", [(None, "unknown"), ("", "unknown"), ("garbage", "garbage")])
def test_local_time_degrades_visibly(ts, shown):
    assert ss._local_time(ts) == shown


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
    # Local wall-clock time, not the raw UTC ISO string (2026-09-17).
    assert f"Captured: {ss._local_time('2026-09-13T19:04:11+00:00')}" in captured["message"]
    assert "+00:00" not in captured["message"]
    assert "1.2.0" in captured["message"]
    assert captured["cancel_text"]  # a confirm/cancel, not a bare OK


def test_a_bundle_without_firmware_says_not_recorded_not_none(
        screen, tmp_path, fake_usb, monkeypatch):
    path = tmp_path / "reflex-commissioning-elspi-20260918T003909Z.yaml"
    path.write_text("meta:\n  schema: 1\n  ts: '2026-09-18T00:39:09+00:00'\n"
                    "  hostname: elspi\n  fw: null\nAxis-0:\n  axis_name: X\n")
    fake_usb.find_bundles.return_value = [path]
    captured = {}
    monkeypatch.setattr(ss, "CustomPopup",
                        lambda **kw: captured.update(kw) or MagicMock(name="popup"))

    screen.import_from_usb()

    assert "Firmware: not recorded" in captured["message"]
    assert "None" not in captured["message"]


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
