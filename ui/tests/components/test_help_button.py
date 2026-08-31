"""Tests for HelpButton (reflex/components/widgets/help_button.py).

The button is built for real — Kivy properties and all — with
``apply_class_lang_rules`` patched out, the same way test_els_resync_popup.py
builds ThreadResyncPopup: the kv rule tree pulls in a real child Label whose
texture the mock GL backend cannot service (see test_keypad.py's preamble for
the class of crash). The popup it opens is a real kivy Popup subclass with
the same problem, so ``HelpTextPopup`` is patched wholesale and the CONTRACT
pinned instead: which title and which text the button hands over, and that
the popup is actually opened.

WHAT THESE ARE GUARDING
-----------------------
The widget is the on-demand half of the base/help text split (bench
2026-08-24): the modals stripped their instructions to the doing and moved
the why behind this button. A HelpButton that silently dropped or crossed its
properties would turn "the detail is one tap away" into "the detail is gone"
— and nothing else would fail, because the base text still renders.
"""
from unittest.mock import patch

import pytest

import reflex.components.widgets.help_button as hb_mod
from reflex.components.widgets.help_button import HelpButton


@pytest.fixture
def button():
    def _make(**kwargs):
        with patch.object(HelpButton, "apply_class_lang_rules"):
            return HelpButton(**kwargs)
    return _make


def test_kv_properties_reach_the_widget(button):
    """The API the modals use: `HelpButton: help_title: ...; help_text: ...`."""
    b = button(help_title="Widening a groove", help_text="the why")
    assert b.help_title == "Widening a groove"
    assert b.help_text == "the why"


def test_opening_hands_the_popup_this_buttons_words(button):
    b = button(help_title="T", help_text="the moved detail")
    with patch.object(hb_mod, "HelpTextPopup") as popup_cls:
        b.open_help()
    popup_cls.assert_called_once_with(title="T", help_text="the moved detail")
    popup_cls.return_value.open.assert_called_once()


def test_release_is_what_opens_the_help(button):
    """The kv wires nothing: pressing the button IS the API. A rename of
    open_help that missed on_release would leave a button that beeps and does
    nothing."""
    b = button(help_title="T", help_text="X")
    with patch.object(hb_mod, "HelpTextPopup") as popup_cls:
        b.dispatch("on_release")
    popup_cls.return_value.open.assert_called_once()


def test_the_properties_are_live_not_construction_snapshots(button):
    """The modals bind these through kv (`help_text: root.help_text`), so a
    value assigned after construction must be the one the popup receives."""
    b = button()
    b.help_title = "Later title"
    b.help_text = "later text"
    with patch.object(hb_mod, "HelpTextPopup") as popup_cls:
        b.open_help()
    popup_cls.assert_called_once_with(title="Later title",
                                      help_text="later text")


def test_the_button_reads_no_app_state(button):
    """Deliberately dumb: the caller owns the words. Construction and opening
    must not reach MainApp.get_running_app() — a help surface that read the
    machine would be one more thing that could be wrong about it. (The beep
    on press is BeepMixin's, shared with every button in the app.)"""
    with patch("kivy.app.App.get_running_app") as get_app:
        b = button(help_title="T", help_text="X")
        with patch.object(hb_mod, "HelpTextPopup"):
            b.open_help()
    get_app.assert_not_called()
