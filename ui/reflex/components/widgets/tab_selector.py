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
from kivy.metrics import dp
from kivy.properties import (
    StringProperty,
    ObjectProperty,
    NumericProperty,
    BooleanProperty,
    ListProperty,
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

    # Read-only display: tapping does not cycle the selection; it just fires
    # on_select(current) so a host can react (e.g. open a modal that drives the
    # value). Used by the ELS-bar FEED/THREAD indicator.
    readonly = BooleanProperty(False)

    # Which outer corners the group rounds. "auto" -> vertical groups round the
    # outer-left corners, horizontal groups round the top ("attached" tabs-on-
    # content look). Override with "standalone" (round all outer corners, e.g.
    # the mode tab), "bottom" (round bottom outer only, e.g. arrows anchored to
    # the bottom of a box like DIR), or "none". The per-segment corner radius is
    # computed in _apply_edges and the accent bar insets to match.
    shape = StringProperty("auto")

    __events__ = ("on_select",)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Push the resolved accent edge onto segments reactively. The edge
        # depends on orientation/accent_edge, both of which are often set by KV
        # rules AFTER the child segments are created, so a one-shot read at
        # build time would latch a stale value -- rebind instead.
        self.bind(
            orientation=self._apply_edges,
            accent_edge=self._apply_edges,
            shape=self._apply_edges,
            children=self._apply_edges,
        )

    def _apply_edges(self, *_):
        edge = self.resolved_accent_edge()
        shape = self.resolved_shape()
        segments = [c for c in reversed(self.children)
                    if isinstance(c, TabSegment)]
        last = len(segments) - 1
        for i, seg in enumerate(segments):
            seg.edge = edge
            seg.is_first = (i == 0)
            seg.is_last = (i == last)
            seg.radius = self._segment_radius(shape, edge, i == 0, i == last)

    def resolved_shape(self) -> str:
        if self.shape != "auto":
            return self.shape
        return "attached" if self.orientation == "horizontal" else "vertical"

    @staticmethod
    def _segment_radius(shape, edge, is_first, is_last):
        """Per-segment corner radius [top-left, top-right, bottom-right,
        bottom-left]; only the group's outer corners round."""
        r = dp(7)
        if shape == "none":
            return [0, 0, 0, 0]
        if edge in ("left", "right"):           # vertical stack
            if edge == "right":                 # accent on right -> round left
                return [r if is_first else 0, 0, 0, r if is_last else 0]
            return [0, r if is_first else 0, r if is_last else 0, 0]
        # horizontal row
        if shape == "standalone":               # round all outer corners
            return [r if is_first else 0, r if is_last else 0,
                    r if is_last else 0, r if is_first else 0]
        if shape == "bottom":                   # round bottom outer only
            return [0, 0, r if is_last else 0, r if is_first else 0]
        # "attached"/"vertical" default for horizontal -> round top outer
        return [r if is_first else 0, r if is_last else 0, 0, 0]

    def resolved_accent_edge(self) -> str:
        if self.accent_edge != "auto":
            return self.accent_edge
        # Vertical sidebar groups sit at the screen's left edge, so the accent
        # belongs on the RIGHT (inner) edge of the selected tab -- pointing at
        # the content it selects. Horizontal toggles accent the bottom.
        return "right" if self.orientation == "vertical" else "bottom"

    def select(self, value):
        """Imperatively select the segment with ``value`` and notify listeners."""
        if value != self.selected:
            self.selected = value
        self.dispatch("on_select", value)

    def cycle(self):
        """Advance the one-hot selection to the next segment's value.

        Tapping *any* segment cycles the whole group, so the operator doesn't
        have to hit a specific sub-segment -- much friendlier on a small,
        dirty machine touchscreen. For a 2-segment group this is a toggle.
        """
        segments = [c for c in reversed(self.children)
                    if isinstance(c, TabSegment) and c.value is not None]
        if not segments:
            return
        values = [s.value for s in segments]
        try:
            idx = values.index(self.selected)
        except ValueError:
            idx = -1
        self.select(values[(idx + 1) % len(values)])

    def on_select(self, value):
        pass


class TabSegment(BeepMixin, ButtonBehavior, FloatLayout):
    """A single segment within a :class:`TabSelector`.

    ``is_selected`` is derived from the parent group's ``selected`` matching
    this segment's ``value``; it can also be set directly for standalone use.
    """

    text = StringProperty("")
    # Optional image icons drawn (stacked vertically, centered) instead of the
    # text label -- e.g. the ELS mode tab whose states are stop-sign / U-arrow
    # SVG art rather than glyphs. Up to two are shown; one icon centers larger.
    icons = ListProperty([])
    value = ObjectProperty(None, allownone=True)
    font_size = NumericProperty(0)  # 0 = auto-size from segment height
    font_name = StringProperty("fonts/ChakraPetch-SemiBold.ttf")
    is_selected = BooleanProperty(False)
    # Whether to draw the cyan accent bar on the selected segment.
    show_accent = BooleanProperty(True)
    # Per-corner body radius [TL, TR, BR, BL], computed by the parent group's
    # _apply_edges() from its shape/orientation. The accent bar insets to match.
    radius = ListProperty([0, 0, 0, 0])
    # Which edge the cyan accent sits on; pushed by the parent group's
    # _apply_edges() so it stays in sync with orientation/accent_edge.
    edge = StringProperty("right")
    # Position within the group, pushed by _apply_edges(): the first/last
    # segment rounds its outer (screen-side) corner so the group reads as a
    # single rounded-left tab strip without a separate backing layer.
    is_first = BooleanProperty(False)
    is_last = BooleanProperty(False)

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
        # Tap anywhere on the group cycles the selection (touch-friendly),
        # rather than selecting only the specific segment that was hit. A
        # read-only group instead just notifies (its host opens a modal).
        group = self._group()
        if group is not None:
            if group.readonly:
                group.dispatch("on_select", group.selected)
            else:
                group.cycle()


load_kv(__file__)
