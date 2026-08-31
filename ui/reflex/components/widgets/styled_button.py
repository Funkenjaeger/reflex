from kivy.logger import Logger
from kivy.uix.button import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.properties import (
    StringProperty,
    ColorProperty,
    NumericProperty,
    BooleanProperty,
)

from reflex.components.widgets.beep_mixin import BeepMixin
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class StyledButton(BeepMixin, ButtonBehavior, FloatLayout):
    """Themed button matching the UI-facelift mockup.

    Two looks, selected via :attr:`is_highlighted`:
      * default     -- flat dark "slate" fill, subtle border (e.g. Engage, ADV)
      * highlighted -- cyan-glow border + accent text (e.g. the active Cut button)

    All styling lives in ``styled_button.kv``; this class only declares the
    knobs. Beep-on-press comes from :class:`BeepMixin` (do not override
    ``on_press`` without calling ``super()`` or the beep is lost).
    """

    text = StringProperty("")
    font_size = NumericProperty(20)
    font_name = StringProperty("fonts/ChakraPetch-SemiBold.ttf")
    radius = NumericProperty(8)
    # Inset the drawn box from the widget's cell, so a StyledButton can share a
    # uniform gutter with the inset LED cards in the same row.
    margin_x = NumericProperty(0)
    margin_y = NumericProperty(0)

    # Label colour while disabled. Its own property rather than an opacity,
    # because dimming the whole widget collapses the label into the fill --
    # see the note in styled_button.kv.
    disabled_text_color = ColorProperty([0.4, 0.44, 0.48, 1])

    # When True, render the cyan accent / glow style instead of the slate style.
    is_highlighted = BooleanProperty(False)

    # When True, the button is fully hidden (opacity 0). Use this -- not an
    # instance-level ``opacity:`` rule -- to collapse a StyledButton, because the
    # class kv already binds ``opacity`` to ``disabled``. A second ``opacity:``
    # binding would fight that one, and the disabled-dim can win, leaving a
    # faint zero-width ghost of the label. Folding both into one expression keeps
    # a single opacity binding.
    hidden = BooleanProperty(False)

    # ── Slate (default) palette ────────────────────────────────────────────
    slate_fill = ColorProperty([0.10, 0.13, 0.17, 1])
    slate_sheen = ColorProperty([0.22, 0.27, 0.32, 1])  # subtle inner top bevel
    standard_border_color = ColorProperty([0.27, 0.34, 0.41, 1])
    standard_text_color = ColorProperty([0.62, 0.78, 0.85, 1])

    # ── Highlight (cyan accent) palette ────────────────────────────────────
    highlight_bg_color = ColorProperty([0.07, 0.20, 0.25, 1])
    highlight_border_color = ColorProperty([0.16, 0.82, 0.88, 1])
    highlight_text_color = ColorProperty([0.40, 0.92, 0.98, 1])
    glow_color = ColorProperty([0.16, 0.82, 0.88, 0.22])
