from kivy.logger import Logger
from kivy.properties import ObjectProperty, StringProperty, ListProperty
from kivy.uix.screenmanager import Screen

from reflex.dispatchers.axis_transform import AxisTransform, TransformType
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

TRANSFORM_TYPE_LABELS = {
    TransformType.IDENTITY: "Identity",
    TransformType.SUM: "Sum",
}

LABEL_TO_TRANSFORM_TYPE = {v: k for k, v in TRANSFORM_TYPE_LABELS.items()}


class AxisScreen(Screen):
    axis = ObjectProperty()

    # Editable fields mirroring current transform config
    transform_type_label = StringProperty("Identity")
    input_0 = StringProperty("Input 0")
    input_1 = StringProperty("Input 1")
    input_0_options = ListProperty()
    input_1_options = ListProperty()

    def __init__(self, **kv):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kv)
        self.bind(input_0=self._update_input_options)
        self.bind(transform_type_label=self._update_input_options)

    def _all_input_labels(self):
        return [f"Input {i}" for i in range(len(self.app.inputs))]

    def _provisioned_input_labels(self):
        """Input labels claimed by an axis somebody has named.

        The axis being edited is skipped: its own input is input_0, which this
        list does not gate, and counting it would let an unprovisioned axis
        vouch for itself.
        """
        claimed = set()
        for ax in self.app.axes:
            if ax is self.axis or not ax.is_provisioned:
                continue
            claimed |= ax.transform.input_indices
        return {f"Input {i}" for i in sorted(claimed)}

    def _label_to_index(self, label):
        try:
            return int(label.split()[-1])
        except (ValueError, IndexError, AttributeError):
            return 0

    def _update_input_options(self, *args):
        if self.axis and self.axis.spindleMode:
            # Spindle axis: only spindle-mode inputs, force Identity
            filtered = [
                f"Input {i}" for i, inp in enumerate(self.app.inputs)
                if inp.spindleMode
            ]
            self.input_0_options = filtered if filtered else self._all_input_labels()
            self.transform_type_label = "Identity"
        else:
            # Non-spindle axis: exclude spindle-mode inputs
            filtered = [
                f"Input {i}" for i, inp in enumerate(self.app.inputs)
                if not inp.spindleMode
            ]
            self.input_0_options = filtered if filtered else self._all_input_labels()

        if self.transform_type_label == "Sum":
            # A SUM's SECOND contributor must belong to an axis somebody has
            # actually named. The first is not filtered this way: assigning an
            # input to an axis is how an axis gets provisioned in the first
            # place, so narrowing input_0 would make a fresh axis unreachable.
            #
            # WHY THIS IS WORTH DOING (2026-08-30). Axis.compute() adds both
            # contributors into the displayed position, but consumers that
            # push a single scale index to the firmware -- ElsFsm.
            # set_scale_index is the one that matters -- take
            # contributions[0] alone. Summing in a placeholder that reads zero
            # forever is a DRO quietly disagreeing with the machine for no
            # gain, and the placeholder is present on every board with a spare
            # input rather than being some unusual state.
            eligible = self._provisioned_input_labels()
            self.input_1_options = [l for l in self.input_0_options
                                    if l != self.input_0 and l in eligible]
            if self.input_1 not in self.input_1_options and self.input_1_options:
                self.input_1 = self.input_1_options[0]
        else:
            self.input_1_options = self.input_0_options

    def on_pre_enter(self, *args):
        """Sync UI fields from the current axis transform when entering."""
        if self.axis is None:
            return
        self.axis.bind(spindleMode=self._update_input_options)
        t = self.axis.transform
        self.transform_type_label = TRANSFORM_TYPE_LABELS.get(t.transform_type, "Identity")
        if t.contributions:
            self.input_0 = f"Input {t.contributions[0]}"
        if len(t.contributions) > 1:
            self.input_1 = f"Input {t.contributions[1]}"
        self._update_input_options()

    def on_pre_leave(self, *args):
        if self.axis is not None:
            self.axis.unbind(spindleMode=self._update_input_options)

    def apply_transform(self):
        """Build an AxisTransform from the current UI field values and apply it."""
        tt = LABEL_TO_TRANSFORM_TYPE.get(self.transform_type_label, TransformType.IDENTITY)
        idx0 = self._label_to_index(self.input_0)
        idx1 = self._label_to_index(self.input_1)

        if tt == TransformType.SUM and self.input_1 not in self.input_1_options:
            # FAIL TO IDENTITY, LOUDLY. Reachable whenever nothing is eligible
            # to sum with -- a machine with one provisioned axis, say -- and
            # the alternative is building a SUM over whatever stale index the
            # field happens to hold. An axis that silently reads the wrong
            # scale is the failure this whole guard exists to prevent.
            log.warning(
                "Sum refused for axis %r: %r is not an eligible second input "
                "(options: %s). Falling back to Identity on %r.",
                self.axis.axis_name, self.input_1,
                list(self.input_1_options) or "none", self.input_0)
            tt = TransformType.IDENTITY

        if tt == TransformType.SUM:
            transform = AxisTransform.sum(idx0, idx1)
        else:
            transform = AxisTransform.identity(idx0)

        self.axis.transform = transform
        log.info(f"Applied transform: {tt.value} to axis '{self.axis.axis_name}'")

    def remove_axis(self):
        """Remove this axis from the board, clean up this screen, and go back."""
        if len(self.app.axes) <= 1:
            log.warning("Cannot remove the last axis")
            return
        self.app.board.remove_axis(self.axis)
        self.app.axes = list(self.app.board.axes)
        self.app.manager.back()
        self.app.manager.remove_widget(self)
