"""Minimal probe: does export_to_png capture a laid-out layout of widgets?"""
import os
import reflex
from kivy.resources import resource_add_path
resource_add_path(os.path.dirname(reflex.__file__))

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.lang import Builder

from reflex.components.widgets.styled_button import StyledButton  # noqa
from reflex.components.widgets.icon_button import IconButton  # noqa
from reflex.components.widgets.circular_button import CircularButton  # noqa
from reflex.components.widgets.tab_selector import TabSelector, TabSegment  # noqa

Window.size = (600, 200)
Window.clearcolor = (0.03, 0.05, 0.07, 1)

ROOT = Builder.load_string("""
#: import THEME reflex.components.widgets.facelift_theme
BoxLayout:
    size_hint: None, None
    size: 600, 200
    padding: 16
    spacing: 16
    canvas.before:
        Color:
            rgba: THEME.BACKGROUND
        Rectangle:
            pos: self.pos
            size: self.size
    StyledButton:
        text: 'Engage'
    StyledButton:
        text: 'Cut'
        is_highlighted: True
    CircularButton:
        text: 'Zero'
    IconButton:
        icon: "\\uf013"
""")


class A(App):
    def build(self):
        return ROOT
    def on_start(self):
        Clock.schedule_once(self._s, 1.0)
    def _s(self, _):
        from kivy.base import EventLoop
        out = os.path.join(os.path.dirname(__file__), "preview_probe.png")
        # Force several render passes so the Fbo populates fully.
        for _ in range(3):
            EventLoop.idle()
        self.root.export_to_png(out)
        self.root.export_to_png(out)
        print("WROTE", out, self.root.size, [c.size for c in self.root.children])
        self.stop()


A().run()
