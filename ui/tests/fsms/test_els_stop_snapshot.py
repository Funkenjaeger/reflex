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
``ElsStopRegisters`` gained ``reads_baseline()`` / ``reads_fabricated_since()`` on
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
from reflex.fsms.els_stop_registers import ElsStopRegisters
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
    """One register block. `fail=True` makes refresh() raise like a bad frame;
    `decode_fail=True` makes the hot-group decode raise."""

    def __init__(self, values, fail: bool = False):
        self.values = dict(values)
        self.fail = fail
        self.decode_fail = False
        self.refresh_count = 0

    def refresh(self):
        """The whole-block read: what an on-demand elsStop caller uses. The
        tick no longer does (FakeDevice.read_tick)."""
        self.refresh_count += 1
        if self.fail:
            raise RuntimeError("no communication with the instrument (no answer)")
        return dict(self.values)

    def decode_hot(self, _raw):
        """The tick path's decode of the hot group. No I/O: the registers came
        with the tick's one request."""
        if self.decode_fail:
            raise ValueError("span is 55 registers, format wants 56 -- refusing to decode")
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
        self.tick_reads = 0
        self.tick_fail = False

    def __getitem__(self, name):
        return self.structs[name]

    def read_tick(self):
        """The board tick's ONE request (devices.Global.read_tick): fastData's
        values and the hot group's raw registers, or a raise like a bad frame.
        A single exchange carries both blocks, so it fails as a whole."""
        self.tick_reads += 1
        if self.tick_fail:
            raise RuntimeError("no communication with the instrument (no answer)")
        return dict(self.structs["fastData"].values), ["hot-raw"]


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


def _board(*, els_values=None):
    els = FakeStruct(els_values or {"active": 1, "takeupSeq": 7,
                                    "phaseOffsetSteps": 500, "diagSeq": 3,
                                    "machineMode": 5})
    fast = FakeStruct({"servoMode": 1})
    device = FakeDevice(elsStop=els, fastData=fast)
    cm = FakeConnectionManager(device=device)
    return BoardUnderTest(device, cm), els, device


class HalBoard:
    """The two attributes ElsStopRegisters reads a board through."""

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
    return ElsStopRegisters(board), board, els


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


def test_the_tick_reads_fast_data_and_the_hot_group_in_one_request():
    """THE POINT OF THE HOT/COLD SPLIT AND OF THE MERGED TICK READ.

    The tick reads the HOT group only, and reads it in the same request as
    fastData, which ends exactly where elsStop begins. So a board tick costs
    ONE exchange: it cost three before the split and two after it. Requests
    are the quantity that fails -- see the module docstring and the 2026-08-23
    comms loss -- so each step is a cut in the thing that actually breaks, not
    in bytes.

    Asserted as ONE request rather than "at most two": the moment it becomes
    two the merge has bought nothing and the failure should say so loudly, at
    the boundary, rather than showing up as a timeout during a cut.
    """
    from reflex.utils import devices, els_stop_map, fast_data_map
    cm = ConnectionManager(serial_device="/dev/null")
    cm.device = RecordingInstrument()

    fast, hot_raw = cm['Global'].read_tick()

    assert cm.device.requests == [(devices.TICK_BASE, devices.TICK_COUNT)], (
        f"the tick must be ONE request; got {cm.device.requests}")
    assert devices.TICK_BASE == fast_data_map.FAST_DATA_BASE == 74
    assert (fast_data_map.FAST_DATA_BASE + fast_data_map.TOTAL_REGISTERS
            == els_stop_map.ELS_STOP_BASE + els_stop_map.HOT_BASE == 104), (
        "fastData no longer ends where the hot group begins")
    assert len(hot_raw) == els_stop_map.HOT_COUNT
    assert set(fast) >= set(fast_data_map.ALL_FIELDS)


