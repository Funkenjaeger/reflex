"""BackupScreen's gist-sync toggle, "Sync now" and "Restore from gist".

Driven exactly like ``test_backup_screen_usb.py``: ``apply_class_lang_rules``
stubbed so construction never builds the kv tree, and the real dependency
(``gist_sync``) replaced at the module object ``backup_screen`` imported.

THE TWO THREADING SEAMS ARE MADE SYNCHRONOUS. ``_run_async`` puts work on a
daemon thread in the app and ``_dispatch_to_ui`` marshals results back through
``Clock.schedule_once``; the ``sync_screen`` fixture replaces both with "call
it now". That is the whole reason those are one-line methods -- the flow under
test is the ORDER of the calls and what lands on ``status_text``, and a real
thread would make every assertion here a race.
"""
from unittest.mock import MagicMock, patch

import pytest

import reflex.components.screens.backup_screen as ss
from reflex.utils import gist_sync as real_gist_sync


@pytest.fixture
def screen():
    with patch.object(ss.BackupScreen, "apply_class_lang_rules"):
        return ss.BackupScreen()


@pytest.fixture
def sync_screen(screen, monkeypatch):
    """`screen`, with both threading seams collapsed to a direct call."""
    monkeypatch.setattr(ss.BackupScreen, "_run_async",
                        lambda self, work: work())
    monkeypatch.setattr(ss.BackupScreen, "_dispatch_to_ui",
                        lambda self, work: work())
    return screen


@pytest.fixture
def fake_gist(monkeypatch):
    """A stand-in for the whole ``gist_sync`` module.

    The exception classes and the plain string constants are the REAL ones, so
    ``except gist_sync.GistSyncError`` in the code under test still catches and
    the messages asserted on are the messages the operator sees.
    """
    fake = MagicMock(name="gist_sync")
    fake.GistSyncError = real_gist_sync.GistSyncError
    fake.NotConfigured = real_gist_sync.NotConfigured
    fake.SignInExpired = real_gist_sync.SignInExpired
    fake.last_error = None
    fake.NOT_CONFIGURED_MESSAGE = real_gist_sync.NOT_CONFIGURED_MESSAGE
    fake.REVOKE_URL = real_gist_sync.REVOKE_URL
    fake.is_configured.return_value = True
    fake.is_enabled.return_value = False
    fake.load_token.return_value = None
    monkeypatch.setattr(ss, "gist_sync", fake)
    return fake


@pytest.fixture
def signed_in(fake_gist):
    """A card that already holds a GitHub token. A restore on a card WITHOUT
    one signs in first; see the new-card tests below."""
    fake_gist.load_token.return_value = "gho_token"
    return fake_gist


# ── the not-configured guard ────────────────────────────────────────────────

def test_an_unconfigured_build_says_so_and_starts_nothing(sync_screen, fake_gist):
    fake_gist.is_configured.return_value = False

    sync_screen.toggle_gist_sync(True)

    assert "not configured" in sync_screen.status_text.lower()
    assert sync_screen.gist_enabled is False
    fake_gist.authorize.assert_not_called()
    fake_gist.set_enabled.assert_not_called()


def test_an_unconfigured_build_refuses_restore_too(sync_screen, fake_gist):
    fake_gist.is_configured.return_value = False

    sync_screen.restore_from_gist()

    assert "not configured" in sync_screen.status_text.lower()
    fake_gist.list_machine_gists.assert_not_called()


def test_refresh_puts_the_not_configured_message_on_screen(screen, fake_gist):
    fake_gist.is_configured.return_value = False

    screen.refresh_gist_state()

    # In the small note line, NOT the 1.8x device-code box, where it overflowed
    # its neighbours on the lathe (2026-09-17).
    assert screen.gist_note_text == real_gist_sync.NOT_CONFIGURED_MESSAGE
    assert screen.gist_code_text == ""
    assert screen.gist_configured is False
    assert screen.gist_enabled is False


# ── turning it on ───────────────────────────────────────────────────────────

def test_turning_it_on_runs_the_device_flow_and_shows_the_code(
        sync_screen, fake_gist):
    code = real_gist_sync.DeviceCode(
        device_code="dc", user_code="WDJB-MJHT",
        verification_uri="https://github.com/login/device")

    def authorize(*, sleep, on_code=None, **kw):
        on_code(code)
        return "gho_token"

    fake_gist.authorize.side_effect = authorize

    sync_screen.toggle_gist_sync(True)

    # The code was shown while the flow was in progress...
    assert sync_screen.gist_enabled is True
    fake_gist.set_enabled.assert_called_once_with(True)
    fake_gist.install_ledger_hook.assert_called_once()
    fake_gist.sync_now.assert_called_once()


