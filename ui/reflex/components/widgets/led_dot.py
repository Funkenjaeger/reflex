"""A small domed-lens status LED (the "B / domed lens" treatment).

Replaces the old flat filled circle with a glossy dome: a 4-stop radial shade
that's bright off-centre (top-left crown) and darkens toward the bottom edge,
plus a small specular highlight. The dome is faked with a few concentric,
up-left-shifted ``Ellipse``s (a true radial gradient isn't available in Kivy and
the stepping is imperceptible at ~9 px).

The outer GLOW is intentionally NOT part of this widget -- callers keep their
own glow layer (drawn behind the dot) unchanged.

The four stop colours are derived from the LED's base ``led_color`` per the
handoff recipe, so one widget covers every indicator (amber / cyan / grey-off /
danger / success) just by passing a different colour and ``lit`` flag.
"""

from kivy.uix.widget import Widget
from kivy.properties import (
    ColorProperty, BooleanProperty, NumericProperty, ListProperty,
)

from reflex.utils.kv_loader import load_kv


def _mix(rgb, target: float, amt: float):
    """Linear blend of each RGB channel toward grey ``target`` by ``amt``."""
    return [rgb[i] * (1 - amt) + target * amt for i in range(3)] + [1]


class LedDot(Widget):
    led_color = ColorProperty([0.5, 0.5, 0.5, 1])   # base colour C
    lit = BooleanProperty(False)                    # drives the highlight alpha
    diameter = NumericProperty(9)                   # dot size in px (callers may pass dp())

    # Derived 4-stop dome colours (recomputed from led_color).
    crown = ListProperty([1, 1, 1, 1])              # mix(C, white, 0.72)
    face = ListProperty([1, 1, 1, 1])               # mix(C, white, 0.28)
    base = ListProperty([0.5, 0.5, 0.5, 1])         # C
    edge = ListProperty([0.25, 0.25, 0.25, 1])      # mix(C, black, 0.50)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._recompute()

    def on_led_color(self, *_):
        self._recompute()

    def _recompute(self):
        c = self.led_color
        self.crown = _mix(c, 1.0, 0.72)
        self.face = _mix(c, 1.0, 0.28)
        self.base = [c[0], c[1], c[2], 1]
        self.edge = _mix(c, 0.0, 0.50)


load_kv(__file__)
