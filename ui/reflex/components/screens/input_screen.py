from kivy.logger import Logger
from kivy.properties import ObjectProperty, StringProperty
from kivy.uix.screenmanager import Screen

from reflex.utils.input_axis_map import input_axis_label
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class InputScreen(Screen):
    input = ObjectProperty()

    #: Which axis this input feeds, for the header. Read-only annotation --
    #: assignment still lives on the Axes side, deliberately (Evan, 2026-08-31:
    #: no second way to do the same thing). Empty when nothing claims it.
    axis_label = StringProperty("")

    def on_pre_enter(self, *args):
        """Recomputed on entry rather than bound.

        The mapping changes only when an axis is reconfigured, which happens on
        a different screen -- so there is nothing to observe live from here, and
        a binding across every axis's transform would be a lot of machinery for
        a string that cannot change while this screen is up.
        """
        self._refresh_axis_label()

    def _refresh_axis_label(self):
        from reflex.app import MainApp
        app = MainApp.get_running_app()
        index = getattr(self.input, "inputIndex", None)
        if app is None or index is None:
            self.axis_label = ""
            return
        self.axis_label = input_axis_label(app.axes, index)
