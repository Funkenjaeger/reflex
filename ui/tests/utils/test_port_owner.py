"""PortOwner: the one object every Modbus exchange goes through.

Run against the REAL ConnectionManager and register layout with a fake
instrument underneath, so the read/write helpers, the tick span and the
connection verdict are production code; only the wire is fake.

Most cases use the QUEUED mode (synchronous=False) and call run_cycle by hand:
that is the mode whose ordering guarantees matter once the cycle runs on a
thread of its own, and calling run_cycle directly checks them without one.
The app's own owner is synchronous; its traffic is pinned end to end by
tests/dispatchers/test_board_wire_trace.py.
"""
from types import SimpleNamespace
from unittest.mock import patch

import minimalmodbus
import pytest
from kivy.event import EventDispatcher

from reflex.dispatchers.board import Board
from reflex.utils import communication, port_owner
from reflex.utils.communication import ConnectionManager
from reflex.utils.devices import TICK_BASE, TICK_COUNT
from reflex.utils.port_owner import (
    RETRY_AFTER_FAILED_READ_S, RETRY_AFTER_NO_DEVICE_S, SCAN_PERIOD_S,
    Batch, PortOwner, Read, ReadSpan, Snapshot, Write,
)

TICK = ("read_registers", TICK_BASE, TICK_COUNT)


class FakeInstrument:
    """Records every exchange as a tuple; `fail` makes every exchange raise
    the way minimalmodbus does when the controller does not answer."""

    def __init__(self, wire):
        self.wire = wire
        self.fail = False
        self.serial = SimpleNamespace(close=lambda: wire.append(("close",)))

    def _exchange(self, entry):
        self.wire.append(entry)
        if self.fail:
            raise minimalmodbus.NoResponseError("no answer")

    def read_registers(self, registeraddress, number_of_registers):
        self._exchange(("read_registers", registeraddress, number_of_registers))
        return [0] * number_of_registers

    def read_register(self, address, signed=False):
        self._exchange(("read_register", address))
        return address

    def write_register(self, address, value, signed=False):
        self._exchange(("write_register", address, value))


@pytest.fixture
def rig():
    """A real ConnectionManager over a FakeInstrument, with a QUEUED owner
    installed as its port owner (so its devices' item access reaches it)."""
    cm = ConnectionManager(serial_device="/dev/null")
    wire = []
    cm.device = FakeInstrument(wire)
    owner = PortOwner(cm, synchronous=False)
    cm._port_owner = owner
    owner.scan_device = cm['Global']
    # Never due on its own: a case that calls run_cycle() only to drain the
    # queue gets no scan. Cases about the scan force it or set a due time.
    owner.next_scan_at = float("inf")
    return SimpleNamespace(cm=cm, owner=owner, wire=wire,
                           instrument=cm.device, glob=cm['Global'])


def _read(address):
    return Read(communication.read_unsigned, address)


def _write(address, value):
    return Write(communication.write_unsigned, address, value, f"r{address}")


# ── submission and order ─────────────────────────────────────────────────────

def test_the_synchronous_owner_executes_at_submit():
    """The app's mode: the exchange happens inside submit(), so a write lands
    on the wire exactly where the code that issued it runs."""
    cm = ConnectionManager(serial_device="/dev/null")
    wire = []
    cm.device = FakeInstrument(wire)
    owner = PortOwner(cm)
    future = owner.submit(_read(7))
    assert future.done() and future.result() == 7
    assert wire == [("read_register", 7)]


def test_the_queued_owner_touches_nothing_until_run_cycle(rig):
    future = rig.owner.submit(_read(7))
    assert rig.wire == [] and not future.done()
    rig.owner.run_cycle()
    assert future.result() == 7


def test_commands_execute_in_fifo_order(rig):
    futures = [rig.owner.submit(_write(a, a * 10)) for a in (5, 3, 9, 1)]
    rig.owner.run_cycle()
    assert rig.wire == [("write_register", 5, 50), ("write_register", 3, 30),
                        ("write_register", 9, 90), ("write_register", 1, 10)]
    assert [f.result() for f in futures] == [True] * 4


def test_the_disengage_order_survives_the_queue(rig):
    """sync off on every scale, then enable, then the feed: the order the
    firmware's servoEnableTask makes load-bearing (els_fsm.on_enter_disabled)."""
    sync = [_write(a, 0) for a in (36, 48, 60, 72)]
    for command in sync + [_write(104, 0), _write(102, 0)]:
        rig.owner.submit(command)
    rig.owner.run_cycle()
    assert [entry[1] for entry in rig.wire] == [36, 48, 60, 72, 104, 102]


def test_a_batch_is_executed_whole_and_in_order(rig):
    before = rig.owner.submit(_write(1, 1))
    batch = rig.owner.submit(Batch((_write(10, 1), _read(11), _write(12, 2))))
    after = rig.owner.submit(_write(2, 2))
    rig.owner.run_cycle(force_scan=True)
    assert rig.wire == [("write_register", 1, 1),
                        ("write_register", 10, 1), ("read_register", 11),
                        ("write_register", 12, 2),
                        ("write_register", 2, 2),
                        TICK]
    assert batch.result() == [True, 11, True]
    assert before.result() is True and after.result() is True


