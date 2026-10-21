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


# ── the read twin ────────────────────────────────────────────────────────────
# pyserial's read checks its timer after every os.read(); a reader that got
# the GIL back late returned part of a frame that had already arrived (a
# 177-byte tick reply read as 112 on the lathe). The bytes here come from a
# separate PROCESS, so they keep arriving while this process's GIL is held.

TICK = bytes(range(177))
# Each frame in two parts 40 ms apart, as a reply still on the wire is: a
# reader starved between the parts is the case pyserial gets wrong (its timer
# runs out while it waits for the GIL, and it returns the first part alone).
FEEDER = (
    "import os, sys, time\n"
    "fd = int(sys.argv[1]); frame = bytes(range(177)); n = int(sys.argv[2])\n"
    "for _ in range(n):\n"
    "    os.write(fd, frame[:112]); time.sleep(0.04)\n"
    "    os.write(fd, frame[112:]); time.sleep(0.26)\n"
)


def _a_list_that_sorts_in(seconds):
    probe = [random.random() for _ in range(200_000)]
    t0 = time.monotonic()
    sorted(probe)
    per_item = (time.monotonic() - t0) / len(probe)
    return [random.random() for _ in range(max(200_000, int(seconds / per_item)))]


def test_a_frame_that_arrived_is_read_whole_while_another_thread_holds_the_gil():
    import subprocess
    cm, master, slave = _connected_pty()
    port = cm.device.serial
    frames = 20
    feeder = subprocess.Popen([sys.executable, "-c", FEEDER, str(master), str(frames)],
                              pass_fds=(master,))
    reads, done = [], threading.Event()

    def reader():
        while len(reads) < frames and not done.is_set():
            got = port.read(len(TICK))
            if got:
                reads.append(got)
        done.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    big = _a_list_that_sorts_in(0.12)
    try:
        deadline = time.monotonic() + 30
        while not done.is_set() and time.monotonic() < deadline:
            # Back to back, each one C call holding the GIL ~120 ms: past the
            # 100 ms timeout, and short enough that the reader, running only at
            # the switch between two sorts, is often between a frame's parts.
            sorted(big)
    finally:
        done.set()
        t.join(2)
        feeder.wait(10)
        _close(cm, master, slave)

    short = [len(r) for r in reads if r != TICK]
    assert reads, "the reader never got a byte"
    assert not short, f"{len(short)} of {len(reads)} reads were not a whole frame: {short[:5]}"


def test_a_silent_port_still_gives_up_after_the_timeout():
    cm, master, slave = _connected_pty()
    port = cm.device.serial
    try:
        t0 = time.monotonic()
        got = port.read(len(TICK))
        elapsed = time.monotonic() - t0
    finally:
        _close(cm, master, slave)
    assert got == b""
    assert 0.08 <= elapsed < 0.5, f"a silent read took {elapsed:.3f} s"
