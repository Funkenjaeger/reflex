from kivy.factory import Factory
from kivy.logger import Logger
from kivy.properties import StringProperty, ObjectProperty, NumericProperty, BooleanProperty, AliasProperty
from kivy.uix.boxlayout import BoxLayout
from pydantic import BaseModel

from rcp import feeds
from rcp.dispatchers.saving_dispatcher import SavingDispatcher
from rcp.utils.kv_loader import load_kv


class FeedMode(BaseModel):
    id: int
    name: str

log = Logger.getChild(__name__)
load_kv(__file__)


class ElsBar(BoxLayout, SavingDispatcher):
    feed_button = ObjectProperty(None)
    feed_ratio = ObjectProperty(None)

    mode_name = StringProperty(":(")
    feed_name = StringProperty(":(")
    current_feeds_index = NumericProperty(0)
    els_forward = BooleanProperty(True)
    enable_advanced = BooleanProperty(False)

    def _get_move_type(self):
        if "Thread" in self.mode_name:
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
        from rcp.app import MainApp
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
        self.current_feeds_index = index

    def update_feeds_ratio(self, instance, value):
        ratio = self.current_feeds_table[self.current_feeds_index].ratio
        spindle_axis = self.app.board.get_spindle_axis()
        direction = 1 if self.els_forward else -1
        if spindle_axis is not None:
            spindle_axis.syncRatioNum = ratio.numerator * direction
            spindle_axis.syncRatioDen = ratio.denominator
        self.feed_name = self.current_feeds_table[self.current_feeds_index].name
        log.info(
            f"Configured ratio is: {ratio.numerator}/{ratio.denominator}, "
            f"els_forward={self.els_forward}"
        )

    def _apply_direction(self, *_):
        self.update_feeds_ratio(self, None)
        if self.app.board.connected:
            stop_direction = -1 if self.els_forward else 1
            self.app.board.device['elsStop']['stopDirection'] = stop_direction
            log.info(f"elsStop.stopDirection = {stop_direction}")

    def next_feed(self):
        if self.current_feeds_index < len(self.current_feeds_table) -1:
            self.current_feeds_index = (self.current_feeds_index + 1)

    def previous_feed(self):
        if self.current_feeds_index > 0:
            self.current_feeds_index = (self.current_feeds_index - 1)