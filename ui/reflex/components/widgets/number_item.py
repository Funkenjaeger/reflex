from kivy.logger import Logger
from kivy.properties import (AliasProperty, BooleanProperty, NumericProperty,
                             StringProperty)
from reflex.components.popups.help_popup import HelpPopup  # noqa: F401
from kivy.uix.boxlayout import BoxLayout

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class NumberItem(BoxLayout):
    name = StringProperty("")
    value = NumericProperty(0.0)
    integer = BooleanProperty(False)
    help_file = StringProperty("")

    #: Fixed decimal places for the displayed value. -1 (the default) keeps the
    #: historical `str(value)` rendering EXACTLY, so every existing NumberItem
    #: in the app is untouched -- this is opt-in per field.
    #:
    #: Wanted because a scale resolution of "2" reads as an integer count of
    #: something rather than as a measurement; "2.000 um/count" is obviously a
    #: resolution. Evan, 2026-09-01.
    decimals = NumericProperty(-1)

    def _get_display(self):
        if self.decimals is None or self.decimals < 0:
            return str(self.value)
        try:
            return f"{float(self.value):.{int(self.decimals)}f}"
        except (TypeError, ValueError):
            # A field mid-edit must render something rather than take the
            # setup screen down.
            return str(self.value)

    display = AliasProperty(_get_display, None, bind=["value", "decimals"])

    def validate(self, value):
        try:
            if isinstance(value, str) and "." in value:
                self.value = float(value)
            elif isinstance(value, float):
                self.value = float(value)
            else:
                self.value = int(value)
        except Exception as e:
            log.error(str(e))

    def on_value(self, instance, value):
        self.validate(value)
