"""PortOwner with its own thread: the step-2 mode.

Separate from test_port_owner.py because these cases start a real thread and
wait on real events. They are written to be deterministic anyway: every wait is
on an Event or a predicate with a generous timeout, never on a bare sleep of
"long enough", and the fake instrument's latency is what makes the interesting
interleavings reachable rather than lucky.

What is pinned here is what the thread ADDS, since everything it shares with
the synchronous owner is already pinned by test_port_owner.py and by
tests/dispatchers/test_board_wire_trace.py:

  * snapshots keep arriving, and carry a rising seq and a t_acq;
  * a command submitted from another thread is executed by the loop, and the
    submitter's call() gets its result back;
  * pause() made from another thread waits for the in-flight exchange, closes
    the port and says so -- and a snapshot taken before it is DROPPED at apply
    because its generation is stale;
  * an exception inside the loop does not end the thread: it is counted, a
    link-fault snapshot goes out, and the next cycle still runs;
  * a link that fails produces connected=False and an empty hot group, and
    recovers on its own when the wire comes back;
  * stop() ends the thread and puts the owner back in synchronous mode.
"""
import threading
import time
from types import SimpleNamespace

import minimalmodbus
import pytest

# timing: real threads and real waits. Not known to be flaky; marked
# because contention is exactly what would make a threading test lie.
pytestmark = pytest.mark.timing

from reflex.utils import communication
from reflex.utils.communication import ConnectionManager
from reflex.utils.devices import TICK_BASE, TICK_COUNT
from reflex.utils.port_owner import (
    RETRY_AFTER_FAILED_READ_S, PortOwner, Read,
)

# Long enough that a slow CI box does not fail a correct implementation,
# short enough that a genuine hang is still a quick red.
WAIT_S = 5.0


def wait_for(predicate, timeout_s=WAIT_S, poll_s=0.005):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return predicate()


class SlowInstrument:
    """A fake wire with a settable per-exchange latency and failure mode.

    The latency is the point: it holds the owner thread inside an exchange for
    a known interval, which is what makes "pause arrived mid-exchange" and
    "the main thread fell behind" reachable deliberately instead of by luck.
    """

    def __init__(self, latency_s=0.0):
        self.latency_s = latency_s
        self.fail = False
        self.port_refuses_to_open = False
        self.raise_once = None
        self.exchanges = 0
        self.in_exchange = threading.Event()
        self.lock = threading.Lock()
        self.serial = SimpleNamespace(close=lambda: None)

    def _exchange(self):
        self.in_exchange.set()
        try:
            if self.latency_s:
                time.sleep(self.latency_s)
            with self.lock:
                self.exchanges += 1
                if self.raise_once is not None:
                    exc, self.raise_once = self.raise_once, None
                    raise exc
                if self.fail:
                    raise minimalmodbus.NoResponseError("no answer")
        finally:
            self.in_exchange.clear()

    def read_registers(self, registeraddress, number_of_registers):
        self._exchange()
        return [0] * number_of_registers

    def read_register(self, address, signed=False):
        self._exchange()
        return address

    def write_register(self, address, value, signed=False):
        self._exchange()


class Sink:
    """Stands in for Board: collects snapshots the way the main thread would,
    newest-wins, and records every one it was handed."""

    def __init__(self):
        self.all = []
        self.lock = threading.Lock()

    def __call__(self, snapshot):
        with self.lock:
            self.all.append(snapshot)

    @property
    def count(self):
        with self.lock:
            return len(self.all)

    @property
    def last(self):
        with self.lock:
            return self.all[-1] if self.all else None


