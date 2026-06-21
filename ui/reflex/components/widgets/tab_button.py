"""Tab-styled single button for the sidebar (P-offset, mode selector).

Looks like a *selected* ``TabSegment`` -- slate body rounded on its outer
(left) edge, flush on the right, cyan text + a cyan accent bar on the right
edge -- but behaves as a standalone control rather than a member of a one-hot
``TabSelector`` group:

  * short tap  -> ``on_short_press``  (cycle the value)
  * long press -> ``on_long_press``   (open the existing keypad / mode popup)

This fits controls that present a single current value (P-offset, mode) which
the mockup draws as a highlighted tab, but which aren't simple one-hot groups.
The long-press mechanism mirrors ``TextHeaderButton``.
"""

from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import StringProperty, NumericProperty, BooleanProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout

from reflex.components.widgets.beep_mixin import BeepMixin
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)


class TabButton(BeepMixin, ButtonBehavior, FloatLayout):
    text = StringProperty("")
    font_size = NumericProperty(18)
    font_name = StringProperty("fonts/Manrope-Bold.ttf")
    # These controls always show their current value, so they read as "active"
    # (cyan text + accent) by default. Set False for a dim/slate look.
    is_active = BooleanProperty(True)
    edge = StringProperty("right")
    long_press_threshold = NumericProperty(0.6)

    __events__ = ("on_short_press", "on_long_press")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._long_press_event = None
        self._long_press_fired = False

    def on_press(self):
        super().on_press()  # BeepMixin: press feedback beep
        self._long_press_fired = False
        if self._long_press_event is not None:
            self._long_press_event.cancel()
        self._long_press_event = Clock.schedule_once(
            self._fire_long_press, self.long_press_threshold
        )

    def on_release(self):
        if self._long_press_event is not None:
            self._long_press_event.cancel()
            self._long_press_event = None
        if not self._long_press_fired:
            self.dispatch("on_short_press")

    def _fire_long_press(self, _dt):
        self._long_press_event = None
        self._long_press_fired = True
        self.dispatch("on_long_press")

    def on_short_press(self):
        pass

    def on_long_press(self):
        pass


load_kv(__file__)
