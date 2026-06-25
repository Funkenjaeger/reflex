from kivy.logger import Logger
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.properties import NumericProperty, StringProperty, ColorProperty

from reflex.components.widgets.beep_mixin import BeepMixin
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)


class KeypadIconButton(BeepMixin, ButtonBehavior, FloatLayout):
    """Keypad key that shows a Font Awesome glyph (backspace, +/-, accept,
    cancel).

    The glyph lives in a CHILD Label whose font is the theme icon font, set in
    this widget's own kv rule. A button/label that carries the glyph as its own
    text loses a rule-priority race against the global ``<Button>`` font rule in
    home_screen.kv (which forces Manrope and renders the glyph as tofu) -- the
    same race IconButton and FormHelpButton sidestep by deferring the glyph to a
    child Label.
    """

    return_value = NumericProperty(0)
    # `text` is the glyph; `color` is the optional accept/cancel tint (alpha 0
    # means "follow the theme text color"). Both feed the child Label in the kv.
    text = StringProperty("")
    color = ColorProperty([0, 0, 0, 0])
    # font_size auto-derives from the keypad font size in kv when 0.
    font_size = NumericProperty(0)


load_kv(__file__)
