"""Minimal real object-graph harness for emulator-backed system tests (Task 5).

Builds the REAL reflex-ui dispatcher + FSM stack -- Board, ServoDispatcher,
InputDispatchers, ElsDispatcher, ElsStopHal, ElsFsm, ElsUiFsm, ElsUiController --
wired to the running emulator over its PTY Modbus link, with NO Kivy App/UI and
NO mocks of the code under test.

Key facts that make this possible (verified rev 3, see the plan's Task 5):
  * The entire graph has exactly ONE ``App.get_running_app()`` consumer:
    ``ElsDispatcher.__init__`` (els.py). It touches only ``app.board``,
    ``app.servo``, ``app.axes`` -- so the fake app is a 3-attribute shim.
  * ``Board(formats, offset_provider)`` is the real assembly root: it builds its
    own ``ConnectionManager`` from the Kivy ``config`` device serial port,
    connects, and constructs ServoDispatcher + 4 InputDispatchers + axes.
  * ``ElsUiController(els, board)`` builds its own HAL + ElsFsm + ElsUiFsm.

Two headless wrinkles the harness owns:
  * No Kivy event loop runs, so ``Board``'s ``Clock``-scheduled ``update`` never
    fires on its own. ``pump()`` / ``wait_until`` tick the clock AND call
    ``board.update()`` so register reads and ``update_tick``-bound handlers
    advance.
  * ``SavingDispatcher`` reads/writes YAML under ``~/.config/reflex``; the
    ``harness`` fixture redirects HOME to a tmp dir so runs are hermetic.
"""

import os
import time

# Use Kivy's mock GL backend + window so no real OpenGL context is created.
# On WSLg the real SDL2/GL context init takes ~135 s (and needs a display);
# the harness has no UI, so mock everything graphics. MUST be set before any
# kivy import triggers backend selection.
os.environ.setdefault("KIVY_GL_BACKEND", "mock")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_NO_ARGS", "1")

from fractions import Fraction

from kivy.clock import Clock
from kivy.event import EventDispatcher
from kivy.properties import (
    BooleanProperty, NumericProperty, ObjectProperty, StringProperty,
)


# ── minimal real collaborators (real EventDispatchers, not mocks of code
#    under test -- these stand in for UI-only providers the FSM stack reads) ──

class _Formats(EventDispatcher):
    current_format = StringProperty("MM")
    factor = ObjectProperty(Fraction(1, 1))
    position_format = StringProperty("{:+0.3f}")
    angle_format = StringProperty("{:+0.1f}")
    angle_speed_format = StringProperty("{:+0.1f}")
    speed_format = StringProperty("{:+0.3f} M/min")
    display_color = StringProperty("#ffffffff")
    hide_mouse_cursor = BooleanProperty(False)


class _OffsetProvider(EventDispatcher):
    currentOffset = NumericProperty(0)
    abs_mode = BooleanProperty(False)


class _FakeApp:
    """The 3-attribute shim ElsDispatcher expects from get_running_app()."""

    def __init__(self, board):
        self.board = board
        self.servo = board.servo
        self.axes = board.axes


