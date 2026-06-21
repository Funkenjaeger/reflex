from kivy.logger import Logger
from kivy.properties import BooleanProperty, StringProperty, ColorProperty, NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout

from reflex.components.widgets.beep_mixin import BeepMixin
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class LedButton(BeepMixin, ButtonBehavior, BoxLayout):
    """Status indicator: a small glowing LED dot beside a text label.

    Restyled for the UI facelift: a recessed dark slate pill with a small
    round LED on the left (lit in ``current_color``) and a label to its right.
    The public API is unchanged so existing call sites keep working:

      * ``label``           -- the (possibly multi-line) label text
      * ``checkbox_value``  -- bool; with the default KV binding picks
                               ``color_on``/``color_off`` for the LED
      * ``current_color``   -- LED color (override to drive it directly)
      * ``color_on`` / ``color_off`` -- palette ends (set in KV from app.formats)

    New, optional knobs for the richer mockup labels:

      * ``icon``      -- a Font Awesome glyph drawn before the label
      * ``two_line``  -- when True, label wraps to two lines (left-aligned)
    """

    label = StringProperty()
    checkbox_value = BooleanProperty()
    current_color = ColorProperty((0, 0, 0, 1))
    color_on = ColorProperty((0.16, 0.82, 0.88, 1))
    color_off = ColorProperty((0.25, 0.30, 0.36, 1))
    icon = StringProperty("")
    two_line = BooleanProperty(False)
    font_size = NumericProperty(16)
    font_name = StringProperty("fonts/Manrope-Bold.ttf")
