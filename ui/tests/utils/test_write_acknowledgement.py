"""Step 3: a write's acknowledgement is its own, and nothing can mask it.

`connection_manager.connected` used to be the answer to "did that write land".
It never meant that. It meant "the last exchange on this port succeeded", and
the two only agreed because nothing could run in between. Once the scan runs on
the owner's thread, a tick read landing between a write and the check sets it
True over a write that FAILED -- and the cut arming, which accumulates exactly
this verdict to decide whether to release a cut against the stop, would be told
the stop was armed when it was not.

So the verdict now travels on the write's own future. These cases pin the
properties that makes true:

  * a failed write reports False even when every exchange after it succeeds;
  * the fire-and-forget path does not wait, but still keeps its place in the
    queue, so order is what the code wrote;
  * an array is acknowledged only if EVERY element was;
  * a proxy that never gets an answer reports False rather than hanging the UI.
"""
import threading
import time
from types import SimpleNamespace

import minimalmodbus
import pytest

from reflex.utils import communication
from reflex.utils.communication import ConnectionManager
from reflex.utils.port_owner import PortOwner, Read, Write


class SelectiveInstrument:
    """A wire that fails only the exchanges a predicate picks out.

    The point is to make "this write failed, everything after it worked"
    reachable, which is the interleaving the old ACK channel could not survive.
    """

    def __init__(self):
        self.wire = []
        self.fail_write_to = set()
        self.serial = SimpleNamespace(close=lambda: None)

    def read_registers(self, registeraddress, number_of_registers, **kwargs):
        self.wire.append(("read_registers", registeraddress, number_of_registers))
        return [0] * number_of_registers

    def read_register(self, address, signed=False, **kwargs):
        self.wire.append(("read_register", address))
        return address

    def read_long(self, registeraddress, signed=False, **kwargs):
        self.wire.append(("read_long", registeraddress))
        return registeraddress

    def read_float(self, registeraddress, **kwargs):
        self.wire.append(("read_float", registeraddress))
        return float(registeraddress)

    # **kwargs because minimalmodbus's real signatures carry more than the
    # helpers' obvious arguments (write_long passes byteorder). A fake that is
    # strict about them raises TypeError INSIDE the write helper, which
    # swallows it -- so the exchange silently never happens and the test reads
    # as "the write was skipped" rather than "the fake is wrong".
    def write_register(self, address, value, signed=False, **kwargs):
        self._wrote("write_register", address, value)

    def write_long(self, registeraddress, value, signed=False, **kwargs):
        self._wrote("write_long", registeraddress, value)

    def write_float(self, registeraddress, value, **kwargs):
        self._wrote("write_float", registeraddress, value)

    def _wrote(self, kind, address, value):
        self.wire.append((kind, address, value))
        if address in self.fail_write_to:
            raise minimalmodbus.NoResponseError("the controller did not answer")


@pytest.fixture
def rig():
    cm = ConnectionManager(serial_device="/dev/null")
    instrument = SelectiveInstrument()
    cm.device = instrument
    owner = PortOwner(cm)              # synchronous, as a Board's own owner is
    cm._port_owner = owner
    return SimpleNamespace(cm=cm, owner=owner, instrument=instrument,
                           glob=cm['Global'])


def _write(address, value):
    return Write(communication.write_unsigned, address, value, f"r{address}")


# ── the verdict cannot be masked ─────────────────────────────────────────────

def test_a_failed_write_reports_false(rig):
    rig.instrument.fail_write_to = {7}
    assert rig.owner.call(_write(7, 1)) is False


def test_a_later_success_cannot_mask_an_earlier_failure(rig):
    """THE CASE THE WHOLE STEP EXISTS FOR. The old channel reported the last
    exchange, so the second write here would have said the first one was
    fine."""
    rig.instrument.fail_write_to = {7}
    failed = rig.owner.call(_write(7, 1))
    succeeded = rig.owner.call(_write(8, 1))
    assert failed is False, "the failed write did not report itself"
    assert succeeded is True
    # And the link object, the old channel, now says the OPPOSITE of the
    # verdict the caller needs -- which is precisely why it is no longer read.
    assert rig.cm.connected is True
    assert failed is not rig.cm.connected


def test_a_read_after_a_failed_write_also_recovers_connected(rig):
    """Same masking, through a read instead of a write: on the real board this
    is the tick scan, which runs 30 times a second."""
    rig.instrument.fail_write_to = {7}
    failed = rig.owner.call(_write(7, 1))
    rig.owner.call(Read(communication.read_unsigned, 9))
    assert failed is False
    assert rig.cm.connected is True, "the read recovered the link verdict"


