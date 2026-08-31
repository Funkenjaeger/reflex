"""A small circular "?" that shows extended help text on demand.

Bench feedback 2026-08-24: the instructional text on the ELS modals was
verbose enough to scroll on the machine's 600 px screen, and reading the
bottom of it pushed the next-stage text off-screen. The fix splits every
instruction in two -- a base stripped to the DOING, always on screen, and the
WHY behind this button for the operator who wants it. This widget is the
second half of that split.

Deliberately dumb: two string properties and a popup that renders them.
Nothing here reaches app state -- the caller owns the words, and a help
surface that read the machine would be one more thing that could be wrong
about it.

Example (KV)::

    HelpButton:
        help_title: "Widening a groove"
        help_text: root.help_text
"""

from kivy.logger import Logger
from kivy.properties import StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.popup import Popup

from reflex.components.widgets.beep_mixin import BeepMixin
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)


class HelpTextPopup(Popup):
    """The popup a HelpButton opens: title chrome + scrollable text + Close.

    A separate class rather than a reuse of popups/help_popup.py's HelpPopup,
    which loads its text from a help FILE through the running app -- this one
    renders exactly the string its button was given, so the help stays next
    to the base text it was split from.
    """

    help_text = StringProperty("")


class HelpButton(BeepMixin, ButtonBehavior, FloatLayout):
    help_title = StringProperty("Help")
    help_text = StringProperty("")

    def on_release(self):
        self.open_help()

    def open_help(self):
        HelpTextPopup(title=self.help_title, help_text=self.help_text).open()


load_kv(__file__)
