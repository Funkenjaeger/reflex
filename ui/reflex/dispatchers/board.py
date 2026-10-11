import os
import threading
import time
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

# No snapshot for this long, while not paused, means the owner thread has
# stopped producing: the UI would otherwise sit on stale values believing
# the link is up. Comfortably longer than the 1.0 s failed-read backoff, so
# a link that is merely down does not trip it.
SNAPSHOT_WATCHDOG_S = 1.5

# The Kivy properties below. A write to one of these from any thread but the
# main one is the classic once-a-week graphics crash: Kivy dispatches to bound
# widgets inline, so the setter runs canvas work on whatever thread set it.
# Nothing in the port owner touches an EventDispatcher by design -- this is
# what makes a mistake in that design LOUD instead of intermittent.
_GUARDED_PROPERTIES = frozenset((
    "connected", "protocol_version", "protocol_mismatch", "protocol_message",
    "update_tick", "blink", "link_paused", "device", "servo", "inputs", "axes",
))

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

    # PLAIN CLASS ATTRIBUTES, NOT Kivy properties, and declared here rather
    # than only in __init__ because a Board assembled WITHOUT __init__ (the
    # tests' BoardUnderTest, which wires a fake ConnectionManager by hand) must
    # still read them. __init__ re-binds them per instance; these are what an
    # instance that skipped it sees. None of them is bound in kv, so a class
    # attribute is the whole contract.
    _io_threaded = False
    _pending_snapshot = None
    _watchdog_event = None
    _last_snapshot_at = None
    _watchdog_tripped = False
    # The acquisition time of the applied snapshot: when the last reply byte
    # arrived, NOT when the main thread got round to it. Every rate the host
    # computes should be measured against this.
    t_acq = None
    snapshots_applied = 0
    snapshots_dropped = 0

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

        # Per-instance copies of the I/O-thread state declared on the class.
        self._io_threaded = False
        self._pending_snapshot = None
        self._watchdog_event = None
        self._last_snapshot_at = None
        self._watchdog_tripped = False
        self.t_acq = None
        self.snapshots_applied = 0
        self.snapshots_dropped = 0

        self.task_update = Clock.schedule_interval(self.update, SCAN_PERIOD_S)
        Clock.schedule_interval(self.blinker, 1.0 / 4)

    def __setattr__(self, name, value):
        """Say so, loudly and once, if a Kivy property is set off the main thread.

        NOT an exception: this runs on a lathe, and a raise here would take the
        UI down over a fault whose whole point is that it is survivable-looking.
        The log line plus Sentry is what turns a once-a-week mystery crash into
        a named defect. Checked only for the declared properties, so the cost on
        everything else is one frozenset lookup.
        """
        if name in _GUARDED_PROPERTIES and \
                threading.current_thread() is not threading.main_thread():
            self._report_off_thread_write(name)
        super().__setattr__(name, value)

    # Names already reported, so a property written every tick from the wrong
    # thread produces one line rather than thirty a second.
    _off_thread_reported: frozenset = frozenset()

    def _report_off_thread_write(self, name):
        if name in self._off_thread_reported:
            return
        self._off_thread_reported = self._off_thread_reported | {name}
        thread = threading.current_thread().name
        message = (f"Board.{name} was set from thread {thread!r}, not the main "
                   f"thread; Kivy dispatches property changes inline, so this "
                   f"can run canvas work off the main thread")
        log.error(message)
        try:
            import sentry_sdk
        except Exception:                                # noqa: BLE001
            return
        try:
            sentry_sdk.capture_message(message, level="error")
        except Exception:                                # noqa: BLE001
            pass

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
        """Stop polling and close the serial port. Idempotent.

        With the I/O thread running the close happens on that thread, between
        two exchanges, and pause() waits for it. A False answer means the port
        may STILL BE OPEN: it is logged at error, and the caller (the firmware
        update screen) must treat the handover as failed rather than flash over
        a port this process still holds.
        """
        if self.link_paused:
            return
        self.link_paused = True
        self.task_update.cancel()
        released = self._port().pause()
        self.connected = False
        self.els_stop_values = {}
        self.fast_data_values = {}
        self._pending_snapshot = None
        if released:
            log.warning("Board polling paused and the serial port released")
        else:
            log.error("Board polling paused but the port owner did not confirm "
                      "the port was closed; it may still be open")
        return released

    def resume_polling(self):
        """Re-open the port and resume polling. Idempotent."""
        if not self.link_paused:
            return
        self.link_paused = False
        self._port().resume()
        self._last_snapshot_at = None
        self._watchdog_tripped = False
        if self._io_threaded:
            # The owner thread paces itself; there is no Clock event to re-arm.
            log.info("Board polling resumed (port owner thread)")
            return
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
        owner = self._port()
        if snapshot.generation != owner.generation:
            return

        # ACQUIRED BEFORE A WRITE THAT HAS SINCE LANDED. Applying it would show
        # the machine as it was before the UI changed it -- and the ELS stop
        # detector, reading the snapshot, takes the pre-arming registers of a
        # cut that has just started as the stop having fired. One scan at most
        # is lost per write, and the next one is already on its way.
        if snapshot.write_epoch < owner.write_epoch:
            self.snapshots_dropped += 1
            return

        self._last_snapshot_at = time.monotonic()
        self.snapshots_applied += 1
        # None on a scan that read nothing; readers keep the last good one.
        if snapshot.t_acq is not None:
            self.t_acq = snapshot.t_acq

        if not snapshot.ok_fast:
            self.connected = False
            self.els_stop_values = {}
            self._set_backoff(snapshot.retry_after)
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
            self._set_backoff(SCAN_PERIOD_S)
            self._check_protocol_version()

        self.update_tick = (self.update_tick + 1) % 100

    def _set_backoff(self, seconds):
        """Carry the scan's retry cadence on whatever is pacing it.

        The Clock event is the pacer only while update() is on the Clock; with
        the I/O thread running the owner paces itself from the same constants
        (PortOwner.next_scan_at), so there is nothing to set here.
        """
        if self._io_threaded:
            return
        self.task_update.timeout = seconds

    # ------------------------------------------------------------------
    # The I/O thread
    # ------------------------------------------------------------------
    # OPT-IN, taken only by the running app (app.py). Every test and the system
    # harness keep driving update() directly, which is still a whole cycle on
    # the calling thread -- the determinism the suite depends on.

    def start_io_thread(self):
        """Move the scan off the Kivy thread. Idempotent.

        After this the Clock no longer calls update(); the owner thread runs
        the cycle and each Snapshot comes back through Clock.schedule_once.
        """
        if self._io_threaded:
            return
        self._io_threaded = True
        self.task_update.cancel()
        self._last_snapshot_at = None
        self._watchdog_tripped = False
        self._port().start(self._publish_from_owner)
        self._watchdog_event = Clock.schedule_interval(self._watchdog, 0.5)
        log.info("Modbus I/O moved to the port owner thread")

    def stop_io_thread(self):
        """Stop the owner thread and put the scan back on the Clock. Idempotent."""
        if not self._io_threaded:
            return
        self._port().stop()
        if self._watchdog_event is not None:
            self._watchdog_event.cancel()
            self._watchdog_event = None
        self._io_threaded = False
        self._pending_snapshot = None
        if not self.link_paused:
            self.task_update = Clock.schedule_interval(self.update, SCAN_PERIOD_S)
        log.info("Modbus I/O returned to the Kivy thread")

    def _publish_from_owner(self, snapshot):
        """ON THE OWNER THREAD. Hand one scan to the main thread.

        ONLY THE NEWEST SNAPSHOT IS WORTH APPLYING. If the main thread is busy
        with a long frame, an older scan describes a machine that has already
        moved on, so it is replaced rather than queued and the loss is counted.
        The seq-edge pollers survive the drop because the firmware's counters
        are monotonic -- they compare against what they last saw, not against
        the previous tick.

        Nothing here touches a Kivy property: the rebind below is a plain
        attribute, and schedule_once is what @mainthread itself uses.
        """
        if self._pending_snapshot is not None:
            self.snapshots_dropped += 1
        self._pending_snapshot = snapshot
        Clock.schedule_once(self._apply_pending, 0)

    def _apply_pending(self, _dt=0):
        """On the main thread: apply whatever the owner left, if anything."""
        snapshot, self._pending_snapshot = self._pending_snapshot, None
        if snapshot is None:
            # A second schedule_once for a snapshot the first call already
            # applied. Not a drop; the drop was counted when it was replaced.
            return
        self._apply_snapshot(snapshot)

    def _watchdog(self, _dt):
        """Say so, once, when the owner thread stops producing snapshots.

        A silent link looks exactly like a working one to a UI reading the last
        values, which is the failure this is here to make loud. Paused does not
        count: the port is away on purpose.
        """
        if not self._io_threaded or self.link_paused:
            return
        if not self._port().running:
            if not self._watchdog_tripped:
                self._watchdog_tripped = True
                log.error("the Modbus owner thread is not running; no scans "
                          "are being taken")
            return
        last = self._last_snapshot_at
        if last is None:
            return
        silent_for = time.monotonic() - last
        if silent_for > SNAPSHOT_WATCHDOG_S:
            if not self._watchdog_tripped:
                self._watchdog_tripped = True
                log.error(f"no Modbus snapshot for {silent_for:.1f} s while the "
                          f"link is not paused")
        elif self._watchdog_tripped:
            self._watchdog_tripped = False
            log.warning("Modbus snapshots resumed")

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
                "Firmware has no register protocol version. "
                f"This UI expects version {ELS_PROTOCOL_VERSION}. "
                "Update the firmware."
            )
        elif version < ELS_PROTOCOL_VERSION:
            self.protocol_message = (
                f"Firmware register protocol is version {version}. This UI "
                f"expects version {ELS_PROTOCOL_VERSION}. Update the firmware."
            )
        else:
            self.protocol_message = (
                f"Firmware register protocol is version {version}. This UI "
                f"knows only version {ELS_PROTOCOL_VERSION}. Update the UI."
            )
        log.error(self.protocol_message)

    def blinker(self, *args):
        self.blink = not self.blink
