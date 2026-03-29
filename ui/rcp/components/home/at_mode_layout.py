from rcp.components.home.assisted_threading_bar import AssistedThreadingBar
from rcp.components.home.coordbar import CoordBar
from rcp.components.home.dro_coordbar import DroCoordBar
from rcp.components.home.mode_layout import ModeLayout


class AtModeLayout(ModeLayout):
    """AT mode: spindle axis uses CoordBar (Num/Den visible), all others use DroCoordBar + AssistedThreadingBar."""

    def __init__(self, at_bar: AssistedThreadingBar, **kwargs):
        super().__init__(**kwargs)
        self.at_bar = at_bar
        self.build_axis_bars()
        self.add_widget(self.at_bar)

    def build_axis_bars(self):
        for axis in self.app.axes:
            if axis.spindleMode:
                cb = CoordBar(axis=axis)
            else:
                cb = DroCoordBar(axis=axis)
            self.axis_bars.append(cb)
            self.add_widget(cb)

    def rebuild_axes(self):
        self.remove_widget(self.at_bar)
        super().rebuild_axes()
        self.add_widget(self.at_bar)
