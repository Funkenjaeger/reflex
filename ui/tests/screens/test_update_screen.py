"""UpdateScreen: the dropdown, the toggle, and which install path a tag takes.

REWRITTEN 2026-09-07 with the screen. Every test here used to be about
``DEV_RELEASE`` -- a "dev (experimental)" entry that tracked the dev BRANCH.
That entry is gone, and not because it was awkward to keep: no firmware image
is published for a branch, so installing it could only ever have replaced the
UI half. That is precisely the UI-only update the fw+ui decision rejected, and
a toggle that offers one is a toggle that offers a broken pairing.

``allow_experimental`` now means PRE-RELEASE TAGS, which the same lockstep
workflow builds and which carry both halves. The toggle keeps its name, its
place and its warning dialog; what it selects is now installable.

The protocol gate is not exercised here -- it lives in the session, below the
screen, and is tested in ``tests/utils/test_updater.py`` against a fake board.
"""
from unittest.mock import patch

import pytest

from reflex.components.screens.update_screen import UpdateScreen
from reflex.utils.updater import Release


def _release(tag, prerelease=False):
    return Release(tag=tag, prerelease=prerelease,
                   firmware_url=f"https://example/{tag}/fw.bin",
                   firmware_name=f"reflex-app-{tag.lstrip('v')}.bin")


def _not_the_running_version(screen):
    """A catalogue tag that is not what is installed.

    Picked rather than hardcoded: ``current_release`` comes from the package
    metadata, so this checkout's own version drifts into and out of any
    literal the fixture happens to use -- which is what made two of these
    tests pass or fail depending on the release the repo was sitting on.
    """
    other = next(t for t in screen._catalogue if t != screen.current_release)
    assert other != screen.current_release
    return other


@pytest.fixture
def screen():
    with patch.object(UpdateScreen, "apply_class_lang_rules"):
        s = UpdateScreen()
    s._catalogue = {
        "v1.2.0-rc.1": _release("v1.2.0-rc.1", prerelease=True),
        "v1.1.0": _release("v1.1.0"),
        "v1.0.1": _release("v1.0.1"),
    }
    return s


class TestAllowExperimental:
    def test_prereleases_hidden_by_default(self, screen):
        screen._set_releases()
        assert screen.releases == ["v1.1.0", "v1.0.1"]

    def test_prereleases_offered_when_enabled(self, screen):
        screen.allow_experimental = True
        assert screen.releases == ["v1.2.0-rc.1", "v1.1.0", "v1.0.1"]

    def test_finals_survive_toggling(self, screen):
        screen.allow_experimental = True
        screen.allow_experimental = False
        assert screen.releases == ["v1.1.0", "v1.0.1"]

    def test_no_duplicates_on_repeated_enable(self, screen):
        screen.allow_experimental = True
        screen.allow_experimental = True
        assert screen.releases.count("v1.2.0-rc.1") == 1

    def test_selection_falls_back_when_a_prerelease_is_hidden(self, screen):
        screen.allow_experimental = True
        screen.selected_release = "v1.2.0-rc.1"
        screen.allow_experimental = False
        assert screen.selected_release == "v1.1.0"

    def test_a_final_selection_survives_the_toggle(self, screen):
        screen._set_releases()
        screen.selected_release = "v1.0.1"
        screen.allow_experimental = True
        screen.allow_experimental = False
        assert screen.selected_release == "v1.0.1"

    def test_no_dev_branch_entry_exists_any_more(self, screen):
        """The dropdown holds only things that carry BOTH halves."""
        screen.allow_experimental = True
        for tag in screen.releases:
            assert tag in screen._catalogue
        assert not any("dev" in tag for tag in screen.releases)


class _Device:
    """The one attribute of Device-0 this screen reads and writes."""
    def __init__(self, offer_prereleases=False):
        self.offer_prereleases = offer_prereleases


