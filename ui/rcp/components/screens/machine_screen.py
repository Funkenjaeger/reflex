from kivy.logger import Logger
from kivy.uix.screenmanager import Screen

from rcp.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class MachineScreen(Screen):
    def __init__(self, **kv):
        from rcp.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kv)

    def on_pre_enter(self, *args):
        from rcp.app import USE_CASE_LABELS
        self.ids.use_case_dropdown.options = list(USE_CASE_LABELS.values())
        self.ids.use_case_dropdown.value = USE_CASE_LABELS.get(self.app.use_case, "")

    def on_use_case_selected(self, instance, value):
        from rcp.app import USE_CASE_LABELS
        label_to_key = {label: key for key, label in USE_CASE_LABELS.items()}
        key = label_to_key.get(value)
        if key is None or key == self.app.use_case:
            return
        # Setting use_case triggers MainApp.on_use_case, which re-validates the
        # active mode (falling back to DRO if it is no longer valid).
        self.app.use_case = key
