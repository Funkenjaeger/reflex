"""The serial write's timeout covers the port, not the GIL.

pyserial 3.5 raises 'Write timeout' after os.write() has already handed the
frame to the kernel, when its timer ran out while the writing thread waited to
get the GIL back. With writes executed on the port owner's thread, one long C
call on the UI thread (loading kv files at start-up) was enough: the link was
reset over frames that had been sent. ConnectionManager.connect() replaces the
port's write with one that times only the wait for the port.

Linux/posix only: these tests drive a real pty through pyserial.
"""
import os
import random
import sys
import threading
import time

import pytest
import serial

from reflex.utils.communication import ConnectionManager

# timing: the first test holds the GIL for wall-clock stretches and the second
# bounds how long a stalled write takes to give up; both measure real time.
pytestmark = [
    pytest.mark.skipif(not hasattr(os, "openpty") or sys.platform == "win32",
                       reason="needs a posix pty"),
    pytest.mark.timing,
]

FRAME = bytes([0x11, 0x06, 0x00, 0x10, 0x00, 0x01, 0x4A, 0x9B])


def _connected_pty():
    master, slave = os.openpty()
    cm = ConnectionManager(serial_device=os.ttyname(slave))
    cm.connect()
    assert cm.device is not None, "connect() did not open the pty"
    return cm, master, slave


def _close(cm, master, slave):
    cm.disconnect()
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


def test_a_frame_the_port_took_is_not_a_timeout_while_another_thread_holds_the_gil():
    cm, master, slave = _connected_pty()
    port = cm.device.serial
    got = bytearray()
    stop = threading.Event()
    errors, sent = [], [0]

    def drain():
        os.set_blocking(master, False)
        while not stop.is_set():
            try:
                got.extend(os.read(master, 65536))
            except BlockingIOError:
                time.sleep(0.001)

    def writer():
        while not stop.is_set():
            try:
                port.write(FRAME)
                sent[0] += 1
            except serial.SerialTimeoutException as e:
                errors.append(e)
                sent[0] += 1

    threads = [threading.Thread(target=drain, daemon=True),
               threading.Thread(target=writer, daemon=True)]
    for t in threads:
        t.start()
    big = [random.random() for _ in range(2_000_000)]
    try:
        for _ in range(4):
            sorted(big)             # one C call: holds the GIL for its length
            time.sleep(0.02)        # let the writer run between holds
    finally:
        stop.set()
        for t in threads:
            t.join(2)
        try:
            os.set_blocking(master, False)
            got.extend(os.read(master, 65536))
        except (BlockingIOError, OSError):
            pass
        _close(cm, master, slave)

    assert sent[0] > 0, "the writer never ran"
    assert not errors, f"{len(errors)} of {sent[0]} writes raised 'Write timeout'"
    assert bytes(got).count(FRAME) == sent[0], "a frame went missing"


def test_a_port_that_stops_draining_still_times_out():
    cm, master, slave = _connected_pty()
    port = cm.device.serial
    chunk = b"\x00" * 1024
    try:
        with pytest.raises(serial.SerialTimeoutException):
            for _ in range(1000):          # nobody reads the master: it fills
                t0 = time.monotonic()
                port.write(chunk)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"the stalled write took {elapsed:.2f} s to give up"
    finally:
        _close(cm, master, slave)