def test_a_batch_stops_at_an_item_that_raises(rig):
    """The same calls made in a row stop at the first one that raises; the
    batch does too, and the exception is the batch's result."""
    rig.instrument.fail = True
    future = rig.owner.submit(Batch((ReadSpan(0, 4), ReadSpan(4, 4))))
    rig.owner.run_cycle()
    assert rig.wire == [("read_registers", 0, 4)]
    assert isinstance(future.exception(), minimalmodbus.NoResponseError)


def test_queued_commands_go_ahead_of_the_scan(rig):
    rig.owner.submit(_write(104, 1))
    rig.owner.submit(_write(109, 0))
    snapshot = rig.owner.run_cycle(force_scan=True)
    assert rig.wire == [("write_register", 104, 1), ("write_register", 109, 0), TICK]
    assert snapshot.ok_fast


def test_the_scan_reading_through_the_owner_does_not_wait_on_itself(rig):
    """The scan's read_tick goes through read_span, which submits to this same
    owner. Submitted from inside run_cycle it executes inline; enqueued, it
    would wait on a queue only this thread drains."""
    snapshot = rig.owner.run_cycle(force_scan=True)
    assert rig.wire == [TICK]
    assert snapshot.ok_fast and snapshot.ok_hot


class RecordingOwner(PortOwner):
    """A synchronous owner that also records each command it is handed."""

    def __init__(self, cm):
        super().__init__(cm)
        self.commands = []

    def submit(self, command):
        self.commands.append(command)
        return super().submit(command)


def test_device_access_goes_through_the_owner_one_command_per_exchange_group():
    """BaseDevice is the owner's main client. A field read is a Read, a write
    a Write, an array a Batch (its elements reach the wire back to back), a
    refresh() a Batch of its chunk requests, the tick one ReadSpan. Building a
    sub-structure view is not an exchange and submits nothing."""
    cm = ConnectionManager(serial_device="/dev/null")
    wire = []
    cm.device = FakeInstrument(wire)
    owner = RecordingOwner(cm)
    cm._port_owner = owner
    glob = cm['Global']

    els = glob['elsStop']
    _scales = glob['scales']
    assert owner.commands == [], "a struct view is not an exchange"

    els['enable']
    els['enable'] = 1
    els['calMeasured']
    els.refresh()
    glob.read_tick()

    kinds = [type(c).__name__ for c in owner.commands]
    assert kinds == ["Read", "Write", "Batch", "Batch", "ReadSpan"]
    cal, refresh = owner.commands[2], owner.commands[3]
    assert [type(i).__name__ for i in cal.items] == ["Read"] * 3
    assert [(i.address, i.count) for i in refresh.items] == [
        (els.base_address, 73), (els.base_address + 73, els.size - 73)]
    assert owner.commands[4] == ReadSpan(TICK_BASE, TICK_COUNT)
    assert wire[-1] == TICK


# ── failure results ──────────────────────────────────────────────────────────

def test_a_failed_write_resolves_false_and_drops_connected(rig):
    rig.cm.connected = True
    rig.instrument.fail = True
    future = rig.owner.submit(_write(104, 1))
    rig.owner.run_cycle()
    assert future.result() is False
    assert rig.cm.connected is False


def test_a_failed_read_resolves_zero_and_counts_itself(rig):
    rig.instrument.fail = True
    future = rig.owner.submit(_read(42))
    rig.owner.run_cycle()
    assert future.result() == 0
    assert rig.cm.read_failures == 1


def test_a_failed_span_read_raises_through_the_wait(rig):
    rig.instrument.fail = True
    cm = ConnectionManager(serial_device="/dev/null")
    cm.device = rig.instrument
    sync_owner = PortOwner(cm)
    with pytest.raises(minimalmodbus.NoResponseError):
        sync_owner.call(ReadSpan(0, 4))


# ── the scan, its failure paths and its cadence ──────────────────────────────

def test_a_good_scan(rig, monkeypatch):
    monkeypatch.setattr(port_owner.time, "monotonic", lambda: 100.0)
    snapshot = rig.owner.run_cycle(force_scan=True)
    assert isinstance(snapshot, Snapshot)
    assert (snapshot.ok_fast, snapshot.ok_hot, snapshot.port_open) == (True, True, True)
    assert snapshot.retry_after is None and snapshot.t_acq == 100.0
    assert 'servoMode' in snapshot.fast_data and 'enable' in snapshot.els_stop
    assert rig.cm.connected is True
    assert rig.owner.next_scan_at == 100.0 + SCAN_PERIOD_S


