#!/usr/bin/env python3
"""modbus-flash-recovery-test.py -- the flasher's resync/resume and its
return to the old application, driven end to end against a fake board.

WHY (Open Loops 6aae7131, 6aae7135). 2026-09-19 07:12 on the lathe: a
45172-byte, 226-chunk transfer lost the WRITE at 0x08043840 four times
running ("blSeq never moved from 73") while every status read between the
attempts was answered, and modbus-flash.py exited 1 with the board PARKED IN
THE BOOTLOADER -- run slot untouched, old application intact, a dead DRO for
anyone without a terminal. The in-app updater runs the same script.

WHAT IS REAL HERE: the whole of modbus-flash.py's flash() -- identity, the
enter-bootloader request, ERASE / WRITE x226 / VERIFY / APPLY / JUMP, the
retry layer, stream()'s resync and resume, return_to_app(), the manifest
record. WHAT IS FAKE: the serial port and the board behind it (Lathe below:
a bootloader that keeps blSeq, programs a staging slot with flash semantics
-- bits only clear -- validates it with reflex_image.validate, and applies it),
plus the clock: time.monotonic/time.sleep are virtual and a read with nothing
to read costs its 50 ms, so the real budgets (TRANSFER_BUDGET_S,
RESYNC_WAIT_S, APP_START_WAIT_S ...) are exercised as written and the whole
file still runs in seconds.

The board can be told to DROP a request (it never arrives, nothing runs),
to MUTE a reply (the command runs, the answer is lost), to answer only every
other frame, to lose every FC16 frame, to go silent for a while after the
transfer fails (2026-09-23), or to die outright. It can have the bench
bootloader's diagnostics window at 2420 or, like every field bootloader,
answer exception 2 there. The bench tools --link-probe and --inject are
driven against it too, on scripted answer patterns.

    python3 scripts/lib/modbus-flash-recovery-test.py [--client PATH] [--only NAME]

--client points at another copy of modbus-flash.py (seen-red: see
modbus-flash-recovery-seen-red.py beside this file). Exit 0 all passed,
1 otherwise. Stdlib only.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import types
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import reflex_image as ri  # noqa: E402

OLD_REV = 0x43AC7C5       # what the lathe ran on 2026-09-19
NEW_REV = 0x8483D77
BL_REV = 0x0B1C0DE
IMAGE_LEN = 45172         # the image that failed: 226 chunks, the last 172 bytes
PROTOCOL = 11
BOOT_REG = 168

failures = 0
failed_labels: list[str] = []


def check(ok, label):
    global failures
    label = label.replace("\n", " ")
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        failures += 1
        failed_labels.append(label)


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def make_image(rev=NEW_REV) -> bytes:
    buf = bytearray(IMAGE_LEN)
    for i in range(IMAGE_LEN):
        buf[i] = (i * 7 + (i >> 8)) & 0xFF
    struct.pack_into(ri._HEADER_FMT, buf, ri.IMAGE_HEADER_OFFSET, ri.IMAGE_MAGIC,
                     ri.IMAGE_HEADER_VERSION, 0, 0, 0, rev, 0, 0, 0)
    data = ri.patch(bytes(buf))
    ri.validate(data)
    return data


class Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, dt):
        self.t += max(0.0, dt)


class Info:
    """What one request frame is, for the fault hooks and the log."""

    def __init__(self, fc, reg, kind, cmd=None, addr=None):
        self.fc, self.reg, self.kind, self.cmd, self.addr = fc, reg, kind, cmd, addr
        self.chunk = None if addr is None else (addr - ri.STAGING_SLOT_BASE) // 200


# bootloader register offsets, as the client names them
STATUS, SEQ, RESULT, COMMAND, TLO, THI, LLO, LHI, CLO, CHI, SLOT, ACTIVE, WLEN, ATT, COPY, RUNV = range(16)
DATA = 16
CMD_NAMES = {1: "ERASE", 2: "WRITE", 3: "VERIFY", 4: "APPLY", 5: "JUMP", 6: "STAY", 7: "REVERT"}


class Lathe:
    """The board: an application that answers the identity window and the
    bootCommand register, and a bootloader behind it (bl_core.c's command
    set, minus REVERT, which this file does not drive)."""

    def __init__(self, clock, *, stage=2, run_valid=1):
        self.clock = clock
        self.stage = stage            # 2 application, 1 bootloader, 0 silent (dead)
        self.run_rev = OLD_REV
        self.run_image = b"old"
        self.backup_rev = None
        self.regs = [0] * 116
        self.regs[SLOT] = 1
        # Kept OUTSIDE the registers and published into them, as bl_core.c's
        # blCorePublish does before every read and after every command: the
        # host's WRITE frame spans registers 3..115 and writes zeros over
        # activeSlot/attempts/copyState/runValid every time.
        self.run_valid = run_valid
        self.copy_state = 0
        self.publish()
        self.staging = bytearray(b"\xff" * ri.SLOT_SIZE)
        self.staged_ok = False
        self.frames = 0
        self.log = []                 # (frame, kind, chunk, verdict)
        self.executed = []            # (command name, address or None)
        self.faults = []              # callables(board, Info) -> None | "drop" | "mute"
        self.jump_works = True        # False: JUMP runs, but the board comes back to the bootloader
        self.new_app_dead = False     # True: the NEW image never answers once jumped to
        # The bench bootloader's link counters at 2420 (framesTaken, crcErrors,
        # badFrames, ...). None = a field bootloader of 2026-09-23, which has
        # no such window and answers a read there with exception 2.
        self.diag = None

    # -- wire ------------------------------------------------------------------
    def handle(self, frame: bytes) -> bytes:
        if len(frame) < 4 or struct.unpack("<H", frame[-2:])[0] != crc16(frame[:-2]):
            if self.diag is not None and self.stage == 1:
                self.diag[1] += 1
            return b""
        self.frames += 1
        info = self.classify(frame)
        verdict = None
        for f in self.faults:
            verdict = f(self, info)
            if verdict:
                break
        if self.stage == 0:
            verdict = "drop"
        self.log.append((self.frames, info.kind, info.chunk, verdict))
        if verdict == "drop":
            return b""
        if self.diag is not None and self.stage == 1:
            self.diag[0] = (self.diag[0] + 1) & 0xFFFF
        if verdict == "exc":
            return self._exc(info.fc)
        reply = self.process(frame, info)
        return b"" if verdict == "mute" else reply

    def classify(self, frame) -> Info:
        fc = frame[1]
        reg = struct.unpack(">H", frame[2:4])[0]
        if fc == 3:
            return Info(fc, reg, "id" if reg == 2048 else "read")
        if fc == 6:
            val = struct.unpack(">H", frame[4:6])[0]
            if reg == 2304 + COMMAND:
                return Info(fc, reg, "cmd", cmd=val)
            return Info(fc, reg, "boot" if reg == BOOT_REG else "param")
        if fc == 16:
            count = struct.unpack(">H", frame[4:6])[0]
            vals = struct.unpack(f">{count}H", frame[7:7 + 2 * count])
            if reg == 2304 + COMMAND and vals[0] == 2:
                return Info(fc, reg, "write", cmd=2, addr=vals[1] | (vals[2] << 16))
            if reg == 2304 + COMMAND:
                return Info(fc, reg, "cmd", cmd=vals[0])
            return Info(fc, reg, "param")
        return Info(fc, reg, "other")

    def publish(self):
        self.regs[ACTIVE] = 0
        self.regs[ATT] = 0
        self.regs[COPY] = self.copy_state
        self.regs[RUNV] = self.run_valid

    def _ok(self, body):
        return body + struct.pack("<H", crc16(body))

    def _exc(self, fc, code=2):
        return self._ok(bytes([17, fc | 0x80, code]))

    def identity(self):
        if self.stage == 2:
            rev, proto = self.run_rev, PROTOCOL
        else:
            rev, proto = BL_REV, 0
        return [0x454C, self.stage, 1, rev & 0xFFFF, rev >> 16, 0, proto, 0]

    def process(self, frame, info) -> bytes:
        fc, reg = info.fc, info.reg
        if fc == 3:
            count = struct.unpack(">H", frame[4:6])[0]
            if reg == 2048:
                vals = self.identity()[:count]
            elif self.stage == 1 and self.diag is not None and reg == 2420 and count <= len(self.diag):
                vals = self.diag[:count]
            elif self.stage == 1 and 2304 <= reg and reg + count <= 2304 + 116:
                self.publish()
                vals = self.regs[reg - 2304:reg - 2304 + count]
            else:
                return self._exc(fc)
            return self._ok(bytes([17, 3, 2 * count]) + struct.pack(f">{count}H", *vals))
        if self.stage == 2:
            if fc == 6 and reg == BOOT_REG and struct.unpack(">H", frame[4:6])[0] == 1:
                self.enter_bootloader()
                return self._ok(frame[:6])
            return self._exc(fc)
        if fc == 6:
            vals = [struct.unpack(">H", frame[4:6])[0]]
        elif fc == 16:
            count = struct.unpack(">H", frame[4:6])[0]
            vals = list(struct.unpack(f">{count}H", frame[7:7 + 2 * count]))
        else:
            return self._exc(fc, 1)
        off = reg - 2304
        if off < 0 or off + len(vals) > 116:
            return self._exc(fc)
        self.regs[off:off + len(vals)] = vals
        jumped = self.service()
        reply = self._ok(frame[:6])
        if jumped:
            self.leave()
        return reply

    def enter_bootloader(self):
        self.stage = 1
        self.regs[STATUS] = 0
        self.regs[SEQ] = 0
        self.regs[RESULT] = 0
        self.regs[COMMAND] = 0
        self.staged_ok = False

    def leave(self):
        if not self.jump_works:
            self.enter_bootloader()           # it started, crashed, and the bootloader is back
        elif self.new_app_dead and self.run_rev == NEW_REV:
            self.stage = 0
        else:
            self.stage = 2

    # -- the bootloader's command service (bl_core.c blCoreService) -----------
    def service(self) -> bool:
        cmd = self.regs[COMMAND]
        if cmd == 0:
            return False
        self.regs[COMMAND] = 0
        addr = None
        jumped = False
        if cmd == 1:
            result = 2 if self.regs[SLOT] != 1 else 0
            if result == 0:
                self.staging[:] = b"\xff" * ri.SLOT_SIZE
                self.staged_ok = False
        elif cmd == 2:
            addr = self.regs[TLO] | (self.regs[THI] << 16)
            n = self.regs[WLEN]
            words = self.regs[DATA:DATA + n // 2]
            data = struct.pack(f"<{n // 2}H", *words)
            o = addr - ri.STAGING_SLOT_BASE
            self.staged_ok = False
            if n == 0 or n > 200 or n % 4 or o < 0 or o + n > ri.SLOT_SIZE:
                result = 3
            else:
                # flash programming clears bits and never sets them
                self.staging[o:o + n] = bytes(a & b for a, b in zip(self.staging[o:o + n], data))
                result = 0 if bytes(self.staging[o:o + n]) == data else 7
        elif cmd == 3:
            length = self.regs[LLO] | (self.regs[LHI] << 16)
            crc = self.regs[CLO] | (self.regs[CHI] << 16)
            try:
                hdr = ri.validate(bytes(self.staging[:length]))
                result = 0 if (hdr.image_length, hdr.crc32) == (length, crc) else 13
            except ValueError:
                result = 11
            self.staged_ok = result == 0
        elif cmd == 4:
            if not self.staged_ok:
                result = 14
            else:
                length = self.regs[LLO] | (self.regs[LHI] << 16)
                self.backup_rev = self.run_rev
                self.run_image = bytes(self.staging[:length])
                self.run_rev = ri.validate(self.run_image).build_rev
                self.copy_state = 3            # TRIAL
                self.run_valid = 1
                self.staged_ok = False
                result = 0
        elif cmd == 5:
            if self.run_valid != 1:
                result = 15
            else:
                result = 0
                jumped = True
        else:
            result = 1
        self.executed.append((CMD_NAMES.get(cmd, str(cmd)), addr))
        if cmd == 3 and result != 0:
            self.regs[STATUS] = 4
        elif self.staged_ok:
            self.regs[STATUS] = 5
        elif self.copy_state == 3 and self.run_valid:
            self.regs[STATUS] = 7
        else:
            self.regs[STATUS] = 0
        self.regs[RESULT] = result             # result before seq, as the firmware does
        self.regs[SEQ] = (self.regs[SEQ] + 1) & 0xFFFF
        self.publish()
        return jumped

    # -- what the checks ask -----------------------------------------------------
    def commands(self):
        return [c for c, _ in self.executed]

    def writes_per_chunk(self):
        n = {}
        for c, a in self.executed:
            if c == "WRITE":
                k = (a - ri.STAGING_SLOT_BASE) // 200
                n[k] = n.get(k, 0) + 1
        return n


# --- faults -----------------------------------------------------------------------

def drop_writes(counts: dict[int, int]):
    """The next N WRITE requests for chunk k never arrive."""
    left = dict(counts)

    def f(b, i):
        if i.kind == "write" and left.get(i.chunk, 0) > 0:
            left[i.chunk] -= 1
            return "drop"
    return f


def ran_then_blackout(chunk: int, frames: int):
    """Chunk k's WRITE RUNS but its reply is lost, and the next `frames`
    frames of any kind are lost with it -- the reconciling blSeq reads too."""
    s = {"armed": True, "left": 0}

    def f(b, i):
        if s["left"] > 0:
            s["left"] -= 1
            return "drop"
        if s["armed"] and i.kind == "write" and i.chunk == chunk:
            s["armed"] = False
            s["left"] = frames
            return "mute"
    return f


def blackout_seconds(chunk: int, seconds: float):
    """From chunk k's first WRITE, nothing arrives for `seconds`."""
    s = {"until": None}

    def f(b, i):
        if s["until"] is None and i.kind == "write" and i.chunk == chunk:
            s["until"] = b.clock.t + seconds
        if s["until"] is not None and b.clock.t < s["until"]:
            return "drop"
    return f


