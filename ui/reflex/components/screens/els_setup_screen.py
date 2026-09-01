from kivy.logger import Logger
from kivy.properties import BooleanProperty, ObjectProperty
from kivy.uix.screenmanager import Screen

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

NONE_LABEL = "None"


class ElsSetupScreen(Screen):
    els = ObjectProperty()

    #: Mirrors the assigned X axis's diameter_mode for the toggle below the
    #: Cross Slide dropdown. Mirrored rather than bound straight through
    #: because the axis it describes changes when the role is reassigned, and
    #: a kv binding onto "whichever axis is X right now" has no stable target.
    x_diameter_mode = BooleanProperty(False)

    #: Is there an X axis at all? The toggle collapses without one -- there is
    #: nothing for it to describe, and a setting that applies to nothing is
    #: how a hidden doubling starts.
    has_x_axis = BooleanProperty(False)

    def __init__(self, **kv):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kv)

    def on_pre_enter(self, *args):
        axis_names = [ax.axis_name for ax in self.app.axes]
        options = [NONE_LABEL] + axis_names

        self.ids.spindle_dropdown.options = options
        self.ids.z_dropdown.options = options
        self.ids.x_dropdown.options = options

        self.ids.spindle_dropdown.value = self._index_to_name(self.els.spindle_axis_index)
        self.ids.z_dropdown.value = self._index_to_name(self.els.z_axis_index)
        self.ids.x_dropdown.value = self._index_to_name(self.els.x_axis_index)
        self._refresh_x_diameter()

    def on_spindle_selected(self, instance, value):
        self.els.spindle_axis_index = self._name_to_index(value)

    def on_z_selected(self, instance, value):
        self.els.z_axis_index = self._name_to_index(value)

    def on_x_selected(self, instance, value):
        self.els.x_axis_index = self._name_to_index(value)
        # The toggle describes whichever axis is X, so it has to re-read when
        # that changes -- otherwise reassigning the role leaves the previous
        # axis's setting on screen, attached to a different axis.
        self._refresh_x_diameter()

    # ── X reads diameter ─────────────────────────────────────────────────────

    def _x_axis(self):
        idx = int(self.els.x_axis_index)
        if 0 <= idx < len(self.app.axes):
            return self.app.axes[idx]
        return None

    def _refresh_x_diameter(self):
        axis = self._x_axis()
        self.has_x_axis = axis is not None
        self.x_diameter_mode = bool(axis.diameter_mode) if axis is not None else False

    def on_x_diameter_selected(self, instance, value):
        """Write the operator's choice onto the axis that currently holds the
        X role. No X assigned means there is nothing to write it to -- the
        toggle is collapsed in that case, so this is a guard rather than a
        reachable path."""
        axis = self._x_axis()
        if axis is None:
            return
        if bool(axis.diameter_mode) != bool(value):
            axis.diameter_mode = bool(value)
            axis.save_settings()
            log.info("axis %s diameter_mode -> %s", axis.axis_name, bool(value))
        self.x_diameter_mode = bool(value)

    def _name_to_index(self, name: str) -> int:
        if name == NONE_LABEL:
            return -1
        for i, ax in enumerate(self.app.axes):
            if ax.axis_name == name:
                return i
        return -1

    def _index_to_name(self, index) -> str:
        idx = int(index)
        if 0 <= idx < len(self.app.axes):
            return self.app.axes[idx].axis_name
        return NONE_LABEL
