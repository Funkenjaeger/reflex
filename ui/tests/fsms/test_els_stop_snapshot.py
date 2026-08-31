"""The once-per-tick elsStop snapshot, and the chunking underneath it.

WHAT THIS EXISTS TO PREVENT. On 2026-08-23 a lathe session lost Modbus comms on
six of six cuts, every drop a TIMEOUT at the transition into `cutting` and not
one a corrupted frame -- the firmware failing to answer, not the wiring.
`BaseDevice.__getitem__` is a LIVE per-field read, so each
``device['elsStop']['active']`` in a tick-driven poller was its own request, and
at 30 Hz with several such pollers there were more requests in flight than the
firmware could service while its 100 kHz ISR was saturated at cut-start.

Two changes collapsed that traffic, and this file pins both:

  * bigger chunks in ``BaseDevice.refresh`` (32 -> 64 registers a request), and
  * one ``elsStop`` snapshot per board tick, which the tick-driven pollers read
    instead of going to the wire per field.

THE PART THAT NEEDED THE MOST CARE is not the saving, it is the failure.
``ElsStopHal`` gained ``reads_baseline()`` / ``reads_fabricated_since()`` on
2026-08-23, and several pollers discard a whole poll rather than act on a value
that might be fabricated -- because in this register map 0 is never neutral: it
reads as "not enabled", "no offset", "sequence reset". A snapshot that quietly
served the LAST GOOD values after a failed refresh would be a fabricated read
with the counter unmoved, i.e. exactly the hole that counter was added to
close. So a good snapshot and a failed one have to stay distinguishable, and
that is what most of the cases below are about.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

from types import SimpleNamespace

import pytest
from kivy.event import EventDispatcher

from reflex.dispatchers.board import Board
from reflex.fsms.els_stop_hal import ElsStopHal
from reflex.utils.base_device import BaseDevice
from reflex.utils.communication import ConnectionManager


# ─── doubles ──────────────────────────────────────────────────────────────
# Deliberately real objects with production semantics rather than MagicMocks.
# Everything under test here turns on a value being falsy (an empty snapshot)
# or a counter moving, and a MagicMock answers both truthily -- which would
# make every case below pass without observing anything.

class FakeConnectionManager:
    """Just the read-failure accounting and the link handle Board.update uses."""

    def __init__(self, device=None):
        self.device = device
        self.connected = True
        self.read_failures = 0
        self.errors = []
        self.disconnects = 0

    def reads_failed_since(self, baseline: int) -> bool:
        return self.read_failures != baseline

    def _log_error_once(self, message: str):
        self.errors.append(message)

    def connect(self):
        """A reconnect attempt that fails: Board.update calls this whenever it
        finds no device, and these cases are about what happens on the tick
        where there still isn't one."""
        pass

    def disconnect(self):
        self.disconnects += 1
        self.device = None


class FakeStruct:
    """One register block. `fail=True` makes refresh() raise like a bad frame."""

    def __init__(self, values, fail: bool = False):
        self.values = dict(values)
        self.fail = fail
        self.refresh_count = 0

    def refresh(self):
        self.refresh_count += 1
        if self.fail:
            raise RuntimeError("no communication with the instrument (no answer)")
        return dict(self.values)

    def __getitem__(self, key):
        """A LIVE per-field read -- what the snapshot exists to replace."""
        self.live_reads.append(key)
        return self.values[key]

    live_reads: list = None


class FakeDevice:
    def __init__(self, **structs):
        self.structs = structs
        for s in structs.values():
            s.live_reads = []

    def __getitem__(self, name):
        return self.structs[name]


class BoardUnderTest(Board):
    """A Board with its assembly root skipped.

    `Board.__init__` opens a serial port, builds four InputDispatchers, a
    ServoDispatcher and the axes, and schedules two Clock intervals. None of
    that is what `update()` is being asked about here, and all of it would make
    these cases slow and coupled to settings on disk. The Kivy property machinery
    still has to be initialised, which is what EventDispatcher.__init__ does.
    """

    def __init__(self, device, connection_manager):
        EventDispatcher.__init__(self)
        self.device = device
        self.connection_manager = connection_manager
        self.fast_data_values = dict()
        self.els_stop_values = dict()
        self.task_update = SimpleNamespace(timeout=1.0 / 30)
        self.ticks_seen_with_snapshot = []
        self.bind(update_tick=self._record_tick)

    def _record_tick(self, *_a):
        # Records what a tick-driven poller would find, since every poller is
        # bound to update_tick exactly like this.
        self.ticks_seen_with_snapshot.append(dict(self.els_stop_values))