def test_the_user_code_and_url_are_put_on_screen_with_a_qr_of_the_url(
        screen, fake_gist, monkeypatch):
    """The code and URL as text (the code must still be typed -- GitHub gives
    no code-prefilled URL) plus a QR of the URL (2026-09-17)."""
    monkeypatch.setattr(ss.BackupScreen, "_dispatch_to_ui",
                        lambda self, work: work())
    code = real_gist_sync.DeviceCode(
        device_code="dc", user_code="WDJB-MJHT",
        verification_uri="https://github.com/login/device")

    screen._post_code(code)

    assert "WDJB-MJHT" in screen.gist_code_text
    assert "https://github.com/login/device" in screen.gist_code_text
    assert screen.gist_qr_data == "https://github.com/login/device"


def test_without_segno_the_code_is_text_only_and_no_qr_box(screen, fake_gist, monkeypatch):
    """elspi's site-packages is root-owned, so a code deploy can land before
    segno is installed. That must degrade to the pre-QR screen, not crash."""
    monkeypatch.setattr(ss.BackupScreen, "_dispatch_to_ui",
                        lambda self, work: work())
    monkeypatch.setattr(ss.qr_code, "segno", None)
    code = real_gist_sync.DeviceCode(
        device_code="dc", user_code="WDJB-MJHT",
        verification_uri="https://github.com/login/device")

    screen._post_code(code)

    assert "WDJB-MJHT" in screen.gist_code_text
    assert "Enter this at https://github.com/login/device" in screen.gist_code_text
    assert screen.gist_qr_data == "", "no QR data -> the kv gives the QR zero width"


def test_clearing_the_code_clears_the_qr(screen):
    screen.gist_code_text = "WDJB-MJHT\nScan, or go to https://github.com/login/device"
    screen.gist_qr_data = "https://github.com/login/device"

    screen.gist_code_text = ""  # every clearing path ends here

    assert screen.gist_qr_data == ""


def test_an_existing_token_skips_the_device_flow(sync_screen, fake_gist):
    fake_gist.load_token.return_value = "gho_already"

    sync_screen.toggle_gist_sync(True)

    fake_gist.authorize.assert_not_called()
    assert sync_screen.gist_enabled is True
    fake_gist.sync_now.assert_called_once()


@pytest.mark.parametrize("message", [
    "The code expired before it was entered. Turn the toggle on again for a fresh code.",
    "Authorization was denied on GitHub. Gist sync stays off.",
])
def test_a_terminal_device_flow_error_is_shown_and_leaves_it_off(
        sync_screen, fake_gist, message):
    fake_gist.authorize.side_effect = real_gist_sync.GistSyncError(message)

    sync_screen.toggle_gist_sync(True)

    assert sync_screen.status_text == message
    assert sync_screen.gist_enabled is False
    assert sync_screen.gist_code_text == ""
    fake_gist.set_enabled.assert_not_called()


def test_an_unreachable_github_never_raises_into_the_ui(sync_screen, fake_gist):
    fake_gist.authorize.side_effect = OSError("Network is unreachable")

    sync_screen.toggle_gist_sync(True)  # must not raise

    assert "Could not reach GitHub" in sync_screen.status_text
    assert sync_screen.gist_enabled is False


def test_a_second_press_does_not_start_a_second_flow(screen, fake_gist,
                                                     monkeypatch):
    started = []
    monkeypatch.setattr(ss.BackupScreen, "_run_async",
                        lambda self, work: started.append(work))

    screen.start_device_flow()
    screen.start_device_flow()

    assert len(started) == 1


# ── turning it off ──────────────────────────────────────────────────────────

def test_turning_it_off_forgets_the_token_and_names_the_revoke_page(
        sync_screen, fake_gist):
    sync_screen.gist_enabled = True

    sync_screen.toggle_gist_sync(False)

    fake_gist.set_enabled.assert_called_once_with(False)
    fake_gist.forget_token.assert_called_once()
    assert sync_screen.gist_enabled is False
    assert real_gist_sync.REVOKE_URL in sync_screen.status_text


