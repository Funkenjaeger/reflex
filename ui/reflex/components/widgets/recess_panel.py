"""Recessed / inset panel container for the UI facelift.

Gives a sunken look: a slightly darker fill with a dark top/left inner shadow
edge and a faint light bottom/right edge. Use it to wrap the status bar, the
DRO field, the central graphic, and value displays so they read as recessed
into the dark background.

``RecessPanel`` is a plain vertical ``BoxLayout`` -- add children as usual and
they sit inside the recess. ``radius`` and ``inset`` (the inner-shadow inset)
are tunable.

For an overlay-only treatment (when you can't reparent an existing widget),
the companion ``recess_panel.kv`` also defines a ``RecessFrame`` dynamic class
(``<RecessFrame@Widget>``) you can drop into a FloatLayout/RelativeLayout
behind existing content with ``pos``/``size`` bound to the target.
"""

from kivy.logger import Logger
from kivy.uix.boxlayout import BoxLayout
from kivy.properties import NumericProperty, ColorProperty

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)


class RecessPanel(BoxLayout):
    radius = NumericProperty(8)
    inset = NumericProperty(2)
    # Override the sunken fill if a given panel wants a different base tone.
    fill_color = ColorProperty([0.05, 0.07, 0.09, 1])


load_kv(__file__)
