from kivy.logger import Logger
from kivy.properties import ObjectProperty, StringProperty
from kivy.uix.screenmanager import Screen

from reflex.utils.input_axis_map import input_axis_label
from reflex.utils.kv_loader import load_kv
from reflex.utils.scale_resolution import (
    default_entry_mode, ratio_from_resolution_um, resolution_um,
)

log = Logger.getChild(__name__)
load_kv(__file__)

ENTRY_MODES = ["Resolution", "Ratio"]


class InputScreen(Screen):
    input = ObjectProperty()

    #: "Resolution" (microns per count, the number on the scale's sticker) or
    #: "Ratio" (the stored ratioNum/ratioDen, millimetres per count). Chosen
    #: per visit rather than persisted: it is a preference about how to READ a
    #: setting, not part of the machine's configuration, and defaulting it from
    #: the stored value is more useful than remembering a stale choice.
    entry_mode = StringProperty(ENTRY_MODES[0])

    entry_modes = ENTRY_MODES

    #: Which axis this input feeds, for the header. Read-only annotation --
    #: assignment still lives on the Axes side, deliberately (Evan, 2026-08-31:
    #: no second way to do the same thing). Empty when nothing claims it.
    axis_label = StringProperty("")

    def on_pre_enter(self, *args):
        """Recomputed on entry rather than bound.

        The mapping changes only when an axis is reconfigured, which happens on
        a different screen -- so there is nothing to observe live from here, and
        a binding across every axis's transform would be a lot of machinery for
        a string that cannot change while this screen is up.
        """
        self._refresh_axis_label()
        self._pick_entry_mode()

    # ── scale entry: resolution or ratio ─────────────────────────────────────

    def _pick_entry_mode(self):
        """Open on whichever form can express what is already stored.

        Resolution for every real scale; ratio when the stored value cannot be
        carried as a resolution, so the screen never opens showing a number
        that would change the setting if accepted.
        """
        if self.input is None:
            return
        self.entry_mode = default_entry_mode(
            getattr(self.input, "ratioNum", 1), getattr(self.input, "ratioDen", 1))

    def get_resolution(self, ratio_num, ratio_den):
        """Microns per count, for the kv.

        Both ratio components are arguments rather than read off self.input so
        the kv expression names them as dependencies and rebinds when either
        moves -- the same reason ElsBar.image_for takes its theme argument.
        """
        return resolution_um(ratio_num, ratio_den)

    def set_resolution(self, microns):
        """Store a typed resolution as the exact ratio behind it.

        Writes the denominator FIRST. Both properties are bound to the axis
        recompute, so an intermediate state is published either way -- but
        ratioNum is the smaller number and landing it second means the
        transient is a scale that is too FINE (position under-reported) rather
        than too coarse. Nothing moves off this screen; this is about what a
        watching axis briefly displays.
        """
        if self.input is None:
            return
        num, den = ratio_from_resolution_um(microns)
        self.input.ratioDen = den
        self.input.ratioNum = num
        log.info("input %s scale set to %s um/count (%d/%d)",
                 getattr(self.input, "inputIndex", "?"), microns, num, den)

    def _refresh_axis_label(self):
        from reflex.app import MainApp
        app = MainApp.get_running_app()
        index = getattr(self.input, "inputIndex", None)
        if app is None or index is None:
            self.axis_label = ""
            return
        self.axis_label = input_axis_label(app.axes, index)
