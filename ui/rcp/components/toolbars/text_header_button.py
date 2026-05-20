from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.clock import Clock

from rcp.components.widgets.beep_mixin import BeepMixin
from rcp.utils.kv_loader import load_kv


class TextHeaderButton(BeepMixin, ButtonBehavior, BoxLayout):
    """A toolbar button with a text label above it.

    Emits two press events so callers can distinguish gestures:
    - on_short_press: a normal tap (press → release within the threshold)
    - on_long_press:  press held ≥ long_press_threshold seconds
    Binding `on_release` directly still works for callers that don't care
    about the distinction, but they will receive the release on both short
    and long gestures.
    """

    text_header = StringProperty("")
    text_button = StringProperty("")
    blink_enable = BooleanProperty(False)
    _blink = BooleanProperty(False)
    font_name = StringProperty("fonts/Manrope-Bold.ttf")
    long_press_threshold = NumericProperty(1.0)

    __events__ = ("on_short_press", "on_long_press")

    def __init__(self, **kv):
        super().__init__(**kv)
        Clock.schedule_interval(self.blinker, 1.0 / 4)
        self._long_press_event = None
        self._long_press_fired = False

    def blinker(self, *args):
        self._blink = not self._blink if self.blink_enable else False

    def on_press(self):
        # BeepMixin.on_press plays the press feedback sound.
        super().on_press()
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
