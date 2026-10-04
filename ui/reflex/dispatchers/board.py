import os
from pathlib import Path

from kivy.clock import Clock
from kivy.event import EventDispatcher
from kivy.properties import (NumericProperty, BooleanProperty, ObjectProperty,
                             ListProperty, StringProperty)

from reflex.components.appsettings import config
from reflex.dispatchers.axis import AxisDispatcher
from reflex.dispatchers.axis_transform import AxisTransform
from reflex.dispatchers.input import InputDispatcher
from reflex.dispatchers.saving_dispatcher import read_settings
from reflex.dispatchers.servo import ServoDispatcher
from reflex.utils.communication import ConnectionManager
from reflex.utils.paths import config_dir
from reflex.utils.port_owner import SCAN_PERIOD_S, PortOwner, Snapshot

from kivy.logger import Logger
log = Logger.getChild(__name__)


class Board(EventDispatcher):
    connected = BooleanProperty(False)
    # Firmware register-layout version, read once per connection. 0 means the
    # firmware predates the register (an unwritten appended register reads 0) —
    # i.e. "too old", not "unknown but fine".
    protocol_version = NumericProperty(0)
    protocol_mismatch = BooleanProperty(False)
    protocol_message = StringProperty("")
    update_tick = NumericProperty(0)
    blink = BooleanProperty(False)
    # True while something else owns the serial port (the firmware flasher).
    # A Kivy property so the UI can say "the link is down ON PURPOSE" rather
    # than showing the same disconnected state a broken cable produces.
    link_paused = BooleanProperty(False)
    device = ObjectProperty(None, allownone=True)
    servo = ObjectProperty(None, allownone=True)
    inputs = ListProperty()
    axes = ListProperty()

    def __init__(self, formats, offset_provider, **kv):
        super().__init__(**kv)
        self.formats = formats
        self.offset_provider = offset_provider
        self.fast_data_values = dict()
        # This tick's elsStop registers. See PortOwner._decode_hot for what an
        # EMPTY dict means and why it is the only honest way to say it.
        self.els_stop_values = dict()

        serial_port = config.getdefault("device", "serial_port", "/dev/serial0")
        baudrate = int(config.getdefault("device", "baudrate", 115200))
        address = int(config.getdefault("device", "address", 17))

        self.connection_manager = ConnectionManager(
            serial_device=serial_port,
            baudrate=baudrate,
            address=address,
        )
        self.device = self.connection_manager['Global']
        self._port()
        self.connection_manager.connect()

        self.servo = ServoDispatcher(board=self, formats=formats, id_override="0")
        for i in range(4):
            self.inputs.append(InputDispatcher(
                board=self, inputIndex=i, id_override=f"{i}",
            ))

        self._create_axes()

        self.task_update = Clock.schedule_interval(self.update, SCAN_PERIOD_S)
        Clock.schedule_interval(self.blinker, 1.0 / 4)

    def _port(self) -> PortOwner:
        """The owner of this board's serial port: every Modbus exchange, the
        tick's and every BaseDevice field access's, goes through it. Looked up
        rather than stored, so a Board assembled without __init__ (the tests'
        BoardUnderTest) reaches the same owner as its devices do."""
        owner = PortOwner.of(self.connection_manager)
        if owner.scan_device is None:
            owner.scan_device = self.device
        return owner

    # ------------------------------------------------------------------
    # Handing the serial port to something else
    # ------------------------------------------------------------------
    # fw/scripts/modbus-flash.py opens the port itself, so an in-app firmware
    # update needs this UI to genuinely let go of it -- not merely stop asking
    # for data. Cancelling the tick alone leaves minimalmodbus holding an open
    # file descriptor, and the flasher's own open would fail or, worse,
    # succeed on a second handle and interleave frames with a poll that has
    # not actually stopped.
    #
    # A PAIR OF METHODS RATHER THAN THE SCREEN REACHING IN, because the resume
    # half is not symmetric with the pause half: the tick scan re-connects on
    # its own when `connection_manager.device` is None, but only if its Clock
    # event is running, and the event's timeout has to go back to the fast
    # cadence rather than whatever backoff it was left on.
    #
    # The port itself is opened and closed by the PortOwner (pause/resume),
    # which also bumps the link generation so that no tick read from before
    # the handover can be applied after it.

    def pause_polling(self):
        """Stop polling and close the serial port. Idempotent."""
        if self.link_paused:
            return
        self.link_paused = True
        self.task_update.cancel()
        self._port().pause()
        self.connected = False
        self.els_stop_values = {}
        self.fast_data_values = {}
        log.warning("Board polling paused and the serial port released")

    def resume_polling(self):
        """Re-open the port and resume polling. Idempotent."""
        if not self.link_paused:
            return
        self.link_paused = False
        self._port().resume()
        # A fresh event: a cancelled ClockEvent is not re-armed by assigning to
        # its timeout, and `update()` reads `self.task_update.timeout` on every
        # tick.
        self.task_update = Clock.schedule_interval(self.update, SCAN_PERIOD_S)
        log.info("Board polling resumed")

    def _settings_folder(self) -> Path:
        return config_dir()

    def _create_axes(self):
        """Create AxisDispatchers, migrating from scale configs if needed."""
        settings_folder = self._settings_folder()
        axis_files = sorted(settings_folder.glob("Axis-*.yaml")) if settings_folder.exists() else []

        max_id = -1
        if axis_files:
            # Load existing axes from YAML files
            for f in axis_files:
                axis_id = f.stem.replace("Axis-", "")
                try:
                    max_id = max(max_id, int(axis_id))
                except ValueError:
                    pass
                ax = AxisDispatcher(
                    board=self, formats=self.formats, servo=self.servo,
                    offset_provider=self.offset_provider,
                    inputs=list(self.inputs),
                    id_override=axis_id,
                )
                self.axes.append(ax)
        else:
            # Migration: create 4 identity axes from existing CoordBar configs
            log.info("No Axis YAML files found — migrating from input configs")
            for i in range(4):
                # Read the CoordBar YAML to extract migration data
                coordbar_file = settings_folder / f"CoordBar-{i}.yaml"
                migration_data = read_settings(coordbar_file) or {}

                ax = AxisDispatcher(
                    board=self, formats=self.formats, servo=self.servo,
                    offset_provider=self.offset_provider,
                    inputs=list(self.inputs),
                    transform=AxisTransform.identity(i),
                    id_override=f"{i}",
                    axis_name=migration_data.get("axisName", "?"),
                    axis_index=i,
                    syncRatioNum=migration_data.get("syncRatioNum", 360),
                    syncRatioDen=migration_data.get("syncRatioDen", 100),
                    spindleMode=migration_data.get("spindleMode", False),
                )
                # Migrate offsets if present
                migrated_offsets = migration_data.get("offsets")
                if migrated_offsets:
                    ax.offsets = migrated_offsets
                ax._save_transform_config()
                self.axes.append(ax)
            max_id = 3

        self._next_axis_id = max_id + 1

    def add_axis(self, transform: AxisTransform | None = None, axis_name: str = "?") -> AxisDispatcher:
        """Add a new axis with the given transform (defaults to identity on first unused input)."""
        axis_id = self._next_axis_id
        self._next_axis_id += 1

        if transform is None:
            used_inputs = set()
            for ax in self.axes:
                used_inputs |= ax.transform.input_indices
            available = [i for i in range(len(self.inputs)) if i not in used_inputs]
            input_idx = available[0] if available else 0
            transform = AxisTransform.identity(input_idx)

        ax = AxisDispatcher(
            board=self, formats=self.formats, servo=self.servo,
            offset_provider=self.offset_provider,
            inputs=list(self.inputs),
            transform=transform,
            id_override=f"{axis_id}",
            axis_name=axis_name,
            axis_index=len(self.axes),
        )
        ax._save_transform_config()
        self.axes.append(ax)
        return ax

    def remove_axis(self, axis: "AxisDispatcher"):
        """Remove the given axis and delete its config file."""
        try:
            self.axes.remove(axis)
        except ValueError:
            log.warning(f"Axis '{axis.axis_name}' not found in axes list")
            return
        config_file = axis.filename
        if config_file.exists():
            os.remove(config_file)
            log.info(f"Removed axis config: {config_file}")

    def get_spindle_axis(self):
        """Find the axis with spindleMode=True."""
        filtered = [a for a in self.axes if a.spindleMode is True]
        if len(filtered) != 1:
            return None
        return filtered[0]

    def update(self, *args):
        """One board tick: the port owner's cycle, then its snapshot applied.

        Both on the main thread. The Kivy Clock event is the pacer -- its
        timeout carries the failure backoff -- so every call scans
        (force_scan); see the PortOwner module docstring for the scan itself
        and its failure policy.
        """
        snapshot = self._port().run_cycle(force_scan=True)
        if snapshot is not None:
            self._apply_snapshot(snapshot)

    def _apply_snapshot(self, snapshot: Snapshot):
        """Publish one tick scan to the board's properties. Main thread only.

        A FAILED TICK READ IS A FAILED fastData READ, for every caller: the
        tick's one request carries both blocks, so its failure takes the path
        a failed fastData read always took -- link down, retry backoff,
        fast_data_values left as it was, els_stop_values cleared (without the
        clear, the pollers would read a snapshot from before the link dropped,
        on a tick where update_tick still fires). No port at all is the same,
        with the longer backoff.

        A snapshot from an older link generation (taken before a pause) is
        dropped: pause_polling cleared the dicts, and they stay clear.
        """
        if snapshot.generation != self._port().generation:
            return

        if not snapshot.ok_fast:
            self.connected = False
            self.els_stop_values = {}
            self.task_update.timeout = snapshot.retry_after
            self.update_tick = (self.update_tick + 1) % 100
            return

        self.fast_data_values = snapshot.fast_data
        # Taken BEFORE update_tick is bumped, because the tick-driven pollers
        # that consume it are bound to update_tick -- so by the time any of them
        # runs, the snapshot it reads is this tick's.
        self.els_stop_values = snapshot.els_stop

        was_disconnected = not self.connected
        self.connected = True

        if was_disconnected:
            self.task_update.timeout = SCAN_PERIOD_S
            self._check_protocol_version()

        self.update_tick = (self.update_tick + 1) % 100

    def _check_protocol_version(self):
        """Verify the firmware's register-layout version on each new connection.

        The whole shared struct is memory-mapped onto Modbus holding registers
        with no translation layer, so firmware whose `elsStop_t` differs from
        `reflex/utils/devices.py` reinterprets every register after the point of
        divergence. Without this check that surfaces as plausible-looking
        garbage — a wrong backlash here, an impossible Z there — and gets
        diagnosed as a hardware fault. One register read at connect turns it
        into a named version problem instead.

        Deliberately NON-FATAL: it sets a flag and logs. The UI is useful
        against a mismatched firmware for everything that does not touch the
        moved registers, and refusing to start would make recovery (flashing
        from the same UI) harder than the fault warrants.
        """
        from reflex.utils.devices import ELS_PROTOCOL_VERSION
        try:
            # From THIS tick's snapshot, applied moments ago. It used
            # to refresh the block again here, which read the same 122
            # registers twice on the tick a connection comes up. An empty
            # snapshot (the refresh failed) raises KeyError and lands in the
            # same branch a failed read always did.
            version = int(self.els_stop_values['protocolVersion'])
        except Exception as e:
            # A read failure here is not a version verdict — leave the previous
            # state alone rather than reporting a mismatch we did not observe.
            log.warning(f"Could not read firmware protocol version: {e}")
            return

        self.protocol_version = version
        self.protocol_mismatch = (version != ELS_PROTOCOL_VERSION)

        if not self.protocol_mismatch:
            self.protocol_message = ""
            log.info(f"Firmware register protocol version {version} (expected "
                     f"{ELS_PROTOCOL_VERSION})")
            return

        if version == 0:
            self.protocol_message = (
                "Firmware predates the register protocol version field. "
                f"This UI expects version {ELS_PROTOCOL_VERSION}. "
                "Update the firmware."
            )
        elif version < ELS_PROTOCOL_VERSION:
            self.protocol_message = (
                f"Firmware register protocol is version {version}; this UI "
                f"expects {ELS_PROTOCOL_VERSION}. Update the firmware."
            )
        else:
            self.protocol_message = (
                f"Firmware register protocol is version {version}; this UI "
                f"only understands {ELS_PROTOCOL_VERSION}. Update the UI."
            )
        log.error(self.protocol_message)

    def blinker(self, *args):
        self.blink = not self.blink
