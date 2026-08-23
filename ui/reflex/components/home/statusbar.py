from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import NumericProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)


class StatusBar(BoxLayout):
    update_tick = NumericProperty(0)
    interval = NumericProperty(0)
    cycles = NumericProperty(0)
    fps = NumericProperty(0)

    # ── Transient operator notice ────────────────────────────────────────────
    # A local mirror of ElsUiController.notice_text / notice_severity, NOT a kv
    # expression reaching through the app.
    #
    # WHY THE INDIRECTION. `app.els_uic` is an ObjectProperty that is None until
    # MainApp.build() constructs the controller, and this bar is built during
    # that same assembly. A kv rule reading `app.els_uic.notice_text` therefore
    # evaluates against None on its very first pass; Kivy swallows the
    # AttributeError, and the binding it failed to establish is not retried when
    # els_uic later becomes real. The strip would then be permanently dead in
    # exactly the build order the app actually uses. Mirroring in Python lets
    # the bar follow `els_uic` itself, so it picks the controller up whenever it
    # appears (or is replaced).
    notice_text = StringProperty("")
    notice_severity = StringProperty("")

    def __init__(self, **kv):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        self._notice_source = None
        super().__init__(**kv)
        Clock.schedule_interval(self.update, 1.0 / 5)
        self.app.bind(els_uic=self._follow_notice_source)
        self._follow_notice_source(self.app, self.app.els_uic)

    def _follow_notice_source(self, _app, controller):
        """(Re)attach the notice mirror to whichever controller the app holds.

        Unbinds the previous one first: the app owns a single controller today,
        but a rebuild that replaced it would otherwise leave this bar listening
        to a dead object AND a live one, and the dead one's last value would win
        or lose by binding order.
        """
        if self._notice_source is not None:
            self._notice_source.unbind(notice_text=self._sync_notice,
                                       notice_severity=self._sync_notice)
        self._notice_source = controller
        if controller is not None:
            controller.bind(notice_text=self._sync_notice,
                            notice_severity=self._sync_notice)
        self._sync_notice()

    def _sync_notice(self, *_args):
        source = self._notice_source
        # Severity before text, for the reason ElsUiController._publish_notice
        # gives: the colour must never lag the words by a frame.
        self.notice_severity = source.notice_severity if source is not None else ""
        self.notice_text = source.notice_text if source is not None else ""

    def update(self, *args, **kv):
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
