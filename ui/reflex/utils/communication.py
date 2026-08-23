import copy
import inspect
import time
from typing import Optional

import minimalmodbus
from kivy.logger import Logger
log = Logger.getChild(__name__)

_ERROR_REPEAT_INTERVAL_S = 10.0        # first repeat of a persisting error
_ERROR_REPEAT_INTERVAL_MAX_S = 600.0   # backoff cap: one line per 10 min while stuck


class ConnectionManager:
    def __init__(
        self, serial_device="/dev/ttyUSB0", baudrate=115200, address=17, debug=False
    ):
        self.serial_device = serial_device
        self.baudrate = baudrate
        self.address = address
        self.debug = debug
        self.device: minimalmodbus.Instrument | None = None
        self._connected = False

        # Monotonic count of reads that FAILED (checksum, timeout, short
        # frame). The read helpers below return 0 on failure -- they always
        # have, and every caller is written against that -- so the zero itself
        # is indistinguishable from a real zero at the call site. This counter
        # is what makes it distinguishable: snapshot it before a group of
        # related reads and compare after (see `reads_failed_since`).
        #
        # WHY A COUNTER AND NOT AN EXCEPTION. Raising from read_* would be the
        # tidier design and is a much larger change: every field access in the
        # app goes through __getitem__, and turning each into a throw site
        # would need error handling at hundreds of call sites that today
        # legitimately do not care. A counter lets the handful of readers where
        # a wrong value is a SAFETY matter -- the sequence-edge pollers -- opt
        # into strictness without destabilising the rest.
        self.read_failures: int = 0

        self._last_error_message: str | None = None
        self._last_error_time: float = 0.0
        self._error_repeat_interval: float = _ERROR_REPEAT_INTERVAL_S
        self._error_suppressed: int = 0

        self.definitions = []
        self.structures = dict()
        self._load_structures()

    @property
    def connected(self) -> bool:
        return self._connected

    def reads_failed_since(self, baseline: int) -> bool:
        """Did any read fail since `read_failures` was sampled as `baseline`?

        For readers that must not act on a value they cannot trust. The
        canonical use is a sequence-edge poller: snapshot before the reads,
        check after, and if this is True discard the whole poll rather than
        interpret it -- the next poll re-reads everything anyway, at the cost
        of one tick.
        """
        return self.read_failures != baseline

    @connected.setter
    def connected(self, value: bool):
        if value == self._connected:
            return
        self._connected = value
        if value:
            self._last_error_message = None
            self._error_repeat_interval = _ERROR_REPEAT_INTERVAL_S
            self._error_suppressed = 0
            log.info(
                f"Communication established with {self.serial_device} "
                f"(baudrate={self.baudrate}, address={self.address})"
            )
        else:
            log.warning(f"Communication lost with {self.serial_device}")

    def _log_error_once(self, message: str):
        """Log an error on first occurrence; while the SAME error persists,
        re-log on a DOUBLING interval (10 s, 20, 40, ... capped at 10 min),
        each time saying how many identical repeats were swallowed since the
        last line. A flat 10 s cadence turned the 42-minute post-flash limbo
        of 2026-08-17 into 244 identical checksum-error lines — each also a
        Sentry envelope — which buried the session log. A persistent fault
        now costs ~8 lines the first hour and 6 per hour after, and the
        suppressed-count keeps the line honest about what it stands for.
        Any success (the connected setter) or a different message resets the
        backoff, so a NEW problem always logs immediately."""
        now = time.monotonic()
        if message != self._last_error_message:
            self._last_error_message = message
            self._last_error_time = now
            self._error_repeat_interval = _ERROR_REPEAT_INTERVAL_S
            self._error_suppressed = 0
            log.error(message)
        elif now - self._last_error_time >= self._error_repeat_interval:
            self._last_error_time = now
            suppressed = self._error_suppressed
            self._error_suppressed = 0
            self._error_repeat_interval = min(
                self._error_repeat_interval * 2, _ERROR_REPEAT_INTERVAL_MAX_S)
            log.error(f"{message} (still failing; {suppressed} identical "
                      f"repeats suppressed)")
        else:
            self._error_suppressed += 1

    def connect(self):
        if self.connected:
            return
        if self.device is not None:
            return
        try:
            self.device = minimalmodbus.Instrument(
                port=self.serial_device, slaveaddress=self.address, debug=self.debug
            )
            self.device.serial.timeout = 0.1
            self.device.serial.write_timeout = 0.1
            self.device.serial.baudrate = self.baudrate
        except Exception as e:
            self.device = None
            self.connected = False
            self._log_error_once(f"Failed to connect to {self.serial_device}: {str(e)}")

    def disconnect(self):
        """Close the serial port and reset state so connect() can retry."""
        if self.device is not None:
            try:
                self.device.serial.close()
            except Exception:
                pass
            self.device = None
        self.connected = False

    def _load_structures(self):
        from reflex.utils import devices
        from reflex.utils.base_device import BaseDevice, TypeDefinition

        # First we load and add to our definitions all the base types
        base_types = [
            item
            for item in inspect.getmembers(devices)
            if isinstance(item[1], TypeDefinition)
        ]
        self.definitions += [item[1] for item in base_types]

        # Then we build the complex types
        device_classes = [
            item
            for item in inspect.getmembers(devices, inspect.isclass)
            if issubclass(item[1], BaseDevice) and item[0] != "BaseDevice"
        ]

        unloaded_list = copy.deepcopy(device_classes)
        iterations_limit = 3
        while len(unloaded_list) > 0 and iterations_limit > 0:
            failure_list = []
            for my_class in unloaded_list:
                # my_class[1]: BaseDevice
                try:
                    definition = my_class[1].register_type(self.definitions)
                    self.definitions.append(definition)
                    if my_class[1].root_structure is True:
                        self.structures[my_class[0]] = my_class[1](
                            connection_manager=self,
                            base_address=0
                        )

                    log.info(f"Loaded definition for {my_class[0]}")
                    iterations_limit = 3
                except IndexError:
                    failure_list.append(my_class)
            unloaded_list = copy.deepcopy(failure_list)
            iterations_limit -= 1

    def __getitem__(self, key):
        return self.structures[key]


