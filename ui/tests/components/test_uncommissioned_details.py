"""The dialog behind the strip: what it says, which buttons it grows, and what
each one does.

WHY THE WORDING IS UNDER TEST AT ALL. This dialog is the only on-screen
explanation of a state that otherwise looks like "the app works and silently
forgets everything". Three facts have to survive any edit: the card has no
settings for this lathe, the screen shows defaults, and nothing is saved. The
asserts are on keywords, not sentences, so the drafting stays free.

AND WHY ITS LENGTH IS UNDER TEST TOO. The first version said all of that and
more in three paragraphs, and on the lathe's 1024x600 it overflowed (Evan,
2026-09-26: "too many words there"). The word budgets below are a cheap guard
against it growing back; ``previews/preview_uncommissioned_options.py`` is the
real check, rendering the dialog at 1024x600 in the real fonts.

WHAT IS EXECUTED AND WHAT IS READ. The kv rule tree and the wrapped ``Popup``
both build textures, which segfault under this suite's mock GL backend, so the
tree, the ``Popup`` and the ``Factory`` button classes are replaced with fakes.
Everything the module DECIDES is still real: which options exist, in what
order, what text each button carries, and what pressing it does to
``commissioning_state`` and the screen manager.

SEEN-RED -- see ``tests/utils/test_commissioning_dismissal.py``'s docstring for
the mutation list; the one that lands here is "allow dismissal after an
import", which turns ``test_no_new_machine_button_after_an_import`` red.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from reflex.components.home import uncommissioned_details as details
from reflex.utils import commissioning_bundle, commissioning_state


class FakeButton:
    """Stands in for the themed kv button: keeps its text and its handler."""

    def __init__(self, *, text, primary=False):
        self.text = text
        self.primary = primary
        self._on_release = None

    def bind(self, on_release):
        self._on_release = on_release

    def release(self):
        self._on_release(self)


@pytest.fixture(autouse=True)
def _no_latch_leaks():
    commissioning_state.clear_latch()
    yield
    commissioning_state.clear_latch()


@pytest.fixture(autouse=True)
def _no_real_window(monkeypatch):
    """No kv tree, no Popup, no real buttons: none survives mock GL."""
    monkeypatch.setattr(details.UncommissionedOptions, "apply_class_lang_rules",
                        lambda self, *a, **kw: None)
    monkeypatch.setattr(details.UncommissionedOptions, "add_widget",
                        lambda self, widget, *a, **kw: None)
    monkeypatch.setattr(details, "Popup", MagicMock(name="Popup"))
    monkeypatch.setattr(details, "Factory", SimpleNamespace(
        UncommissionedPrimaryButton=lambda text: FakeButton(text=text, primary=True),
        UncommissionedOptionButton=lambda text: FakeButton(text=text)))


@pytest.fixture
def manager(monkeypatch):
    """The app's screen manager, with a Backup screen to hand back."""
    backup = MagicMock(name="backup_screen")
    mgr = MagicMock(name="manager")
    mgr.get_screen.return_value = backup
    monkeypatch.setattr(details.App, "get_running_app",
                        staticmethod(lambda: SimpleNamespace(manager=mgr)))
    return mgr


@pytest.fixture
def fresh_card(tmp_path, monkeypatch):
    """An uncommissioned card, latched, as MainApp.build leaves it."""
    card = tmp_path / "card"
    card.mkdir()
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(card))
    assert commissioning_state.latch(card) is False
    return card


def build(*, can_dismiss=True, gist_available=True, on_dismissed=None):
    return details.UncommissionedOptions(
        can_dismiss=can_dismiss, gist_available=gist_available,
        on_dismissed=on_dismissed)


def keys(options):
    return [o.key for o in options]


def words(text):
    return len(text.replace("—", " ").split())


# ── what it says ────────────────────────────────────────────────────────────

