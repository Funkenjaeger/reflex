import glob
import os
import re

from kivy.clock import Clock
from kivy.logger import Logger, FileHandler
from kivy.properties import ListProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

_LOG_NAME_RE = re.compile(r"kivy_(\d+)-(\d+)-(\d+)_(\d+)\.txt$")


def _log_sort_key(path):
    """Newest-first by the FILENAME's (date, run counter), mtime only as a
    fallback for foreign names. The kiosk loses power mid-write as a matter
    of routine (machine power is common to the Pi), and journal replay can
    leave the dead log's mtime NEWER than the live one — which is how the
    current session's log sorted second in the browser on 2026-08-17. The
    name counter is ground truth kivy itself maintains; mtime is not."""
    m = _LOG_NAME_RE.search(os.path.basename(path))
    if m:
        yy, mm, dd, n = (int(g) for g in m.groups())
        return (1, yy, mm, dd, n)
    try:
        return (0, 0, 0, 0, os.path.getmtime(path))
    except OSError:
        return (0, 0, 0, 0, 0)


class LogsPanel(BoxLayout):
    log_files = ListProperty([])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Clock.schedule_once(lambda dt: self.refresh_logs())

    @staticmethod
    def get_log_dir() -> str:
        for h in Logger.root.handlers:
            if isinstance(h, FileHandler):
                return os.path.dirname(h.filename)
        # Fallback: use Kivy's default log directory
        from kivy.config import Config
        kivy_home = os.environ.get("KIVY_HOME", os.path.expanduser("~/.kivy"))
        return Config.get("kivy", "log_dir") or os.path.join(kivy_home, "logs")

    def refresh_logs(self):
        log_dir = self.get_log_dir()
        pattern = os.path.join(log_dir, "kivy_*.txt")
        files = sorted(glob.glob(pattern), key=_log_sort_key, reverse=True)
        self.log_files = files
        self._rebuild_file_list()

    def _rebuild_file_list(self):
        file_list = self.ids.get("file_list")
        if not file_list:
            return
        file_list.clear_widgets()

        from reflex.app import MainApp
        app = MainApp.get_running_app()
        font_size = app.formats.font_size if app else 24

        if not self.log_files:
            file_list.add_widget(Button(
                text="No log files found",
                size_hint_y=None, height=60,
                font_size=font_size, disabled=True,
            ))
            return

        for path in self.log_files:
            filename = os.path.basename(path)
            btn = Button(
                text=filename,
                size_hint_y=None, height=60,
                font_size=font_size,
            )
            btn.bind(on_release=lambda b, p=path: self._open_log(p))
            file_list.add_widget(btn)

    def _open_log(self, path: str):
        from reflex.app import MainApp
        app = MainApp.get_running_app()
        app.log_viewer.load_file(path)
        app.manager.goto("log_viewer")