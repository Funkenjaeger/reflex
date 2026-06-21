from kivy.logger import Logger
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout

from reflex.components.popups.mode_popup import ModePopup
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

class HomeToolbar(BoxLayout):
    current_mode_desc = StringProperty("IDX")

    # P-offset presets quick-cycled by a tap; long-press opens the keypad for
    # the full offset range. Matches the mockup's P0–P3 tab group.
    OFFSET_PRESETS = 4

    def __init__(self, **kv):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super(HomeToolbar, self).__init__(**kv)
        self.app.bind(current_mode=self.update_current_mode)
        self.update_current_mode(None, self.app.current_mode)

    # def popup_scene(self, *_):
    #     ScenePopup().open()

    def update_current_mode(self, instance, value):
        if self.app.current_mode == 1:
            self.current_mode_desc = "IDX"
        if self.app.current_mode == 2:
            self.current_mode_desc = "ELS"
        if self.app.current_mode == 3:
            self.current_mode_desc = "JOG"
        if self.app.current_mode == 4:
            self.current_mode_desc = "DRO"

    def popup_mode(self, *_):
        ModePopup().show_with_callback(self.app.set_mode, self.app.current_mode)

    def cycle_offset(self, *_):
        """Tap: advance to the next P-offset preset (wraps at OFFSET_PRESETS)."""
        self.app.currentOffset = (self.app.currentOffset + 1) % self.OFFSET_PRESETS

    def cycle_mode(self, *_):
        """Tap: advance to the next mode allowed for the current use case."""
        modes = self.app.allowed_modes()
        if not modes:
            return
        try:
            i = modes.index(self.app.current_mode)
        except ValueError:
            i = -1
        self.app.set_mode(modes[(i + 1) % len(modes)])
