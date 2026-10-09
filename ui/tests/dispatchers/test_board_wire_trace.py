"""The exact wire traffic of a scripted board session, pinned call for call.

WHAT THIS IS FOR. The path every register read and write takes is about to be
restructured (a single owner of the serial port, so the Modbus exchanges can
later leave the Kivy main thread). A restructuring that is meant to change
NOTHING on the wire needs a check that would see a change: an extra exchange,
a dropped write, two writes in a different order, a different backoff, a log
line that went missing. Unit tests of the new pieces cannot show that; only a
recording of the old behaviour, replayed against the new code, can.

HOW. The real Board, the real ConnectionManager and the real register layout,
with ONE substitution: minimalmodbus.Instrument is replaced by WireInstrument,
which records every call made on it (method, address, value, keyword
arguments) and answers from a register image. The Kivy Clock in board.py is
replaced by events that keep their timeout, so the retry cadence is part of
the record. After every scripted step the board's observable state is
recorded too, and so is every line the comms layer and the board log.

The scripted session covers each kind of exchange the app makes: the connect
at start-up and the connect tick's settings re-push, steady ticks, live
single-register / float / long / array reads, a whole-block refresh(), the
disengage write order (sync off, then enable, then feed), an array write, an
unacknowledged write, a failed tick read with its teardown and its 1.0 s
retry, a port that will not open with its 2.0 s retry, the reconnect, and the
flasher handoff (pause_polling closes the port; a live read while paused never
reaches the wire; resume_polling reopens it).

THE EXPECTED RECORD BELOW WAS CAPTURED FROM THE CODE BEFORE THE RESTRUCTURING
and is not to be regenerated to make a failure go away. A difference is a
change in what the lathe sees; explain it first.
"""
import logging
from types import SimpleNamespace
from unittest.mock import patch

import minimalmodbus
import pytest
from kivy.logger import Logger

from tests.dispatchers.conftest import MockFormats, MockOffsetProvider


class WireFailure(minimalmodbus.NoResponseError):
    """What minimalmodbus raises when the controller does not answer."""


class Wire:
    """The register image and the call record shared by every instrument the
    ConnectionManager opens over the session."""

    def __init__(self, image):
        self.image = dict(image)
        self.trace = []
        self.fail_open = False
        self.fail_reads = False
        self.fail_writes = False

    def instrument_class(self):
        wire = self

        class WireInstrument:
            def __init__(self, port, slaveaddress, debug=False):
                if wire.fail_open:
                    wire.trace.append(("open-failed", port))
                    raise OSError(f"could not open port {port}")
                wire.trace.append(("open", port, slaveaddress))
                self.serial = SimpleNamespace(
                    timeout=None, write_timeout=None, baudrate=None,
                    close=lambda: wire.trace.append(("close",)))

            def _read(self, name, address, kw):
                wire.trace.append((name, address, tuple(sorted(kw.items()))))
                if wire.fail_reads:
                    raise WireFailure("No communication with the instrument (no answer)")
                return wire.image.get(address, 0)

            def _write(self, name, address, kw):
                value = kw.pop("value")
                wire.trace.append((name, address, value, tuple(sorted(kw.items()))))
                if wire.fail_writes:
                    raise WireFailure("No communication with the instrument (no answer)")
                wire.image[address] = value

            def read_registers(self, registeraddress, number_of_registers):
                wire.trace.append(("read_registers", registeraddress, number_of_registers))
                if wire.fail_reads:
                    raise WireFailure("No communication with the instrument (no answer)")
                return [wire.image.get(a, 0)
                        for a in range(registeraddress, registeraddress + number_of_registers)]

            def read_register(self, address, **kw):
                return self._read("read_register", address, kw)

            def read_float(self, address, **kw):
                return float(self._read("read_float", address, kw))

            def read_long(self, address, **kw):
                return self._read("read_long", address, kw)

            def write_register(self, address, **kw):
                self._write("write_register", address, kw)

            def write_float(self, address, **kw):
                self._write("write_float", address, kw)

            def write_long(self, address, **kw):
                self._write("write_long", address, kw)

        return WireInstrument


