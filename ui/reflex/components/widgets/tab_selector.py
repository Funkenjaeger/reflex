"""One-hot tab/segment selector for the UI facelift.

A ``TabSelector`` holds two or more ``TabSegment`` children butted together
(vertically by default, like the left-edge sidebar groups; or horizontally
like the FEED/THREAD and DIR toggles). Exactly one segment is selected at a
time. The selected segment shows cyan text + a glowing accent edge; the
others are dim slate.

Two ways to drive selection:

  * Declarative, by value -- set ``selected`` on the group to a segment's
    ``value`` and the matching segment lights up. Binding ``on_select`` gives
    you ``(group, value)`` on every change. This is the recommended pattern
    for wiring to firmware / app state.

  * Imperative -- tapping a segment sets the group's ``selected`` to that
    segment's ``value`` and dispatches ``on_select``.

Example (KV)::

    TabSelector:
        selected: "MM" if app.formats.current_format == "MM" else "IN"
        on_select: app.formats.toggle()
        TabSegment:
            text: "MM"
            value: "MM"
        TabSegment:
            text: "IN"
            value: "IN"

The accent edge sits on the left for vertical groups and on the bottom for
horizontal groups (set ``accent_edge`` to override: 'left' | 'right' |
'top' | 'bottom').
"""

from kivy.logger import Logger
from kivy.properties import (
    StringProperty,
    ObjectProperty,
    NumericProperty,
    BooleanProperty,
)
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout

from reflex.components.widgets.beep_mixin import BeepMixin
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)


class TabSelector(BoxLayout):
    """Container managing one-hot selection across its ``TabSegment`` children."""

    # The currently selected segment's ``value``. Set this (e.g. bound to app
    # state in KV) to drive which segment lights up.
    selected = ObjectProperty(None, allownone=True)

    # Where the cyan accent edge is drawn on the selected segment. Defaults
    # are chosen from orientation in ``on_orientation`` unless explicitly set.
    accent_edge = StringProperty("auto")

    __events__ = ("on_select",)

    def on_orientation(self, _instance, value):
        if self.accent_edge == "auto":
            # Just trigger a redraw; segments read the resolved edge live.
            pass

    def resolved_accent_edge(self) -> str:
        if self.accent_edge != "auto":
            return self.accent_edge
        return "left" if self.orientation == "vertical" else "bottom"

    def select(self, value):
        """Imperatively select the segment with ``value`` and notify listeners."""
        if value != self.selected:
            self.selected = value
        self.dispatch("on_select", value)

    def on_select(self, value):
        pass


class TabSegment(BeepMixin, ButtonBehavior, FloatLayout):
    """A single segment within a :class:`TabSelector`.

    ``is_selected`` is derived from the parent group's ``selected`` matching
    this segment's ``value``; it can also be set directly for standalone use.
    """

    text = StringProperty("")
    value = ObjectProperty(None, allownone=True)
    font_size = NumericProperty(18)
    font_name = StringProperty("fonts/Manrope-Bold.ttf")
    is_selected = BooleanProperty(False)

    def _update_selected(self, *_):
        group = self._group()
        if group is not None and self.value is not None:
            self.is_selected = group.selected == self.value

    def _group(self):
        parent = self.parent
        while parent is not None and not isinstance(parent, TabSelector):
            parent = parent.parent
        return parent

    def on_parent(self, _instance, parent):
        group = self._group()
        if group is not None:
            group.bind(selected=self._update_selected)
            self._update_selected()

    def on_value(self, *_):
        self._update_selected()

    def on_release(self):
        group = self._group()
        if group is not None and self.value is not None:
            group.select(self.value)


load_kv(__file__)
