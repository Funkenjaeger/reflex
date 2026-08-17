import os

from kivy.graphics import Color, Rectangle
from kivy.logger import Logger
from kivy.properties import StringProperty, ObjectProperty, ListProperty, BooleanProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

_fonts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "fonts")
_available_fonts = sorted([
    f"fonts/{f}" for f in os.listdir(_fonts_dir)
    if f.endswith((".ttf", ".otf")) and "Font Awesome" not in f
])

FONT_PREVIEW_TEXT = "+123.456"


def _font_display_name(font_path: str) -> str:
    return os.path.splitext(os.path.basename(font_path))[0]


class FontPickerEntry(BoxLayout):
    font_path = StringProperty("")
    selected = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        from reflex.app import MainApp
        self.app = MainApp.get_running_app()
        self.orientation = "horizontal"
        self.size_hint_y = None
        self.height = 50

        # Background
        with self.canvas.before:
            self._bg_color = Color(*self.app.theme.surface)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._update_bg, size=self._update_bg, selected=self._update_bg)
        self.app.theme.bind(surface=self._update_bg, accent_bg=self._update_bg)

        # Name label on the left
        self._name_label = Label(
            size_hint_x=0.35,
            halign="left",
            valign="middle",
            shorten=True,
            shorten_from="right",
            padding=[10, 0],
            color=self.app.theme.text_dim,
        )
        self.app.theme.bind(text_dim=lambda _i, v: setattr(self._name_label, "color", v))
        self._name_label.font_name = "fonts/Manrope-Bold.ttf"
        self._name_label.font_size = 18

        # Preview label on the right
        self._preview_label = Label(
            size_hint_x=0.65,
            text=FONT_PREVIEW_TEXT,
            halign="center",
            valign="middle",
            color=self.app.theme.text,
        )
        self._preview_label.font_size = 24
        self.app.theme.bind(text=lambda _i, v: setattr(self._preview_label, "color", v))

        self.add_widget(self._name_label)
        self.add_widget(self._preview_label)

        self.bind(font_path=self._update_content)
        self._name_label.bind(size=self._update_text_size)

    def _update_content(self, *args):
        self._name_label.text = _font_display_name(self.font_path)
        self._preview_label.font_name = self.font_path

    def _update_text_size(self, *args):
        self._name_label.text_size = self._name_label.size

    def _update_bg(self, *args):
        if self.selected:
            self._bg_color.rgba = self.app.theme.accent_bg
        else:
            self._bg_color.rgba = self.app.theme.surface
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size


class FontPickerScreen(Screen):
    font_path = StringProperty("fonts/iosevka-regular.ttf")
    callback = ObjectProperty()
    available_fonts = ListProperty(_available_fonts)

    def on_enter(self, *args):
        grid = self.ids.font_grid
        grid.clear_widgets()
        self._entries = []
        for font_path in self.available_fonts:
            entry = FontPickerEntry()
            entry.font_path = font_path
            entry.selected = (font_path == self.font_path)
            entry.bind(on_touch_down=lambda w, t, fp=font_path: self._select(fp) if w.collide_point(*t.pos) else None)
            grid.add_widget(entry)
            self._entries.append(entry)

    def _select(self, font_path: str):
        self.font_path = font_path
        for entry in self._entries:
            entry.selected = (entry.font_path == font_path)
