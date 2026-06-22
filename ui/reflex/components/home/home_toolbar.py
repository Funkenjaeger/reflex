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
        modes = self.app.allowed_modes()
        # Group height = sum of segment weights (selected 1.0 + unselected 0.68
        # each), matching the static groups in the KV so selected rows stay a
        # uniform height across the sidebar.
        n = max(len(modes), 1)
        tabs.size_hint_y = 1.0 + (n - 1) * 0.68
        for mode_id in modes:
            tabs.add_widget(
                TabSegment(text=MODE_LABELS.get(mode_id, "?"), value=mode_id)
            )
