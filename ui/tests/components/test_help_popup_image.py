"""Tests for the optional illustration on a help topic (popups/help_popup.py).

WHAT THESE ARE GUARDING
-----------------------
Help rendered raw text only, so a finished threading diagram had nowhere to
go. The contract added is deliberately tiny: a topic's picture is
`pictures/help/<the topic's help file, .md -> .png>`, resolved by name, with
no registry to update -- an artist drops a file in and that topic gains a
picture.

The failure mode worth a test is the one that is silent. Kivy renders a
missing image source as a blank texture with no exception and no log line
(the defect test_move_image.py exists for), so a topic that names a drawing
nobody has made yet would show a white rectangle where the picture belongs.
Resolution therefore gates on the file EXISTING, and both the "no drawing"
and "drawing not made yet" paths must come back as no picture at all.

The PNGs here are 4x4 solid grey, built in the fixture. They are stand-ins for
a file existing on disk, not artwork -- the drawings are Evan's.
"""
from unittest.mock import MagicMock, patch

import pytest

import reflex.components.popups.help_popup as hp_mod
from reflex.components.popups.help_popup import HelpPopup, help_image_for


@pytest.fixture
def pictures(tmp_path, monkeypatch):
    """Redirect the illustration directory at a tmp dir, and hand back a
    maker for solid-colour PNGs in it."""
    monkeypatch.setattr(hp_mod, "HELP_PICTURES", str(tmp_path))

    def _draw(name):
        from PIL import Image
        path = tmp_path / name
        Image.new("RGB", (4, 4), (128, 128, 128)).save(path)
        return str(path)

    return _draw


def test_a_topic_with_a_drawing_resolves_to_it(pictures):
    """The naming rule, and the whole API: same stem, .md -> .png."""
    drawn = pictures("els_thread_resync.png")
    assert help_image_for("els_thread_resync.md") == drawn


def test_a_topic_with_no_drawing_has_no_picture(pictures):
    """Every topic today. Not an error -- most help is words."""
    pictures("els_thread_resync.png")
    assert help_image_for("els_phase_offset.md") == ""


def test_a_drawing_that_is_not_there_yet_has_no_picture(pictures):
    """The missing-file path, held apart from the no-drawing one because it is
    the one that fails silently: hand the kv a path that does not exist and
    Kivy draws a blank texture with no error at all."""
    drawn = pictures("els_thread_resync.png")
    import os
    os.remove(drawn)
    assert help_image_for("els_thread_resync.md") == ""


def test_no_help_file_asks_for_nothing():
    assert help_image_for("") == ""


def test_show_help_hands_the_popup_the_illustration(pictures, monkeypatch):
    """The wiring. Without it the property is set by nobody and every popup is
    text-only, which is the state this change is fixing."""
    drawn = pictures("els_thread_resync.png")
    app = MagicMock()
    app.load_help.return_value = "the prose"
    monkeypatch.setattr("kivy.app.App.get_running_app", staticmethod(lambda: app))

    with patch.object(hp_mod, "HelpPopup") as popup_cls:
        HelpPopup.show_help("els_thread_resync.md")

    popup_cls.assert_called_once_with(help_text="the prose", help_image=drawn)
    popup_cls.return_value.open.assert_called_once()


def test_show_help_still_opens_when_the_drawing_is_missing(pictures, monkeypatch):
    """A help popup that crashed because an artist has not drawn something yet
    would be worse than no feature."""
    app = MagicMock()
    app.load_help.return_value = "the prose"
    monkeypatch.setattr("kivy.app.App.get_running_app", staticmethod(lambda: app))

    with patch.object(hp_mod, "HelpPopup") as popup_cls:
        HelpPopup.show_help("els_phase_offset.md")

    popup_cls.assert_called_once_with(help_text="the prose", help_image="")
    popup_cls.return_value.open.assert_called_once()


def test_the_kv_actually_renders_the_property():
    """A StringProperty nothing draws is a dead property, and no Python test
    can see that -- the picture reaches the operator through the kv or not at
    all. Same shape as test_move_image.py's source check."""
    import os
    kv = os.path.splitext(hp_mod.__file__)[0] + ".kv"
    with open(kv, "r") as f:
        src = f.read()
    assert "source: root.help_image" in src
