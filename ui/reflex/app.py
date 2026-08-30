import os

import sentry_sdk
from kivy.app import App
from kivy.config import Config
from kivy.core.audio import SoundLoader
from kivy.properties import (ObjectProperty, ConfigParserProperty, NumericProperty,
                             ListProperty, StringProperty, BooleanProperty,
                             AliasProperty)
from kivy.logger import Logger
log = Logger.getChild(__name__)

from reflex.utils.log_levels import apply_log_levels
# Before the FSMs are built, so `transitions` is already quiet by the time it
# has anything to say. Overridable per-logger via REFLEX_LOG_* -- see the module.
apply_log_levels()

from reflex.components.appsettings import config
import reflex.components.widgets.facelift_chrome  # noqa: F401  (installs global Popup/form chrome)
from reflex.components.widgets.theme_provider import ThemeProvider
from reflex.dispatchers.axis import AxisDispatcher
from reflex.dispatchers.board import Board
from reflex.dispatchers.els import ElsDispatcher
from reflex.dispatchers.formats import FormatsDispatcher
from reflex.dispatchers.input import InputDispatcher
from reflex.dispatchers.servo import ServoDispatcher
from reflex.fsms.ui_controller import ElsUiController

# Operating modes (must match home_screen mode_layouts keys and ModePopup buttons)
MODE_INDEX = 1
MODE_ELS = 2
MODE_JOG = 3
MODE_DRO = 4

# Which modes each use case exposes. DRO is available everywhere.
USE_CASE_MODES = {
    "rotary_table": [MODE_INDEX, MODE_JOG, MODE_DRO],
    "lathe": [MODE_ELS, MODE_DRO],
    "all_features": [MODE_INDEX, MODE_ELS, MODE_JOG, MODE_DRO],
}
# Which use cases expose the PATTERN screen (hole circles, lines, rectangles),
# reached from the wand in the sidebar. A rotary-table feature: it lays holes
# out on a face. A lathe has nothing to point it at, so the button is not shown
# there at all -- the same way USE_CASE_MODES keeps ELS off a rotary table.
#
# This is a separate table because the pattern screen is not a MODE. Folding it
# into USE_CASE_MODES would have made it selectable by the mode one-hot.
USE_CASE_PATTERNS = {
    "rotary_table": True,
    "lathe": False,
    "all_features": True,
}
USE_CASE_LABELS = {
    "rotary_table": "Rotary Table",
    "lathe": "Lathe",
    "all_features": "All Features",
}
DEFAULT_USE_CASE = "rotary_table"


