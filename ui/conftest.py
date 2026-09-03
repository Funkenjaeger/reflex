"""Repo-root pytest configuration.

Force Kivy's mock GL/window backends for the entire test suite. Any test that
imports the app or a dispatcher pulls in Kivy; on WSLg (and headless CI) a real
SDL2/OpenGL context takes ~135 s to initialize and needs a display, which is
what made test collection crawl. No pytest test renders anything (the README
screenshot capture is a standalone script, not a test), so mock graphics is
safe and makes imports near-instant.

This MUST run before any `import kivy` selects a backend. A repo-root conftest
is imported by pytest before collecting any test module, so this is the earliest
reliable hook.
"""
import os

os.environ.setdefault("KIVY_GL_BACKEND", "mock")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_NO_ARGS", "1")

import pytest


@pytest.fixture(autouse=True, scope="session")
def _flight_recorder_sandbox(tmp_path_factory):
    """Keep the flight recorder off the developer's real home directory.

    FlightRecorder proves its directory is writable IN ITS CONSTRUCTOR -- that
    self-test is the mechanism that makes an unwritable path loud at startup
    instead of silently discovered during a cut, so it is not something to
    weaken for tests. The consequence is that every test which builds an
    ElsUiController would otherwise mkdir and write a status file under
    ``~/.config/reflex/flight``.

    Session-scoped and env-based rather than per-test, because the pollution is
    a property of constructing the controller at all, not of any one test. Tests
    that care about the recorder's files pass ``directory=`` explicitly and
    never see this.

    ``setdefault``, so an explicitly exported ``REFLEX_FLIGHT_DIR`` still wins.
    That is how a system-test run gets pointed at a directory somebody can open
    afterwards -- the only way to confirm the recorder CAPTURES a real
    emulator-driven cut rather than merely failing to crash during one.
    """
    os.environ.setdefault("REFLEX_FLIGHT_DIR",
                          str(tmp_path_factory.mktemp("flight")))
    yield


@pytest.fixture(autouse=True)
def _headless_window():
    """Give widget-instantiating tests a stub Window under the mock backend.

    ``KIVY_WINDOW=mock`` above is not a real window provider (Kivy has none), so
    ``EventLoop.window`` stays ``None`` and any test that constructs a real Kivy
    ``Widget`` hits ``EventLoop.ensure_window()`` -> ``sys.exit(1)`` ("Unable to
    get a Window, abort."). Dispatcher/FSM/system tests never build widgets so
    they were unaffected; the screen tests are. A real sdl2 window is not an
    option here -- it needs OpenGL (the ``dummy`` SDL video driver has none) and,
    with a display, costs ~2 min just for window init.

    So install a lightweight stub window that satisfies ``ensure_window()`` and
    lets widgets build headlessly. Idempotent and a no-op when a real window
    already exists (e.g. running the suite on a machine with a display)."""
    from kivy.base import EventLoop
    if EventLoop.window is None:
        from unittest.mock import MagicMock
        EventLoop.window = MagicMock()
    yield