def test_the_revoke_url_is_exposed_to_the_kv(screen):
    assert screen.gist_revoke_text == "https://github.com/settings/applications"


# ── sync now ────────────────────────────────────────────────────────────────

def test_sync_now_reports_the_gist_id(sync_screen, fake_gist):
    fake_gist.sync_now.return_value = "gist-1"

    sync_screen.sync_now()

    assert "gist-1" in sync_screen.status_text


def test_a_failed_sync_says_it_will_retry(sync_screen, fake_gist):
    fake_gist.sync_now.return_value = None

    sync_screen.sync_now()

    assert "retry at the next change" in sync_screen.status_text


def test_a_rejected_sign_in_says_so_and_shows_sync_off(sync_screen, fake_gist):
    """Lathe, 2026-09-19: a 401 was shown as "will retry" with the toggle ON."""
    fake_gist.sync_now.return_value = None
    fake_gist.last_error = real_gist_sync.SIGN_IN_EXPIRED_MESSAGE
    fake_gist.is_enabled.return_value = False       # sync_now turned it off

    sync_screen.sync_now()

    assert "no longer accepts" in sync_screen.status_text
    assert "retry" not in sync_screen.status_text
    assert sync_screen.gist_enabled is False


def test_a_rejected_sign_in_during_restore_turns_sync_off(sync_screen, fake_gist,
                                                          signed_in):
    fake_gist.list_machine_gists.side_effect = real_gist_sync.SignInExpired()

    sync_screen.restore_from_gist()

    fake_gist.sign_in_expired.assert_called_once()
    assert "no longer accepts" in sync_screen.status_text


# ── restore on a new card: sign in first, and do NOT turn sync on ───────────

def _grant(fake_gist, code=None):
    """Make ``authorize`` succeed the way the real one does: show the code,
    save the token, return it."""
    code = code or real_gist_sync.DeviceCode(
        device_code="dc", user_code="WDJB-MJHT",
        verification_uri="https://github.com/login/device")

    def authorize(*, sleep, on_code=None, **kw):
        on_code(code)
        fake_gist.load_token.return_value = "gho_token"
        return "gho_token"

    fake_gist.authorize.side_effect = authorize


def test_a_restore_with_no_sign_in_signs_in_then_lists(sync_screen, fake_gist):
    """Until 2026-09-26 this said "Not connected to GitHub yet" and stopped,
    and the guide sent the operator to the sync toggle first."""
    _grant(fake_gist)
    fake_gist.list_machine_gists.return_value = []

    sync_screen.restore_from_gist()

    fake_gist.authorize.assert_called_once()
    fake_gist.list_machine_gists.assert_called_once()
    assert sync_screen.gist_code_text == "", "the code is cleared once signed in"


def test_signing_in_to_restore_does_not_turn_sync_on_or_push(sync_screen, fake_gist):
    """THE junk-gist bug. The toggle's sign-in ends in a push, and on a new
    card that uploaded the card's DEFAULTS as a new gist -- which then sorted
    to the top of the restore list, above the backup the operator came for."""
    _grant(fake_gist)
    fake_gist.list_machine_gists.return_value = []

    sync_screen.restore_from_gist()

    fake_gist.set_enabled.assert_not_called()
    fake_gist.install_ledger_hook.assert_not_called()
    fake_gist.sync_now.assert_not_called()
    fake_gist.push_bundle.assert_not_called()
    assert sync_screen.gist_enabled is False


def test_a_failed_sign_in_for_restore_lists_nothing(sync_screen, fake_gist):
    fake_gist.authorize.side_effect = real_gist_sync.GistSyncError(
        "Authorization was denied on GitHub. Gist sync stays off.")

    sync_screen.restore_from_gist()

    assert "denied" in sync_screen.status_text
    fake_gist.list_machine_gists.assert_not_called()


def test_a_token_that_did_not_stick_does_not_start_a_second_sign_in(
        sync_screen, fake_gist):
    fake_gist.authorize.side_effect = lambda **kw: "gho_token"  # never saved

    sync_screen.restore_from_gist()

    fake_gist.authorize.assert_called_once()
    fake_gist.list_machine_gists.assert_not_called()
    assert "could not be saved" in sync_screen.status_text