def alternate(chunk: int, lost: str = "drop"):
    """From chunk k's first WRITE on, every other frame is lost -- the state
    the bootloader was left in on 2026-09-19. `lost` = "drop" (the frame never
    arrives) or "mute" (it runs, the reply is lost)."""
    s = {"on": False, "n": 0}

    def f(b, i):
        if not s["on"] and i.kind == "write" and i.chunk == chunk:
            s["on"] = True
        if s["on"]:
            s["n"] += 1
            if s["n"] % 2 == 1:
                return lost
    return f


def fc16_dead(chunk: int):
    """From chunk k's first WRITE on, no FC16 frame gets through (the 235-byte
    frames die, the 8-byte ones survive)."""
    s = {"on": False}

    def f(b, i):
        if i.kind == "write" and i.chunk == chunk:
            s["on"] = True
        if s["on"] and i.fc == 16:
            return "drop"
    return f


def dead_from(chunk: int):
    """From chunk k's first WRITE on, nothing arrives, ever."""
    s = {"on": False}

    def f(b, i):
        if i.kind == "write" and i.chunk == chunk:
            s["on"] = True
        if s["on"]:
            return "drop"
    return f


def dead_after_command(cmd: int):
    """The command runs, its reply is lost, and nothing arrives after it."""
    s = {"on": False}

    def f(b, i):
        if s["on"]:
            return "drop"
        if i.kind == "cmd" and i.cmd == cmd and b.stage == 1:
            s["on"] = True
            return "mute"
    return f


