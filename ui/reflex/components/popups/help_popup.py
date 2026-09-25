import os

from kivy.logger import Logger
from kivy.properties import StringProperty
from kivy.uix.modalview import ModalView

import reflex
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

# Illustrations sit with the app's other images, one per help topic, named
# after the topic's help file: help/els_thread_resync.md is illustrated by
# pictures/help/els_thread_resync.png. Drop the file in and the topic gains a
# picture -- no code or kv change.
HELP_PICTURES = os.path.join(os.path.dirname(reflex.__file__), "pictures", "help")


def help_image_for(help_file: str) -> str:
    """Path to a topic's illustration, or "" when there isn't one.

    Most topics have no drawing, and a topic whose drawing has not been made
    yet resolves the same way. The existence check belongs here because Kivy
    renders a missing source as a blank texture with no error (the defect
    tests/components/test_move_image.py exists for) -- the popup would show a
    white rectangle instead of simply showing no picture.
    """
    if not help_file:
        return ""
    path = os.path.join(HELP_PICTURES, os.path.splitext(help_file)[0] + ".png")
    return path if os.path.isfile(path) else ""


class HelpPopup(ModalView):
    help_text = StringProperty("")
    help_image = StringProperty("")

    @staticmethod
    def show_help(help_file: str):
        if not help_file:
            return
        from reflex.app import MainApp
        app = MainApp.get_running_app()
        text = app.load_help(help_file)
        popup = HelpPopup(help_text=text, help_image=help_image_for(help_file))
        popup.open()
