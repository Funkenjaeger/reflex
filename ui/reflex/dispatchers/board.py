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
    device = ObjectProperty(None, allownone=True)
    servo = ObjectProperty(None, allownone=True)
    inputs = ListProperty()
    axes = ListProperty()

    def __init__(self, formats, offset_provider, **kv):
        super().__init__(**kv)
        self.formats = formats
        self.offset_provider = offset_provider
        self.fast_data_values = dict()

        serial_port = config.getdefault("device", "serial_port", "/dev/serial0")
        baudrate = int(config.getdefault("device", "baudrate", 115200))
        address = int(config.getdefault("device", "address", 17))

        self.connection_manager = ConnectionManager(
            serial_device=serial_port,
            baudrate=baudrate,
            address=address,
        )
        self.device = self.connection_manager['Global']
        self.connection_manager.connect()

        self.servo = ServoDispatcher(board=self, formats=formats, id_override="0")
        for i in range(4):
            self.inputs.append(InputDispatcher(
                board=self, inputIndex=i, id_override=f"{i}",
            ))

        self._create_axes()

        self.task_update = Clock.schedule_interval(self.update, 1.0 / 30)
        Clock.schedule_interval(self.blinker, 1.0 / 4)

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
        if self.connection_manager.device is None:
            self.connection_manager.connect()

        if self.connection_manager.device is None:
            self.connected = False
            self.task_update.timeout = 2.0
            self.update_tick = (self.update_tick + 1) % 100
            return

        try:
            self.fast_data_values = self.device['fastData'].refresh()
        except Exception as e:
            self.connection_manager._log_error_once(str(e))
            self.connection_manager.disconnect()
            self.connected = False
            self.task_update.timeout = 1.0
            self.update_tick = (self.update_tick + 1) % 100
            return

        was_disconnected = not self.connected
        self.connection_manager.connected = True
        self.connected = True

        if was_disconnected:
            self.task_update.timeout = 1.0 / 30
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
            version = int(self.device['elsStop'].refresh()['protocolVersion'])
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
        from reflex.app import MainApp
        app = MainApp.get_running_app()
        if app is not None and app.replay_mode:
            # The COM led's blink phase is part of the captured frame.
            return
        self.blink = not self.blink