def drop_command(cmd: int):
    """Every request carrying this command is lost; everything else is fine."""
    def f(b, i):
        if i.kind == "cmd" and i.cmd == cmd:
            return "drop"
    return f


def board_resets_at(chunk: int):
    """At chunk k the bootloader resets (IWDG); with a valid run slot it
    starts the application by itself. The WRITE is lost with it."""
    def f(b, i):
        if i.kind == "write" and i.chunk == chunk and b.stage == 1:
            b.stage = 2
            return "drop"
    return f


# --- plumbing -----------------------------------------------------------------------

class FakeSerial:
    def __init__(self, board, clock):
        self.board, self.clock, self.buf = board, clock, b""
        self.sent = []                            # every write, as bytes, in order

    def reset_input_buffer(self):
        self.buf = b""

    def write(self, data):
        self.sent.append(bytes(data))
        self.clock.t += len(data) * 10 / 115200
        self.buf += self.board.handle(bytes(data))

    def flush(self):
        pass

    def read(self, n):
        if not self.buf:
            self.clock.t += 0.05                  # the port's own read timeout
            return b""
        out, self.buf = self.buf[:n], self.buf[n:]
        self.clock.t += len(out) * 10 / 115200
        return out

    def close(self):
        pass


def load_client(path: Path):
    fake = types.ModuleType("serial")
    fake.Serial = lambda *a, **k: None
    sys.modules["serial"] = fake
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("modbus_flash_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Run:
    def __init__(self, mf, board, clock, image_path, manifest):
        self.board, self.clock = board, clock
        bus = mf.Rtu("fake", 115200, 17)
        bus.ser = FakeSerial(board, clock)
        out = io.StringIO()
        t0 = clock.t
        self.exit_msg = None
        with redirect_stdout(out):
            try:
                self.rc = mf.flash(bus, str(image_path), False, manifest=str(manifest),
                                   variant="release", tag="v9.9.9")
            except SystemExit as e:
                self.rc = e.code if isinstance(e.code, int) else 1
                self.exit_msg = str(e.code)
            except mf.ModbusError as e:
                # Escaped as a traceback on the real script: never a verdict.
                # Kept as a result so the scenario's checks go red, not the
                # whole run (seen-red needs [FAIL] lines to count).
                self.rc = 99
                self.exit_msg = f"ESCAPED {type(e).__name__}: {e}"
        self.elapsed = clock.t - t0
        self.out = out.getvalue()
        self.everything = self.out + "\n" + (self.exit_msg or "")
        self.records = []
        if os.path.exists(manifest):
            with open(manifest) as f:
                self.records = [json.loads(l) for l in f.read().splitlines() if l.strip()]


def scenario(mf, image, tmp, name, setup, *, stage=2, run_valid=1):
    clock = Clock()
    mf.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
    board = Lathe(clock, stage=stage, run_valid=run_valid)
    setup(board)
    img = Path(tmp) / f"{name}.bin"
    img.write_bytes(image)
    return Run(mf, board, clock, img, Path(tmp) / name / "flashed.json")


def no_success(r):
    return "VERDICT: OK" not in r.everything


N_CHUNKS = (IMAGE_LEN + 199) // 200


# --- the scenarios -------------------------------------------------------------------

def s_clean(mf, image, tmp):
    """(e) A clean transfer is what it always was: same frames, same output."""
    r = scenario(mf, image, tmp, "clean", lambda b: None)
    tag = "clean"
    check(r.rc == 0 and "VERDICT: OK -- application 8483d77 is running" in r.out,
          f"{tag}: succeeds with the usual verdict (rc {r.rc}; {r.exit_msg})")
    check(r.board.commands() == ["ERASE"] + ["WRITE"] * N_CHUNKS + ["VERIFY", "APPLY", "JUMP"],
          f"{tag}: exactly ERASE, {N_CHUNKS} WRITEs, VERIFY, APPLY, JUMP")
    check(r.board.writes_per_chunk() == {k: 1 for k in range(N_CHUNKS)},
          f"{tag}: every chunk written once, in order")
    stream = [k for _, k, c, _ in r.board.log if c is not None or k == "read"]
    first = stream.index("write") - 1
    check(stream[first:first + 3 * N_CHUNKS] == ["read", "write", "read"] * N_CHUNKS,
          f"{tag}: per chunk exactly blSeq read, WRITE, result read -- no pad reads, no resends")
    check("resync" not in r.out and r.out.count("  link: ") == 1,
          f"{tag}: no resync narration; the one link line it always printed")
    check("  link: 0 read retries, 0 commands resent, 0 replies lost after the command had run" in r.out,
          f"{tag}: the link line is byte-identical to before")
    rec = r.records[-1] if r.records else {}
    check(rec.get("rev") == "8483d77" and rec.get("link_resyncs") == 0
          and rec.get("link_chunks_resumed") == 0 and rec.get("link_read_retries") == 0,
          f"{tag}: recorded, link_* keys present and zero ({rec})")


def s_drops_resume(mf, image, tmp):
    """(a) Four-plus lost WRITEs at three chunks: resync and resume, each time."""
    r = scenario(mf, image, tmp, "drops", lambda b: b.faults.append(drop_writes({40: 4, 120: 5, 200: 4})))
    tag = "drops"
    check(r.rc == 0 and "VERDICT: OK" in r.out,
          f"{tag}: completes via resync/resume (rc {r.rc}; {r.exit_msg})")
    check(r.board.run_image == image, f"{tag}: the run slot holds exactly the new image")
    check(r.board.writes_per_chunk() == {k: 1 for k in range(N_CHUNKS)},
          f"{tag}: every chunk executed exactly once -- nothing skipped, nothing doubled")
    check("resync 1/" in r.out and "resync 3/" in r.out and "resync 4/" not in r.out,
          f"{tag}: three resyncs, said out loud")
    rec = r.records[-1] if r.records else {}
    check(rec.get("link_resyncs") == 3 and rec.get("link_chunks_resumed") == 3
          and rec.get("link_commands_resent", 0) >= 9,
          f"{tag}: the manifest records the resyncs and resumes ({ {k: v for k, v in rec.items() if k.startswith('link_')} })")
    check("3 resyncs, 3 transfers resumed" in r.out, f"{tag}: the verdict's link lines say so")


def s_landed_unacked(mf, image, tmp):
    """(a) A chunk RAN, then its reply and every read after it were lost: the
    resync finds blSeq one ahead and must NOT write that chunk again."""
    r = scenario(mf, image, tmp, "landed", lambda b: b.faults.append(ran_then_blackout(77, 12)))
    tag = "landed"
    check(r.rc == 0 and "VERDICT: OK" in r.out, f"{tag}: completes (rc {r.rc}; {r.exit_msg})")
    check(r.board.writes_per_chunk().get(77) == 1,
          f"{tag}: chunk 77 executed once, not re-sent after it had landed "
          f"({r.board.writes_per_chunk().get(77)}x)")
    check(r.board.run_image == image, f"{tag}: the run slot holds exactly the new image")
    rec = r.records[-1] if r.records else {}
    check(rec.get("link_resyncs") == 1 and rec.get("link_replies_recovered", 0) >= 1,
          f"{tag}: one resync, the landed chunk counted as a recovered reply")


def s_blackout(mf, image, tmp):
    """(a) The line goes quiet for 8 s mid-transfer, then comes back."""
    r = scenario(mf, image, tmp, "blackout", lambda b: b.faults.append(blackout_seconds(150, 8.0)))
    tag = "blackout"
    check(r.rc == 0 and "VERDICT: OK" in r.out, f"{tag}: completes (rc {r.rc}; {r.exit_msg})")
    check(r.board.writes_per_chunk() == {k: 1 for k in range(N_CHUNKS)},
          f"{tag}: every chunk executed exactly once")


def s_alternate(mf, image, tmp):
    """The 2026-09-19 afterstate: from chunk 100 the bootloader answers only
    every other request. Pad mode must get it through inside the budget."""
    for lost in ("drop", "mute"):
        r = scenario(mf, image, tmp, f"alt-{lost}", lambda b: b.faults.append(alternate(100, lost)))
        tag = f"alternate/{lost}"
        check(r.rc == 0 and "VERDICT: OK" in r.out, f"{tag}: completes (rc {r.rc}; {r.exit_msg})")
        check(r.board.writes_per_chunk() == {k: 1 for k in range(N_CHUNKS)},
              f"{tag}: every chunk executed exactly once")
        check(r.board.commands().count("APPLY") == 1, f"{tag}: APPLY ran exactly once")
        check(r.elapsed < mf.TRANSFER_BUDGET_S + 60,
              f"{tag}: inside the transfer budget ({r.elapsed:.0f}s of virtual time)")


def s_dead_fc16_return(mf, image, tmp):
    """(b) The transfer can never finish (every FC16 lost from chunk 150), but
    short frames still work: give up, jump back, PROVE the old rev."""
    r = scenario(mf, image, tmp, "fc16dead", lambda b: b.faults.append(fc16_dead(150)))
    tag = "return"
    check(r.rc not in (0, None), f"{tag}: exits non-zero (rc {r.rc})")
    check("NOTHING CHANGED" in (r.exit_msg or "") and "43ac7c5 is running again" in (r.exit_msg or ""),
          f"{tag}: says NOTHING CHANGED and names the previous rev ({r.exit_msg})")
    check(r.board.stage == 2 and r.board.run_rev == OLD_REV,
          f"{tag}: the board is back in the application, running the old rev "
          f"(stage {r.board.stage}, rev {r.board.run_rev:07x})")
    check("APPLY" not in r.board.commands() and r.board.commands().count("JUMP") == 1,
          f"{tag}: no APPLY; exactly one JUMP ({r.board.commands()[-3:]})")
    check(no_success(r) and r.records == [], f"{tag}: no success claimed, nothing recorded")
    check("all 16 resyncs spent" in r.everything or "budget is spent" in r.everything,
          f"{tag}: it gave up on a bound, not on the first bad chunk")


def s_reset_to_app(mf, image, tmp):
    """(b) The bootloader resets mid-transfer and starts the old application
    by itself: nothing to jump -- just prove it and say so."""
    r = scenario(mf, image, tmp, "reset", lambda b: b.faults.append(board_resets_at(90)))
    tag = "reset"
    check(r.rc not in (0, None) and "NOTHING CHANGED" in (r.exit_msg or ""),
          f"{tag}: non-zero, NOTHING CHANGED ({r.exit_msg})")
    check("JUMP" not in r.board.commands(), f"{tag}: no JUMP sent to a board already in the application")


def s_stuck_dead(mf, image, tmp):
    """(c) The board dies outright: cannot get back. Say so; never succeed."""
    r = scenario(mf, image, tmp, "dead", lambda b: b.faults.append(dead_from(150)))
    tag = "dead"
    msg = r.exit_msg or ""
    check(r.rc not in (0, None), f"{tag}: exits non-zero (rc {r.rc})")
    check("could NOT be returned" in msg and ("does not answer" in msg or "did not answer" in msg),
          f"{tag}: says it could not get back and that the board is silent ({msg})")
    check("--boot-app" in msg and "SWD" in msg, f"{tag}: gives the manual recovery")
    check("NOTHING CHANGED" not in msg and no_success(r) and r.records == [],
          f"{tag}: claims neither success nor 'nothing changed'")
    # 2026-09-23: the operator the in-app updater shows this to has no
    # terminal. The power-cycle comes FIRST, says it is not proven yet, and
    # the SSH text stays, after it.
    step = msg.find("turn the machine OFF, wait 10 seconds, and turn it back ON")
    check(0 <= step < msg.find("--boot-app") and step < msg.find("Board state:"),
          f"{tag}: the power-cycle step leads, before the board state and the SSH recovery")
    check("bench-verified" in msg and "not proven" in msg and "reads normally" in msg,
          f"{tag}: says the power-cycle path is not proven yet, and what to check after it")
    check("runValid was 1" in msg, f"{tag}: names the run slot's validity as this run found it")
    # getattr: --client may be a copy from before RECOVERY_TOTAL_S existed
    check(r.elapsed < mf.TRANSFER_BUDGET_S + mf.RESYNC_WAIT_S + getattr(mf, "RECOVERY_TOTAL_S", 0) + 90,
          f"{tag}: patience is still bounded ({r.elapsed:.0f}s of virtual time)")
    check("recovery: still looking for the board" in r.out,
          f"{tag}: says it is still looking, for the Update screen's status box")


def s_stuck_jump_fails(mf, image, tmp):
    """(c) JUMP runs but the board keeps coming back to the bootloader: three
    gated attempts, then the bootloader state is named."""
    def setup(b):
        b.faults.append(fc16_dead(150))
        b.jump_works = False
    r = scenario(mf, image, tmp, "nojump", setup)
    tag = "nojump"
    msg = r.exit_msg or ""
    check(r.rc not in (0, None) and "could NOT be returned" in msg,
          f"{tag}: non-zero, could not return ({msg[:160]})")
    check("status=" in msg and "copyState=IDLE" in msg and "runValid=1" in msg,
          f"{tag}: names the bootloader state fields")
    check(r.board.commands().count("JUMP") == mf.JUMP_ATTEMPTS,
          f"{tag}: exactly {mf.JUMP_ATTEMPTS} JUMPs, each after an identity read showed the bootloader "
          f"({r.board.commands().count('JUMP')})")
    check("NOTHING CHANGED" not in msg and no_success(r), f"{tag}: no success, no 'nothing changed'")


def s_refuse_jump(mf, image, tmp):
    """(c) runValid 0: the gate refuses to jump and says why."""
    r = scenario(mf, image, tmp, "runvalid0", lambda b: b.faults.append(fc16_dead(150)), run_valid=0)
    tag = "runValid0"
    msg = r.exit_msg or ""
    check(r.rc not in (0, None) and "not jumping, because runValid" in msg,
          f"{tag}: refuses to jump, naming runValid ({msg[:160]})")
    check("JUMP" not in r.board.commands(), f"{tag}: no JUMP sent")


def s_started_in_bootloader(mf, image, tmp):
    """A board that was ALREADY in the bootloader: no rev to prove, no jump."""
    r = scenario(mf, image, tmp, "inbl", lambda b: b.faults.append(fc16_dead(150)), stage=1)
    tag = "started-in-bootloader"
    msg = r.exit_msg or ""
    check(r.rc not in (0, None) and "already in the bootloader" in msg,
          f"{tag}: says why it will not jump ({msg[:160]})")
    check("JUMP" not in r.board.commands(), f"{tag}: no JUMP sent")


def s_after_apply_dead(mf, image, tmp):
    """(d) APPLY ran, then the board went silent: report, never a blind JUMP."""
    r = scenario(mf, image, tmp, "applydead", lambda b: b.faults.append(dead_after_command(4)))
    tag = "after-apply"
    msg = r.exit_msg or ""
    check(r.rc not in (0, None) and "after APPLY started -- not jumping blind" in msg,
          f"{tag}: non-zero, reported as after APPLY ({msg[:160]})")
    check(r.board.commands().count("APPLY") == 1 and "JUMP" not in r.board.commands(),
          f"{tag}: APPLY once, no JUMP ({r.board.commands()[-2:]})")
    check("NOTHING CHANGED" not in msg and no_success(r) and r.records == [],
          f"{tag}: no success, no 'nothing changed', nothing recorded")
    check("--revert --expect-rev 43ac7c5" in msg, f"{tag}: names the deliberate way back")


def s_new_app_dead(mf, image, tmp):
    """(d) APPLY and JUMP ran, the new image never answers: one JUMP only."""
    def setup(b):
        b.new_app_dead = True
    r = scenario(mf, image, tmp, "newdead", setup)
    tag = "new-app-dead"
    msg = r.exit_msg or ""
    check(r.rc not in (0, None) and "after APPLY started" in msg,
          f"{tag}: non-zero, reported as after APPLY ({msg[:160]})")
    check(r.board.commands().count("JUMP") == 1, f"{tag}: exactly one JUMP -- none added by recovery")
    check("NOTHING CHANGED" not in msg and no_success(r), f"{tag}: no success, no 'nothing changed'")


def s_apply_never_ran(mf, image, tmp):
    """(d, the other side) Every APPLY request is lost, reads work: blSeq
    PROVES it never ran, so this is still 'before APPLY' and safe to undo."""
    r = scenario(mf, image, tmp, "applylost", lambda b: b.faults.append(drop_command(4)))
    tag = "apply-lost"
    msg = r.exit_msg or ""
    check("APPLY" not in r.board.commands(), f"{tag}: APPLY never executed on the board")
    check(r.rc not in (0, None) and "NOTHING CHANGED" in msg and "blSeq proves never ran" in msg,
          f"{tag}: returned to the old app on blSeq's proof ({msg[:200]})")
    check(r.board.stage == 2 and r.board.run_rev == OLD_REV, f"{tag}: old application running")


def mute_command(cmd: int):
    """This command runs, but its reply is lost."""
    def f(b, i):
        if i.kind == "cmd" and i.cmd == cmd and b.stage == 1:
            return "mute"
    return f


def s_jump_reply_lost(mf, image, tmp):
    """JUMP ran and its reply was lost. Bootloader.jump's docstring always
    said that is accepted and settled by watching idStage; until 2026-09-19
    the code let the Timeout out as a traceback instead."""
    try:
        r = scenario(mf, image, tmp, "jumpreply", lambda b: b.faults.append(mute_command(5)))
    except mf.ModbusError as e:
        check(False, f"jump-reply-lost: escaped as {type(e).__name__}: {e}")
        return
    check(r.rc == 0 and "VERDICT: OK -- application 8483d77" in r.out,
          f"jump-reply-lost: settled on idStage, flash succeeds (rc {r.rc}; {r.exit_msg})")
    check(r.board.commands().count("JUMP") == 1, "jump-reply-lost: one JUMP, not resent")


def silent_after_failure(seconds: float):
    """The 2026-09-23 shape: once the transfer has given up (the first
    identity read after any WRITE went out), nothing is answered for
    `seconds`; then the bootloader answers normally again."""
    s = {"wrote": False, "until": None}

    def f(b, i):
        if i.kind == "write":
            s["wrote"] = True
        if s["wrote"] and s["until"] is None and i.kind == "id":
            s["until"] = b.clock.t + seconds
        if s["until"] is not None and b.clock.t < s["until"]:
            return "drop"
    return f


def s_silent_then_answers(mf, image, tmp):
    """(6a) 2026-09-23 on the lathe: the transfer failed, the bootloader said
    nothing to return_to_app's 30 s of identity looks, and the flasher gave
    up -- a few minutes later it answered and `--boot-app` worked first try.
    Silent for 60 s after the failure, then answering: patience must get the
    board back to the old application. RED against the 30 s return_to_app."""
    def setup(b):
        b.faults.append(fc16_dead(150))
        b.faults.append(silent_after_failure(60.0))
    r = scenario(mf, image, tmp, "silent60", setup)
    tag = "silent60"
    msg = r.exit_msg or ""
    check(r.rc not in (0, None) and "NOTHING CHANGED" in msg and "43ac7c5 is running again" in msg,
          f"{tag}: back on the old application after 60 s of silence ({msg[:200]})")
    check(r.board.stage == 2 and r.board.run_rev == OLD_REV,
          f"{tag}: the board runs the old rev (stage {r.board.stage})")
    check("APPLY" not in r.board.commands() and r.board.commands().count("JUMP") == 1,
          f"{tag}: no APPLY; exactly one JUMP, sent after the board answered ({r.board.commands()[-2:]})")
    check("recovery: still looking for the board" in r.out and "answered again after" in r.out,
          f"{tag}: says it is still looking, then that the board came back")
    check(no_success(r) and r.records == [], f"{tag}: no success claimed, nothing recorded")


def s_diag_in_failure(mf, image, tmp):
    """(6c, 5) The diagnostics window in a failure report. A field bootloader
    answers 2420 with exception 2: that is 'no diagnostics', and the failure
    report is what it always was. A bench bootloader with the window gets its
    counters appended to the status line."""
    def nojump(b):
        b.faults.append(fc16_dead(150))
        b.jump_works = False
    r = scenario(mf, image, tmp, "diag-none", nojump)
    msg = r.exit_msg or ""
    check(r.rc not in (0, None) and "could NOT be returned" in msg and "diag" not in msg,
          f"diag/field bootloader: the exception at 2420 is 'no diagnostics', not a failure of its own "
          f"({msg[:120]})")
    check(any(k == "read" for _, k, _, _ in r.board.log), "diag/field bootloader: (sanity) reads were made")

    def nojump_diag(b):
        nojump(b)
        b.diag = [0, 7, 3, 0, 0, 1, 0, 2, 1]
    r = scenario(mf, image, tmp, "diag-bench", nojump_diag)
    msg = r.exit_msg or ""
    check("runValid=1; diag framesTaken=" in msg and "crcErrors=7 badFrames=3" in msg
          and "dmaRestarts=2 clockHse=1" in msg,
          f"diag/bench bootloader: the counters follow the status line ({msg[msg.find('Board state'):][:220]})")
    check(r.board.commands().count("JUMP") == mf.JUMP_ATTEMPTS,
          f"diag/bench bootloader: reading the counters changed nothing about the jumps")

    # read_diag itself, straight: exception -> None, silence -> None, window -> 9 values
    clock = Clock()
    mf.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
    board = Lathe(clock, stage=1)
    bus = mf.Rtu("fake", 115200, 17)
    bus.ser = FakeSerial(board, clock)
    try:
        got = mf.read_diag(bus)
        check(got is None, f"diag/read_diag: exception 2 reads as None ({got})")
    except Exception as e:  # noqa: BLE001 -- the point is that nothing escapes
        check(False, f"diag/read_diag: exception 2 escaped as {type(e).__name__}: {e}")
    check(bus.retries == 0, "diag/read_diag: a missing window adds nothing to the link's retry count")
    board.stage = 0
    check(mf.read_diag(bus) is None, "diag/read_diag: silence reads as None")
    board.stage = 1
    board.diag = [5, 0, 0, 0, 0, 0, 0, 0]
    check(mf.read_diag(bus) is None,
          "diag/read_diag: the 09-23 bench bootloader's 8-register window refuses a 9-register "
          "read (exception 2), which reads as None")
    board.diag = [5, 0, 0, 0, 0, 0, 0, 0, 1]
    got = mf.read_diag(bus)
    check(got is not None and len(got) == 9 and got[0] == 6 and got[8] == 1,
          f"diag/read_diag: a bootloader with the window gives its 9 registers, clock flag last ({got})")


# --- bench tools: --link-probe and --inject (2026-09-23) ------------------------------

def scripted(pattern: str, kind: str = "read", reg: int = 2304, skip: int = 0):
    """The probe's reads of (kind, reg), after the first `skip`, follow
    `pattern`: '.' answered, 'x' dropped, 'E' answered with exception 2."""
    s = {"n": 0}

    def f(b, i):
        if i.kind == kind and i.reg == reg and i.fc == 3:
            k = s["n"] - skip
            s["n"] += 1
            if 0 <= k < len(pattern):
                return {"x": "drop", "E": "exc"}.get(pattern[k])
    return f


class Bench:
    """One call of a bench function against the fake board, stdout captured."""

    def __init__(self, mf, board, clock, call):
        self.board, self.clock = board, clock
        self.bus = mf.Rtu("fake", 115200, 17)
        self.bus.ser = FakeSerial(board, clock)
        self.regs_before = list(board.regs)
        out = io.StringIO()
        self.msg = None
        with redirect_stdout(out):
            try:
                self.rc = call(self.bus)
            except SystemExit as e:
                self.rc = e.code if isinstance(e.code, int) else 1
                self.msg = str(e.code)
        self.out = out.getvalue()
        self.sent = self.bus.ser.sent


def bench(mf, call, *, stage=1, diag=None, faults=()):
    clock = Clock()
    mf.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
    board = Lathe(clock, stage=stage)
    board.diag = diag
    board.faults.extend(faults)
    return Bench(mf, board, clock, call)


def is_fc3_request(frame: bytes) -> bool:
    return (len(frame) == 8 and frame[1] == 3
            and struct.unpack("<H", frame[-2:])[0] == crc16(frame[:-2]))


def s_link_probe(mf, image, tmp):
    """(6b) --link-probe on a scripted answer pattern with alternating misses:
    one mark per read, rows of 50, and a summary that names the alternation."""
    pattern = "." * 10 + "x." * 10 + "xxxx" + "." * 16 + "E" + "x.x"
    assert len(pattern) == 54
    b = bench(mf, lambda bus: mf.link_probe(bus, len(pattern), 0.25),
              diag=[100, 0, 0, 0, 0, 0, 0, 0, 1], faults=[scripted(pattern)])
    rows = [l[8:] for l in b.out.splitlines() if l.startswith("      0 ") or l.startswith("     50 ")]
    check(rows == [pattern[:50], pattern[50:]],
          f"probe: the marks are the pattern, in rows of 50 ({rows})")
    check("probe: 37/54 answered, 16 missed, 1 exceptions" in b.out, "probe: counts answered/missed/exceptions")
    check("probe: longest run of misses: 4 (from read 30)" in b.out, "probe: longest run of misses")
    check("misses ALTERNATE with answers: reads 9-30 (22 reads)" in b.out,
          "probe: names the alternating stretch")
    check(b.rc == 1, f"probe: exit 1 when any read went unanswered (rc {b.rc})")
    # The fake counts every frame that reaches it, the diag reads included:
    # 100 + the identity read + the diag read itself = 102 before; the 37
    # answered probe reads plus the E (all 38 arrived) and the diag read after.
    check("diag before: framesTaken=102 " in b.out and "diag change: framesTaken+39 " in b.out,
          "probe: the bench bootloader's counters before, and the change after")
    check(all(is_fc3_request(f) for f in b.sent), "probe: read-only -- every frame sent is an FC3 read")
    check(b.board.executed == [] and b.board.regs == b.regs_before, "probe: nothing changed on the board")
    n = len(pattern)
    check(b.clock.t - 1000.0 >= (n - 1) * 0.25, "probe: the reads are spaced by --probe-interval")

    runs = "....xxxx....xxxx...."
    b = bench(mf, lambda bus: mf.link_probe(bus, len(runs), 0.25), faults=[scripted(runs)])
    check("misses do not alternate" in b.out and "longest run of misses: 4 (from read 4)" in b.out,
          "probe: misses in runs are not called alternating")
    check("diag: register 2420 not served" in b.out and "diag change" not in b.out,
          "probe: a field bootloader's exception at 2420 means no counters, and the probe goes on")

    b = bench(mf, lambda bus: mf.link_probe(bus, 20, 0.25), stage=2)
    check(b.rc == 0 and "probe: 20/20 answered" in b.out and "no misses" in b.out,
          f"probe/application: probes the identity window, all answered (rc {b.rc})")
    check(all(is_fc3_request(f) and struct.unpack(">H", f[2:4])[0] == 2048 for f in b.sent),
          "probe/application: reads only the identity window -- not 2304, not 2420")


def s_inject(mf, image, tmp):
    """(6d) --inject: refused in the application; in the bootloader it sends
    exactly the documented bytes, once, and nothing it sends is a write."""
    b = bench(mf, lambda bus: mf.inject(bus, "badcrc", 40, 0.25, None), stage=2)
    check(b.rc not in (0, None) and "REFUSING --inject" in (b.msg or ""),
          f"inject/application: refused ({b.msg})")
    check(all(is_fc3_request(f) and struct.unpack(">H", f[2:4])[0] == 2048 for f in b.sent),
          f"inject/application: nothing but identity reads went out ({len(b.sent)} frames)")

    read = bytes([17, 3, 0x09, 0x00, 0x00, 0x10])
    read += struct.pack("<H", crc16(read))
    expected = {
        "truncated": read[:3],
        "badcrc": read[:6] + bytes(x ^ 0xFF for x in read[6:]),
        "burst": read + read,
    }
    check(expected["truncated"].hex() == "110309", "inject: (sanity) truncated is 11 03 09")
    for kind in ("truncated", "badcrc", "burst", "garbage"):
        b = bench(mf, lambda bus, k=kind: mf.inject(bus, k, 40, 0.25, 12345),
                  diag=[0, 0, 0, 0, 0, 0, 0, 0, 1])
        line = next((l for l in b.out.splitlines() if l.startswith(f"inject {kind}: sending")), "")
        printed = bytes.fromhex(line.split(":", 2)[2].split("(")[0].strip()) if line else b""
        odd = [f for f in b.sent if not is_fc3_request(f)]
        check(odd == [printed], f"inject/{kind}: exactly one non-read frame went out, and it is the one "
                                f"printed ({[f.hex() for f in odd]} vs {printed.hex()})")
        if kind in expected:
            check(printed == expected[kind], f"inject/{kind}: the documented bytes ({printed.hex(' ')})")
        else:
            valid = any(struct.unpack("<H", printed[j - 2:j])[0] == crc16(printed[i:j - 2])
                        for i in range(len(printed)) for j in range(i + 4, len(printed) + 1))
            check(len(printed) == 20 and not valid,
                  f"inject/garbage: 20 bytes, no slice of which is a frame with a valid CRC")
            again = bench(mf, lambda bus: mf.inject(bus, "garbage", 40, 0.25, 12345))
            check([f for f in again.sent if not is_fc3_request(f)] == [printed],
                  "inject/garbage: --inject-seed repeats the same bytes")
        check(b.board.executed == [] and b.board.regs == b.regs_before,
              f"inject/{kind}: nothing executed, no register written")
        check("probe: 40/40 answered" in b.out and "diag change:" in b.out,
              f"inject/{kind}: the short probe and the counters follow it")
    check(b.board.diag[1] >= 1, f"inject: (sanity) the fake counted the damaged frame ({b.board.diag})")


SCENARIOS = {
    "silent60": s_silent_then_answers, "diag": s_diag_in_failure,
    "probe": s_link_probe, "inject": s_inject,
    "jumpreply": s_jump_reply_lost,
    "clean": s_clean, "drops": s_drops_resume, "landed": s_landed_unacked, "blackout": s_blackout,
    "alternate": s_alternate, "return": s_dead_fc16_return, "reset": s_reset_to_app,
    "dead": s_stuck_dead, "nojump": s_stuck_jump_fails, "runvalid0": s_refuse_jump,
    "inbl": s_started_in_bootloader, "afterapply": s_after_apply_dead,
    "newdead": s_new_app_dead, "applylost": s_apply_never_ran,
}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default=str(SCRIPTS / "modbus-flash.py"))
    ap.add_argument("--only", action="append", choices=sorted(SCENARIOS))
    args = ap.parse_args(argv[1:])
    mf = load_client(Path(args.client))
    mf.RETRY_PAUSE = 0.0
    image = make_image()
    assert len(image) == IMAGE_LEN and (IMAGE_LEN + 199) // 200 == 226
    with tempfile.TemporaryDirectory() as tmp:
        for name in args.only or SCENARIOS:
            print(f"--- {name}")
            SCENARIOS[name](mf, image, tmp)
    print("FAILURES" if failures else "all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
