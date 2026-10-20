"""Build a REAL HomePage through its own __init__, headlessly.

WHY THIS EXISTS. The mode-switch tests build HomePage with
``HomePage.__new__``, which skips the constructor's path to the first layout.
That path must draw the layout at once, even while the servo moves, and write
no register.

What is real and what is stood in for:

* REAL: ``HomePage.__init__``, ``change_mode``, ``change_mode_speed_check``
  and whatever servo, controller and Clock the caller hands in. A system test
  passes the harness's live ServoDispatcher and ElsUiController, so every
  register write the screen makes reaches the emulator.
* STOOD IN: the kv rule tree (the mock GL backend cannot build its graphics),
  so ``ids`` gets one real BoxLayout as ``bars_container``; the status bar,
  the ElsBar and the four mode layouts, which are plain Widgets here because
  only their presence in ``bars_container`` is under test; and the app, a
  small EventDispatcher with the two properties HomePage binds; and the
  mock backend's Window, which is None.
"""
from types import SimpleNamespace
from unittest.mock import patch

from kivy.event import EventDispatcher
from kivy.properties import ListProperty, NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget

MODE_INDEX, MODE_ELS, MODE_JOG, MODE_DRO = 1, 2, 3, 4


class HomeApp(EventDispatcher):
    """The app surface HomePage reads: current_mode and axes (bound),
    allowed_modes(), servo and els_uic."""
    current_mode = NumericProperty(MODE_ELS)
    axes = ListProperty()

    def __init__(self, servo, els_uic, current_mode=MODE_ELS, **kw):
        super().__init__(**kw)
        self.servo = servo
        self.els_uic = els_uic
        self.current_mode = current_mode

    def allowed_modes(self):
        return [MODE_INDEX, MODE_ELS, MODE_JOG, MODE_DRO]


class _Stub(Widget):
    def __init__(self, *a, **kw):
        super().__init__()

    def rebuild_axes(self):
        pass


class _JogBar:
    enable_jog = False


class _JogLayout(_Stub):
    def __init__(self, *a, **kw):
        super().__init__()
        self.jog_bar = _JogBar()


class _Keyboard:
    def bind(self, **kw):
        pass


def _fake_rules(self, *a, **kw):
    self.ids = {"bars_container": BoxLayout()}


def build_home_page(app):
    """Construct HomePage(name="home") against `app`. Returns the page."""
    import kivy.app
    from reflex.components.screens import home_screen

    with patch.object(home_screen.HomePage, "apply_class_lang_rules", _fake_rules), \
            patch.object(home_screen, "StatusBar", _Stub), \
            patch.object(home_screen, "ElsBar", _Stub), \
            patch.object(home_screen, "IndexModeLayout", _Stub), \
            patch.object(home_screen, "ElsModeLayout", _Stub), \
            patch.object(home_screen, "JogModeLayout", _JogLayout), \
            patch.object(home_screen, "DroModeLayout", _Stub), \
            patch.object(home_screen, "Window", SimpleNamespace(_system_keyboard=_Keyboard())), \
            patch.object(kivy.app.App, "get_running_app", staticmethod(lambda: app)):
        return home_screen.HomePage(name="home")


def shown_layout(page):
    """The mode layout in bars_container, or None. Asserts at most one."""
    shown = [w for w in page.bars_container.children
             if w in page.mode_layouts.values()]
    assert len(shown) <= 1, f"more than one mode layout shown: {shown}"
    return shown[0] if shown else None