def test_the_whole_block_refresh_is_still_two_requests(els_stop_device):
    """The ON-DEMAND path, which still reads everything.

    142 registers at 72 a request: two requests (72 + 70), margin 2.

    2026-09-07 (protocolVersion 10): the block was split into a hot group read
    on the tick and a cold group read on demand, and the two hand-placed pads
    (machineModeReserved, stopTriggerReserved) became generator-emitted
    alignment, so the block is 142 registers -- 140 of content plus two pads.
    A whole-block read is no longer on the tick path, so this number governs
    only what an on-demand caller pays; the tick is guarded by
    test_the_hot_group_is_read_in_exactly_one_request above.

    History kept because the arithmetic is the point: the block was 122 when
    this case was written, 128 at the STEP pulse instrument (2026-08-25) which
    landed exactly on the old 2x64 boundary, 130 at bootCommand/bootSeq
    (2026-09-06), and 140 at the trigger snapshot (2026-09-07) -- which left
    FOUR registers of tail growth against an auto-start feature that needs ten,
    and is what forced the split.
    """
    device, transport = els_stop_device
    assert device.size == 142, (
        f"elsStop is {device.size} registers, not the 142 this case was "
        f"reasoned about -- re-check the chunk arithmetic, do not just "
        f"update the number")

    device.refresh()

    base = device.base_address
    assert len(transport.requests) == 2
    assert transport.requests == [(base, 72), (base + 72, 70)]