class TestTheStatement:
    def test_it_says_the_card_has_no_settings_for_this_lathe(self):
        text = f"{details.HEADLINE} {details.LINE}".lower()
        assert "no settings" in text
        assert "this lathe" in text or "this machine" in text

    def test_it_says_the_screen_shows_defaults_and_nothing_is_saved(self):
        text = f"{details.HEADLINE} {details.LINE}".lower()
        assert "default" in text
        assert "nothing is saved" in text or "not saved" in text

    def test_the_statement_is_two_short_lines(self):
        """The overflow Evan found (2026-09-26) was three paragraphs of this."""
        assert words(details.HEADLINE) <= 10
        assert words(details.LINE) <= 12

    def test_the_restart_branch_says_restart_and_why_the_button_is_gone(self):
        """An operator who came looking for the new-machine button has to be
        told where it went, or the missing button is just a bug."""
        text = " ".join((details.AFTER_IMPORT_HEADLINE, details.AFTER_IMPORT_LINE,
                         details.AFTER_IMPORT_NOTE)).lower()
        assert "restart" in text
        assert "new-machine setup is off" in text
        assert "overwrite" in text

    def test_the_restart_branch_puts_its_text_on_the_widget(self):
        options = build(can_dismiss=False)
        assert options.headline == details.AFTER_IMPORT_HEADLINE
        assert options.line == details.AFTER_IMPORT_LINE
        assert options.note == details.AFTER_IMPORT_NOTE

    def test_the_ordinary_branch_has_no_note(self):
        assert build().note == ""


# ── which buttons, in what order ────────────────────────────────────────────

class TestTheOptions:
    def test_new_machine_leads_and_is_the_only_primary(self):
        """Evan, 2026-09-26: lead with the new machine. 'a user who's not new
        to it won't be confused.'"""
        options = details.options_for(can_dismiss=True, gist_available=True)
        assert options[0].key == details.NEW_MACHINE
        assert [o.key for o in options if o.primary] == [details.NEW_MACHINE]

    def test_both_restores_are_offered_as_buttons(self):
        """Evan, 2026-09-26: offer USB and gist 'as direct immediately
        actionable options', not only SSH."""
        assert keys(details.options_for(can_dismiss=True, gist_available=True)) == [
            details.NEW_MACHINE, details.RESTORE_USB, details.RESTORE_GIST,
            details.NOT_NOW]

    def test_a_build_without_gist_sync_leaves_the_gist_button_out(self):
        assert keys(details.options_for(can_dismiss=True, gist_available=False)) == [
            details.NEW_MACHINE, details.RESTORE_USB, details.NOT_NOW]

    def test_ssh_is_not_a_button(self):
        """Demoted to the guide page: whoever restores over SSH has a shell."""
        for option in details.options_for(can_dismiss=True, gist_available=True):
            assert "ssh" not in details.button_text(option).lower()

    def test_no_new_machine_button_after_an_import(self):
        """(D)'s UI half. ABSENT, not greyed: at a lathe with no terminal an
        inert control is indistinguishable from a broken one, which is the
        defect test_custom_popup.py exists because of."""
        options = details.options_for(can_dismiss=False, gist_available=True)
        assert keys(options) == [details.RESTART_OK]

    def test_the_new_machine_button_says_what_pressing_it_costs(self):
        """Pressing it opens the write gate for good; from then on what is
        typed is this machine's baseline. The button is where that is said."""
        new_machine = details.options_for(can_dismiss=True, gist_available=True)[0]
        caption = new_machine.caption.lower()
        assert "saving" in caption
        assert "measured" in caption
        assert new_machine.caption in details.button_text(new_machine)

    def test_every_button_label_is_short(self):
        for option in details.options_for(can_dismiss=True, gist_available=True):
            assert words(option.text) <= 7, option.text
            assert words(option.caption) <= 10, option.caption

    def test_the_widget_builds_one_button_per_option_with_its_text(self):
        options = build()
        assert list(options.buttons) == keys(options.options)
        for option in options.options:
            button = options.buttons[option.key]
            assert button.text == details.button_text(option)
            assert button.primary is option.primary


# ── what each button does ───────────────────────────────────────────────────

