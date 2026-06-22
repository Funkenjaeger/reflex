"""Slate "card" button with a consistent status LED, used across the ELS bars.

One widget for the recurring pattern: a rounded slate card (cyan-tinted when
``highlighted``) with a small round status LED inset square from the top-left
corner, optional status text vertically centred on that LED, an optional
centred image, and a main label. Sync Enable, ADV and Engage all use it so the
LED placement, insets and fills stay identical. More bespoke one-hots (DIR,
feed/thread, the wizard/stop mode tab) stay custom.

Example (KV)::

    LedCardButton:
        text: "ADV"
        highlighted: root.enable_advanced
        led_color: app.theme.accent if root.enable_advanced else app.theme.border_dim
        on_release: root.enable_advanced = not root.enable_advanced
"""

from kivy.logger import Logger
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.properties import (
    StringProperty,
    NumericProperty,
    BooleanProperty,
    ColorProperty,
)

from reflex.components.widgets.beep_mixin import BeepMixin
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)


class LedCardButton(BeepMixin, ButtonBehavior, FloatLayout):
    text = StringProperty("")               # main label (supports "\n")
    font_size = NumericProperty(17)
    image_source = StringProperty("")       # optional centred image
    led_color = ColorProperty([0, 0, 0, 0])  # alpha 0 -> LED hidden
    status_text = StringProperty("")        # optional text beside the LED
    status_color = ColorProperty([0.6, 0.6, 0.6, 1])
    highlighted = BooleanProperty(False)    # cyan-tinted fill + border + label
    radius = NumericProperty(7)


load_kv(__file__)
