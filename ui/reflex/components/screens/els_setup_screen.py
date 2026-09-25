from kivy.logger import Logger
from kivy.properties import BooleanProperty, ObjectProperty, StringProperty
from kivy.uix.screenmanager import Screen

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

NONE_LABEL = "None"

# The two things an X readout can mean, named. This is presented as a choice
# between two named conventions rather than as an on/off "X reads diameter",
# because OFF then has to be read as "reads radius instead" -- an inference
# only someone already fluent in the setting can make, and the dimmed half of
# a boolean is exactly where that inference gets skipped (Evan, 2026-09-01).
# The stored form stays the boolean `diameter_mode` on the axis; these labels
# are the UI's vocabulary, not the config's.
RADIUS_LABEL = "Radius"
DIAMETER_LABEL = "Diameter"
DRO_READS_OPTIONS = [RADIUS_LABEL, DIAMETER_LABEL]


class ElsSetupScreen(Screen):
    els = ObjectProperty()

    #: Mirrors the assigned X axis's diameter_mode for the dropdown below the
    #: Cross Slide dropdown. Mirrored rather than bound straight through
    #: because the axis it describes changes when the role is reassigned, and
    #: a kv binding onto "whichever axis is X right now" has no stable target.
    x_dro_reads = StringProperty(RADIUS_LABEL)

    #: Is there an X axis at all? The row collapses without one -- there is
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
        self._refresh_x_dro_reads()

    def on_spindle_selected(self, instance, value):
        self.els.spindle_axis_index = self._name_to_index(value)

    def on_z_selected(self, instance, value):
        self.els.z_axis_index = self._name_to_index(value)

    def on_x_selected(self, instance, value):
        self.els.x_axis_index = self._name_to_index(value)
        # The row describes whichever axis is X, so it has to re-read when
        # that changes -- otherwise reassigning the role leaves the previous
        # axis's setting on screen, attached to a different axis.
        self._refresh_x_dro_reads()

    # ── X DRO reads: Radius / Diameter ───────────────────────────────────────

    def _x_axis(self):
        idx = int(self.els.x_axis_index)
        if 0 <= idx < len(self.app.axes):
            return self.app.axes[idx]
        return None

    def _refresh_x_dro_reads(self):
        axis = self._x_axis()
        self.has_x_axis = axis is not None
        self.x_dro_reads = (DIAMETER_LABEL
                            if (axis is not None and axis.diameter_mode)
                            else RADIUS_LABEL)
        # Pushed in rather than kv-bound, like the three dropdowns above: a kv
        # `value:` is applied during the build, so it would write this
        # property's DEFAULT onto the axis before `els` is even set. Absent
        # from `ids` under the headless tests, which patch the rules away.
        row = self.ids.get("x_dro_reads_dropdown")
        if row is not None:
            row.options = DRO_READS_OPTIONS
            row.value = self.x_dro_reads

    def on_x_dro_reads_selected(self, instance, value):
        """Write the operator's choice onto the axis that currently holds the
        X role. No X assigned means there is nothing to write it to -- the row
        is collapsed in that case, so that is a guard rather than a reachable
        path.

        An UNRECOGNIZED label is ignored rather than read as radius.
        DropDownItem.value is a free StringProperty that starts empty, so the
        kv binding can hand us "" during construction; treating anything that
        is not DIAMETER_LABEL as radius would let that empty string quietly
        halve a diameter machine's readout.
        """
        if value not in DRO_READS_OPTIONS:
            return
        axis = self._x_axis()
        if axis is None:
            return
        diameter = (value == DIAMETER_LABEL)
        if bool(axis.diameter_mode) != diameter:
            axis.diameter_mode = diameter
            axis.save_settings()
            log.info("axis %s DRO reads %s", axis.axis_name, value)
        self.x_dro_reads = value

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
