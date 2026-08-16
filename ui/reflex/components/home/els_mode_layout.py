from kivy.clock import Clock
from kivy.metrics import dp
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget

from reflex.components.home.dro_coordbar import DroCoordBar
from reflex.components.home.els_advbar import ElsAdvancedBar
from reflex.components.home.elsbar import ElsBar
from reflex.components.home.mode_layout import ModeLayout
from reflex.utils.kv_loader import load_kv

from kivy.logger import Logger
log = Logger.getChild(__name__)

load_kv(__file__)

# Font Awesome 6 icons for rotation direction
ICON_CW = "\uf01e"   # rotate-right
ICON_CCW = "\uf0e2"  # rotate-left
ICON_STOP = "\uf04d"  # stop

LONG_PRESS_THRESHOLD = 1.0

class ElsSpindleInfo(BoxLayout):
    """Displays spindle speed with direction icon and absolute position with zero button."""
    spindle_rpm = StringProperty("--")
    # spindle_rpm with the leading sign stripped (DSEG7 has no "+" glyph). A
    # real property rather than an inline `.replace()` in the kv binding: when a
    # direct root property is the *receiver* of a method call, Kivy fails to
    # track it as a dependency, so the readout would freeze at its first value.
    display_rpm = StringProperty("--")
    direction_icon = StringProperty(ICON_STOP)

    def __init__(self, **kwargs):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        self._long_press_event = None
        super().__init__(**kwargs)
        self.app.board.bind(update_tick=self._update_spindle)

    def on_spindle_rpm(self, *_):
        self.display_rpm = self.spindle_rpm.replace("+", "")

    def _update_spindle(self, *args):
        axis = self.app.els.get_spindle_axis()
        if axis is None:
            if self.spindle_rpm != "--":
                self.spindle_rpm = "--"
            if self.direction_icon != ICON_STOP:
                self.direction_icon = ICON_STOP
            return

        rpm = axis.formattedPosition
        if rpm != self.spindle_rpm:
            self.spindle_rpm = rpm

        speed = self.app.els.get_spindle_speed()
        if speed > 0.5:
            icon = ICON_CW
        elif speed < -0.5:
            icon = ICON_CCW
        else:
            icon = ICON_STOP
        if icon != self.direction_icon:
            self.direction_icon = icon

    def on_zero_press(self):
        self._long_press_event = Clock.schedule_once(self._do_undo_zero, LONG_PRESS_THRESHOLD)

    def on_zero_release(self):
        if self._long_press_event is not None:
            self._long_press_event.cancel()
            self._long_press_event = None
            axis = self.app.els.get_spindle_axis()
            if axis is not None:
                axis.zero_position()

    def _do_undo_zero(self, dt):
        self._long_press_event = None
        axis = self.app.els.get_spindle_axis()
        if axis is not None:
            axis.undo_zero()


