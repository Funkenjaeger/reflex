import re

from kivy.clock import Clock
from kivy.properties import ObjectProperty
from kivy.uix.boxlayout import BoxLayout

from reflex.utils.kv_loader import load_kv

load_kv(__file__)

LONG_PRESS_THRESHOLD = 1.0

_DIGITS = re.compile(r"\d")


def all_segments(text: str) -> str:
    """Mask every digit to '8' so a dim copy behind a 7-seg readout lights all
    segments -- the authentic LCD "off-segment ghost". Sign and decimal point
    are kept so the ghost lines up with the live value. Shared by the DRO rows
    and the RPM readout (imported into both KV files)."""
    return _DIGITS.sub("8", text or "")


class CoordBar(BoxLayout):
    """Pure UI widget displaying axis state. All logic lives in AxisDispatcher."""
    axis = ObjectProperty()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._long_press_event = None

    @staticmethod
    def all_segments(text: str) -> str:
        return all_segments(text)

    def update_position(self):
        if self.axis is not None:
            self.axis.update_position()

    def toggle_sync(self):
        if self.axis is not None:
            from reflex.app import MainApp
            app = MainApp.get_running_app()
            self.axis.toggle_sync(all_axes=list(app.axes))

    def on_zero_press(self):
        self._long_press_event = Clock.schedule_once(self._do_undo_zero, LONG_PRESS_THRESHOLD)

    def on_zero_release(self):
        if self._long_press_event is not None:
            self._long_press_event.cancel()
            self._long_press_event = None
            if self.axis is not None:
                self.axis.zero_position()

    def _do_undo_zero(self, dt):
        self._long_press_event = None
        if self.axis is not None:
            self.axis.undo_zero()
