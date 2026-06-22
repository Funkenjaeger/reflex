from kivy.clock import Clock
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
            # Rebuild the options on a theme switch, but DEFER it: ThemeProvider
            # applies its tokens one at a time, so a synchronous rebuild fired by
            # `surface` would read a still-stale `text` (the dark theme's cyan) and
            # bake low-contrast option labels. Scheduling for next frame lets the
            # whole token sweep finish first.
            self.app.theme.bind(surface=self._schedule_rebuild)

    def _schedule_rebuild(self, *args):
        Clock.schedule_once(lambda _dt: self.on_options(self, self.options), 0)

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
        # Drop the stale references too; otherwise the list grows by len(options)
        # on every rebuild (e.g. each theme switch) and we re-remove dead widgets.
        self._options.clear()

    def on_value(self, instance, value):
        self.main_button.text = value

    def on_options(self, instance, value):
        # Clean any existing
        self.delete_all_dropdown_options()

        font_size = self.app.formats.font_size if self.app else 24
        # Copy the theme colors so each option keeps its own values across a
        # later theme switch (which rebuilds these options).
        surface = list(self._theme("surface"))
        text = list(self._theme("text"))
        fontb = self._theme("font_bold", "fonts/ChakraPetch-SemiBold.ttf")

        for item in self.options:
            btn = Button(
                text=item, size_hint_y=None, height=60, font_size=font_size,
                background_normal="", background_down="", background_color=(0, 0, 0, 0),
                color=text, font_name=fontb,
            )
            # Draw an opaque themed fill behind the label: the stock button
            # background is disabled, so without this the option would be
            # see-through and read with poor contrast over the rows behind it.
            with btn.canvas.before:
                Color(*surface)
                rect = Rectangle(pos=btn.pos, size=btn.size)
                # Reset the color context to white so it doesn't tint the
                # button's text texture (which is drawn afterwards).
                Color(1, 1, 1, 1)
            btn.bind(
                pos=lambda b, v, r=rect: setattr(r, "pos", v),
                size=lambda b, v, r=rect: setattr(r, "size", v),
            )
            btn.bind(on_release=lambda b: self.dropdown.select(b.text))
            self.dropdown.add_widget(btn)
            self._options.append(btn)
