"""The modal behind the strip: what it says, and which buttons it grows.

WHY THE WORDING IS UNDER TEST AT ALL. This dialog is the entire operator-facing
explanation of a state that otherwise presents as "the app looks fine and
silently forgets everything". Three facts have to survive any future edit of
the prose -- the card carries no measured configuration, the values on screen
are defaults rather than this machine, and settings are not being saved -- and
so do both ways out. A rewrite that drops one of them is not a style change,
it is the operator losing the only sentence that told them what to do. These
assert on keywords rather than sentences for exactly that reason: the drafting
stays free, the content does not.

WHAT IS EXECUTED AND WHAT IS READ. ``CustomPopup``'s kv carries a canvas and
its wrapped ``kivy.uix.popup.Popup`` builds real textures, either of which
segfaults the interpreter under this suite's mock GL backend -- so this file
patches both out, exactly as ``test_custom_popup.py`` does, and exercises the
python: which strings were passed, which buttons were wired, and whether the
confirm callback reaches ``commissioning_state.dismiss()``.

SEEN-RED -- see ``tests/utils/test_commissioning_dismissal.py``'s docstring for
the mutation list; the one that lands here is "allow dismissal after an
import", which turns ``test_no_dismiss_button_after_an_import`` red.
"""
from unittest.mock import MagicMock, patch

import pytest

from reflex.components.home import uncommissioned_details as details
from reflex.components.popups.custom_popup import CustomPopup
from reflex.utils import commissioning_bundle, commissioning_state


@pytest.fixture(autouse=True)
def _no_latch_leaks():
    commissioning_state.clear_latch()
    yield
    commissioning_state.clear_latch()


@pytest.fixture(autouse=True)
def _no_real_window():
    """Neither the kv rule tree nor the wrapped Popup survives mock GL."""
    with patch.object(CustomPopup, "apply_class_lang_rules"), \
         patch("reflex.components.popups.custom_popup.Popup", MagicMock()):
        yield


class TestTheThreeFacts:
    """Present in BOTH branches: the hazard changes what can be done about the
    state, never what the state is."""

    @pytest.mark.parametrize("can_dismiss", [True, False])
    def test_it_says_the_card_carries_no_measured_configuration(self, can_dismiss):
        text = details.details_message(can_dismiss=can_dismiss).lower()
        assert "no measured configuration" in text

    @pytest.mark.parametrize("can_dismiss", [True, False])
    def test_it_says_the_values_on_screen_are_defaults_not_this_machine(
            self, can_dismiss):
        text = details.details_message(can_dismiss=can_dismiss).lower()
        assert "default" in text
        assert "this lathe" in text or "this machine" in text

    @pytest.mark.parametrize("can_dismiss", [True, False])
    def test_it_says_settings_are_not_being_saved(self, can_dismiss):
        text = details.details_message(can_dismiss=can_dismiss).lower()
        assert "not being saved" in text or "not saved" in text


class TestBothWaysOutAreStated:
    def test_the_restore_exit_names_ssh_and_the_restart(self):
        text = details.details_message(can_dismiss=True).lower()
        assert "restore" in text
        assert "ssh" in text
        assert "restart" in text

    def test_the_restore_exit_says_the_warning_does_not_come_back(self):
        """Why the restore is the better exit, in the operator's terms: the
        capture clears the bar on the next boot, so there is nothing to undo
        and nothing left behind."""
        text = details.details_message(can_dismiss=True).lower()
        assert "not appear again" in text or "does not appear again" in text

    def test_the_dismiss_exit_is_named_and_says_what_it_costs(self):
        text = details.details_message(can_dismiss=True).lower()
        assert "dismiss" in text
        assert "by hand" in text
        assert "baseline" in text, (
            "dismissal without the consequence stated is an invitation to "
            "record guesses as this machine's commissioning baseline")

    def test_the_post_import_text_still_accounts_for_dismissal(self):
        """Both exits are stated even when one is closed -- an operator who
        came looking for the Dismiss button has to be told where it went, or
        the missing button is just a bug."""
        text = details.details_message(can_dismiss=False).lower()
        assert "dismiss" in text
        assert "restart" in text