def _board(*, els_fail=False, fast_fail=False, els_values=None):
    els = FakeStruct(els_values or {"active": 1, "takeupSeq": 7,
                                    "phaseOffsetSteps": 500, "diagSeq": 3,
                                    "machineMode": 5}, fail=els_fail)
    fast = FakeStruct({"servoMode": 1}, fail=fast_fail)
    device = FakeDevice(elsStop=els, fastData=fast)
    cm = FakeConnectionManager(device=device)
    return BoardUnderTest(device, cm), els, fast


class HalBoard:
    """The two attributes ElsStopHal reads a board through."""

    def __init__(self, device, connection_manager, snapshot=None, connected=True):
        self.device = device
        self.connection_manager = connection_manager
        self.els_stop_values = dict(snapshot or {})
        self.connected = connected


def _hal(snapshot=None, live=None, connected=True):
    els = FakeStruct(live or {"enable": 1, "phaseOffsetSteps": 999,
                              "calSeq": 4, "latchSeq": 2, "phaseOffsetSeq": 6,
                              "active": 0})
    device = FakeDevice(elsStop=els)
    cm = FakeConnectionManager(device=device)
    board = HalBoard(device, cm, snapshot=snapshot, connected=connected)
    return ElsStopHal(board), board, els


# ─── 1. chunking ──────────────────────────────────────────────────────────

class RecordingInstrument:
    """A minimalmodbus stand-in that records the shape of every FC3 request."""

    def __init__(self):
        self.requests = []      # (address, number_of_registers)

    def read_registers(self, registeraddress, number_of_registers, **_kw):
        self.requests.append((registeraddress, number_of_registers))
        return [0] * number_of_registers


@pytest.fixture
def els_stop_device():
    """The REAL ElsStop device over a recording transport.

    Built through the real ConnectionManager, which parses the real typedef in
    utils/devices.py -- so the register count below is the actual firmware
    layout and not a number this file made up. ConnectionManager() does no I/O
    when it is never connected.
    """
    cm = ConnectionManager(serial_device="/dev/null")
    cm.device = RecordingInstrument()
    return cm['Global']['elsStop'], cm.device


def test_the_els_stop_block_is_read_in_two_requests(els_stop_device):
    """128 registers at 64 a request: two FULL requests, margin ZERO.

    The absolute number matters more than the ratio: this block is read once
    per board tick now, so every request in it is paid 30 times a second.

    THE MARGIN IS NOW EXACTLY ZERO. The block was 122 when this case was
    first written, 124 after executionCyclesPeak (2026-08-23), and 128 after
    the STEP pulse width instrument (2026-08-25) -- which lands PRECISELY on
    the 2x64 boundary. The NEXT register appended, even one, makes this THREE
    requests and raises the per-tick cost by 50%. That is not a reason to
    avoid appending -- it is a reason the next append MUST come with the
    chunk-size decision made deliberately, with the same firmware-buffer
    arithmetic that chose 64 (Modbus FC3 tops out at 125 registers a request,
    so headroom exists), rather than paying a silent third request.
    """
    device, transport = els_stop_device
    assert device.size == 128, (
        f"elsStop is {device.size} registers, not the 128 this case was "
        f"reasoned about -- re-check the chunk arithmetic, do not just "
        f"update the number")

    device.refresh()

    base = device.base_address
    assert len(transport.requests) == 2
    assert transport.requests == [(base, 64), (base + 64, 64)]


def test_the_block_still_fits_in_two_requests_with_room_to_spare(els_stop_device):
    """The boundary, asserted rather than left in a comment: a block that
    quietly grew past 128 would cost a third request on every one of 30 ticks a
    second, and nothing else in the suite would notice."""
    device, _ = els_stop_device
    from reflex.utils.base_device import BaseDevice

    chunk = BaseDevice.MAX_REGISTERS_PER_READ
    assert device.size <= 2 * chunk, (
        f"elsStop ({device.size} registers) no longer fits in two requests of "
        f"{chunk}. Raise the chunk size against the firmware buffer arithmetic "
        f"(process_FC3, MAX_BUFFER 256, uint8_t length field) -- do not let it "
        f"silently become three.")


