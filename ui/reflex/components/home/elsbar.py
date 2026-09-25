from kivy.factory import Factory
from kivy.logger import Logger
from kivy.properties import StringProperty, ObjectProperty, NumericProperty, BooleanProperty, AliasProperty
from kivy.uix.boxlayout import BoxLayout
from pydantic import BaseModel

from reflex import feeds
from reflex.dispatchers.saving_dispatcher import SavingDispatcher
from reflex.utils.kv_loader import load_kv
from reflex.utils.notices import NOTICE_WARNING
from reflex.utils.operator_notice import notify_operator


class FeedMode(BaseModel):
    id: int
    name: str

log = Logger.getChild(__name__)
load_kv(__file__)


def move_image(move_type: str, background=None) -> str:
    """Resource path for the ELS bar's move illustration.

    ONLY THE THREADING PAIR IS THEMED. thread_rh/thread_lh carry a baked-in
    RH/LH label that has to follow the theme (it was pure yellow in both, and
    unreadable on the light one); turn_in/turn_out carry no text and are used
    as-is.

    This is a function rather than a kv expression because the kv version
    appended a theme suffix to EVERY move_type, so feed mode asked for
    `turn_in_dark.png` and the widget drew a white rectangle where the
    illustration belongs. Nothing rendered feed mode, so nothing caught it;
    tests/components/test_move_image.py now does.

    `background` is the theme's background colour, used only for its luminance,
    so a user theme works without being named. None means dark.
    """
    if move_type.startswith("thread_"):
        light = bool(background) and background[0] > 0.5
        return "pictures/%s%s.png" % (move_type, "_light" if light else "_dark")
    return "pictures/%s.png" % move_type


class _ElsBarMoveTypes:
    """Every value :meth:`ElsBar._get_move_type` can return.

    Named so the test can enumerate them without reaching into the method, and
    so adding a fifth illustration has one obvious place to register it.
    """

    ALL = ("thread_rh", "thread_lh", "turn_in", "turn_out")