@pytest.fixture
def rig():
    """A real ConnectionManager and register layout over a SlowInstrument.

    The owner is NOT started here: each case starts it when it is ready, and
    the fixture stops it afterwards so a failing assertion never leaks a
    thread into the next test.
    """
    cm = ConnectionManager(serial_device="/dev/null")
    instrument = SlowInstrument()
    cm.device = instrument
    # The real connect() opens a real serial port, and /dev/null cannot be
    # reopened, so after the first failed read tore the link down the owner
    # could never get it back and recovery was untestable. Standing in for the
    # open is the smallest honest stub: the wire comes back unless it is still
    # refusing, which is exactly what a re-plugged cable does.
    def reconnect():
        if instrument.port_refuses_to_open:
            cm.device = None
            return
        cm.device = instrument
    cm.connect = reconnect
    owner = PortOwner(cm, synchronous=False)
    cm._port_owner = owner
    owner.scan_device = cm['Global']
    sink = Sink()
    try:
        yield SimpleNamespace(cm=cm, owner=owner, instrument=instrument,
                              sink=sink, glob=cm['Global'])
    finally:
        owner.stop()


# ── the loop runs ────────────────────────────────────────────────────────────

def test_the_thread_produces_snapshots_with_a_rising_seq_and_a_t_acq(rig):
    rig.owner.start(rig.sink)
    assert wait_for(lambda: rig.sink.count >= 3), "the loop produced no snapshots"
    seqs = [s.seq for s in rig.sink.all]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    good = [s for s in rig.sink.all if s.ok_fast]
    assert good, "no successful scan"
    assert all(s.t_acq is not None for s in good)
    # t_acq is the acquisition instant, so it only moves forward.
    stamps = [s.t_acq for s in good]
    assert stamps == sorted(stamps)


def test_start_leaves_synchronous_mode_and_stop_returns_to_it(rig):
    assert not rig.owner.running
    rig.owner.start(rig.sink)
    assert rig.owner.running
    assert rig.owner._synchronous is False
    rig.owner.stop()
    assert not rig.owner.running
    assert rig.owner._synchronous is True


def test_stop_is_idempotent_and_ends_the_thread(rig):
    rig.owner.start(rig.sink)
    assert wait_for(lambda: rig.sink.count >= 1)
    thread = rig.owner._thread
    rig.owner.stop()
    rig.owner.stop()
    assert not thread.is_alive(), "the owner thread outlived stop()"


# ── commands cross the thread boundary ───────────────────────────────────────

def test_a_command_submitted_from_another_thread_is_executed_by_the_loop(rig):
    rig.owner.start(rig.sink)
    assert wait_for(lambda: rig.sink.count >= 1)
    # call() from THIS thread blocks on the future the owner thread resolves.
    assert rig.owner.call(Read(communication.read_unsigned, 7)) == 7


def test_the_blocking_proxy_returns_the_owner_threads_result_not_the_callers(rig):
    """The exchange must happen on the owner thread, never on the caller's:
    that is the whole reason minimalmodbus's lack of thread safety stops
    mattering."""
    rig.instrument.latency_s = 0.01
    seen = {}

    def exchange_thread_recorder(address, signed=False):
        seen["thread"] = threading.current_thread().name
        return address

    rig.instrument.read_register = exchange_thread_recorder
    rig.owner.start(rig.sink)
    assert rig.owner.call(Read(communication.read_unsigned, 11)) == 11
    assert seen["thread"] == "modbus-owner"


# ── the flasher handover ─────────────────────────────────────────────────────

def test_pause_from_another_thread_waits_for_the_exchange_and_closes_the_port(rig):
    rig.instrument.latency_s = 0.02
    rig.owner.start(rig.sink)
    assert wait_for(lambda: rig.sink.count >= 2)
    generation_before = rig.owner.generation

    assert rig.owner.pause() is True, "pause was not acknowledged"
    assert rig.owner.paused
    assert rig.cm.device is None, "the port was not closed"
    assert rig.owner.generation == generation_before + 1
    assert not rig.instrument.in_exchange.is_set(), "paused mid-exchange"

    # Nothing more is scanned while paused.
    settled = rig.sink.count
    time.sleep(4 * (1.0 / 30))
    assert rig.sink.count == settled, "the loop kept scanning while paused"


def test_resume_reopens_the_port_and_scanning_restarts(rig):
    rig.owner.start(rig.sink)
    assert wait_for(lambda: rig.sink.count >= 1)
    assert rig.owner.pause() is True
    paused_at = rig.sink.count

    rig.owner.resume()
    assert not rig.owner.paused
    assert wait_for(lambda: rig.sink.count > paused_at), "no scan after resume"