class TestNewMachine:
    def test_it_opens_the_gate_and_goes_to_setup(self, fresh_card, manager):
        fired = []
        options = build(on_dismissed=lambda: fired.append(1))

        options.buttons[details.NEW_MACHINE].release()

        assert commissioning_state.latched() is True
        assert commissioning_state.dismissal_marker_path(fresh_card).is_file()
        assert fired == [1], "the banner must be told the gate opened"
        manager.goto.assert_called_once_with("setup")

    def test_an_import_while_the_dialog_was_open_gets_the_restart_branch(
            self, tmp_path, fresh_card, manager, monkeypatch):
        """The dialog can sit open while the operator imports a bundle.
        Availability when it was BUILT is not the question the button answers."""
        source = tmp_path / "source"
        source.mkdir()
        (source / "Axis-1.yaml").write_text("axis_name: Z\nbacklash: 0.04\n")
        (source / "Els-0.yaml").write_text("spindle_axis_index: 0\n")
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(source))
        doc = commissioning_bundle.build(fw_rev="1.2.0")
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(fresh_card))

        fired = []
        options = build(on_dismissed=lambda: fired.append(1))
        assert commissioning_bundle.apply(doc, fresh_card).ok

        with patch.object(details, "open_details") as reopened:
            options.buttons[details.NEW_MACHINE].release()

        assert commissioning_state.latched() is False
        assert not commissioning_state.dismissal_marker_path(fresh_card).exists()
        assert fired == [], "the banner was told to collapse on a gate that never opened"
        manager.goto.assert_not_called()
        reopened.assert_called_once()


class TestTheRestores:
    def test_usb_goes_to_backup_and_starts_the_import(self, fresh_card, manager):
        options = build()

        options.buttons[details.RESTORE_USB].release()

        manager.goto.assert_called_once_with("backup")
        manager.get_screen.return_value.import_from_usb.assert_called_once()
        options._popup.dismiss.assert_called()

    def test_gist_goes_to_backup_and_starts_the_gist_restore(self, fresh_card, manager):
        options = build()

        options.buttons[details.RESTORE_GIST].release()

        manager.goto.assert_called_once_with("backup")
        manager.get_screen.return_value.restore_from_gist.assert_called_once()

    @pytest.mark.parametrize("key", [details.RESTORE_USB, details.RESTORE_GIST])
    def test_a_restore_never_opens_the_gate(self, key, fresh_card, manager):
        build().buttons[key].release()

        assert commissioning_state.latched() is False
        assert not commissioning_state.dismissal_marker_path(fresh_card).exists()


class TestTheQuietExits:
    def test_not_now_closes_and_changes_nothing(self, fresh_card, manager):
        options = build()

        options.buttons[details.NOT_NOW].release()

        options._popup.dismiss.assert_called_once()
        assert commissioning_state.latched() is False
        assert not commissioning_state.dismissal_marker_path(fresh_card).exists()
        manager.goto.assert_not_called()

    def test_the_restart_branch_ok_only_closes(self, fresh_card, manager):
        options = build(can_dismiss=False)

        options.buttons[details.RESTART_OK].release()

        options._popup.dismiss.assert_called_once()
        manager.goto.assert_not_called()

    def test_an_unknown_choice_is_loud(self):
        with pytest.raises(ValueError):
            build().choose("resize_partition")


# ── open_details reads the state itself ─────────────────────────────────────

class TestOpenDetailsPicksTheBranchItself:
    def test_an_ordinary_uncommissioned_card_gets_the_options(self, fresh_card):
        assert details.NEW_MACHINE in details.open_details().buttons

    def test_a_card_written_to_since_the_latch_gets_the_restart_branch(
            self, fresh_card):
        (fresh_card / "Axis-1.yaml").write_text("axis_name: Z\n")
        assert list(details.open_details().buttons) == [details.RESTART_OK]

    def test_gist_availability_comes_from_gist_sync(self, fresh_card, monkeypatch):
        monkeypatch.setattr(details.gist_sync, "is_configured", lambda: False)
        assert details.RESTORE_GIST not in details.open_details().buttons
