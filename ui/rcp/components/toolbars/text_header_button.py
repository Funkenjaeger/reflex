from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.clock import Clock

from rcp.components.widgets.beep_mixin import BeepMixin
from rcp.utils.kv_loader import load_kv


class TextHeaderButton(BeepMixin, ButtonBehavior, BoxLayout):
    """A toolbar button with a text label above it.
    """

    text_header = StringProperty("")
    text_button = StringProperty("")
    blink_enable = BooleanProperty(False)
    _blink = BooleanProperty(False)
    font_name = StringProperty("fonts/Manrope-Bold.ttf")    

    def __init__(self, **kv):
        super().__init__(**kv)
        Clock.schedule_interval(self.blinker, 1.0 / 4)

    def blinker(self, *args):
        self._blink = not self._blink if self.blink_enable else False

load_kv(__file__)