def test_the_toggle_still_turns_sync_on_after_its_sign_in(sync_screen, fake_gist):
    """The restore's continuation is its own; the toggle's is unchanged."""
    _grant(fake_gist)

    sync_screen.toggle_gist_sync(True)

    fake_gist.set_enabled.assert_called_once_with(True)
    fake_gist.list_machine_gists.assert_not_called()


# ── restore: the SAME confirm dialog and apply() path as USB ────────────────

DOC = {"meta": {"schema": 1, "ts": "2026-09-13T19:04:11+00:00",
                "hostname": "elspi", "fw": "1.2.0", "machine_id": "5a2f"},
       "Axis-0": {"axis_name": "C"}}


@pytest.fixture
def one_gist(fake_gist, signed_in):
    fake_gist.list_machine_gists.return_value = [
        real_gist_sync.GistRef(id="g-new", description="reflex commissioning "
                               "bundle: 5a2f", machine_id="5a2f",
                               updated_at="2026-09-01T00:00:00Z")]
    fake_gist.fetch_bundle.return_value = DOC
    return fake_gist


def test_restore_opens_the_shared_confirm_dialog_with_the_bundle_meta(
        sync_screen, one_gist, monkeypatch):
    captured = {}

    def fake_custom_popup(**kwargs):
        captured.update(kwargs)
        return MagicMock(name="popup")

    monkeypatch.setattr(ss, "CustomPopup", fake_custom_popup)

    sync_screen.restore_from_gist()

    assert sync_screen.import_popup is not None, "the USB import's own dialog"
    assert "elspi" in captured["message"]
    assert f"Captured: {ss._local_time('2026-09-13T19:04:11+00:00')}" in captured["message"]
    assert captured["cancel_text"], "a confirm/cancel, not a bare OK"
    assert captured["confirm_callback"] == sync_screen._apply_pending_import


def test_restore_hands_the_parsed_document_to_apply(
        sync_screen, one_gist, monkeypatch):
    monkeypatch.setattr(ss, "CustomPopup", lambda **kw: MagicMock(name="popup"))
    bundle = MagicMock(name="commissioning_bundle")
    bundle.apply.return_value = MagicMock(ok=True, written=["Axis-0"], skipped=[])
    monkeypatch.setattr(ss, "commissioning_bundle", bundle)

    sync_screen.restore_from_gist()
    sync_screen._apply_pending_import()  # the operator presses Apply

    bundle.apply.assert_called_once()
    assert bundle.apply.call_args[0][0] == DOC, "the SAME apply(), same document"
    assert "Imported 1 file(s)" in sync_screen.status_text
    assert sync_screen._pending_doc is None


def test_restore_with_several_candidates_offers_a_picker(
        sync_screen, fake_gist, signed_in, monkeypatch):
    fake_gist.list_machine_gists.return_value = [
        real_gist_sync.GistRef(id="g-new", description="d", machine_id="5a2f",
                               updated_at="2026-09-01T00:00:00Z"),
        real_gist_sync.GistRef(id="g-old", description="d", machine_id="beef",
                               updated_at="2026-07-01T00:00:00Z"),
    ]
    offered = []
    monkeypatch.setattr(ss.BackupScreen, "_open_restore_picker",
                        lambda self, refs: offered.extend(refs))

    sync_screen.restore_from_gist()

    assert [r.id for r in offered] == ["g-new", "g-old"]
    # Nothing is fetched until the operator picks one.
    fake_gist.fetch_bundle.assert_not_called()


def test_restore_with_no_candidates_says_so(sync_screen, fake_gist, signed_in):
    fake_gist.list_machine_gists.return_value = []

    sync_screen.restore_from_gist()

    assert "No commissioning bundles found" in sync_screen.status_text
    assert sync_screen.import_popup is None


def test_a_restore_failure_is_reported_not_raised(sync_screen, fake_gist, signed_in):
    fake_gist.list_machine_gists.side_effect = OSError("Network is unreachable")

    sync_screen.restore_from_gist()  # must not raise

    assert "Could not reach GitHub" in sync_screen.status_text


def test_an_unreadable_gist_is_reported_not_raised(sync_screen, one_gist):
    one_gist.fetch_bundle.side_effect = real_gist_sync.GistSyncError(
        "Gist g-new has no commissioning bundle in it.")

    sync_screen.restore_from_gist()  # must not raise

    assert "no commissioning bundle" in sync_screen.status_text.lower()
    assert sync_screen.import_popup is None