class ElsModeLayout(ModeLayout):
    """ELS mode: spindle info bar + DroCoordBars for Z/X axes + ElsBar + ElsAdvancedBar."""

    def __init__(self, els_bar: ElsBar, **kwargs):
        super().__init__(**kwargs)
        self.els_bar = els_bar
        self.spindle_info = ElsSpindleInfo()
        self.spacer = Widget()
        self.els_adv_bar = ElsAdvancedBar(els_bar=els_bar)
        self.els_adv_bar.size_hint_y = None

        self.build_axis_bars()
        self.add_widget(self.spindle_info)
        self.add_widget(self.spacer)
        self.add_widget(self.els_adv_bar)
        self.add_widget(self.els_bar)

        # Rebuild when ELS axis assignments change
        self.app.els.bind(
            spindle_axis_index=lambda *a: self.rebuild_axes(),
            z_axis_index=lambda *a: self.rebuild_axes(),
            x_axis_index=lambda *a: self.rebuild_axes(),
        )

        self.app.formats.bind(max_row_height=lambda *_: self._update_row_heights())
        self.app.formats.bind(show_speeds=lambda *_: self.rebuild_axes())
        self.bind(height=self._update_row_heights)
        self.els_bar.bind(enable_advanced=self._apply_adv_visibility)
        # A notice strip appearing changes what the bar needs. Without this the
        # bar keeps whatever height it was last given and the strip overflows it.
        self.els_adv_bar.bind(natural_height=self._apply_adv_visibility)
        self._apply_adv_visibility()
        self._update_row_heights()

    def _apply_adv_visibility(self, *_):
        """Show or hide the advanced bar, at whatever height it currently needs.

        Reads `natural_height` LIVE rather than a height measured once at
        construction. The bar grows when a notice strip appears, and a snapshot
        taken before any strip existed pins it at the base height forever — which
        is what made the take-up warning render over the DRO rows on 2026-08-16.
        Bound to natural_height in __init__ so a strip appearing re-applies this.
        """
        shown = bool(self.els_bar.enable_advanced)
        self.els_adv_bar.height = self.els_adv_bar.natural_height if shown else 0
        self.els_adv_bar.opacity = 1 if shown else 0
        self.els_adv_bar.disabled = not shown
        self._update_row_heights()
        # TWO samples on purpose. Kivy defers BoxLayout re-layout to the next
        # frame, so the immediate one shows what we just set and the deferred one
        # shows where the parent actually PUT things. If the bar's y stays put
        # while its height grows, its top pushes upward into the spacer and the
        # spindle row -- which is exactly where the banner is being seen. The two
        # samples differing is that hypothesis confirmed; identical kills it.
        self._log_geometry("immediate")
        Clock.schedule_once(lambda *_: self._log_geometry("after layout"), 0)

    def _log_geometry(self, when=""):
        """TEMPORARY, 2026-08-16. Remove once the take-up banner is placed right.

        The banner renders outside the advanced bar and two reasoned fixes have
        not moved it, which means my model of this layout is wrong rather than
        incomplete. Reasoning harder about it a third time is not the move --
        this prints where the widgets ACTUALLY are, the way the firmware settle
        question got answered.

        Deliberately at INFO despite the log-noise work: it fires only when the
        bar's visibility or height changes, not per frame.
        """
        try:
            adv = self.els_adv_bar
            parts = [
                f"layout h={self.height:.0f}",
                f"els_bar h={self.els_bar.height:.0f} y={self.els_bar.y:.0f}",
                f"adv natural={adv.natural_height:.0f} h={adv.height:.0f} "
                f"y={adv.y:.0f} top={adv.top:.0f}",
                f"spacer h={self.spacer.height:.0f} y={self.spacer.y:.0f}",
                f"spindle h={self.spindle_info.height:.0f} y={self.spindle_info.y:.0f}",
            ]
            for name in ("reframe_notice", "takeup_notice"):
                w = adv.ids.get(name)
                parts.append(
                    f"{name} h={w.height:.0f} y={w.y:.0f} op={w.opacity:.1f}"
                    if w is not None else f"{name}=<no id>")
            parts.append(f"warning={self.els_bar and repr(getattr(adv.controller, 'takeup_warning', None))[:40]}")
            log.info(f"ELS geometry [{when}]: " + " | ".join(parts))
        except Exception as e:
            log.info(f"ELS geometry [{when}]: unavailable ({e})")

    def _update_row_heights(self, *args):
        num_rows = len(self.axis_bars) + 1  # axis bars + spindle info
        if num_rows == 0:
            return

        # Reserve a small margin between the DRO section and the ELS bars,
        # symmetric with the gap under the top status bar. The spacer (which
        # absorbs leftover space) ends up exactly this gap on the target res.
        dro_els_gap = dp(8)
        available = self.height - self.els_bar.height - self.els_adv_bar.height
        row_height = min((available - dro_els_gap) / num_rows, self.app.formats.max_row_height)

        self.spindle_info.size_hint_y = None
        self.spindle_info.height = row_height
        for bar in self.axis_bars:
            bar.size_hint_y = None
            bar.height = row_height

        # spacer absorbs remaining space (size_hint_y defaults to 1)

    def build_axis_bars(self):
        for axis in [self.app.els.get_z_axis(), self.app.els.get_x_axis()]:
            if axis is None:
                continue
            cb = DroCoordBar(axis=axis)
            self.axis_bars.append(cb)
            self.add_widget(cb)

    def rebuild_axes(self):
        self.remove_widget(self.spindle_info)
        self.remove_widget(self.spacer)
        self.remove_widget(self.els_bar)
        self.remove_widget(self.els_adv_bar)
        super().rebuild_axes()
        self.add_widget(self.spindle_info)
        self.add_widget(self.spacer)
        self.add_widget(self.els_adv_bar)
        self.add_widget(self.els_bar)
        self._update_row_heights()
