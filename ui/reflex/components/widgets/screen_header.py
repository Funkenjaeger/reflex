from kivy.logger import Logger
from kivy.properties import BooleanProperty
from kivy.uix.boxlayout import BoxLayout

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class ScreenHeader(BoxLayout):
    """A titled header with a Back button.

    ``back_disabled`` exists for the update screen: while a firmware flash is
    in progress the serial link is down on purpose, so navigating to Home
    would show a dead DRO with nothing on screen saying why. Defaults to False,
    so every other screen keeps the behaviour it has always had.
    """
    back_disabled = BooleanProperty(False)
