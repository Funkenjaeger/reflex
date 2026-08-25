from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import NumericProperty
from kivy.uix.boxlayout import BoxLayout

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class StatusBar(BoxLayout):
    update_tick = NumericProperty(0)
    interval = NumericProperty(0)
    cycles = NumericProperty(0)
    fps = NumericProperty(0)

    def __init__(self, **kv):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kv)
        Clock.schedule_interval(self.update, 1.0 / 5)

    def update(self, *args, **kv):
        if self.app.replay_mode:
            # A replayed frame carries the interval/fps/cycles that were on
            # screen when it was captured; this timer would overwrite them with
            # the replay box's own numbers.
            return
        self.fps = Clock.get_fps()
        if not self.app.board.connected:
            return

        if self.app.board.fast_data_values is None:
            # There is no connection yet
            return
        try:
            self.interval = self.app.board.fast_data_values['executionInterval']
            self.cycles = self.app.board.fast_data_values['cycles']
        except Exception as e:
            log.debug(str(e), exc_info=True)