class TestTogglePersistsAndNeedsNoRefresh:
    """Bench 2026-09-19: the toggle reset to off on every visit, and turning
    it on showed nothing until Refresh was tapped, because the fetch itself
    honoured the toggle."""

    def test_the_fetch_takes_prereleases_whatever_the_toggle_says(self, screen):
        """MUTATION EVIDENCE: fetching with allow_prerelease=<toggle> fails."""
        class FakeSession:
            def list_release_catalogue(self):
                return [_release("v1.3.0-rc.1", True), _release("v1.2.0")]

            def list_releases(self, **kw):
                raise AssertionError("the screen must fetch the whole catalogue")
        screen.allow_experimental = False
        with patch.object(screen, "_session", return_value=FakeSession()):
            got = screen._fetch_releases()
        assert [r.tag for r in got] == ["v1.3.0-rc.1", "v1.2.0"]

    def test_turning_it_on_shows_prereleases_at_once(self, screen):
        screen._set_releases()
        with patch.object(screen, "schedule_refresh_releases") as refresh:
            screen.allow_experimental = True
        assert "v1.2.0-rc.1" in screen.releases
        refresh.assert_not_called()

    def test_the_toggle_is_restored_on_entry(self, screen):
        dev = _Device(offer_prereleases=True)
        with patch.object(UpdateScreen, "_device", return_value=dev), \
             patch.object(screen, "schedule_refresh_releases"):
            screen.on_pre_enter()
        assert screen.allow_experimental is True
        assert "v1.2.0-rc.1" in screen.releases

    def test_the_toggle_is_saved_when_changed(self, screen):
        dev = _Device(offer_prereleases=False)
        with patch.object(UpdateScreen, "_device", return_value=dev):
            screen.allow_experimental = True
            assert dev.offer_prereleases is True
            screen.allow_experimental = False
            assert dev.offer_prereleases is False

    def test_entry_does_not_refetch_a_catalogue_it_already_has(self, screen):
        with patch.object(UpdateScreen, "_device", return_value=None), \
             patch.object(screen, "schedule_refresh_releases") as refresh:
            screen.on_pre_enter()
        refresh.assert_not_called()


class TestStatusInView:
    def test_starting_an_update_scrolls_to_the_status_box(self, screen):
        class Scroller:
            scroll_y = 1
        scroller = Scroller()
        screen.ids["scroller"] = scroller
        screen._scroll_to_status()
        assert scroller.scroll_y == 0

    def test_the_scroll_is_scheduled_by_the_install(self, screen):
        with patch("reflex.components.screens.update_screen.Clock") as clock:
            screen._do_install(_release("v1.1.0"))
        callbacks = [c.args[0] for c in clock.schedule_once.call_args_list]
        assert screen._scroll_to_status in callbacks


class TestInstallRelease:
    def test_a_final_release_installs_without_a_dialog(self, screen):
        screen._set_releases()
        screen.selected_release = "v1.1.0"
        with patch.object(screen, "_do_install") as do, \
             patch.object(screen, "_confirm_prerelease") as confirm:
            screen.install_release()
            do.assert_called_once()
            confirm.assert_not_called()

    def test_a_prerelease_asks_first(self, screen):
        screen.allow_experimental = True
        screen.selected_release = "v1.2.0-rc.1"
        with patch.object(screen, "_do_install") as do, \
             patch.object(screen, "_confirm_prerelease") as confirm:
            screen.install_release()
            confirm.assert_called_once()
            do.assert_not_called()

    def test_a_tag_not_in_the_catalogue_installs_nothing(self, screen):
        """The dropdown is built from the catalogue, so this should be
        unreachable -- which is exactly why it must not fall through to an
        install if it ever happens."""
        screen.selected_release = "v9.9.9"
        with patch.object(screen, "_do_install") as do, \
             patch.object(screen, "_confirm_prerelease") as confirm:
            screen.install_release()
            do.assert_not_called()
            confirm.assert_not_called()


class TestInstallButton:
    def test_disabled_for_the_running_version(self, screen):
        screen.selected_release = screen.current_release
        assert not screen.enable_update_button

    def test_the_installed_pre_release_is_recognized(self, screen):
        """2026-09-25: the package reports 1.2.0rc7, the tag is v1.2.0-rc.7.
        Compared as strings ("v" + package) they never matched, so the
        installed pre-release was always offered for install."""
        screen.current_release = "v1.2.0-rc.1"      # installed_tag() of 1.2.0rc1
        screen.selected_release = "v1.1.0"
        assert screen.enable_update_button
        screen.selected_release = "v1.2.0-rc.1"
        assert not screen.enable_update_button
        # And the old spelling of the same release, should one ever arrive.
        screen.current_release = "v1.2.0rc1"
        screen.selected_release = "v1.1.0"
        screen.selected_release = "v1.2.0-rc.1"
        assert not screen.enable_update_button

    def test_current_release_is_shown_in_tag_spelling(self):
        with patch("importlib.metadata.version", return_value="1.2.0rc7"):
            from reflex.utils import release_version
            assert release_version.installed_tag() == "v1.2.0-rc.7"

    def test_enabled_for_a_different_version(self, screen):
        other = _not_the_running_version(screen)
        screen.selected_release = other
        assert screen.enable_update_button

    def test_disabled_while_an_update_is_running(self, screen):
        other = _not_the_running_version(screen)
        screen.selected_release = other
        assert screen.enable_update_button
        screen._do_install(screen._catalogue[other])
        assert screen.busy
        assert not screen.enable_update_button


