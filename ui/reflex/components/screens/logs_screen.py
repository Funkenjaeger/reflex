from kivy.logger import Logger
from kivy.uix.screenmanager import Screen

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class LogsScreen(Screen):
    def on_pre_enter(self, *args):
        """Re-list the log files every time the screen is shown. The panel
        used to populate once at app construction and never again, so the
        list was a startup snapshot: files created later never appeared and
        the ordering aged with it."""
        panel = self.ids.get("logs_panel")
        if panel is not None:
            panel.refresh_logs()