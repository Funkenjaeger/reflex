"""Standalone visual preview for the UI-facelift reusable widgets.

Renders the new widgets on the dark background and exports a PNG via
``export_to_png`` (Window.screenshot returns black on this headless GL).

Run:
    DISPLAY=:0 SDL_AUDIODRIVER=dummy KIVY_INPUT=mouse \
        uv run python previews/preview_widgets.py
"""
import os

import reflex
from kivy.resources import resource_add_path

resource_add_path(os.path.dirname(reflex.__file__))

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.uix.boxlayout import BoxLayout

# Import widgets so their KV + classes register.
from reflex.components.widgets import facelift_theme as THEME  # noqa: F401
from reflex.components.widgets.tab_selector import TabSelector, TabSegment  # noqa: F401
from reflex.components.widgets.icon_button import IconButton  # noqa: F401
from reflex.components.widgets.circular_button import CircularButton  # noqa: F401
from reflex.components.widgets.recess_panel import RecessPanel  # noqa: F401
from reflex.components.widgets.styled_button import StyledButton  # noqa: F401
from reflex.components.toolbars.led_button import LedButton  # noqa: F401

Window.size = (1024, 600)
Window.clearcolor = (0.03, 0.05, 0.07, 1)

ROOT = Builder.load_string(
    """
#: import THEME reflex.components.widgets.facelift_theme

BoxLayout:
    orientation: 'horizontal'
    padding: 16
    spacing: 16
    canvas.before:
        Color:
            rgba: THEME.BACKGROUND
        Rectangle:
            pos: self.pos
            size: self.size

    # Left sidebar one-hot tab groups (vertical).
    BoxLayout:
        orientation: 'vertical'
        size_hint_x: None
        width: 90
        spacing: 12

        TabSelector:
            orientation: 'vertical'
            selected: 'MM'
            TabSegment:
                text: 'MM'
                value: 'MM'
            TabSegment:
                text: 'IN'
                value: 'IN'

        TabSelector:
            orientation: 'vertical'
            selected: 'P1'
            TabSegment:
                text: 'P0'
                value: 'P0'
            TabSegment:
                text: 'P1'
                value: 'P1'
            TabSegment:
                text: 'P2'
                value: 'P2'
            TabSegment:
                text: 'P3'
                value: 'P3'

        TabSelector:
            orientation: 'vertical'
            selected: 'ELS'
            TabSegment:
                text: 'ELS'
                value: 'ELS'
            TabSegment:
                text: 'DRO'
                value: 'DRO'

    BoxLayout:
        orientation: 'vertical'
        spacing: 16

        # Horizontal FEED/THREAD toggle + value display.
        BoxLayout:
            size_hint_y: None
            height: 64
            spacing: 16
            TabSelector:
                orientation: 'horizontal'
                size_hint_x: None
                width: 220
                selected: 'FEED'
                TabSegment:
                    text: 'FEED'
                    value: 'FEED'
                TabSegment:
                    text: 'THREAD'
                    value: 'THREAD'
            RecessPanel:
                Label:
                    text: '0.040 in'
                    font_name: 'fonts/Manrope-Bold.ttf'
                    font_size: 28
                    color: THEME.ACCENT_TEXT

        # Icon buttons row.
        BoxLayout:
            size_hint_y: None
            height: 64
            spacing: 12
            IconButton:
                size_hint_x: None
                width: 64
                icon: "\\uf013"
            IconButton:
                size_hint_x: None
                width: 64
                icon: "\\uf085"
            IconButton:
                size_hint_x: None
                width: 64
                icon: "\\uf062"
                is_highlighted: True
            IconButton:
                size_hint_x: None
                width: 64
                icon: "\\uf063"
            Widget:

        # Circular Zero buttons + StyledButtons.
        BoxLayout:
            size_hint_y: None
            height: 96
            spacing: 12
            CircularButton:
                size_hint_x: None
                width: 96
                text: 'Zero'
            CircularButton:
                size_hint_x: None
                width: 96
                text: 'Zero'
                is_highlighted: True
            StyledButton:
                size_hint_x: None
                width: 110
                text: 'Engage'
            StyledButton:
                size_hint_x: None
                width: 150
                text: 'Cut'
                is_highlighted: True
            Widget:

        # Recessed status bar with LED dots.
        RecessPanel:
            orientation: 'horizontal'
            size_hint_y: None
            height: 40
            radius: 6
            spacing: 4
            padding: 4
            LedButton:
                label: 'COM'
                checkbox_value: True
            LedButton:
                label: '10000'
                checkbox_value: True
            LedButton:
                label: '67'
                checkbox_value: True
            LedButton:
                label: '600'
                checkbox_value: True

        # LED button with icon + two-line label (Sync Enable) and amber disabled.
        BoxLayout:
            size_hint_y: None
            height: 60
            spacing: 12
            LedButton:
                size_hint_x: None
                width: 200
                icon: "\\uf013"
                label: 'Sync Enable'
                checkbox_value: True
            LedButton:
                size_hint_x: None
                width: 200
                label: 'DISABLED'
                checkbox_value: False
                current_color: [0.95, 0.55, 0.12, 1]
            Widget:

        Widget:
    """
)


class PreviewApp(App):
    def build(self):
        return ROOT

    def on_start(self):
        Clock.schedule_once(self._shot, 0.8)

    def _shot(self, _dt):
        from kivy.base import EventLoop
        out = os.path.join(os.path.dirname(__file__), "preview_widgets.png")
        # Several render passes + a double export so the Fbo fully populates
        # (single export_to_png on a fresh layout renders only one tile).
        for _ in range(3):
            EventLoop.idle()
        self.root.export_to_png(out)
        self.root.export_to_png(out)
        print("WROTE", out, "root size", self.root.size)
        self.stop()


if __name__ == "__main__":
    PreviewApp().run()
