"""Small square Font Awesome icon button for the UI facelift.

Slate fill with a subtle border by default; pass ``is_highlighted: True`` for
the cyan-glow active look (matches :class:`StyledButton`). Set ``icon`` to a
Font Awesome glyph (e.g. ``"\\uf013"`` gear). The Font Awesome font is wired
in by default.

Example (KV)::

    IconButton:
        icon: "\\uf013"          # gear / settings
        on_release: app.manager.goto("setup_screen")

    IconButton:
        icon: "\\uf062"          # up arrow
        is_highlighted: True
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


class IconButton(BeepMixin, ButtonBehavior, FloatLayout):
    icon = StringProperty("")
    font_name = StringProperty("fonts/Font Awesome 6 Free-Solid-900.otf")
    # font_size auto-derives from height when 0 (the default); set to override.
    font_size = NumericProperty(0)
    radius = NumericProperty(8)
    is_highlighted = BooleanProperty(False)

    # Optional explicit icon color override (defaults follow the theme).
    icon_color = ColorProperty([0, 0, 0, 0])


load_kv(__file__)
