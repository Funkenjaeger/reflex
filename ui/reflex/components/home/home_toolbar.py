from kivy.logger import Logger
from kivy.uix.boxlayout import BoxLayout

from reflex.components.widgets.tab_selector import TabSegment
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

# current_mode int -> sidebar tab label.
MODE_LABELS = {1: "IDX", 2: "ELS", 3: "JOG", 4: "DRO"}


class HomeToolbar(BoxLayout):
    def __init__(self, **kv):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kv)
        # Modes available depend on the use case, so the mode tab segments are
        # built dynamically and rebuilt if the use case changes.
        self.app.bind(use_case=self._rebuild_mode_tabs)

    def on_kv_post(self, base_widget):
        self._rebuild_mode_tabs()

    def _rebuild_mode_tabs(self, *_):
        tabs = self.ids.get("mode_tabs")
        if tabs is None:
            return
        tabs.clear_widgets()
        for mode_id in self.app.allowed_modes():
            tabs.add_widget(
                TabSegment(text=MODE_LABELS.get(mode_id, "?"), value=mode_id)
            )
