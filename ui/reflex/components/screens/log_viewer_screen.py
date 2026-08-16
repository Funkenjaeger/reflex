import os
from collections import deque

from kivy.logger import Logger
from kivy.properties import StringProperty
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

MAX_LINES = 500

# Lines per rendered Label. Each Label becomes one GL texture, and the Pi's
# limit is as low as 2048px; at font size 14 a line is ~18px, so 60 lines is
# ~1080px — comfortably inside the limit with room for wrapped long lines.
# Rendering the whole file as ONE Label is what made large logs invisible.
LINES_PER_CHUNK = 60
FONT_SIZE = 14


class LogViewerScreen(Screen):
    log_file_path = StringProperty("")
    log_content = StringProperty("")
    log_file_name = StringProperty("Log Viewer")

    def load_file(self, path: str):
        self.log_file_path = path
        self.log_file_name = os.path.basename(path)
        try:
            with open(path, "r", errors="replace") as f:
                lines = list(deque(f, maxlen=MAX_LINES))
            self.log_content = "".join(lines)
        except OSError as e:
            lines = [f"Error reading log file: {e}"]
            self.log_content = lines[0]

        if not lines:
            lines = ["(this log file is empty)"]
        self._render(lines)

    def on_pre_enter(self, *args):
        # Rebuild on entry: the widget tree may not have existed when
        # load_file() was called from the file list.
        if self.log_content and not self.ids.get("content", None):
            return
        if self.log_content:
            self._render(self.log_content.splitlines(keepends=True))

    def _render(self, lines):
        """Lay the file out as a stack of chunk Labels.

        See the note in the .kv: one Label for the whole file overflows the GL
        texture limit on large logs and renders as an invisible blank.
        """
        content = self.ids.get("content")
        if content is None:
            return
        content.clear_widgets()

        from reflex.app import MainApp
        app = MainApp.get_running_app()
        color = app.theme.text if app else (1, 1, 1, 1)

        for i in range(0, len(lines), LINES_PER_CHUNK):
            chunk = "".join(lines[i:i + LINES_PER_CHUNK]).rstrip()
            lbl = Label(
                text=chunk,
                color=color,
                font_size=FONT_SIZE,
                size_hint_y=None,
                halign="left",
                valign="top",
            )
            # Bind width -> text_size so wrapping tracks the viewport, and
            # texture_size -> height so the chunk claims exactly its own space.
            lbl.bind(
                width=lambda inst, w: setattr(inst, "text_size", (w, None)),
                texture_size=lambda inst, ts: setattr(inst, "height", ts[1]),
            )
            content.add_widget(lbl)