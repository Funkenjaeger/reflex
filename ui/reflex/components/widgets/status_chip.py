"""A small flat tinted pill that reads as PRINTED STATUS, not a control.

The advanced ELS bar's status gutter carries two of these: the thread-reference
latch on the left, the thread-phase offset on the right. Both answer "what does
the machine currently hold?" and neither is pressable, so the treatment is
deliberately the opposite of every button on that bar -- no slate, no border, no
raised edge, no LED, no left accent rule. A flat low-alpha tint of one colour
with mono text on it, sized to its own content.

TWO WEIGHTS OF THE SAME COLOUR, not two colours. ``text`` (the label: what this
chip is about) is drawn in ``chip_color`` and ``value`` (the number, if there is
one) in a lighter tint of it, so a chip reads label-then-value at a glance
without introducing a second hue that would have to mean something.

``lit`` IS THE RELEVANCE FLAG, AND IT IS THE POINT OF THIS WIDGET.
A chip has three looks, not two, because the reference chip has three states.
In FEED mode the thread reference is still LATCHED -- the firmware clears
``referenceLatched`` only on an ``elsStop.enable`` 0->1 edge, and a mode switch
never writes ``enable`` -- it is simply not being used for anything right now.
Hiding the chip there would tell the operator his phase reference was lost
across a turn-feed-turn swap, which is exactly wrong and exactly what he needs
the cue for. So ``lit: False`` DARKENS the whole chip -- fill and both text
weights scale together toward the background -- leaving it present, legible and
visibly not-currently-in-force.

Note the metaphor: a chip is "lit" when its state is IN FORCE, not when its
subject is true. An unreferenced chip in threading mode is lit (that answer is
live and correct); a latched chip in feed mode is not. Which of the two colour
states a chip shows is the caller's business, passed in as ``chip_color``.

Example (KV)::

    StatusChip:
        text: "REF LATCHED" if root.controller.thread_ref_latched else "NO REFERENCE"
        chip_color: app.theme.accent if root.controller.thread_ref_latched else app.theme.text_dim
        lit: root.controller.is_threading
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.properties import (
    StringProperty,
    BooleanProperty,
    ColorProperty,
    ListProperty,
)

from reflex.utils.kv_loader import load_kv

# Fill alpha of the pill. Low enough that the chip is a tint on the recess
# rather than a filled control, high enough to bound the text at a glance.
FILL_ALPHA = 0.13

# How far the value text is blended toward white from `chip_color`. The value
# has to sit a step lighter than its label without becoming a second colour.
VALUE_LIGHTEN = 0.42

# Everything (fill and both texts) scales by this when `lit` is False. Over a
# dark background a lower alpha IS darker, which is the look wanted: the chip
# recedes without moving, resizing or changing what it says.
DIM = 0.42


def _lighten(rgba, amt: float):
    """Blend RGB toward white by ``amt``, keeping the source alpha."""
    return [rgba[i] + (1.0 - rgba[i]) * amt for i in range(3)] + [rgba[3]]


def _scale_alpha(rgba, amt: float):
    return [rgba[0], rgba[1], rgba[2], rgba[3] * amt]


class StatusChip(BoxLayout):
    text = StringProperty("")                        # the label half
    value = StringProperty("")                       # the number half (optional)
    chip_color = ColorProperty([0.38, 0.46, 0.53, 1])
    lit = BooleanProperty(True)                      # False -> darkened, see above

    # Derived, recomputed from the three above so the kv holds no arithmetic
    # and the dim applies to every layer from one place.
    fill_color = ListProperty([0, 0, 0, 0])
    label_color = ListProperty([1, 1, 1, 1])
    value_color = ListProperty([1, 1, 1, 1])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._recompute()

    def on_chip_color(self, *_):
        self._recompute()

    def on_lit(self, *_):
        self._recompute()

    def _recompute(self):
        c = self.chip_color
        dim = 1.0 if self.lit else DIM
        self.fill_color = [c[0], c[1], c[2], FILL_ALPHA * dim]
        self.label_color = _scale_alpha([c[0], c[1], c[2], 1], dim)
        self.value_color = _scale_alpha(_lighten(c, VALUE_LIGHTEN), dim)


load_kv(__file__)