class FakeClockEvent:
    def __init__(self, timeout):
        self.timeout = timeout
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class LogRecorder(logging.Handler):
    """Every line the comms layer and the board emit, in order."""

    SOURCES = ("reflex.utils.communication", "reflex.dispatchers.board")

    def __init__(self, sink):
        super().__init__(level=logging.DEBUG)
        self.sink = sink

    def emit(self, record):
        if record.name.endswith(self.SOURCES):
            self.sink.append(("log", record.levelname, record.getMessage()))


def _address(device, field):
    var = device._variable_index[field]
    return device.base_address + var.address


def run_session(tmp_path, monkeypatch):
    """The scripted session. Returns the combined record."""
    from reflex.dispatchers.board import Board
    from reflex.fsms.els_stop_registers import ElsStopRegisters
    from reflex.utils import communication
    from reflex.utils.communication import ConnectionManager
    from reflex.utils.devices import ELS_PROTOCOL_VERSION

    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / ".config" / "reflex"))

    layout = ConnectionManager(serial_device="/dev/null")['Global']
    image = {
        # A matching firmware, so the connect tick's version check passes.
        _address(layout['elsStop'], 'protocolVersion'): ELS_PROTOCOL_VERSION,
        # A feed the firmware is running at connect, so the disengage order
        # has a feed to stop (stop_feed writes nothing when servoMode is 0).
        _address(layout['fastData'], 'servoMode'): 1,
        _address(layout['servo'], 'maxSpeed'): 1234,
        _address(layout['servo'], 'stepsToGo'): -7,
    }
    wire = Wire(image)
    record = wire.trace

    handler = LogRecorder(record)
    saved_level = Logger.level
    Logger.addHandler(handler)
    Logger.setLevel(logging.DEBUG)
    try:
        with patch.object(communication.minimalmodbus, "Instrument",
                          wire.instrument_class()), \
             patch("reflex.dispatchers.board.Clock") as clock:
            clock.schedule_interval.side_effect = \
                lambda _cb, timeout: FakeClockEvent(timeout)

            board = Board(formats=MockFormats(), offset_provider=MockOffsetProvider())
            cm = board.connection_manager
            hal = ElsStopRegisters(board)

            def state(step):
                record.append((
                    "state", step,
                    board.connected, cm.connected, board.update_tick,
                    board.task_update.timeout, board.task_update.cancelled,
                    board.link_paused, cm.read_failures,
                    sorted(board.fast_data_values)[:3],
                    board.fast_data_values.get('servoMode'),
                    board.els_stop_values.get('protocolVersion'),
                    len(board.els_stop_values),
                ))

            state("constructed")
            for i in range(3):
                board.update()
                state(f"tick {i}")

            record.append(("live",
                           board.device['elsStop']['enable'],
                           board.device['servo']['maxSpeed'],
                           board.device['servo']['stepsToGo'],
                           board.device['elsStop']['calMeasured']))
            state("live reads")

            block = board.device['elsStop'].refresh()
            record.append(("refresh", block['protocolVersion'], len(block)))
            state("refresh")

            # The disengage order (els_fsm.on_enter_disabled).
            hal.stop_sync()
            hal.set_enable(False)
            board.servo.stop_feed()
            state("disengage")

            board.device['elsStop']['calMeasured'] = [365, 373, 366]
            state("array write")

            wire.fail_writes = True
            hal.set_active(True)
            wire.fail_writes = False
            state("unacknowledged write")

            board.update()
            state("tick after the unacknowledged write")

            wire.fail_reads = True
            board.update()
            state("failed tick")
            board.update()
            state("failed tick, port reopened")

            wire.fail_open = True
            board.update()
            state("no device")
            wire.fail_open = False
            wire.fail_reads = False

            board.update()
            state("reconnect tick")
            board.update()
            state("steady tick")

            board.pause_polling()
            state("paused")
            record.append(("live while paused", board.device['servo']['maxSpeed']))
            state("live read while paused")
            board.resume_polling()
            state("resumed")
            board.update()
            state("tick after resume")
            board.update()
            state("steady tick after resume")
    finally:
        Logger.removeHandler(handler)
        Logger.setLevel(saved_level)
    return record