def test_a_snapshot_from_before_the_pause_is_stale_by_generation(rig):
    """The property Board._apply_snapshot drops on: a scan already in flight
    when the port was handed over must not repopulate what pause cleared."""
    rig.owner.start(rig.sink)
    assert wait_for(lambda: rig.sink.count >= 1)
    before = rig.sink.last
    assert rig.owner.pause() is True
    assert before.generation != rig.owner.generation


# ── nothing gets out of the loop ─────────────────────────────────────────────

def test_a_wire_that_raises_is_a_failed_read_not_a_loop_error(rig):
    """Worth pinning because it is the boundary the next two cases sit on: an
    exception from the WIRE is the scan's own business (link down, backoff,
    empty hot group) and must never reach the loop's catch-all."""
    rig.instrument.raise_once = RuntimeError("the wire misbehaved")
    rig.owner.start(rig.sink)
    assert wait_for(lambda: any(not s.ok_fast for s in rig.sink.all)), \
        "a raising wire produced no failed snapshot"
    assert rig.owner.loop_exceptions == 0, \
        "a wire error was counted as a loop error"


def test_an_exception_in_the_loop_is_counted_and_the_thread_survives(rig):
    """The catch-all is for everything OUTSIDE the scan's own try. The real
    one is the publish callback: Board hands in Clock.schedule_once, and an
    error there must not take the link down with it."""
    boom = {"left": 2}

    def sometimes_explodes(snapshot):
        if boom["left"] > 0:
            boom["left"] -= 1
            raise RuntimeError("the main thread handover failed")
        rig.sink(snapshot)

    rig.owner.start(sometimes_explodes)
    assert wait_for(lambda: rig.owner.loop_exceptions >= 1), \
        "the loop did not record the exception"
    assert wait_for(lambda: rig.sink.count >= 1), \
        "the thread stopped producing after an exception"
    assert rig.owner.running, "the owner thread died on an exception"


def test_a_loop_error_publishes_a_link_fault_snapshot(rig):
    """A reader must never have to tell 'the loop broke' from 'the link is
    down': the fault snapshot has the same shape a failed read produces. The
    publish raises ONCE, so the fault snapshot that follows it gets through."""
    first = {"done": False}

    def explode_once(snapshot):
        if not first["done"]:
            first["done"] = True
            raise RuntimeError("boom")
        rig.sink(snapshot)

    rig.owner.start(explode_once)
    assert wait_for(lambda: rig.owner.loop_exceptions >= 1)
    assert wait_for(lambda: rig.sink.count >= 1), "nothing was published after the error"
    fault = rig.sink.all[0]
    assert fault.ok_fast is False, "the first snapshot after the error was not a fault"
    assert fault.els_stop == {} and fault.fast_data is None
    assert fault.ok_hot is False
    assert fault.retry_after == RETRY_AFTER_FAILED_READ_S


# ── the link goes away and comes back ────────────────────────────────────────

def test_a_failing_link_reports_down_and_recovers_on_its_own(rig):
    """The failure-injection case: a wire that stops answering must show as
    down within one backoff, and come back without anyone restarting anything."""
    rig.owner.start(rig.sink)
    assert wait_for(lambda: any(s.ok_fast for s in rig.sink.all)), "never came up"

    rig.instrument.fail = True
    assert wait_for(lambda: rig.sink.last is not None and not rig.sink.last.ok_fast,
                    timeout_s=RETRY_AFTER_FAILED_READ_S + WAIT_S), \
        "a dead wire never produced a failed snapshot"
    down = rig.sink.last
    assert down.els_stop == {} and down.fast_data is None

    rig.instrument.fail = False
    assert wait_for(lambda: rig.sink.last is not None and rig.sink.last.ok_fast,
                    timeout_s=RETRY_AFTER_FAILED_READ_S + WAIT_S), \
        "the link never recovered"