def test_no_request_ever_exceeds_the_chunk_size(els_stop_device):
    device, transport = els_stop_device
    device.refresh()
    assert all(n <= BaseDevice.MAX_REGISTERS_PER_READ
               for _addr, n in transport.requests)


def test_the_whole_block_is_covered_exactly_once(els_stop_device):
    """Chunking must not skip or double-read a register: the response is
    struct-unpacked positionally, so a gap silently reinterprets every field
    after it as some other field."""
    device, transport = els_stop_device
    device.refresh()

    covered = []
    for addr, n in transport.requests:
        covered.extend(range(addr, addr + n))
    base = device.base_address
    assert covered == list(range(base, base + device.size))


def test_the_chunk_size_keeps_real_headroom_against_the_firmware():
    """The number, checked against the firmware's own arithmetic.

    An FC3 response is 5 + 2N bytes and the firmware assembles it in a
    256-byte buffer (MAX_BUFFER, fw/Core/Inc/ModbusConfig.h) whose length it
    tracks in a uint8_t, with NO bounds check in the copy loop
    (process_FC3, fw/Core/Src/Modbus.c). Both limits put the ceiling at
    N = 125, and every way of exceeding it fails silently -- a buffer overrun
    into whatever follows, or a wrapped length field.

    So this is not "assert 64 == 64". It asserts the chunk stays at most half
    of BOTH limits, which is what "conservative" was decided to mean here, and
    it is the check that fires if someone later reaches for the ceiling to buy
    one more request back.
    """
    from_buffer = (256 - 5) // 2      # 5 + 2N must fit MAX_BUFFER
    from_uint8 = (255 - 5) // 2       # ...and fit the uint8_t counting it
    ceiling = min(from_buffer, from_uint8)
    assert ceiling == 125, (
        f"the firmware's FC3 ceiling worked out to {ceiling}, not 125 -- "
        f"re-derive the chunk size before trusting it")

    n = BaseDevice.MAX_REGISTERS_PER_READ
    assert n <= 0.6 * ceiling, (
        f"chunking at {n} registers is {n / ceiling:.0%} of what the firmware "
        f"can serve; the whole point of picking a conservative number was to "
        f"stay far from a cliff that fails silently")
    # 64 of 125: 61 registers and 123 buffer bytes in hand. It must also still
    # be worth doing -- below 61 the elsStop block needs three requests instead
    # of two, which is the entire reason the number went up.
    assert n >= 61, (
        f"chunking at {n} registers puts elsStop back above two requests")


# ─── 2. the board takes the snapshot ──────────────────────────────────────

def test_update_publishes_one_els_stop_snapshot_per_tick():
    board, els, _fast = _board()
    board.update()          # the connect tick, which also checks the protocol
    els.refresh_count = 0

    board.update()
    board.update()

    assert els.refresh_count == 2, "one block read per tick, no more and no less"
    assert board.els_stop_values["takeupSeq"] == 7
    assert els.live_reads == [], (
        "the snapshot must not be assembled out of per-field live reads")


def test_the_connect_tick_does_not_read_the_block_twice():
    """The protocol-version check runs on the tick a connection comes up and
    wants the same 122 registers the snapshot just read. Reading them again
    doubled the cost of the one tick where the link is least established."""
    board, els, _fast = _board(
        els_values={"protocolVersion": 5, "takeupSeq": 0})
    board.update()
    assert els.refresh_count == 1
    assert board.protocol_version == 5


def test_the_snapshot_is_in_place_before_the_pollers_run():
    """Ordering, and it is load-bearing: every poller is bound to update_tick,
    so a snapshot taken after the tick was bumped would serve the PREVIOUS
    tick's registers to everything that reads it."""
    board, _els, _fast = _board()
    board.update()
    assert board.ticks_seen_with_snapshot == [{"active": 1, "takeupSeq": 7,
                                               "phaseOffsetSteps": 500,
                                               "diagSeq": 3, "machineMode": 5}]


def test_a_failed_refresh_clears_the_snapshot_rather_than_keeping_the_old_one():
    """THE CASE THIS DESIGN TURNS ON. Serving the previous tick's values after
    a failed refresh would hand every poller a plausible number with nothing
    saying it was stale -- a fabricated read that no guard can see."""
    board, els, _fast = _board()
    board.update()
    assert board.els_stop_values, "fixture precondition: a good snapshot first"

    els.fail = True
    board.update()

    assert board.els_stop_values == {}
    assert board.ticks_seen_with_snapshot[-1] == {}