# ---------------------------------------------------------------------------
# "Offer integration builds": the optional home source's alphas
# ---------------------------------------------------------------------------

def _alpha(tag):
    return Release(tag=tag, prerelease=True, firmware_url=f"https://forge.example/{tag}.bin",
                   firmware_name=f"reflex-app-{tag.lstrip('v')}.bin", source="home")


class _IntegrationDevice(_Device):
    def __init__(self, offer_prereleases=False, offer_integration_builds=False):
        super().__init__(offer_prereleases)
        self.offer_integration_builds = offer_integration_builds


@pytest.fixture
def home_screen(screen):
    screen._catalogue = {"v1.3.0-alpha.2": _alpha("v1.3.0-alpha.2"), **screen._catalogue}
    return screen


class TestIntegrationBuilds:
    def test_without_a_home_source_the_row_is_hidden_and_offers_nothing(self, home_screen):
        """MUTATION EVIDENCE: dropping the home_available condition from
        _offered turns this red."""
        with patch("reflex.utils.release_source.load_home_source", return_value=None), \
             patch.object(UpdateScreen, "_device",
                          return_value=_IntegrationDevice(offer_integration_builds=True)), \
             patch.object(home_screen, "schedule_refresh_releases"):
            home_screen.on_pre_enter()
        assert home_screen.home_available is False
        assert home_screen.allow_integration is False
        home_screen.allow_integration = True       # even forced on
        assert "v1.3.0-alpha.2" not in home_screen.releases

    def test_alphas_follow_their_own_toggle_not_the_prerelease_one(self, home_screen):
        home_screen.home_available = True
        home_screen.allow_experimental = True
        assert "v1.3.0-alpha.2" not in home_screen.releases
        home_screen.allow_integration = True
        assert home_screen.releases[0] == "v1.3.0-alpha.2"
        home_screen.allow_experimental = False
        assert home_screen.releases == ["v1.3.0-alpha.2", "v1.1.0", "v1.0.1"]

    def test_the_toggle_is_restored_and_saved_when_a_source_exists(self, home_screen):
        dev = _IntegrationDevice(offer_integration_builds=True)
        with patch("reflex.utils.release_source.load_home_source", return_value=object()), \
             patch.object(UpdateScreen, "_device", return_value=dev), \
             patch.object(home_screen, "schedule_refresh_releases"):
            home_screen.on_pre_enter()
            assert home_screen.home_available and home_screen.allow_integration
            assert "v1.3.0-alpha.2" in home_screen.releases
            home_screen.allow_integration = False
            assert dev.offer_integration_builds is False

    def test_the_session_gets_the_home_source(self, home_screen):
        marker = object()
        home_screen._home_source = marker
        with patch("reflex.components.screens.update_screen.updater.resolve_checkout",
                   return_value="/tmp/checkout"):
            s = home_screen._session()
        assert s.home_source is marker

    def test_installing_an_alpha_asks_first(self, home_screen):
        home_screen.home_available = True
        home_screen.allow_integration = True
        home_screen.selected_release = "v1.3.0-alpha.2"
        with patch.object(home_screen, "_confirm_prerelease") as ask, \
             patch.object(home_screen, "_install_unless_engaged") as go:
            home_screen.install_release()
        ask.assert_called_once()
        go.assert_not_called()


# ---------------------------------------------------------------------------
# Reinstall firmware: the UI is the selected release, the firmware is not
# ---------------------------------------------------------------------------

_DIAG = ('{"utc":"2026-10-17T14:02:11Z","variant":"diagnostic","probe":"isr-gap",'
         '"rev":"dd26b76","dirty":false,"md5":"0"}\n')
_RELEASE_1_1_0 = ('{"utc":"2026-10-16T09:00:00Z","variant":"release","rev":"43ac7c5",'
                  '"via":"modbus","protocol":11,"tag":"v1.1.0"}\n')
_RELEASE_1_0_1 = ('{"utc":"2026-10-15T09:00:00Z","variant":"release","rev":"5d0e1aa",'
                  '"via":"modbus","protocol":11,"tag":"v1.0.1"}\n')


def _check_firmware(screen, tmp_path, manifest_text, tag_rev=None):
    """Run the screen's own firmware check against a manifest on disk."""
    import asyncio
    manifest = tmp_path / "flashed.json"
    if manifest_text is not None:
        manifest.write_text(manifest_text)
    us = "reflex.components.screens.update_screen.updater"
    with patch(f"{us}.resolve_checkout", return_value=tmp_path), \
         patch(f"{us}.manifest_path_for", return_value=manifest), \
         patch(f"{us}.tag_rev", return_value=tag_rev), \
         patch.object(screen, "update_status", screen._append):
        asyncio.run(screen.check_firmware())


