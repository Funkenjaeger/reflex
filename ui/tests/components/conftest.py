"""Shared fixtures for the ELS widget-layer construction/wiring tests.

Widgets under test (ElsAdvancedBar, ElsSettingsPopup, Keypad) all call
``MainApp.get_running_app()`` from inside their methods. ``MainApp`` inherits
``get_running_app`` from ``kivy.app.App`` (a ``@staticmethod``), so — mirroring
``tests/system/harness.py`` — the one place that reaches every call site is
patching that staticmethod on ``kivy.app.App`` itself.

Hermeticity note (Windows-specific finding): ``ElsAdvancedBar`` is a
``SavingDispatcher`` subclass, which does real YAML I/O under
``Path.home() / ".config" / "reflex"`` on construction and on every later
property write. The ``tests/system/conftest.py`` pattern of
``monkeypatch.setenv("HOME", str(tmp_path))`` does NOT make that hermetic on
this Windows Python: verified empirically that ``pathlib.Path.home()`` (and
``os.path.expanduser("~")``) read ``USERPROFILE`` and ignore ``HOME``
entirely here, even when ``HOME`` is set directly via ``os.environ``. Setting
only ``HOME`` silently left every construction test writing YAML into the
REAL ``~/.config/reflex`` on the machine running these tests (confirmed, then
cleaned up, during development of this suite). So instead of an env-var
redirect, ``SavingDispatcher.read_settings``/``save_settings`` are no-op'd
directly for the duration of every test in this package.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import kivy.app
import pytest

from reflex.dispatchers.saving_dispatcher import SavingDispatcher


@pytest.fixture(autouse=True)
def _no_settings_disk_io(monkeypatch):
    """Never let a SavingDispatcher subclass (ElsAdvancedBar) touch the real
    ~/.config/reflex while this test package runs. See module docstring for
    why an env-var HOME redirect doesn't work on this platform."""
    monkeypatch.setattr(SavingDispatcher, "read_settings", lambda self: None)
    monkeypatch.setattr(SavingDispatcher, "save_settings", lambda self, *a, **kw: None)


@pytest.fixture
def fake_app():
    """Minimal stand-in for MainApp.get_running_app(), exposing only the
    attributes els_advbar.py / els_settings_popup.py actually read:
    els_uic (controller), els (get_z_axis/get_x_axis), formats.current_format,
    servo.ratioNum/ratioDen."""
    controller = MagicMock(name="els_uic")
    controller.in_cycle = False  # must be a real bool: mirrored into a BooleanProperty
    return SimpleNamespace(
        els_uic=controller,
        els=MagicMock(name="els"),
        formats=SimpleNamespace(current_format="MM"),
        servo=SimpleNamespace(ratioNum=127, ratioDen=64000),
    )


@pytest.fixture
def running_app(fake_app, monkeypatch):
    """Install `fake_app` as the object MainApp.get_running_app() returns."""
    monkeypatch.setattr(kivy.app.App, "get_running_app", staticmethod(lambda: fake_app))
    return fake_app


@pytest.fixture
def advbar_factory(running_app):
    """Build a real ElsAdvancedBar headlessly against `running_app`.

    apply_class_lang_rules is patched out (same pattern as
    test_custom_popup.py's popup_factory) so the widget's own els_advbar.kv
    rule tree — which pulls in real child widgets/graphics that the mock GL
    backend can't service — never gets built. The Python constructor wiring
    under test runs for real.
    """
    from reflex.components.home.els_advbar import ElsAdvancedBar

    def _make(**kwargs):
        with patch.object(ElsAdvancedBar, "apply_class_lang_rules"):
            return ElsAdvancedBar(**kwargs)

    return _make


@pytest.fixture
def make_axis():
    """Build a MagicMock standing in for an AxisDispatcher, with a settable
    scaledPosition (the only attribute the widget code under test reads)."""
    def _make(position=0.0):
        axis = MagicMock(name="axis")
        axis.scaledPosition = position
        return axis

    return _make