# ── through BaseDevice, which is what the HAL uses ───────────────────────────

def test_write_confirmed_reports_the_controllers_answer(rig):
    els = rig.glob['elsStop']
    address = els.base_address + els._variable_index['enable'].address
    assert els.write_confirmed('enable', 1) is True
    rig.instrument.fail_write_to = {address}
    assert els.write_confirmed('enable', 0) is False


def test_an_array_is_acknowledged_only_if_every_element_was(rig):
    els = rig.glob['elsStop']
    # calMeasured is the array write the calibration round-trip needs; fail
    # one leg of it and the whole write must report unacknowledged.
    var = els._variable_index['calMeasured']
    second = els.base_address + var.address + var.type.length
    rig.instrument.fail_write_to = {second}
    assert els.write_confirmed('calMeasured', [1, 2, 3, 4]) is False, \
        "one unacknowledged element must fail the whole array"
    assert els.write_confirmed('calMeasured', [0, 0, 0, 0]) is False, \
        "the same leg still fails"
    rig.instrument.fail_write_to = set()
    assert els.write_confirmed('calMeasured', [1, 2, 3, 4]) is True


# ── fire and forget keeps its place in the queue ─────────────────────────────

def test_plain_assignment_does_not_wait_but_keeps_its_order(rig):
    """Order is the property the disengage sequence rests on. The queue is
    FIFO, so a fire-and-forget write still reaches the wire before anything
    issued after it."""
    els = rig.glob['elsStop']
    els['stopDirection'] = 1            # fire and forget
    els['hysteresis'] = 2               # fire and forget
    els.write_confirmed('enable', 1)    # waits
    rig.owner.call(Read(communication.read_unsigned, 9))

    kinds = [entry[0] for entry in rig.instrument.wire]
    assert len(kinds) == 4, f"not every exchange reached the wire: {rig.instrument.wire}"
    assert all(k.startswith("write_") for k in kinds[:3]), \
        f"the three writes did not come first: {rig.instrument.wire}"
    assert kinds[3] == "read_register", \
        f"the read did not come last: {rig.instrument.wire}"


def test_a_failed_fire_and_forget_write_still_reaches_the_wire(rig):
    """It is not confirmed, but it is not skipped either -- the exchange
    happens and the helper logs it, which is how these surface."""
    els = rig.glob['elsStop']
    address = els.base_address + els._variable_index['hysteresis'].address
    rig.instrument.fail_write_to = {address}
    els['hysteresis'] = 5
    assert any(entry[1] == address and entry[2] == 5
               for entry in rig.instrument.wire), \
        f"the failed write never reached the wire: {rig.instrument.wire}"


# ── an answer that never comes is not an acknowledgement ─────────────────────

def test_a_proxy_that_gets_no_answer_reports_false_rather_than_hanging():
    """A queued owner with nobody draining it: the UI must not wait for ever.
    Before the bound, a dead owner thread froze the Kivy thread permanently --
    a worse failure than a refused write, because the operator gets a locked
    screen instead of an alarm."""
    cm = ConnectionManager(serial_device="/dev/null")
    cm.device = SelectiveInstrument()
    owner = PortOwner(cm, synchronous=False)   # queued, and never run
    cm._port_owner = owner
    els = cm['Global']['elsStop']

    started = time.monotonic()
    acknowledged = els.write_confirmed('enable', 1)
    waited = time.monotonic() - started

    assert acknowledged is False, "a write nobody executed must not report True"
    assert waited < 10.0, f"the proxy waited {waited:.1f} s"


def test_the_proxy_timeout_is_configurable_and_short_when_asked():
    cm = ConnectionManager(serial_device="/dev/null")
    cm.device = SelectiveInstrument()
    owner = PortOwner(cm, synchronous=False)
    cm._port_owner = owner

    started = time.monotonic()
    with pytest.raises(Exception):
        owner.call(_write(7, 1), timeout_s=0.05)
    assert time.monotonic() - started < 2.0


def test_a_live_owner_answers_well_inside_the_bound():
    """The bound must not be reachable in normal running, or it would turn a
    slow link into a false refusal."""
    cm = ConnectionManager(serial_device="/dev/null")
    instrument = SelectiveInstrument()
    cm.device = instrument
    owner = PortOwner(cm, synchronous=False)
    cm._port_owner = owner
    owner.scan_device = cm['Global']
    sink = []
    owner.start(sink.append)
    try:
        els = cm['Global']['elsStop']
        started = time.monotonic()
        assert els.write_confirmed('enable', 1) is True
        assert time.monotonic() - started < 1.0
    finally:
        owner.stop()