class TestReinstallFirmware:
    @pytest.fixture
    def at_1_1_0(self, screen):
        screen.current_release = "v1.1.0"
        screen._set_releases()
        return screen

    def test_a_diagnostic_image_under_this_release_offers_a_reinstall(self, at_1_1_0, tmp_path):
        _check_firmware(at_1_1_0, tmp_path, _RELEASE_1_1_0 + _DIAG)
        at_1_1_0.selected_release = "v1.1.0"
        assert at_1_1_0.firmware_only
        assert at_1_1_0.enable_update_button
        assert "diagnostic firmware (rev dd26b76)" in at_1_1_0.status
        assert "Reinstall firmware" in at_1_1_0.status

    def test_another_releases_firmware_under_this_release_offers_a_reinstall(self, at_1_1_0, tmp_path):
        _check_firmware(at_1_1_0, tmp_path, _RELEASE_1_1_0 + _RELEASE_1_0_1)
        at_1_1_0.selected_release = "v1.1.0"
        assert at_1_1_0.firmware_only and at_1_1_0.enable_update_button
        assert "5d0e1aa" in at_1_1_0.status

    def test_a_matching_pair_offers_nothing_new(self, at_1_1_0, tmp_path):
        _check_firmware(at_1_1_0, tmp_path, _RELEASE_1_0_1 + _RELEASE_1_1_0)
        at_1_1_0.selected_release = "v1.1.0"
        assert not at_1_1_0.firmware_only
        assert not at_1_1_0.enable_update_button
        assert at_1_1_0.status == ""

    def test_no_record_offers_nothing_new(self, at_1_1_0, tmp_path):
        _check_firmware(at_1_1_0, tmp_path, None)
        at_1_1_0.selected_release = "v1.1.0"
        assert not at_1_1_0.firmware_only
        assert not at_1_1_0.enable_update_button

    def test_another_release_is_still_a_full_update(self, at_1_1_0, tmp_path):
        _check_firmware(at_1_1_0, tmp_path, _RELEASE_1_1_0 + _DIAG)
        at_1_1_0.selected_release = "v1.0.1"
        assert at_1_1_0.enable_update_button
        assert not at_1_1_0.firmware_only

    def test_the_offer_follows_a_check_that_lands_after_the_selection(self, at_1_1_0, tmp_path):
        at_1_1_0.selected_release = "v1.1.0"
        assert not at_1_1_0.enable_update_button
        _check_firmware(at_1_1_0, tmp_path, _RELEASE_1_1_0 + _DIAG)
        assert at_1_1_0.firmware_only and at_1_1_0.enable_update_button

    def test_a_reinstall_asks_no_pre_release_question(self, screen):
        """The release is already installed; the question is about choosing
        one. The ELS gate still applies (test_update_screen_els.py)."""
        screen._catalogue["v1.2.0-rc.1"] = _release("v1.2.0-rc.1", prerelease=True)
        screen.current_release = "v1.2.0-rc.1"
        screen.allow_experimental = True
        screen.firmware_mismatch = "The controller runs diagnostic firmware."
        screen.selected_release = "v1.2.0-rc.1"
        with patch.object(screen, "_confirm_prerelease") as ask, \
             patch.object(screen, "_install_unless_engaged") as go:
            screen.install_release()
        ask.assert_not_called()
        go.assert_called_once()

    def test_the_reinstall_runs_the_firmware_only_session_path(self, at_1_1_0):
        import asyncio
        at_1_1_0.firmware_mismatch = "The controller runs diagnostic firmware."
        at_1_1_0.selected_release = "v1.1.0"
        at_1_1_0._firmware_only_install = True
        lines = []
        with patch.object(at_1_1_0, "update_status", lines.append), \
             patch.object(at_1_1_0, "_install_blocking") as full, \
             patch.object(at_1_1_0, "_reinstall_blocking") as fw_only, \
             patch.object(at_1_1_0, "check_firmware", side_effect=_async_noop):
            asyncio.run(at_1_1_0.perform_install(at_1_1_0._catalogue["v1.1.0"]))
        full.assert_not_called()
        fw_only.assert_called_once()
        assert "Reinstalling the v1.1.0 firmware. This UI stays as it is." in lines
        assert "Firmware reinstalled. The controller runs the v1.1.0 firmware." in lines
        assert at_1_1_0.busy is False


async def _async_noop(*_a, **_k):
    return None