class ElsBar(BoxLayout, SavingDispatcher):
    feed_button = ObjectProperty(None)
    feed_ratio = ObjectProperty(None)

    mode_name = StringProperty(":(")
    feed_name = StringProperty(":(")
    current_feeds_index = NumericProperty(0)
    els_forward = BooleanProperty(True)
    enable_advanced = BooleanProperty(False)

    # The words the operator sees when ADV refuses. A constant so the test
    # asserts on the string that ships, not a paraphrase.
    HIDE_REFUSED_NOTICE = ("ELS stop is engaged — disengage before hiding the "
                           "advanced bar")

    def toggle_advanced(self) -> bool:
        """Show or hide the advanced ELS bar; refuse to HIDE while engaged.

        Returns True iff the visibility actually changed.

        WHY REFUSE AT ALL. This bar is the only place armed-ness appears. The
        plain bar has a Sync Enable LED (elsbar.kv:22) and no armed indicator,
        and that is deliberate -- Evan ruled the indicator out on 2026-08-20
        and this notice surface is the agreed fix instead. So hiding this bar
        with a stop job engaged leaves an armed machine and an idle one
        looking identical on the visible UI. Refusing SILENTLY would be worse
        than allowing it, which is why the refusal says why.

        WHY servoMode IS NOT IN THE CONDITION. The task body proposed "stop
        engaged, and probably servoMode != 0 as well". The second half is
        wrong, and Evan caught it on 2026-08-31: "sync armed but stop
        disengaged is a perfectly valid condition when in vanilla ELS mode".
        It is -- it is the ordinary ELS feed, and elsbar.kv:27 names that case
        explicitly. Refusing on servoMode would refuse during normal turning,
        and refuse on behalf of a state that has its own LED two widgets away.
        Hiding this bar conceals nothing about servoMode.

        WHY `engaged` AND NOT `enable_stop`. enable_stop is a MODE flag on
        ElsAdvancedBar -- which sub-features the operator has chosen to show,
        persisted in its YAML. It says nothing about whether a job is live.
        ElsUiController.engaged is the domain FSM being out of 'disabled',
        which is the actual armed question.

        SHOWING IS NEVER REFUSED. More information on screen is not the unsafe
        direction, so the guard is one-way by construction.
        """
        if not self.enable_advanced:
            self.enable_advanced = True
            return True
        if self._els_engaged():
            notify_operator(self.HIDE_REFUSED_NOTICE, NOTICE_WARNING)
            log.info("ADV hide refused: ELS stop engaged")
            return False
        self.enable_advanced = False
        return True

    @staticmethod
    def _els_engaged() -> bool:
        """Is a stop job live? False whenever the question cannot be answered.

        FAILS OPEN, deliberately, and this is the opposite of the usual rule.
        The consequence of a wrong False is a bar the operator hid while armed
        -- bad, and exactly the defect being fixed. The consequence of a wrong
        True is a bar that CANNOT BE HIDDEN AT ALL, on a machine where the app
        is already in trouble. A control that cannot be dismissed is worse at
        the lathe than one that can be dismissed when it should not have been,
        so an unanswerable question yields to the operator.
        """
        try:
            from reflex.app import MainApp
            app = MainApp.get_running_app()
            uic = getattr(app, "els_uic", None) if app is not None else None
            return bool(uic is not None and uic.engaged)
        except Exception as e:
            log.error(f"ADV refusal could not read engaged state: {e}")
            return False

    @staticmethod
    def image_for(move_type, background=None):
        """kv's route to :func:`move_image`.

        Called as `root.image_for(root.move_type, app.theme.background)` so the
        expression names both dependencies and kv rebinds on either. A kv-level
        `#: import` of this module cannot work: elsbar.py loads elsbar.kv while
        it is still initialising, so the directive hits a circular import.
        """
        return move_image(move_type, background)

    def _get_move_type(self):
        # Classify via the feeds table's structured mode field, not the display
        # name — see feeds.is_threading_table (a table rename must not silently
        # flip ELS between thread and feed behavior).
        if feeds.is_threading_table(self.mode_name):
            return "thread_rh" if self.els_forward else "thread_lh"
        else:
            return "turn_in" if self.els_forward else "turn_out"

    move_type = AliasProperty(_get_move_type, bind=["els_forward", "mode_name"])

    _skip_save = [
        "position",
        "x", "y",
        "minimum_width",
        "minimum_height",
        "width", "height",
        "move_type",
    ]

    def __init__(self, **kwargs):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kwargs)
        if not self.mode_name in feeds.table.keys():
            self.mode_name = next(iter(feeds.table.keys()))
        self.current_feeds_table = feeds.table[self.mode_name]
        self.update_feeds_ratio(self, None)
        self.bind(current_feeds_index=self.update_feeds_ratio)
        self.bind(els_forward=self._apply_direction)

    def toggle_move_direction(self):
        self.els_forward = not self.els_forward

    def update_current_position(self):
        Factory.Keypad().show_with_callback(self.app.servo.set_current_position, self.app.servo.scaledPosition)

    def set_feed_ratio(self, table_name, index):
        table_instance = feeds.table[table_name]
        self.mode_name = table_name
        self.current_feeds_table = table_instance
        # Apply EXPLICITLY. update_feeds_ratio is bound to current_feeds_index,
        # and a Kivy property does not dispatch when assigned its current
        # value -- so switching tables to an entry at the SAME list index
        # (Thread IN "12" tpi and Feed IN "0.020" are both index 12) left the
        # old ratio on the spindle axis and the old name on the display.
        # Observed on elspi 2026-08-21: "0.020" picked, "12 in" shown, the
        # carriage fed at 2.117 mm/rev instead of 0.508. The binding still
        # covers next_feed/previous_feed; this covers the popup.
        self.current_feeds_index = index
        self.update_feeds_ratio(self, None)

    def update_feeds_ratio(self, instance, value):
        ratio = self.current_feeds_table[self.current_feeds_index].ratio
        spindle_axis = self.app.board.get_spindle_axis()
        direction = self.app.els.direction_sign(self.els_forward)
        if spindle_axis is not None:
            spindle_axis.syncRatioNum = ratio.numerator * direction
            spindle_axis.syncRatioDen = ratio.denominator
        self.feed_name = self.current_feeds_table[self.current_feeds_index].name
        log.info(
            f"Configured ratio is: {ratio.numerator}/{ratio.denominator}, "
            f"els_forward={self.els_forward} sign={direction}"
        )

    def _apply_direction(self, *_):
        self.update_feeds_ratio(self, None)
        if self.app.board.connected:
            stop_direction = self.app.els.stop_direction_value(self.els_forward)
            self.app.board.device['elsStop']['stopDirection'] = stop_direction
            log.info(f"elsStop.stopDirection = {stop_direction}")

    def next_feed(self):
        if self.current_feeds_index < len(self.current_feeds_table) -1:
            self.current_feeds_index = (self.current_feeds_index + 1)

    def previous_feed(self):
        if self.current_feeds_index > 0:
            self.current_feeds_index = (self.current_feeds_index - 1)