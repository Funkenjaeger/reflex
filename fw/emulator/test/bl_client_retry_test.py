#!/usr/bin/env python3
"""bl_client_retry_test.py -- the host client's retry, against a fake board
that drops frames on demand.

The point of this test is the ONE thing a retry layer can get quietly and
expensively wrong: re-running a command that already ran. The bootloader's
ERASE and APPLY are not idempotent, and the only thing standing between a
lost reply and a second APPLY is that modbus-flash.py reads blSeq back and
believes it. So the assertions here are about how many times the BOARD
executed something, not about whether the client returned without raising.

No pyserial, no hardware, no emulator: `serial` is stubbed before the client
is imported, and the board is 120 lines of Modbus RTU that counts what it did.

MUTATIONS (seen red 2026-09-07, 13 checks):
  * WRITE_ATTEMPTS = 1, i.e. retry off: 2 red, the lost-request cases.
  * Bootloader._commit's "blSeq advanced" branch disabled, so a lost reply
    falls through to the another-master guard: 3 red.
  * The whole blSeq reconciliation removed from _commit, so a lost reply is
    blindly resent: 3 red, and the failure is visible as the real hazard --
    the fake board reports blSeq 0 -> 2 and "executed 2x", meaning the APPLY
    ran twice. That is the one this file is for.
"""
import importlib.util
import os
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(HERE, "..", "..", "scripts", "modbus-flash.py")

failures = 0


def check(ok, label):
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        failures += 1


def guarded(mf, fn):
    """Run an operation that is expected to SUCCEED, and turn any failure into
    a string instead of letting it abort the run. A retry bug raises SystemExit
    from deep inside the client; if that propagated, this file would stop
    printing and a harness counting [FAIL] lines would see nothing wrong."""
    try:
        fn()
        return None
    except SystemExit as e:
        return f"SystemExit: {e}"
    except mf.ModbusError as e:
        return f"{type(e).__name__}: {e}"


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class Board:
    """A Modbus RTU bootloader that answers the control window and counts
    every command it actually executes."""

    BASE = 2304
    SEQ, RESULT, COMMAND = 1, 2, 3

    def __init__(self):
        self.regs = [0] * 116          # 16 head + 100 data
        self.executed = []             # one entry per command actually run
        self.received = 0              # frames that reached the board
        self.sent = 0                  # frames the board tried to answer
        self.drop_request = set()      # 1-based frame numbers to swallow whole
        self.drop_reply = set()        # ... to execute but not answer
        self.exception_on = set()      # ... to answer with exception 2

    def _reply(self, body: bytes) -> bytes:
        self.sent += 1
        if self.sent in self.drop_reply:
            return b""
        return body + struct.pack("<H", crc16(body))

    def handle(self, frame: bytes) -> bytes:
        if struct.unpack("<H", frame[-2:])[0] != crc16(frame[:-2]):
            return b""                                  # mangled on the wire
        self.received += 1
        if self.received in self.drop_request:
            return b""                                  # never arrived
        addr, fc = frame[0], frame[1]
        if self.received in self.exception_on:
            self.sent += 1
            body = bytes([addr, fc | 0x80, 2])
            return body + struct.pack("<H", crc16(body))

        if fc == 3:
            reg, count = struct.unpack(">HH", frame[2:6])
            off = reg - self.BASE
            vals = self.regs[off:off + count]
            body = bytes([addr, 3, 2 * count]) + struct.pack(f">{count}H", *vals)
            return self._reply(body)

        if fc == 6:
            reg, val = struct.unpack(">HH", frame[2:6])
            self._write(reg - self.BASE, [val])
            return self._reply(frame[:6])

        if fc == 16:
            reg, count = struct.unpack(">HH", frame[2:6])
            vals = list(struct.unpack(f">{count}H", frame[7:7 + 2 * count]))
            self._write(reg - self.BASE, vals)
            return self._reply(frame[:6])

        raise AssertionError(f"unexpected FC{fc}")

    def _write(self, off, vals):
        for i, v in enumerate(vals):
            self.regs[off + i] = v
        if off <= self.COMMAND < off + len(vals) and self.regs[self.COMMAND] != 0:
            # The bootloader consumes the command, does the work, bumps blSeq.
            self.executed.append((self.regs[self.COMMAND], list(self.regs[16:20])))
            self.regs[self.COMMAND] = 0
            self.regs[self.SEQ] = (self.regs[self.SEQ] + 1) & 0xFFFF
            self.regs[self.RESULT] = 0