def test_a_failed_els_stop_refresh_does_not_tear_down_the_link():
    """fastData owns the connection verdict. Dropping the link over one bad
    elsStop frame would take the DRO down with it for no reason."""
    board, els, _fast = _board(els_fail=True)
    board.update()
    assert board.connected is True
    assert board.connection_manager.disconnects == 0
    assert board.connection_manager.errors, "the failure must still be logged"


def test_a_failed_fast_data_refresh_also_clears_the_snapshot():
    """That path returns early, before the elsStop refresh -- so without an
    explicit clear the pollers would read a snapshot from before the link
    dropped, on a tick where update_tick still fires."""
    board, _els, fast = _board()
    board.update()
    assert board.els_stop_values

    fast.fail = True
    board.update()

    assert board.els_stop_values == {}
    assert board.connected is False


def test_no_device_clears_the_snapshot():
    board, _els, _fast = _board()
    board.update()
    assert board.els_stop_values

    board.connection_manager.device = None
    board.update()

    assert board.els_stop_values == {}


# ─── 3. TickReads: values, and the fabrication signal ─────────────────────

def test_tick_reads_come_from_the_snapshot_without_touching_the_wire():
    hal, _board_, els = _hal(snapshot={"active": 1, "takeupSeq": 9,
                                       "takeupResult": 4,
                                       "lastTakeupZDelta": -3,
                                       "takeupThreshCounts": 11,
                                       "phaseOffsetSteps": 250,
                                       "diagSeq": 2, "machineMode": 6})
    assert hal.tick.active() is True
    assert hal.tick.takeup_seq() == 9
    assert hal.tick.takeup_result() == 4
    assert hal.tick.last_takeup_z_delta() == -3
    assert hal.tick.takeup_thresh_counts() == 11
    assert hal.tick.phase_offset_steps() == 250
    assert hal.tick.diag_seq() == 2
    assert hal.tick.current_mode() == 6
    assert els.live_reads == [], (
        f"snapshot reads went to the wire: {els.live_reads}")


def test_a_good_snapshot_reports_no_fabrication():
    """The other half of the pair below, and the one that keeps it honest: a
    guard that reported fabrication unconditionally would pass every failure
    case in this file while making the pollers permanently inert."""
    hal, board, _els = _hal(snapshot={"takeupSeq": 9, "takeupResult": 0,
                                      "lastTakeupZDelta": 5,
                                      "takeupThreshCounts": 2})
    baseline = hal.reads_baseline()
    hal.tick.takeup_seq()
    hal.tick.takeup_result()
    hal.tick.last_takeup_z_delta()
    hal.tick.takeup_thresh_counts()
    assert hal.reads_fabricated_since(baseline) is False
    assert board.connection_manager.read_failures == 0


def test_an_absent_snapshot_is_reported_as_a_fabricated_read():
    """A failed refresh has to be distinguishable from a good one AT THE
    CONSUMER, not just in the log -- the pollers decide whether to act on the
    value, and they decide it with this counter."""
    hal, board, _els = _hal(snapshot={})
    baseline = hal.reads_baseline()
    assert hal.tick.takeup_seq() == 0
    assert hal.reads_fabricated_since(baseline) is True
    assert board.connection_manager.read_failures == 1


def test_every_absent_snapshot_read_is_counted_not_just_the_first():
    """One increment per read that could not happen, matching what the live
    per-field readers did. A caller that samples its baseline mid-poll must
    still see the reads after it move the counter."""
    hal, board, _els = _hal(snapshot={})
    hal.tick.active()
    hal.tick.phase_offset_steps()
    hal.tick.diag_seq()
    assert board.connection_manager.read_failures == 3


def test_absent_snapshot_fallbacks_match_the_live_readers():
    """The fallbacks are what a caller sees when it does not check the
    counter, so they must not differ between the two paths -- otherwise moving
    a poller onto the snapshot silently changes its behaviour on a bad frame."""
    hal, _board_, _els = _hal(snapshot={})
    assert hal.tick.active() is False
    assert hal.tick.takeup_seq() == 0
    assert hal.tick.takeup_result() == 0
    assert hal.tick.last_takeup_z_delta() == 0
    assert hal.tick.takeup_thresh_counts() == 0
    assert hal.tick.phase_offset_steps() == 0
    assert hal.tick.diag_seq() == 0
    assert hal.tick.current_mode() == 0