def test_the_wire_trace_of_a_scripted_session_is_unchanged(tmp_path, monkeypatch):
    record = run_session(tmp_path, monkeypatch)
    assert record == EXPECTED, _first_difference(record, EXPECTED)


def _first_difference(got, want):
    for i, (g, w) in enumerate(zip(got, want)):
        if g != w:
            return f"entry {i}: got {g!r}, expected {w!r}"
    return f"lengths differ: got {len(got)}, expected {len(want)}"


# Captured from the code before the port owner existed (see the module
# docstring). Entries: wire calls, log lines, and a ('state', step, ...)
# row after each step: board.connected, cm.connected, update_tick, the
# tick event's timeout and cancelled flag, link_paused, read_failures,
# the first three fastData keys, fastData servoMode, the snapshot's
# protocolVersion and its field count.
EXPECTED = [('log', 'INFO', 'Loaded definition for ElsStop'),
 ('log', 'INFO', 'Loaded definition for FastData'),
 ('log', 'INFO', 'Loaded definition for Scale'),
 ('log', 'INFO', 'Loaded definition for Servo'),
 ('log', 'INFO', 'Loaded definition for Global'),
 ('open', '/dev/serial0', 17),
 ('log', 'INFO', 'No Axis YAML files found — migrating from input configs'),
 ('state', 'constructed', False, False, 0, 0.03333333333333333, False, False, 0, [], None, None, 0),
 ('read_registers', 74, 86),
 ('log', 'INFO', 'Communication established with /dev/serial0 (baudrate=115200, address=17)'),
 ('write_float', 8, 1000, (('byteorder', 3),)),
 ('log', 'DEBUG', 'Write maxSpeed: float 1000 to address 8'),
 ('write_float', 14, 1000, (('byteorder', 3),)),
 ('log', 'DEBUG', 'Write acceleration: float 1000 to address 14'),
 ('write_float', 12, 0, (('byteorder', 3),)),
 ('log', 'DEBUG', 'Write jogSpeed: float 0 to address 12'),
 ('write_register', 24, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write servoDir: signed 1 to address 24'),
 ('write_register', 37, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 37'),
 ('write_register', 49, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 49'),
 ('write_register', 61, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 61'),
 ('write_register', 73, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 73'),
 ('read_register', 36, (('signed', False),)),
 ('write_long', 32, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 32'),
 ('write_long', 34, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 34'),
 ('read_register', 48, (('signed', False),)),
 ('write_long', 44, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 44'),
 ('write_long', 46, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 46'),
 ('read_register', 60, (('signed', False),)),
 ('write_long', 56, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 56'),
 ('write_long', 58, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 58'),
 ('read_register', 72, (('signed', False),)),
 ('write_long', 68, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 68'),
 ('write_long', 70, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 70'),
 ('log', 'INFO', 'Firmware register protocol version 11 (expected 11)'),
 ('state',
  'tick 0',
  True,
  True,
  1,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  1,
  11,
  35),
 ('read_registers', 74, 86),
 ('state',
  'tick 1',
  True,
  True,
  2,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  1,
  11,
  35),
 ('read_registers', 74, 86),
 ('state',
  'tick 2',
  True,
  True,
  3,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  1,
  11,
  35),
 ('read_register', 104, (('signed', False),)),
 ('read_float', 8, (('byteorder', 3),)),
 ('read_long', 16, (('byteorder', 3), ('signed', True))),
 ('read_long', 170, (('byteorder', 3), ('signed', True))),
 ('read_long', 172, (('byteorder', 3), ('signed', True))),
 ('read_long', 174, (('byteorder', 3), ('signed', True))),
 ('live', 0, 1000.0, -7, [0, 0, 0]),
 ('state',
  'live reads',
  True,
  True,
  3,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  1,
  11,
  35),
 ('read_registers', 104, 72),
 ('read_registers', 176, 70),
 ('refresh', 11, 59),
 ('state',
  'refresh',
  True,
  True,
  3,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  1,
  11,
  35),
 ('write_register', 36, 0, (('signed', False),)),
 ('log', 'DEBUG', 'Write syncEnable: unsigned 0 to address 36'),
 ('write_register', 48, 0, (('signed', False),)),
 ('log', 'DEBUG', 'Write syncEnable: unsigned 0 to address 48'),
 ('write_register', 60, 0, (('signed', False),)),
 ('log', 'DEBUG', 'Write syncEnable: unsigned 0 to address 60'),
 ('write_register', 72, 0, (('signed', False),)),
 ('log', 'DEBUG', 'Write syncEnable: unsigned 0 to address 72'),
 ('write_register', 104, 0, (('signed', False),)),
 ('log', 'DEBUG', 'Write enable: unsigned 0 to address 104'),
 ('write_register', 102, 0, (('signed', False),)),
 ('log', 'DEBUG', 'Write servoMode: unsigned 0 to address 102'),
 ('state',
  'disengage',
  True,
  True,
  3,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  1,
  11,
  35),
 ('write_long', 170, 365, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write calMeasured[0]: long 365 to address 170'),
 ('write_long', 172, 373, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write calMeasured[1]: long 373 to address 172'),
 ('write_long', 174, 366, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write calMeasured[2]: long 366 to address 174'),
 ('state',
  'array write',
  True,
  True,
  3,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  1,
  11,
  35),
 ('write_register', 109, 1, (('signed', False),)),
 ('log', 'WARNING', 'Communication lost with /dev/serial0'),
 ('log', 'ERROR', 'No communication with the instrument (no answer)'),
 ('state',
  'unacknowledged write',
  True,
  False,
  3,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  1,
  11,
  35),
 ('read_registers', 74, 86),
 ('log', 'INFO', 'Communication established with /dev/serial0 (baudrate=115200, address=17)'),
 ('state',
  'tick after the unacknowledged write',
  True,
  True,
  4,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  0,
  11,
  35),
 ('read_registers', 74, 86),
 ('log', 'ERROR', 'No communication with the instrument (no answer)'),
 ('close',),
 ('log', 'WARNING', 'Communication lost with /dev/serial0'),
 ('state',
  'failed tick',
  False,
  False,
  5,
  1.0,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  0,
  None,
  0),
 ('open', '/dev/serial0', 17),
 ('read_registers', 74, 86),
 ('close',),
 ('state',
  'failed tick, port reopened',
  False,
  False,
  6,
  1.0,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  0,
  None,
  0),
 ('open-failed', '/dev/serial0'),
 ('log', 'ERROR', 'Failed to connect to /dev/serial0: could not open port /dev/serial0'),
 ('state',
  'no device',
  False,
  False,
  7,
  2.0,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  0,
  None,
  0),
 ('open', '/dev/serial0', 17),
 ('read_registers', 74, 86),
 ('log', 'INFO', 'Communication established with /dev/serial0 (baudrate=115200, address=17)'),
 ('write_float', 8, 1000, (('byteorder', 3),)),
 ('log', 'DEBUG', 'Write maxSpeed: float 1000 to address 8'),
 ('write_float', 14, 1000, (('byteorder', 3),)),
 ('log', 'DEBUG', 'Write acceleration: float 1000 to address 14'),
 ('write_float', 12, 0, (('byteorder', 3),)),
 ('log', 'DEBUG', 'Write jogSpeed: float 0 to address 12'),
 ('write_register', 24, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write servoDir: signed 1 to address 24'),
 ('write_register', 37, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 37'),
 ('write_register', 49, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 49'),
 ('write_register', 61, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 61'),
 ('write_register', 73, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 73'),
 ('read_register', 36, (('signed', False),)),
 ('write_long', 32, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 32'),
 ('write_long', 34, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 34'),
 ('read_register', 48, (('signed', False),)),
 ('write_long', 44, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 44'),
 ('write_long', 46, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 46'),
 ('read_register', 60, (('signed', False),)),
 ('write_long', 56, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 56'),
 ('write_long', 58, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 58'),
 ('read_register', 72, (('signed', False),)),
 ('write_long', 68, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 68'),
 ('write_long', 70, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 70'),
 ('log', 'INFO', 'Firmware register protocol version 11 (expected 11)'),
 ('state',
  'reconnect tick',
  True,
  True,
  8,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  0,
  11,
  35),
 ('read_registers', 74, 86),
 ('state',
  'steady tick',
  True,
  True,
  9,
  0.03333333333333333,
  False,
  False,
  0,
  ['_pad1', 'cycles', 'executionInterval'],
  0,
  11,
  35),
 ('close',),
 ('log', 'WARNING', 'Communication lost with /dev/serial0'),
 ('log', 'WARNING', 'Board polling paused and the serial port released'),
 ('state', 'paused', False, False, 9, 0.03333333333333333, True, True, 0, [], None, None, 0),
 ('log', 'ERROR', "'NoneType' object has no attribute 'read_float'"),
 ('live while paused', 0),
 ('state',
  'live read while paused',
  False,
  False,
  9,
  0.03333333333333333,
  True,
  True,
  1,
  [],
  None,
  None,
  0),
 ('open', '/dev/serial0', 17),
 ('log', 'INFO', 'Board polling resumed'),
 ('state', 'resumed', False, False, 9, 0.03333333333333333, False, False, 1, [], None, None, 0),
 ('read_registers', 74, 86),
 ('log', 'INFO', 'Communication established with /dev/serial0 (baudrate=115200, address=17)'),
 ('write_float', 8, 1000, (('byteorder', 3),)),
 ('log', 'DEBUG', 'Write maxSpeed: float 1000 to address 8'),
 ('write_float', 14, 1000, (('byteorder', 3),)),
 ('log', 'DEBUG', 'Write acceleration: float 1000 to address 14'),
 ('write_float', 12, 0, (('byteorder', 3),)),
 ('log', 'DEBUG', 'Write jogSpeed: float 0 to address 12'),
 ('write_register', 24, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write servoDir: signed 1 to address 24'),
 ('write_register', 37, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 37'),
 ('write_register', 49, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 49'),
 ('write_register', 61, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 61'),
 ('write_register', 73, 1, (('signed', True),)),
 ('log', 'DEBUG', 'Write scaleDir: signed 1 to address 73'),
 ('read_register', 36, (('signed', False),)),
 ('write_long', 32, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 32'),
 ('write_long', 34, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 34'),
 ('read_register', 48, (('signed', False),)),
 ('write_long', 44, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 44'),
 ('write_long', 46, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 46'),
 ('read_register', 60, (('signed', False),)),
 ('write_long', 56, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 56'),
 ('write_long', 58, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 58'),
 ('read_register', 72, (('signed', False),)),
 ('write_long', 68, 81, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioNum: long 81 to address 68'),
 ('write_long', 70, 25, (('byteorder', 3), ('signed', True))),
 ('log', 'DEBUG', 'Write syncRatioDen: long 25 to address 70'),
 ('log', 'INFO', 'Firmware register protocol version 11 (expected 11)'),
 ('state',
  'tick after resume',
  True,
  True,
  10,
  0.03333333333333333,
  False,
  False,
  1,
  ['_pad1', 'cycles', 'executionInterval'],
  0,
  11,
  35),
 ('read_registers', 74, 86),
 ('state',
  'steady tick after resume',
  True,
  True,
  11,
  0.03333333333333333,
  False,
  False,
  1,
  ['_pad1', 'cycles', 'executionInterval'],
  0,
  11,
  35)]