class MainApp(App):
    formats = ObjectProperty()
    # Active UI color theme. Bound to the persisted formats.theme selector so
    # KV can reference app.theme.<token> and recolor live on switch.
    theme = ObjectProperty()
    currentOffset = NumericProperty(0)
    abs_mode = BooleanProperty(False)

    board = ObjectProperty()

    servo: ServoDispatcher = ObjectProperty()

    inputs: list[InputDispatcher] = ListProperty()

    # Backward compat alias for KV files that reference app.scales
    scales: list[InputDispatcher] = ListProperty()

    axes: list[AxisDispatcher] = ListProperty()

    els: ElsDispatcher = ObjectProperty()

    els_uic: ElsUiController = ObjectProperty()

    current_mode = ConfigParserProperty(
        defaultvalue=1, section="device", key="current_mode", config=config, val_type=int
    )

    use_case = ConfigParserProperty(
        defaultvalue=DEFAULT_USE_CASE, section="device", key="use_case", config=config, val_type=str
    )

    def _get_patterns_available(self):
        """Does this use case expose the pattern screen at all?

        An AliasProperty rather than a method so kv rebinds when `use_case`
        changes: the sidebar button has to appear and disappear with the
        setting, not only at startup.
        """
        return USE_CASE_PATTERNS.get(self.use_case,
                                     USE_CASE_PATTERNS[DEFAULT_USE_CASE])

    patterns_available = AliasProperty(_get_patterns_available,
                                       bind=["use_case"], cache=True)

    manager = ObjectProperty()

    sound = ObjectProperty()

    version = StringProperty()

    def __init__(self, **kv):
        super().__init__(**kv)


    def beep(self, *args, **kv):
        if self.sound and hasattr(self, "formats"):
            self.sound.volume = self.formats.volume
            self.sound.play()

    @staticmethod
    def load_help(help_file_name):
        """
        Loads the specified help file text from the help files folder.
        """
        help_file_path = os.path.join(
            os.path.dirname(__file__),
            "help",
            help_file_name
        )
        if not os.path.exists(help_file_path):
            return "Help file not found"

        with open(help_file_path, "r") as f:
            return f.read()

    def allowed_modes(self) -> list[int]:
        """Modes selectable for the current use case (DRO is always allowed)."""
        return USE_CASE_MODES.get(self.use_case, USE_CASE_MODES[DEFAULT_USE_CASE])

    def set_mode(self, mode_id: int):
        if mode_id not in self.allowed_modes():
            mode_id = MODE_DRO
        self.current_mode = mode_id

    def on_use_case(self, instance, value):
        # If the active mode is no longer valid for the new use case, fall back
        # to DRO (common to all use cases and the most benign).
        self.set_mode(self.current_mode)

    def on_servo_enable_pressed(self):
        """Sync-Enable (servo power-feed) button intent, with the ELS feed guard.

        In advanced ELS mode, enabling the feed with no armed ELS stop would run
        the carriage with no automatic stop (audit H2b/H6) — so ask for explicit
        confirmation first. In every other mode (basic power feed) and when a
        stop is armed, toggle the feed directly. Disabling is never gated."""
        if self.current_mode != MODE_ELS or self.els_uic is None:
            self.servo.toggle_enable()
            return
        if self.els_uic.request_feed_enable():
            return  # enabled directly (feed off, or a stop is armed)
        # Refused: no armed stop. Confirm to override.
        from reflex.components.popups.custom_popup import CustomPopup
        CustomPopup(
            title="No ELS Stop Armed",
            # Cause-specific: "no stop set", "not engaged", and "set but not
            # armed (Z at/past it)" are different operator problems with
            # different remedies, and the old one-size text claimed the first
            # in all three cases (round-2 finding, 2026-08-17).
            message=self.els_uic.unarmed_stop_message(),
            button_text="Enable Feed",
            cancel_text="Cancel",
            confirm_callback=lambda: self.els_uic.request_feed_enable(confirmed=True),
        ).open()

    def get_spindle_axis(self):
        return self.board.get_spindle_axis()

    def build(self):
        # Neutralize Kivy's stock exit_on_escape default app-side. On elspi,
        # ~/.kivy/config.ini is regenerable machine state (deploy/reflex-ui.service
        # runs as root, so this is /root/.kivy/config.ini) -- we don't want the
        # backstop for "the app comes back up after a clean exit" to depend on a
        # config.ini value surviving a regenerate. This survives regardless.
        # Escape is checked live against Config on each keypress, so setting it
        # here (before any window exists yet) is early enough.
        Config.set('kivy', 'exit_on_escape', '0')

        self.formats = FormatsDispatcher(id_override="0")
        # Reactive theme: seed from the persisted selection and keep the two in
        # sync so the formats-menu picker drives a live recolor. Coerce any
        # stale/invalid persisted value to the default and write it back, so the
        # selector, the saved file, and the live theme can never disagree.
        if self.formats.theme not in ThemeProvider.available_themes():
            self.formats.theme = ThemeProvider.DEFAULT
        self.theme = ThemeProvider(name=self.formats.theme)

        def _sync_theme(_inst, value):
            # Guard: `name` is an OptionProperty, so an unknown value would raise.
            if value not in ThemeProvider.available_themes():
                log.warning(f"Ignoring unknown UI theme {value!r}")
                return
            self.theme.name = value
            # Seed the operator-configurable readout/indicator colors with the
            # new theme's recommendations (the operator can re-override after).
            # Skip any seed that backfilled to None (a user theme may omit the
            # [seeds] section); a None would raise from ColorProperty and abort
            # the switch mid-loop.
            from reflex.components.widgets import palettes
            for prop, color in palettes.FORMATS_RECOMMENDED.get(value, {}).items():
                if color is None:
                    continue
                setattr(self.formats, prop, color)
        self.formats.bind(theme=_sync_theme)
        # The screen background: clearcolor is the flat fallback, and a gradient
        # texture is painted over it on the Window's own canvas (before any
        # screen) so empty areas read with a faint top-down lift instead of a
        # flat slab. No widget owns the backdrop, so the Window canvas is the
        # one place that covers every screen at once.
        from kivy.core.window import Window
        from kivy.graphics import Color, Rectangle
        Window.clearcolor = self.theme.background
        self.theme.bind(background=lambda _inst, value: setattr(Window, "clearcolor", value))
        with Window.canvas.before:
            self._bg_color = Color(1, 1, 1, 1)
            self._bg_rect = Rectangle(
                pos=(0, 0), size=Window.size, texture=self.theme.background_texture
            )

        def _resize_bg(*_):
            self._bg_rect.pos = (0, 0)
            self._bg_rect.size = Window.size
        Window.bind(size=_resize_bg)
        self.theme.bind(
            background_texture=lambda _i, v: setattr(self._bg_rect, "texture", v)
        )
        self.board = Board(formats=self.formats, offset_provider=self)

        # Load beep sound
        sound_path = os.path.join(os.path.dirname(__file__), "sounds", "snap.wav")
        self.sound = SoundLoader.load(sound_path)
        if self.sound is None:
            log.warning(f"Failed to load sound from {sound_path}")

        if not self.formats.disable_error_reporting:
            log.info("Error reporting is enabled, configuring Sentry")
            sentry_sdk.init(
                dsn="https://8fd20c0607e9c930a16d51a4b1eacc94@o4509625403506688.ingest.us.sentry.io/4509625405014016",
                send_default_pii=False,
                traces_sample_rate=0.2,
            )

        # Backward compat aliases — most KV files use app.servo / app.inputs / app.axes
        self.servo = self.board.servo
        self.inputs = list(self.board.inputs)
        self.scales = list(self.board.inputs)  # backward compat alias
        self.axes = list(self.board.axes)

        self.els = ElsDispatcher(id_override="0")

        self.els_uic = ElsUiController(els=self.els, board=self.board)

        self.beep()

        import importlib.metadata
        dist = importlib.metadata.distribution("reflex")
        origin = dist.read_text('direct_url.json')
        self.version = "v" + importlib.metadata.version("reflex")
        if origin and '"dir_info": {"editable": true}' in origin:
            self.version = self.version + "*"

        self._apply_mouse_cursor()
        self.formats.bind(hide_mouse_cursor=lambda *_: self._apply_mouse_cursor())

        from reflex.components.manager import Manager
        self.manager = Manager()
        return self.manager

    def _apply_mouse_cursor(self):
        if self.formats.hide_mouse_cursor:
            Config.set('graphics', 'show_cursor', '0')
        else:
            Config.set('graphics', 'show_cursor', '1')
        from kivy.core.window import Window
        Window.show_cursor = not self.formats.hide_mouse_cursor
