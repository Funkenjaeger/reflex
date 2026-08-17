from kivy.logger import Logger
from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout

from rcp.components.widgets.beep_mixin import BeepMixin
from rcp.utils.kv_loader import load_kv

log = Logger.getChild(__name__)


class TwoStateButton(BeepMixin, ButtonBehavior, BoxLayout):
    """A square toolbar button with two stacked labels representing boolean states.

    The active state's label is shown in the normal display color; the inactive
    state's label is muted.  Releasing the button toggles ``value``.
    """

    value = BooleanProperty(False)
    label_true = StringProperty("ON")
    label_false = StringProperty("OFF")
    blink = BooleanProperty(False)
    font_name = StringProperty("fonts/Manrope-Bold.ttf")



load_kv(__file__)
