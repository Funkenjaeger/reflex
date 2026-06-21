"""Circular outline button for the UI facelift (e.g. the per-axis "Zero" keys).

A cyan outline ring with centered text on the dark background. When
``is_highlighted`` is True the ring fills with a faint cyan tint and glows,
otherwise it is a slate-tinted outline. Press feedback (the brief held look)
is driven by ButtonBehavior's ``state``.

Press/release callbacks come through normal ButtonBehavior events; the beep
is handled by :class:`BeepMixin`. The drawn diameter tracks the smaller of
width/height so it stays a true circle in any cell.

Example (KV)::

    CircularButton:
        text: "Zero"
        on_press: root.on_zero_press()
        on_release: root.on_zero_release()
"""

from kivy.logger import Logger
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.properties import (
    StringProperty,
    NumericProperty,
    BooleanProperty,
)

from reflex.components.widgets.beep_mixin import BeepMixin
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)


class CircularButton(BeepMixin, ButtonBehavior, FloatLayout):
    text = StringProperty("Zero")
    font_size = NumericProperty(20)
    font_name = StringProperty("fonts/Manrope-Bold.ttf")
    is_highlighted = BooleanProperty(False)


load_kv(__file__)