def test_the_block_still_fits_in_two_requests_with_room_to_spare(els_stop_device):
    """The boundary, asserted rather than left in a comment: a block that
    quietly grew past 2 x MAX_REGISTERS_PER_READ would cost a third request on
    every one of 30 ticks a second, and nothing else in the suite would notice.

    The ceiling is 2 x 72 = 144 and the block is 140 as of protocolVersion 9,
    so there are FOUR registers of slack -- this is a live constraint on the
    next append, not a formality."""
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

    THIS GOVERNS THE refresh() CHUNK ONLY. The largest request on the wire is
    no longer a chunk but the board tick's merged read, which deliberately
    sits past this 60% line; its own margin is pinned in
    test_the_tick_read_keeps_its_stated_margin_against_the_firmware below.
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
    # 72 of 125: 53 registers and 106 buffer bytes in hand. It must also still
    # be worth doing -- below ceil(size/2) the elsStop block needs three
    # requests instead of two, which is the entire reason the number went up.
    # Derived from the live block size rather than hard-coded: the floor was 61
    # at 122 registers, 65 at 130, and 70 at 140; a stale literal here would
    # stop being the floor the moment the block grew again.
    #
    # 2026-09-07: protocolVersion 10 split the block into a HOT group read on
    # the tick and a COLD group read on demand, and the block became 142
    # registers (140 of content plus two generator-emitted alignment pads),
    # making the floor 71. This assertion now governs only the WHOLE-BLOCK
    # refresh() -- the tick path reads HOT_COUNT registers in ONE request and
    # is guarded by test_the_hot_group_is_one_request instead. The chunk still
    # matters because an on-demand whole-block read paying three requests
    # instead of two is still worth not doing by accident.
    from reflex.utils.communication import ConnectionManager
    size = ConnectionManager(serial_device="/dev/null")['Global']['elsStop'].size
    floor = -(-size // 2)          # ceil, so two requests still cover the block
    assert floor == 71, (
        f"the two-request floor is now {floor}, not the 71 that 142 registers "
        f"gives -- re-derive the chunk size rather than editing this number")
    assert n >= floor, (
        f"chunking at {n} registers puts elsStop ({size} registers) back above "
        f"two requests")


def test_the_tick_read_keeps_its_stated_margin_against_the_firmware():
    """The merged tick read, checked against the same firmware arithmetic.

    fastData (30) plus the elsStop hot group (56) is 86 registers in one FC3
    request, a reply of 5 + 2*86 = 177 bytes. That is 69% of the 125-register
    ceiling and 69% of the 256-byte buffer: PAST the 60% this file holds the
    refresh() chunk to, by decision rather than by drift. One request per tick
    instead of two was judged worth nine points of headroom, because requests
    are what time out when the ISR is saturated, and 39 registers (79 buffer
    bytes) still stand between this read and the cliff where every failure is
    silent.

    So the pin is exact. The size is asserted, not bounded, so that the next
    hot field -- which grows this read with it -- fails here and gets the
    margin re-decided, instead of eroding it one register at a time. 70% is
    the line the decision accepted; the read is not allowed to pass it without
    another one.
    """
    from reflex.utils import devices
    ceiling = 125                       # see the chunk test above for the derivation
    buffer_bytes = 256                  # MAX_BUFFER, fw/Core/Inc/ModbusConfig.h

    n = devices.TICK_COUNT
    assert n == 86, (
        f"the tick read is {n} registers, not the 86 this margin was decided "
        f"at -- re-decide it against the ceiling, do not just edit the number")
    reply = 5 + 2 * n
    assert reply == 177
    assert reply <= buffer_bytes and n <= ceiling
    assert n <= 0.70 * ceiling, (
        f"the tick read is {n / ceiling:.0%} of the FC3 ceiling, past the 70% "
        f"that was accepted for it")
    assert reply <= 0.70 * buffer_bytes, (
        f"the tick reply is {reply} of {buffer_bytes} buffer bytes, past 70%")
    # read_span() refuses anything larger than the tick, so the tick is the
    # largest request anything here makes.
    assert BaseDevice.MAX_REGISTERS_PER_SPAN == n
    assert BaseDevice.MAX_REGISTERS_PER_READ <= n


# ─── 2. the board takes the snapshot ──────────────────────────────────────

def test_update_publishes_one_els_stop_snapshot_per_tick():
    board, els, device = _board()
    board.update()          # the connect tick, which also checks the protocol
    device.tick_reads = 0

    board.update()
    board.update()

    assert device.tick_reads == 2, "one tick read per tick, no more and no less"
    assert els.refresh_count == 0, "the tick must not refresh the whole block"
    assert board.els_stop_values["takeupSeq"] == 7
    assert els.live_reads == [], (
        "the snapshot must not be assembled out of per-field live reads")


def test_the_connect_tick_does_not_read_the_block_twice():
    """The protocol-version check runs on the tick a connection comes up and
    wants the same registers the snapshot just read. Reading them again
    doubled the cost of the one tick where the link is least established."""
    board, els, device = _board(
        els_values={"protocolVersion": 5, "takeupSeq": 0})
    board.update()
    assert device.tick_reads == 1
    assert els.refresh_count == 0
    assert board.protocol_version == 5


def test_the_snapshot_is_in_place_before_the_pollers_run():
    """Ordering, and it is load-bearing: every poller is bound to update_tick,
    so a snapshot taken after the tick was bumped would serve the PREVIOUS
    tick's registers to everything that reads it."""
    board, _els, _device = _board()
    board.update()
    assert board.ticks_seen_with_snapshot == [{"active": 1, "takeupSeq": 7,
                                               "phaseOffsetSteps": 500,
                                               "diagSeq": 3, "machineMode": 5}]


def test_a_failed_refresh_clears_the_snapshot_rather_than_keeping_the_old_one():
    """THE CASE THIS DESIGN TURNS ON. Serving the previous tick's values after
    a failed read would hand every poller a plausible number with nothing
    saying it was stale -- a fabricated read that no guard can see. Both ways
    the snapshot can fail now: the tick's one request, and the hot decode."""
    board, els, device = _board()
    board.update()
    assert board.els_stop_values, "fixture precondition: a good snapshot first"

    device.tick_fail = True
    board.update()
    assert board.els_stop_values == {}

    board.connection_manager.device = device    # the failure disconnected it
    device.tick_fail = False
    board.update()
    assert board.els_stop_values, "precondition: a good snapshot again"

    els.decode_fail = True
    board.update()
    assert board.els_stop_values == {}
    assert board.ticks_seen_with_snapshot[-1] == {}


def test_a_hot_group_that_fails_to_decode_does_not_tear_down_the_link():
    """The tick read owns the connection verdict. Dropping the link over a
    hot group that would not decode would take the DRO down with it for no
    reason -- the policy a failed elsStop READ had when it was its own
    request, kept for the one elsStop failure that can still happen alone."""
    board, els, _device = _board()
    els.decode_fail = True
    board.update()
    assert board.connected is True
    assert board.connection_manager.disconnects == 0
    assert board.fast_data_values == {"servoMode": 1}, "fastData still lands"
    assert board.connection_manager.errors, "the failure must still be logged"


def test_a_failed_tick_read_is_handled_as_a_failed_fast_data_read():
    """One request carries both blocks, so its failure takes the path a
    failed fastData read always took, and every consumer sees what it saw
    then: link torn down, a one-second retry, fast_data_values left as it was
    (that path never cleared it), and the elsStop snapshot CLEARED -- that
    path returns before the snapshot is published, so without the explicit
    clear the pollers would read one from before the link dropped, on a tick
    where update_tick still fires."""
    board, _els, device = _board()
    board.update()
    assert board.els_stop_values
    before = dict(board.fast_data_values)

    device.tick_fail = True
    board.update()

    assert board.els_stop_values == {}
    assert board.connected is False
    assert board.connection_manager.disconnects == 1
    assert board.task_update.timeout == 1.0
    assert board.fast_data_values == before
    assert board.connection_manager.errors, "the failure must still be logged"


def test_no_device_clears_the_snapshot():
    board, _els, _device = _board()
    board.update()
    assert board.els_stop_values

    board.connection_manager.device = None
    board.update()

    assert board.els_stop_values == {}


# ─── 2b. the tick read, through the REAL register layout ──────────────────

class AddressEchoInstrument(RecordingInstrument):
    """Answers every register with its own address. Distinct values per
    register make a mis-sliced span visible as a wrong field, and small values
    keep every float a finite denormal, so decoded dicts compare with ==."""

    def read_registers(self, registeraddress, number_of_registers, **_kw):
        self.requests.append((registeraddress, number_of_registers))
        return list(range(registeraddress, registeraddress + number_of_registers))


def _real_board():
    cm = ConnectionManager(serial_device="/dev/null")
    cm.device = AddressEchoInstrument()
    board = BoardUnderTest(cm['Global'], cm)
    return board, cm


def test_a_board_tick_is_one_request_spanning_both_blocks():
    """fastData (74-103) and the elsStop hot group (104-159) are adjacent in
    rampsSharedData_t, and the firmware serves FC3 from that one struct
    (process_FC3 over u16regs), so the tick reads them as ONE 86-register
    request. It used to be two; requests, not bytes, are what time out."""
    board, cm = _real_board()
    board.update()
    assert cm.device.requests == [(74, 86)], (
        f"a board tick must be ONE request; got {cm.device.requests}")


def test_the_merged_read_fills_both_dicts_exactly_as_the_two_reads_did():
    """Same registers, same decoders, so every consumer sees the same dicts:
    fastData through BaseDevice's typedef decode (pad field included), the hot
    group through the generated decoder."""
    from reflex.utils import els_stop_map
    board, cm = _real_board()
    board.update()

    expected_fast = cm['Global']['fastData'].refresh()       # the old tick read
    expected_hot = els_stop_map.decode_hot(
        list(range(104, 104 + els_stop_map.HOT_COUNT)))
    assert board.fast_data_values == expected_fast
    assert board.els_stop_values == expected_hot
    assert board.fast_data_values['servoMode'] == 74 + 28    # spot-check the slice
    assert board.els_stop_values['enable'] == 104


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
