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

    SPINDLE_SCALE_INDEX = 0  # scales[0] = spindle
    Z_SCALE_INDEX = 1        # scales[1] = Z axis (emulator main.cpp)
    X_SCALE_INDEX = 2        # scales[2] = X / cross-slide

    def __init__(self, pty_path: str, address: int = 17, baudrate: int = 115200,
                 proc=None):
        self.pty_path = pty_path
        self.address = address
        self.baudrate = baudrate
        # Optional emulator Popen handle (emulator_process fixture) -- lets
        # emu_cmd() write to the emulator's stdin command channel
        # (reflex-fw emulator/src/main.cpp stdinCommandThreadFunc). None if
        # the harness was built without one; emu_cmd() raises in that case.
        self.proc = proc
        self.board = None
        self.els = None
        self.controller = None
        self.els_fsm = None
        self.ui_fsm = None
        self.hal = None
        self._fake_app = None
        self._orig_get_running_app = None

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
        # Capture the original so disconnect() can restore it — otherwise a
        # later non-system test in the same process gets a stale _FakeApp
        # bound to a dead board instead of None.
        import kivy.app
        self._fake_app = _FakeApp(self.board)
        self._orig_get_running_app = kivy.app.App.get_running_app
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
        if not self.wait_until(lambda: self.connected, timeout_s=5):
            raise RuntimeError(
                f"harness never established a Modbus connection to {self.pty_path}"
            )
        return self

    @property
    def connected(self) -> bool:
        return bool(self.board and self.board.connected)

    # ── headless clock pumping ────────────────────────────────────────────
    def pump(self):
        """Advance the Kivy clock and run one Board update, so register reads
        and update_tick-bound handlers fire without a running App loop.

        Board.update() handles its own Modbus/comms failures internally; we do
        NOT swallow exceptions here so a genuine crash in an update_tick-bound
        handler (ElsFsm._on_board_update, controller polls — the code under
        test) surfaces loudly instead of hiding as a generic wait_until timeout."""
        Clock.tick()
        self.board.update()

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

    def x_position_counts(self) -> int:
        return self._scale_position(self.X_SCALE_INDEX)

    # ── emulator stdin command channel (physics-only, e.g. X/cross-slide moves) ─
    def emu_cmd(self, line: str):
        """Write one command line to the emulator's stdin channel and flush.

        Requires the harness to have been constructed with `proc` (the
        emulator_process fixture's Popen handle) -- the plain `harness`
        fixture wires this up automatically. See reflex-fw
        emulator/src/main.cpp (stdinCommandThreadFunc) for the grammar:
            "x move <mm>"    -> physics->moveCrossSlideTo(mm)
            "x jog <-1|0|1>" -> physics->jogCrossSlide(dir)
            "z move <mm>"    -> physics->moveCarriageTo(mm)
            "z jog <-1|0|1>" -> physics->jogCarriage(dir)
            "halfnut open"   -> physics->setHalfNutEngaged(False)
            "halfnut close"  -> physics->setHalfNutEngaged(True)

        The z + halfnut commands together stage the machine state the emulator
        could not previously represent -- UNCOUPLED BUT MOVING ANYWAY, i.e. the
        operator pushing the carriage with the nut open. That absence is why the
        2026-08-08 take-up gate defect was unreachable by test; see
        test_els_takeup_attribution.py.

        Two behaviours worth knowing before using them:
          * "z jog" models an arrow key being HELD. LathePhysics stops it after
            0.15 s of silence, so a single command is a tap -- re-send it each
            poll to keep the carriage moving, then "z jog 0" to stop.
          * "halfnut" takes an explicit STATE, not a toggle, and is idempotent.
            A toggle issued while an engage is waiting for phase alignment
            CANCELS it, so toggle-shaped commands mean different things
            depending on state the test cannot see.
        """
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError(
                "SystemHarness has no emulator stdin handle "
                "(constructed without proc=, or the emulator has exited)"
            )
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def register(self, struct: str, field: str):
        return self.board.device[struct][field]

    # ── display-frame helpers (units + DRO re-zero) ───────────────────────
    def set_imperial(self):
        """Switch the display frame to inches, mirroring FormatsDispatcher's
        update_format (factor 10/254, current_format "IN"). The real machine
        runs imperial ("Thread IN"); the harness boots metric like the app
        does, and tests that claim real-machine fidelity should switch."""
        self.board.formats.current_format = "IN"
        self.board.formats.factor = Fraction(10, 254)
        self.pump()

    def rezero_z(self):
        """Operator 'Zero' on the ELS Z axis (axis.zero_position()) — the
        display re-frames, the physical encoder does not move. This is the
        event the encoder-anchored ELS targets exist to survive."""
        self.els.get_z_axis().zero_position()
        self.pump()

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
        # Route through the operator commit path so the stop is anchored to the
        # physical encoder (not just the scaled display mirror).
        self.controller.commit_standalone_stop_z(value)
        self.pump()

    def set_retract_z(self, value: float):
        self.controller.commit_standalone_retract_z(value)
        self.pump()

    def cut(self):
        """Press the action button to start the cut. Drives it through the UI
        FSM (waiting_to_cut -> cutting -> start_cut) so the UI FSM is in
        `cutting` when the stop fires and the cut_done/retract cascade can run
        (start_cut alone drives only the domain FSM)."""
        self.controller.on_action_button_clicked()
        self.pump()

    def trigger_retract(self):
        """Press the action button in waiting_to_retract to start the retract
        (retracting -> start_retract -> ElsFsm.retract)."""
        self.controller.on_action_button_clicked()
        self.pump()

    # ── production write-path toggle setters (fire the real handlers) ─────
    def set_servo_reverse(self, reverse: bool):
        self.board.servo.reverse = reverse

    def commission_servo(self, *, reverse: bool = True, max_speed: float = 10000,
                         acceleration: float = 20000):
        """Apply the real machine's servo commissioning (see elspi's ServoBar-0:
        reverse=true, maxSpeed=10000, acceleration=20000).

        Two reasons a test needs this instead of the hermetic defaults:
          * ``reverse`` sets servoDir=-1. The direct-servo retract move
            (``stepsToGo``) depends on servoDir, so without the real polarity it
            drives the carriage the WRONG way (the EMU_RPM sign band-aid only
            fixes the sync-mediated cut, not the retract).
          * ``max_speed`` at the real 10000 (vs the hermetic 1000 default) keeps
            the emulated servo up with the sync feed. At 1000 a cut-time step
            backlog flushes after the ELS stop and drags the carriage past it —
            an emulator 10 kHz-ISR artifact, negligible on ~100 kHz hardware.
        All go through the production property write-path (fires the real
        on_reverse / on_maxSpeed / on_acceleration handlers)."""
        self.board.servo.reverse = reverse
        self.board.servo.maxSpeed = max_speed
        self.board.servo.acceleration = acceleration
        self.pump()

    def _input(self, scale_index: int):
        for inp in self.board.inputs:
            if inp.inputIndex == scale_index:
                return inp
        raise KeyError(f"no InputDispatcher for scale index {scale_index}")

    def set_input_reverse(self, scale_index: int, reverse: bool):
        self._input(scale_index).reverse = reverse

    def commission_geometry(self, *, z_counts_per_mm=400, x_counts_per_mm=400,
                            spindle_ppr=4000, leadscrew_steps=800):
        """Commission the UI's machine geometry, through the production
        settings write paths. Call AFTER configure() — the spindle sync-ratio
        push below needs the spindle axis role assigned.

        Without this, the hermetic default settings (1 count/mm scales, the
        rotary 400/360 ≈ 1.1111 mm/step servo ratio) make every count-domain
        value the UI pushes to firmware (syncRatioNum/Den, threadPitchSteps,
        zCountsPerPitch) physically meaningless relative to the emulator's
        physics — its dashboard flags exactly this ("WARN geom mismatch",
        dashboard.cpp geometry cross-check). With it, scaled positions, spans
        and tolerances in tests are REAL MILLIMETERS of the machine the
        emulator config was built to match.

        The keyword-only params default to the emulator REFERENCE machine's
        geometry (reflex-fw emulator/config/lathe.toml) — every existing
        caller that doesn't pass overrides gets exactly the prior behavior:

          * Z & X scales: 400 counts/mm  → input ratio 1/400 mm per count
          * Spindle: 4000 counts/rev (encoder_ppr=4000, gear 1:1)
                     → 1/4000 rev per count in the sync-ratio math
          * Servo leadscrew: 8 TPI (0.125 in = 3.175 mm) at 800 steps/rev
                     → exactly 127/32000 mm per step (0.00396875)

        To commission a DIFFERENT machine's geometry (e.g. the real elspi
        lathe: z_counts_per_mm=200, spindle_ppr=6144, leadscrew_steps=1600),
        pass the overrides here AND patch the emulator's own TOML to match via
        tests/system/wiring.py's make_config (see test_els_elspi_geometry.py)
        — otherwise the UI and the emulator's physics disagree by whatever
        ratio separates the two geometries (see the fidelity-gap note in
        test_els_real_config.py, which mirrors only the reference geometry
        for exactly this reason).

        Exactness: 0.125 in is binary-exact, so configure_lead_screw_ratio's
        Fraction chain ((1/8) × 254/10 / leadscrew_steps) yields exactly
        127/(40*leadscrew_steps) with no float error (asserted below). Scale
        ratios are exact int properties.

        Real-units consequences tests must respect:
          * The reference machine starts at Z=0 mm with min_position_mm=-5, and
            an els_forward=True cut feeds physically -Z — so cut spans must
            clear the ~3.49 mm safety margin yet stay well inside 5 mm.
          * Feed selection is separate (a per-job operator choice, not machine
            geometry): call set_feed() after this, or the spindle axis's
            default syncRatio (360/100, a rotary-axis default) is what feeds.
        """
        for index, counts_per_mm in (
            (self.Z_SCALE_INDEX, z_counts_per_mm),
            (self.X_SCALE_INDEX, x_counts_per_mm),
        ):
            inp = self._input(index)
            inp.ratioNum = 1
            inp.ratioDen = counts_per_mm
            inp.stepsPerMM = counts_per_mm

        spindle = self._input(self.SPINDLE_SCALE_INDEX)
        spindle.spindleMode = True
        spindle.encoder_ppr = spindle_ppr
        spindle.gear_ratio_num = 1
        spindle.gear_ratio_den = 1

        servo = self.board.servo
        servo.elsMode = True            # linear leadscrew, not rotary indexing
        servo.leadScrewPitchIn = True   # 8 TPI leadscrew: pitch entered in inches
        servo.leadScrewPitch = 0.125
        servo.leadScrewPitchSteps = leadscrew_steps
        # elsMode isn't bound to configure_lead_screw_ratio (only the pitch
        # properties are), so run the production handler explicitly in case
        # every pitch property above was already at its target value.
        servo.configure_lead_screw_ratio(servo, None)

        servo_ratio = Fraction(servo.ratioNum, servo.ratioDen)
        # 0.125 in = 127/40 mm, so mm/step = (127/40) / leadscrew_steps. Kept
        # as an assertion (not assumed) — it's the guard that the Fraction
        # chain stayed exact for WHATEVER leadscrew_steps was passed in.
        expected_ratio = Fraction(127, 40 * leadscrew_steps)
        assert servo_ratio == expected_ratio, (
            f"leadscrew commissioning is not exact: got {servo_ratio}, "
            f"want {expected_ratio} ({float(expected_ratio)} mm/step)"
        )

        # The connect-time sync-ratio push ran against the hermetic defaults
        # (and before the spindle axis role existed, so through the wrong
        # branch). Re-run the same production handler so the firmware's
        # syncRatioNum/Den registers reflect the commissioned geometry.
        for axis in self.board.axes:
            axis._set_sync_ratio()
        self.pump()

    def set_feed(self, pitch_mm):
        """Select the ELS feed / thread pitch (mm of carriage travel per
        spindle revolution) through the production path — mirrors
        ElsBar.update_feeds_ratio: writes the signed spindle-axis
        syncRatioNum/Den (sign from the operator's els_forward), which the
        property binding pushes to the firmware sync registers.

        Call AFTER configure() (needs the spindle axis role assigned and
        els_forward set); a later els_forward change does NOT re-sign the
        feed here (in production ElsBar re-applies it) — re-call set_feed.

        Pass an exact Fraction matching a feeds.py table entry — the suite's
        default is 16 TPI (Thread IN "16", Fraction(254, 160) = 1.5875 mm/rev).
        Wall-clock note: carriage feed = pitch × rpm/60, so at EMU_RPM=30 that
        pitch feeds ~0.79 mm/s — pick production values coarse enough that cut
        spans complete well inside test timeouts."""
        pitch = Fraction(pitch_mm)
        spindle_axis = self.board.get_spindle_axis()
        if spindle_axis is None:
            raise RuntimeError(
                "no spindle axis assigned — call configure() before set_feed()"
            )
        direction = self.els.direction_sign(self.controller.els_forward)
        spindle_axis.syncRatioNum = pitch.numerator * direction
        spindle_axis.syncRatioDen = pitch.denominator
        self.pump()

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
        # callback with no stored handle needed; Board schedules update+blinker,
        # and each InputDispatcher schedules a 25 Hz _speed_task.
        if self.board is not None:
            try:
                Clock.unschedule(self.board.update)
                Clock.unschedule(self.board.blinker)
                for inp in self.board.inputs:
                    Clock.unschedule(inp._speed_task)
            except Exception:
                pass
        try:
            from reflex.fsms.fsm_event_bus import fsm_event_bus
            fsm_event_bus._subs.clear()
        except Exception:
            pass
        # Restore the App.get_running_app we monkeypatched in connect(), so a
        # later (non-system) test doesn't inherit a stale _FakeApp.
        if self._orig_get_running_app is not None:
            try:
                import kivy.app
                kivy.app.App.get_running_app = self._orig_get_running_app
            except Exception:
                pass
            self._orig_get_running_app = None
        if self.board is not None:
            try:
                self.board.connection_manager.disconnect()
            except Exception:
                pass
