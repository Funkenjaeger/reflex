"""Tests for CustomPopup's confirm/cancel wiring.

These exist because of a silent failure mode that is easy to reintroduce:
Kivy's ``Widget.__init__`` pulls every kwarg whose name starts with ``on_`` out
of kwargs and passes it to ``self.bind()`` as an EVENT handler. A property named
``on_dismiss_callback`` therefore never receives its constructor value — it stays
None, and the dialog's primary button silently does nothing, behaving exactly
like Cancel. That shipped in the ELS "no armed ELS stop" feed dialog: the
operator pressed "Enable Feed" and the feed never enabled.

The point of a test here is that it fails if the callback does not RUN, so
rewiring the dialog (or renaming the property back into the ``on_*`` namespace)
cannot pass.
"""
from unittest.mock import MagicMock, patch

import pytest

from reflex.components.popups.custom_popup import CustomPopup


@pytest.fixture
def popup_factory():
    def _make(**kwargs):
        # apply_class_lang_rules pulls in the kv rule (and app.theme), and the
        # wrapped kivy Popup builds a real canvas/texture that the mock GL
        # backend can't service. The button wiring under test is the python side.
        with patch.object(CustomPopup, "apply_class_lang_rules"), \
             patch("reflex.components.popups.custom_popup.Popup", MagicMock()):
            return CustomPopup(**kwargs)
    return _make


def test_primary_button_runs_the_confirm_callback(popup_factory):
    fired = []
    popup = popup_factory(
        title="No ELS Stop Armed",
        message="Enable feed anyway?",
        button_text="Enable Feed",
        cancel_text="Cancel",
        confirm_callback=lambda: fired.append(True),
    )
    # The constructor value must reach the property, not be swallowed as a bind.
    assert popup.confirm_callback is not None
    with patch.object(popup, "dismiss"):
        popup.on_button_press()
    assert fired == [True], "confirm button did not run its callback"


def test_cancel_button_does_not_run_the_confirm_callback(popup_factory):
    fired = []
    popup = popup_factory(
        button_text="Enable Feed",
        cancel_text="Cancel",
        confirm_callback=lambda: fired.append(True),
    )
    with patch.object(popup, "dismiss"):
        popup.on_cancel_press()
    assert fired == []


def test_legacy_on_dismiss_callback_kwarg_is_repaired(popup_factory):
    """The old spelling is accepted and rerouted rather than silently eaten."""
    fired = []
    popup = popup_factory(
        button_text="OK",
        on_dismiss_callback=lambda: fired.append(True),
    )
    assert popup.confirm_callback is not None
    with patch.object(popup, "dismiss"):
        popup.on_button_press()
    assert fired == [True]


def test_kivy_swallows_on_prefixed_kwargs():
    """Pin the Kivy behaviour that caused the bug, so the naming constraint on
    `confirm_callback` is not just a comment.

    Renaming the property back into the ``on_*`` namespace — even consistently,
    with every reference updated — silently reintroduces the dead confirm
    button, because the constructor value never reaches the property at all.
    """
    from kivy.properties import ObjectProperty
    from kivy.uix.boxlayout import BoxLayout

    class _Probe(BoxLayout):
        on_some_callback = ObjectProperty(None, allownone=True)
        plain_callback = ObjectProperty(None, allownone=True)

    sentinel = lambda: None
    with patch.object(_Probe, "apply_class_lang_rules"):
        probe = _Probe(on_some_callback=sentinel, plain_callback=sentinel)

    assert probe.plain_callback is sentinel
    assert probe.on_some_callback is None, (
        "Kivy no longer swallows on_* kwargs — the naming rule on "
        "CustomPopup.confirm_callback can be revisited"
    )


def test_single_button_dialog_without_a_callback_still_dismisses(popup_factory):
    popup = popup_factory(title="Axis Not Configured", button_text="OK")
    with patch.object(popup, "dismiss") as dismiss:
        popup.on_button_press()
    dismiss.assert_called_once()
