from kivy.graphics import Color, Rectangle
from kivy.logger import Logger
from kivy.properties import StringProperty, ListProperty, ObjectProperty
from reflex.components.popups.help_popup import HelpPopup  # noqa: F401
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.dropdown import DropDown
from kivy.uix.button import Button

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class DropDownItem(BoxLayout):
    name = StringProperty("")
    value = StringProperty("")
    options = ListProperty([])
    help_file = StringProperty("")
    dropdown = ObjectProperty()
    main_button: Button = ObjectProperty()

    def __init__(self, **kv):
        super().__init__(**kv)
        from reflex.app import MainApp
        self.app = MainApp.get_running_app()
        self.dropdown = DropDown()
        self.dropdown.container.padding = [4, 4, 4, 4]
        self.dropdown.container.spacing = 2
        with self.dropdown.canvas.before:
            self._bg_color = Color(*self._theme("recess"))
            self._bg_rect = Rectangle()
        self.dropdown.bind(pos=self._update_bg, size=self._update_bg)
        self._options = []
        self.dropdown.bind(on_select=lambda instance, x: setattr(self, 'value', x))
        # Recolor the open dropdown live when the theme changes.
        if self.app is not None:
            self.app.theme.bind(recess=lambda _i, v: setattr(self._bg_color, "rgba", v))
            self.app.theme.bind(surface=lambda *_: self.on_options(self, self.options))

    def _theme(self, token, default=(0.05, 0.07, 0.09, 1)):
        if self.app is not None:
            return getattr(self.app.theme, token)
        return list(default)

    def _update_bg(self, *args):
        self._bg_rect.pos = self.dropdown.pos
        self._bg_rect.size = self.dropdown.size

    def delete_all_dropdown_options(self):
        for item in self._options:
            self.dropdown.remove_widget(item)

    def on_value(self, instance, value):
        self.main_button.text = value

    def on_options(self, instance, value):
        # Clean any existing
        self.delete_all_dropdown_options()

        from reflex.app import MainApp
        app = MainApp.get_running_app()
        font_size = app.formats.font_size if app else 24

        for item in self.options:
            btn = Button(
                text=item, size_hint_y=None, height=60,
                font_size=font_size, background_normal="", background_down="",
                background_color=self._theme("surface"),
                color=self._theme("text"),
                font_name=self._theme("font_bold", "fonts/ChakraPetch-SemiBold.ttf"),
            )
            btn.bind(on_release=lambda btn: self.dropdown.select(btn.text))
            self.dropdown.add_widget(btn)
            self._options.append(btn)