class FakeSerial:
    def __init__(self, board):
        self.board = board
        self.buf = b""

    def reset_input_buffer(self):
        self.buf = b""

    def write(self, data):
        self.buf += self.board.handle(bytes(data))

    def flush(self):
        pass

    def read(self, n):
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def close(self):
        pass


def load_client():
    fake = types.ModuleType("serial")
    fake.Serial = lambda *a, **k: None
    sys.modules["serial"] = fake
    spec = importlib.util.spec_from_file_location("modbus_flash", CLIENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make(mf, board):
    bus = mf.Rtu("fake", 115200, 17)
    bus.ser = FakeSerial(board)
    return bus, mf.Bootloader(bus, dry_run=False)


CHUNK = bytes(range(200))


def main():
    mf = load_client()
    mf.RETRY_PAUSE = 0.0                     # do not sit through the backoff

    # --- clean run: no retry machinery fires ---------------------------------
    b = Board()
    bus, bl = make(mf, b)
    err = guarded(mf, lambda: bl.write_chunk(0x08040000, CHUNK, len(CHUNK), 0x1234))
    check(err is None and len(b.executed) == 1,
          f"clean link: the chunk write executes exactly once ({err or 'no error'})")
    check(bl.retries == 0 and bl.recovered == 0 and bus.retries == 0,
          "clean link: nothing is counted as a retry")

    # --- the request is lost: nothing ran, so resend -------------------------
    b = Board()
    b.drop_request = {2}                     # frame 1 is the blSeq read
    bus, bl = make(mf, b)
    err = guarded(mf, lambda: bl.write_chunk(0x08040000, CHUNK, len(CHUNK), 0x1234))
    check(err is None and len(b.executed) == 1,
          f"lost request: the chunk still lands, exactly once ({err or 'no error'})")
    check(bl.retries == 1, "lost request: counted as one resend")
    check(bl.recovered == 0, "lost request: not counted as a recovered reply")

    # --- the reply is lost: it DID run, so do not run it again ---------------
    b = Board()
    b.drop_reply = {2}
    bus, bl = make(mf, b)
    err = guarded(mf, lambda: bl.write_chunk(0x08040000, CHUNK, len(CHUNK), 0x1234))
    check(err is None and len(b.executed) == 1,
          f"lost reply: the command is NOT executed a second time ({err or 'no error'})")
    check(bl.recovered == 1 and bl.retries == 0,
          "lost reply: counted as recovered, not as a resend")

    # --- same, for a command that must never run twice -----------------------
    b = Board()
    b.drop_reply = {2}
    bus, bl = make(mf, b)
    err = guarded(mf, lambda: bl.command(mf.CMD_APPLY, "APPLY", timeout=0.05))
    check(err is None and b.executed == [(mf.CMD_APPLY, [0, 0, 0, 0])],
          f"lost reply to APPLY: applied once, not twice ({err or 'no error'}, "
          f"executed {len(b.executed)}x)")

    # --- a read is retried and succeeds --------------------------------------
    b = Board()
    b.drop_reply = {1}
    bus, bl = make(mf, b)
    head = [None] * 16
    err = guarded(mf, lambda: head.__setitem__(slice(None), bl.head()))
    check(err is None and head[mf.BL_SEQ] == 0 and bus.retries == 1,
          "a lost read reply is retried and the read succeeds")

    # --- the retry budget is bounded -----------------------------------------
    b = Board()
    b.drop_request = {2, 4, 6, 8, 10, 12, 14, 16}    # every command frame
    bus, bl = make(mf, b)
    try:
        bl.command(mf.CMD_ERASE, "ERASE", timeout=0.02)
        bounded = False
    except SystemExit as e:
        bounded = "attempts" in str(e)
    check(bounded, "a link that never answers gives up with an error, not a loop")
    check(len(b.executed) == 0, "...having executed nothing")

    # --- an exception response is an ANSWER, not a lost frame ----------------
    b = Board()
    b.exception_on = {1}
    bus, bl = make(mf, b)
    try:
        bl.head()
        raised = False
    except mf.ExceptionResponse:
        raised = True
    check(raised, "an exception response propagates")
    check(b.received == 1 and bus.retries == 0,
          "...on the first try, not after four (this is how the app is detected)")

    print("FAILURES" if failures else "all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