class SystemHarness:
    """Assembles and drives the real ELS stack against the emulator PTY."""

    Z_SCALE_INDEX = 1  # scales[1] = Z axis (emulator main.cpp:479-480)
    SPINDLE_SCALE_INDEX = 0

    def __init__(self, pty_path: str, address: int = 17, baudrate: int = 115200):
        self.pty_path = pty_path
        self.address = address
        self.baudrate = baudrate
        self.board = None
        self.els = None
        self.controller = None
        self.els_fsm = None
        self.ui_fsm = None
        self.hal = None
        self._fake_app = None

    def connect(self):
        # Point Board's ConnectionManager at the emulator PTY before Board is
        # built (Board reads these in __init__ and connects immediately).
        from reflex.components.appsettings import config
        if not config.has_section("device"):
            config.add_section("device")
        config.set("device", "serial_port", self.pty_path)
        config.set("device", "baudrate", str(self.baudrate))
        config.set("device", "address", str(self.address))

        from reflex.dispatchers.board import Board
        self.board = Board(_Formats(), _OffsetProvider())

        # Register the fake app BEFORE ElsDispatcher (its __init__ calls
        # App.get_running_app()). MainApp inherits get_running_app from App.
        import kivy.app
        self._fake_app = _FakeApp(self.board)
        kivy.app.App.get_running_app = staticmethod(lambda: self._fake_app)

        from reflex.dispatchers.els import ElsDispatcher
        self.els = ElsDispatcher()

        from reflex.fsms.ui_controller import ElsUiController
        self.controller = ElsUiController(self.els, self.board)
        self.els_fsm = self.controller._els_fsm
        self.ui_fsm = self.controller._ui_fsm
        self.hal = self.controller._hal

        # Establish the link: Board.update() flips board.connected True on the
        # first successful refresh, which fires the dispatchers' on-connect
        # handlers (ServoDispatcher servoDir / InputDispatcher scaleDir writes).
        self.wait_until(lambda: self.connected, timeout_s=5)
        return self

    @property
    def connected(self) -> bool:
        return bool(self.board and self.board.connected)

    # ── headless clock pumping ────────────────────────────────────────────
    def pump(self, seconds: float = 0.0):
        """Advance the Kivy clock and run one Board update, so register reads
        and update_tick-bound handlers fire without a running App loop."""
        Clock.tick()
        try:
            self.board.update()
        except Exception:
            pass
        if seconds:
            time.sleep(seconds)

    def wait_until(self, predicate, timeout_s: float, poll_s: float = 0.02) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.pump()
            if predicate():
                return True
            time.sleep(poll_s)
        self.pump()
        return bool(predicate())

    # ── read-back helpers ─────────────────────────────────────────────────
    def _scale_position(self, index: int) -> int:
        return int(self.board.device['scales'][index]['position'])

    def carriage_position_counts(self) -> int:
        return self._scale_position(self.Z_SCALE_INDEX)

    def register(self, struct: str, field: str):
        return self.board.device[struct][field]

    # ── ELS cut-cycle driving (through the real controller/FSM) ───────────
    def configure(self, *, is_threading=False, retract_enabled=False,
                  wizard_enabled=False, els_forward=True,
                  spindle_axis_index=0, z_axis_index=1, x_axis_index=2):
        """Map the ELS axes and set operator-mode flags via the real controller."""
        self.els.spindle_axis_index = spindle_axis_index
        self.els.z_axis_index = z_axis_index
        self.els.x_axis_index = x_axis_index
        self.controller.is_threading = is_threading
        self.controller.retract_enabled = retract_enabled
        self.controller.wizard_enabled = wizard_enabled
        self.controller.els_forward = els_forward
        self.pump()

    def z_scaled_position(self) -> float:
        return self.els.get_z_axis().scaledPosition

    def safety_margin(self) -> float:
        return self.els_fsm._safety_margin_display()

    def engage(self):
        """Engage ELS (domain FSM disabled→stopped). Arms + holds via active=1
        when Z is on the safe side of the current stop_z."""
        self.els_fsm.enable()
        self.pump()

    def enable_sync(self):
        """Operator 'Sync Enable': start spindle-synced servo feed. active=1 (from
        engage) holds it until cut releases."""
        self.board.servo.toggle_enable()
        self.pump()

    def set_stop_z(self, value: float):
        self.controller.stop_z = value
        self.pump()

    def cut(self):
        """Release the armed stop so the carriage feeds to stop_z."""
        self.controller.start_cut()
        self.pump()

    # ── production write-path toggle setters (fire the real handlers) ─────
    def set_servo_reverse(self, reverse: bool):
        self.board.servo.reverse = reverse

    def set_input_reverse(self, scale_index: int, reverse: bool):
        for inp in self.board.inputs:
            if inp.inputIndex == scale_index:
                inp.reverse = reverse
                return
        raise KeyError(f"no InputDispatcher for scale index {scale_index}")

    X_SCALE_INDEX = 2

    def apply_wiring_toggles(self, toggles: dict):
        """Set the four operator reverse toggles to CANCEL a wiring permutation,
        through the production write path (fires the real reverse handlers).

        `toggles` keys: spindle_reverse, z_reverse, x_reverse, servo_reverse
        (as produced by tests/system/wiring.py's canceling_toggles)."""
        self.set_input_reverse(self.SPINDLE_SCALE_INDEX, toggles["spindle_reverse"])
        self.set_input_reverse(self.Z_SCALE_INDEX, toggles["z_reverse"])
        self.set_input_reverse(self.X_SCALE_INDEX, toggles["x_reverse"])
        self.set_servo_reverse(toggles["servo_reverse"])
        self.pump()

    def disconnect(self):
        # Clean up GLOBAL state so parametrized/batched tests don't accumulate
        # stale Clock intervals + event-bus subscribers (which slow every pump
        # and cause cross-test drift). Kivy Clock.unschedule(cb) removes a
        # callback with no stored handle needed; Board schedules update+blinker.
        if self.board is not None:
            try:
                Clock.unschedule(self.board.update)
                Clock.unschedule(self.board.blinker)
            except Exception:
                pass
        try:
            from reflex.fsms.fsm_event_bus import fsm_event_bus
            fsm_event_bus._subs.clear()
        except Exception:
            pass
        if self.board is not None:
            try:
                self.board.connection_manager.disconnect()
            except Exception:
                pass