def test_a_failed_tick_read_closes_the_port_and_backs_off_one_second(rig, monkeypatch):
    monkeypatch.setattr(port_owner.time, "monotonic", lambda: 100.0)
    rig.cm.connected = True
    rig.instrument.fail = True
    snapshot = rig.owner.run_cycle(force_scan=True)
    assert rig.wire == [TICK, ("close",)]
    assert rig.cm.device is None and rig.cm.connected is False
    assert (snapshot.ok_fast, snapshot.ok_hot, snapshot.port_open) == (False, False, True)
    assert snapshot.fast_data is None and snapshot.els_stop == {}
    assert snapshot.retry_after == RETRY_AFTER_FAILED_READ_S == 1.0
    assert snapshot.t_acq is None
    assert rig.owner.next_scan_at == 101.0


def test_no_port_backs_off_two_seconds(rig, monkeypatch):
    monkeypatch.setattr(port_owner.time, "monotonic", lambda: 100.0)
    rig.cm.device = None

    def refuse(**_kw):
        raise OSError("could not open port")

    with patch.object(communication.minimalmodbus, "Instrument", refuse):
        snapshot = rig.owner.run_cycle(force_scan=True)
    assert rig.wire == []
    assert snapshot.port_open is False and snapshot.ok_fast is False
    assert snapshot.retry_after == RETRY_AFTER_NO_DEVICE_S == 2.0
    assert rig.owner.next_scan_at == 102.0


def test_a_hot_group_that_fails_to_decode_keeps_the_link(rig):
    class BadHot:
        def decode_hot(self, _raw):
            raise ValueError("span is 55 registers, format wants 56")

    class Device:
        """The real tick read, with an elsStop whose hot decode fails."""
        read_tick = staticmethod(rig.glob.read_tick)

        def __getitem__(self, name):
            return BadHot()

    rig.owner.scan_device = Device()
    snapshot = rig.owner.run_cycle(force_scan=True)
    assert snapshot.ok_fast is True and snapshot.ok_hot is False
    assert snapshot.els_stop == {} and snapshot.fast_data
    assert rig.cm.connected is True and rig.cm.device is not None


def test_the_scan_waits_for_its_due_time_unless_forced(rig, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(port_owner.time, "monotonic", lambda: now[0])
    rig.owner.next_scan_at = 0.0
    rig.instrument.fail = True
    rig.owner.run_cycle()                       # due at once: fails, 1.0 s retry
    assert rig.wire == [TICK, ("close",)]
    rig.cm.device = rig.instrument
    rig.instrument.fail = False
    now[0] = 100.9
    assert rig.owner.run_cycle() is None        # not due yet
    assert rig.wire == [TICK, ("close",)]
    now[0] = 101.0
    assert rig.owner.run_cycle().ok_fast        # due
    now[0] = 101.0 + SCAN_PERIOD_S / 2
    assert rig.owner.run_cycle() is None        # back on the 30 Hz cadence
    assert rig.owner.run_cycle(force_scan=True).ok_fast


def test_seq_counts_scans(rig):
    seqs = [rig.owner.run_cycle(force_scan=True).seq for _ in range(3)]
    assert seqs == [1, 2, 3]


# ── the flasher handover ─────────────────────────────────────────────────────

def test_pause_closes_the_port_bumps_the_generation_and_stops_scanning(rig):
    generation = rig.owner.generation
    rig.owner.pause()
    assert rig.wire == [("close",)] and rig.cm.device is None
    assert rig.owner.generation == generation + 1
    assert rig.owner.run_cycle(force_scan=True) is None
    rig.owner.pause()
    assert rig.wire == [("close",)], "pause is idempotent"


def test_resume_reopens_the_port_and_scanning(rig):
    rig.owner.pause()
    opened = []
    with patch.object(communication.minimalmodbus, "Instrument",
                      lambda **kw: opened.append(kw) or FakeInstrument(rig.wire)):
        rig.owner.resume()
    assert len(opened) == 1
    assert rig.owner.run_cycle(force_scan=True).ok_fast


class _ApplyBoard(Board):
    """A Board with its assembly root skipped, as in test_els_stop_snapshot."""

    def __init__(self, connection_manager, device):
        EventDispatcher.__init__(self)
        self.connection_manager = connection_manager
        self.device = device
        self.fast_data_values = {}
        self.els_stop_values = {}
        self.task_update = SimpleNamespace(timeout=SCAN_PERIOD_S)


def test_a_snapshot_from_before_a_pause_is_not_applied(rig):
    board = _ApplyBoard(rig.cm, rig.glob)
    stale = rig.owner.run_cycle(force_scan=True)
    rig.owner.pause()
    board._apply_snapshot(stale)
    assert board.fast_data_values == {} and board.els_stop_values == {}
    assert board.update_tick == 0 and board.connected is False


def test_a_current_snapshot_is_applied(rig):
    board = _ApplyBoard(rig.cm, rig.glob)
    board._apply_snapshot(rig.owner.run_cycle(force_scan=True))
    assert board.fast_data_values and board.els_stop_values
    assert board.update_tick == 1 and board.connected is True