class TestTheButtonsItGrows:
    def test_the_dismiss_branch_offers_dismiss_and_a_way_to_back_out(self):
        popup = details.build_details_popup(can_dismiss=True)
        assert popup.button_text == "Dismiss"
        assert popup.cancel_text, "no way to close the dialog without dismissing"
        assert popup.confirm_callback is not None

    def test_no_dismiss_button_after_an_import(self):
        """(D)'s UI half. ABSENT, not greyed: at a lathe with no terminal an
        inert control is indistinguishable from a broken one, which is the
        defect test_custom_popup.py exists because of."""
        popup = details.build_details_popup(can_dismiss=False)
        assert popup.confirm_callback is None
        assert popup.cancel_text == "", (
            "a second button in the no-dismiss branch is a control the "
            "operator has to guess the meaning of")
        assert "dismiss" not in popup.button_text.lower()

    def test_the_confirm_button_actually_reaches_dismiss(self, tmp_path,
                                                          monkeypatch):
        """Wired, not merely present -- the ELS 'Enable Feed' failure was a
        confirm button whose callback never fired."""
        card = tmp_path / "card"
        card.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(card))
        assert commissioning_state.latch(card) is False

        fired = []
        popup = details.build_details_popup(can_dismiss=True,
                                            on_dismissed=lambda: fired.append(1))
        with patch.object(popup, "dismiss"):
            popup.on_button_press()

        assert commissioning_state.latched() is True
        assert commissioning_state.dismissal_marker_path(card).is_file()
        assert fired == [1]

    def test_cancel_does_not_dismiss(self, tmp_path, monkeypatch):
        card = tmp_path / "card"
        card.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(card))
        commissioning_state.latch(card)

        popup = details.build_details_popup(can_dismiss=True)
        with patch.object(popup, "dismiss"):
            popup.on_cancel_press()

        assert commissioning_state.latched() is False
        assert not commissioning_state.dismissal_marker_path(card).exists()


class TestTheDialogReReadsTheStateWhenPressed:
    def test_an_import_between_opening_and_pressing_still_refuses(
            self, tmp_path, monkeypatch):
        """The dialog can sit open while the operator walks to another machine
        and imports a bundle. Availability at BUILD time is not the question
        the button is answering."""
        source = tmp_path / "source"
        source.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(source))
        (source / "Axis-1.yaml").write_text("axis_name: Z\nbacklash: 0.04\n")
        (source / "Els-0.yaml").write_text("spindle_axis_index: 0\n")
        doc = commissioning_bundle.build(fw_rev="1.2.0")

        card = tmp_path / "card"
        card.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(card))
        commissioning_state.latch(card)

        fired = []
        popup = details.build_details_popup(can_dismiss=True,
                                            on_dismissed=lambda: fired.append(1))
        assert commissioning_bundle.apply(doc, card).ok

        with patch.object(popup, "dismiss"):
            popup.on_button_press()

        assert commissioning_state.latched() is False
        assert not commissioning_state.dismissal_marker_path(card).exists()
        assert fired == [], "the banner was told to collapse on a gate that never opened"


class TestOpenDetailsPicksTheBranchItself:
    def test_an_ordinary_uncommissioned_card_gets_the_dismiss_branch(
            self, tmp_path, monkeypatch):
        card = tmp_path / "card"
        card.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(card))
        commissioning_state.latch(card)
        assert details.open_details().confirm_callback is not None

    def test_a_card_written_to_since_the_latch_gets_the_no_dismiss_branch(
            self, tmp_path, monkeypatch):
        card = tmp_path / "card"
        card.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(card))
        commissioning_state.latch(card)
        (card / "Axis-1.yaml").write_text("axis_name: Z\n")
        assert details.open_details().confirm_callback is None
