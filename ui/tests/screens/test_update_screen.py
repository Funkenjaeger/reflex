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