def read_float(dm: ConnectionManager, address) -> float:
    try:
        value = dm.device.read_float(
            address, byteorder=minimalmodbus.BYTEORDER_LITTLE_SWAP
        )
        dm.connected = True
        return value
    except Exception as e:
        dm.connected = False
        dm.read_failures += 1
        dm._log_error_once(str(e))
        return 0


def write_float(dm, address, value, variable_name: Optional[str] = ""):
    try:
        dm.device.write_float(
            address, byteorder=minimalmodbus.BYTEORDER_LITTLE_SWAP, value=value
        )
        dm.connected = True
        log.debug(f"Write {variable_name}: float {value} to address {address}")
    except Exception as e:
        dm.connected = False
        dm._log_error_once(str(e))


def read_long(dm, address) -> int:
    try:
        value = dm.device.read_long(
            address, signed=True, byteorder=minimalmodbus.BYTEORDER_LITTLE_SWAP
        )
        dm.connected = True
        return value
    except Exception as e:
        dm.connected = False
        dm.read_failures += 1
        dm._log_error_once(str(e))
        return 0


def write_long(dm, address, value, variable_name: Optional[str] = ""):
    try:
        dm.device.write_long(
            address,
            signed=True,
            byteorder=minimalmodbus.BYTEORDER_LITTLE_SWAP,
            value=int(value),
        )
        dm.connected = True
        log.debug(f"Write {variable_name}: long {value} to address {address}")
    except Exception as e:
        dm.connected = False
        dm._log_error_once(str(e))


def read_unsigned(dm, address):
    try:
        value = dm.device.read_register(address, signed=False)
        dm.connected = True
        return value
    except Exception as e:
        dm.connected = False
        dm.read_failures += 1
        dm._log_error_once(str(e))
        return 0


def write_unsigned(dm, address, value, variable_name: Optional[str] = ""):
    try:
        dm.device.write_register(address, signed=False, value=int(value))
        dm.connected = True
        log.debug(f"Write {variable_name}: unsigned {value} to address {address}")
    except Exception as e:
        dm.connected = False
        dm._log_error_once(str(e))


def read_signed(dm, address):
    try:
        value = dm.device.read_register(address, signed=True)
        dm.connected = True
        return value
    except Exception as e:
        dm.connected = False
        dm._log_error_once(str(e))
        return 0


def write_signed(dm, address, value, variable_name: Optional[str] = ""):
    try:
        dm.device.write_register(address, signed=True, value=int(value))
        dm.connected = True
        log.debug(f"Write {variable_name}: signed {value} to address {address}")
    except Exception as e:
        dm.connected = False
        dm._log_error_once(str(e))


if __name__ == "__main__":
    connection_manager = ConnectionManager()
    connection_manager.connect()
    device = connection_manager['Global']

    while True:
        time.sleep(0.5)
        values = device['servo'].refresh()
        print(values)