def test_a_name_the_register_map_does_not_have_raises():
    """A missing KEY is a bug, not a comms failure. Folding it into the
    absent-snapshot path would turn a typo into a permanent, silent
    'communications problem' that no amount of good wiring would fix."""
    hal, _board_, _els = _hal(snapshot={"active": 1})
    with pytest.raises(KeyError):
        hal.tick._get("takeupSeq", 0)


# ─── 4. on-demand reads stay live ─────────────────────────────────────────

def test_on_demand_reads_ignore_the_snapshot_and_go_to_the_wire():
    """An apply, a wizard step, a calibration run: the operator has just acted
    and the answer must describe the machine NOW, not up to a tick ago. The
    snapshot here deliberately disagrees with the live values, so a reader that
    quietly took the cheap path is visible rather than merely unproven."""
    stale = {"enable": 0, "phaseOffsetSteps": 111, "calSeq": 0,
             "latchSeq": 0, "phaseOffsetSeq": 0, "active": 1}
    hal, _board_, els = _hal(snapshot=stale)

    assert hal.read_enable() is True            # live says 1, snapshot says 0
    assert hal.read_phase_offset_steps() == 999  # live 999, snapshot 111
    assert hal.read_cal_seq() == 4
    assert hal.read_latch_seq() == 2
    assert hal.read_phase_offset_seq() == 6
    assert hal.read_active() is False            # live 0, snapshot 1

    assert els.live_reads == ['enable', 'phaseOffsetSteps', 'calSeq',
                              'latchSeq', 'phaseOffsetSeq', 'active']


def test_a_disconnected_board_still_counts_a_live_read_as_fabricated():
    """Unchanged behaviour, pinned here because the snapshot path now shares
    _no_link with it -- a change to one must not quietly redefine the other."""
    hal, board, _els = _hal(connected=False)
    baseline = hal.reads_baseline()
    assert hal.read_enable() is False
    assert hal.reads_fabricated_since(baseline) is True
    assert board.connection_manager.read_failures == 1


# ─── array WRITES through the real register layout ─────────────────────────
# __setitem__ historically passed a whole list to the scalar write function,
# whose int() coercion raised, was swallowed by the write path's except, and
# surfaced only as connected=False: a silent no-op. The calMeasured
# round-trip (reconcile re-teaching the calibration legs) is the first array
# write in the codebase, so the element-wise path is pinned here against the
# REAL parsed layout, not a fake.

def test_array_write_goes_element_wise_at_type_strides(els_stop_device):
    device, _transport = els_stop_device
    var = device._variable_index["calMeasured"]
    writes = []
    original = var.type.write_function
    try:
        var.type.write_function = (
            lambda dm, addr, value, name="": writes.append((addr, value, name)))
        device["calMeasured"] = [365, 373, 366]
    finally:
        var.type.write_function = original

    base = var.address + device.base_address
    stride = var.type.length
    assert [(a, v) for a, v, _n in writes] == [
        (base, 365), (base + stride, 373), (base + 2 * stride, 366)]
    assert [n for _a, _v, n in writes] == [
        "calMeasured[0]", "calMeasured[1]", "calMeasured[2]"]


def test_array_write_never_exceeds_the_declared_count(els_stop_device):
    """A four-element list against calMeasured[3] writes three registers and
    stops -- the fourth would land on calCeilingSteps, silently."""
    device, _transport = els_stop_device
    var = device._variable_index["calMeasured"]
    writes = []
    original = var.type.write_function
    try:
        var.type.write_function = (
            lambda dm, addr, value, name="": writes.append(addr))
        device["calMeasured"] = [1, 2, 3, 4]
    finally:
        var.type.write_function = original

    assert len(writes) == 3


def test_scalar_writes_are_unchanged(els_stop_device):
    device, _transport = els_stop_device
    var = device._variable_index["calCommand"]
    writes = []
    original = var.type.write_function
    try:
        var.type.write_function = (
            lambda dm, addr, value, name="": writes.append((addr, value)))
        device["calCommand"] = 1
    finally:
        var.type.write_function = original

    assert writes == [(var.address + device.base_address, 1)]
