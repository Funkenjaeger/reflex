#!/usr/bin/env python3
"""modbus-flash.py -- flash reflex ELS firmware over the RS-485 Modbus link.

Runs ON THE PROBE HOST (elspi) with the UI STOPPED: the UI owns the serial
port. Python 3 + pyserial only; the Modbus RTU framing is hand-rolled here
(about eighty lines) rather than borrowed from the UI's minimalmodbus, so the
script has no dependency on the UI's venv, controls its own per-request
timeouts (a sector erase stalls the board for 1-4 s), and can send the one
113-register FC 0x10 frame per chunk the bootloader is shaped for.

    modbus-flash.py --identity                 read the identity window, exit
    modbus-flash.py IMAGE.bin                  full update
    modbus-flash.py IMAGE.bin --dry-run        everything except writes
    modbus-flash.py --enter-bootloader         reboot into the bootloader and stay
    modbus-flash.py --boot-app                 tell a resident bootloader to jump

THE SEQUENCE (docs/decisions/els-modbus-register-map.md, Implemented):
  1. read the identity window at 2048 FIRST, ALWAYS; refuse on any idMagic
     mismatch -- nothing else is known to be safe to read;
  2. if idStage == 2 (application): write bootCommand = 1 and wait for
     idStage == 1; a refusal (job live) shows as idStage staying 2;
  3. ERASE staging; stream the image 200 bytes per FC16 (command + address +
     length + CRC + data in one frame; the bootloader finishes the write
     before replying); VERIFY against the header and the host's own numbers;
     APPLY (backup + copy, journaled in flash); JUMP;
  4. poll the identity window until idStage == 2 and idBuildRev matches the
     image header; print one verdict line.

Every operation is edge-detected on blSeq and judged on blResult, never on
blCommand (the firmware clears that the instant it consumes the command).

blSeq is also what makes retry safe: on a lost frame the counter says whether
the command ran, so a resend only happens when it demonstrably did not. See
the retry notes above READ_ATTEMPTS.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reflex_image as ri  # noqa: E402

try:
    import serial
except ImportError:  # pragma: no cover
    print("modbus-flash: pyserial is required (pip install pyserial)", file=sys.stderr)
    sys.exit(2)

# --- mirrors of els_identity.h ------------------------------------------------
ID_BASE, ID_SIZE, ID_MAGIC = 2048, 8, 0x454C
ID_STAGE_BOOTLOADER, ID_STAGE_APP = 1, 2
(ID_MAGIC_OFF, ID_STAGE_OFF, ID_WINDOW_VERSION_OFF, ID_REV_LO_OFF, ID_REV_HI_OFF,
 ID_DIRTY_OFF, ID_APP_PROTOCOL_OFF, ID_RESERVED_OFF) = range(8)

BL_BASE = 2304
(BL_STATUS, BL_SEQ, BL_RESULT, BL_COMMAND, BL_TARGET_LO, BL_TARGET_HI, BL_LEN_LO, BL_LEN_HI,
 BL_CRC_LO, BL_CRC_HI, BL_SLOT, BL_ACTIVE_SLOT, BL_WRITE_LEN, BL_ATTEMPTS, BL_COPY_STATE,
 BL_RUN_VALID) = range(16)
BL_DATA = 16
BL_DATA_REGS = 100
CMD_ERASE, CMD_WRITE, CMD_VERIFY, CMD_APPLY, CMD_JUMP, CMD_STAY = 1, 2, 3, 4, 5, 6
SLOT_RUN, SLOT_STAGING, SLOT_BACKUP = 0, 1, 2

STATUS_NAMES = {0: "IDLE", 1: "ERASING", 2: "WRITING", 3: "VERIFYING", 4: "BAD_IMAGE",
                5: "STAGED", 6: "APPLYING", 7: "READY_TO_JUMP", 8: "STRUCK_OUT"}
RESULT_NAMES = {0: "OK", 1: "BAD_COMMAND", 2: "SLOT", 3: "ADDR_RANGE", 4: "WRITE_LEN",
                5: "FLASH_ERASE", 6: "FLASH_PROG", 7: "FLASH_VERIFY", 8: "HDR_MAGIC",
                9: "HDR_VERSION", 10: "HDR_LENGTH", 11: "HDR_CRC", 12: "HOST_LEN",
                13: "HOST_CRC", 14: "NOT_STAGED", 15: "NO_RUN_IMAGE", 16: "VECTORS",
                17: "JOURNAL", 18: "BACKUP_FAILED", 19: "COPY_FAILED"}
STATE_NAMES = {0: "IDLE", 1: "BACKUP", 2: "COPY", 3: "TRIAL", 4: "REVERT", 5: "REVERTED"}

# The app-side command register lives in rampsSharedData_t and so moves with
# protocolVersion; the identity window tells us which layout is running.
# Register index = byte offset / 2 (elsStop.bootCommand, Ramps.h).
# 9 (2026-09-07) keeps 232: the trigger-instant snapshot was appended at the
# TAIL of elsStop_t, behind bootCommand/bootSeq, so every offset that existed
# under protocolVersion 8 is unmoved. Same value, listed separately rather than
# inferred -- the table is a whitelist of layouts that have been CHECKED, and a
# guess about an unlisted one is how a flash lands on the wrong register.
APP_BOOT_COMMAND_REG = {8: 232, 9: 232}
BOOT_CMD_BOOTLOADER = 1

CHUNK_BYTES = BL_DATA_REGS * 2   # 200

# --- retry --------------------------------------------------------------------
# A 222-chunk transfer is 222 chances to lose one frame, and until now a single
# loss ended the flash. These are ATTEMPT counts, not retry counts: 4 means the
# first try plus three more.
#
# What may be retried is decided by idempotency, not by convenience:
#   * a READ changes nothing and is repeated freely;
#   * a PARAMETER write (slot, length, CRC) carries no command, so the
#     bootloader does not act on it and blSeq does not move -- writing the same
#     values again is a no-op, and it is repeated freely too;
#   * a COMMAND write is repeated only after blSeq has been read back and shown
#     that it did not land. A WRITE of the same bytes to the same address is
#     idempotent in itself, but ERASE and APPLY are emphatically not, so the
#     rule is one rule for all of them and it is blSeq, not the function code;
#   * JUMP is never retried. See Bootloader.jump.
READ_ATTEMPTS = 4
WRITE_ATTEMPTS = 4
RETRY_PAUSE = 0.05      # a beat for the bootloader's next poll, small enough
                        # that 222 chunks do not notice it


class ModbusError(Exception):
    pass


class Timeout(ModbusError):
    pass


class ExceptionResponse(ModbusError):
    def __init__(self, fc: int, code: int):
        super().__init__(f"exception {code} on FC{fc}")
        self.fc, self.code = fc, code


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class Rtu:
    """The smallest Modbus RTU master that does FC3 / FC6 / FC16."""

    def __init__(self, port: str, baud: int, address: int):
        self.address = address
        self.retries = 0          # reads re-sent after a lost frame, for the verdict line
        self.ser = serial.Serial(port, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                                 timeout=0.05, write_timeout=1.0)

    def close(self):
        self.ser.close()

    def _xact(self, body: bytes, timeout: float) -> bytes:
        frame = body + struct.pack("<H", crc16(body))
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()
        deadline = time.monotonic() + timeout
        buf = b""
        need = 3
        while len(buf) < need:
            chunk = self.ser.read(need - len(buf))
            if chunk:
                buf += chunk
                if len(buf) == 3:
                    fc = buf[1]
                    if fc & 0x80:
                        need = 5
                    elif fc == 3:
                        need = 3 + buf[2] + 2
                    else:
                        need = 8
            elif time.monotonic() > deadline:
                raise Timeout(f"no reply within {timeout:.1f}s to FC{body[1]} ({buf.hex()!r} so far)")
        if struct.unpack("<H", buf[-2:])[0] != crc16(buf[:-2]):
            raise ModbusError(f"bad CRC in reply {buf.hex()}")
        if buf[0] != self.address:
            raise ModbusError(f"reply from address {buf[0]}, expected {self.address}")
        if buf[1] & 0x80:
            raise ExceptionResponse(buf[1] & 0x7F, buf[2])
        return buf

    def read(self, addr: int, count: int, timeout: float = 0.5,
             attempts: int = READ_ATTEMPTS) -> list[int]:
        """Retries on a lost frame: a read changes nothing on the board.

        An ExceptionResponse is NOT a lost frame -- the board answered, and
        one of those answers (exception 2 at the bootloader window) is how the
        caller tells the application from the bootloader. It is raised
        immediately rather than retried four times.

        `attempts` is 1 for the callers that are already a retry loop
        (read_identity, wait_for_stage): nesting a retry inside a poll spends
        the poll's deadline four times as fast for no extra chances."""
        last: ModbusError | None = None
        for attempt in range(attempts):
            try:
                r = self._xact(struct.pack(">BBHH", self.address, 3, addr, count), timeout)
                n = r[2] // 2
                return list(struct.unpack(f">{n}H", r[3:3 + 2 * n]))
            except ExceptionResponse:
                raise
            except ModbusError as e:
                last = e
                if attempt + 1 < attempts:
                    self.retries += 1
                    time.sleep(RETRY_PAUSE)
        assert last is not None
        raise last

    def write_one(self, addr: int, value: int, timeout: float = 0.5) -> None:
        self._xact(struct.pack(">BBHH", self.address, 6, addr, value & 0xFFFF), timeout)

    def write_many(self, addr: int, values: list[int], timeout: float = 0.5) -> None:
        body = struct.pack(">BBHHB", self.address, 16, addr, len(values), 2 * len(values))
        body += struct.pack(f">{len(values)}H", *[v & 0xFFFF for v in values])
        self._xact(body, timeout)


# --- identity ----------------------------------------------------------------

class Identity:
    def __init__(self, regs: list[int]):
        self.regs = regs
        self.magic = regs[ID_MAGIC_OFF]
        self.stage = regs[ID_STAGE_OFF]
        self.window_version = regs[ID_WINDOW_VERSION_OFF]
        self.build_rev = regs[ID_REV_LO_OFF] | (regs[ID_REV_HI_OFF] << 16)
        self.dirty = bool(regs[ID_DIRTY_OFF])
        self.app_protocol = regs[ID_APP_PROTOCOL_OFF]

    @property
    def stage_name(self) -> str:
        return {ID_STAGE_BOOTLOADER: "bootloader", ID_STAGE_APP: "application"}.get(
            self.stage, f"unknown({self.stage})")

    @property
    def rev_str(self) -> str:
        return f"{self.build_rev:07x}" + ("-dirty" if self.dirty else "")

    def __str__(self) -> str:
        return (f"idMagic=0x{self.magic:04x} stage={self.stage_name} windowVersion={self.window_version} "
                f"rev={self.rev_str} appProtocol={self.app_protocol}")


def read_identity(bus: Rtu, tries: int = 5) -> Identity:
    last = None
    for _ in range(tries):
        try:
            regs = bus.read(ID_BASE, ID_SIZE, attempts=1)   # this loop IS the retry
            ident = Identity(regs)
            if ident.magic != ID_MAGIC:
                raise SystemExit(f"REFUSING: idMagic 0x{ident.magic:04x} at register {ID_BASE}, "
                                 f"expected 0x{ID_MAGIC:04x}. This is not a reflex identity window; "
                                 f"nothing else is known to be safe to touch. ({ident})")
            return ident
        except ModbusError as e:
            last = e
            time.sleep(0.2)
    raise SystemExit(f"no identity window at {ID_BASE}: {last}")


def wait_for_stage(bus: Rtu, stage: int, timeout: float, rev: int | None = None) -> Identity:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            regs = bus.read(ID_BASE, ID_SIZE, timeout=0.3, attempts=1)  # ditto
            ident = Identity(regs)
            if ident.magic == ID_MAGIC and ident.stage == stage and (rev is None or ident.build_rev == rev):
                return ident
            last = str(ident)
        except ModbusError as e:
            last = str(e)
        time.sleep(0.2)
    raise SystemExit(f"timed out after {timeout:.0f}s waiting for stage {stage}"
                     f"{'' if rev is None else f' rev {rev:07x}'}; last: {last}")


# --- bootloader control ------------------------------------------------------

class Bootloader:
    def __init__(self, bus: Rtu, dry_run: bool):
        self.bus = bus
        self.dry_run = dry_run
        self.retries = 0        # commands re-sent because they never landed
        self.recovered = 0      # commands that HAD landed; only the reply was lost

    def head(self, timeout: float = 0.5) -> list[int]:
        return self.bus.read(BL_BASE, 16, timeout=timeout)

    def describe(self) -> str:
        h = self.head()
        return (f"status={STATUS_NAMES.get(h[BL_STATUS], h[BL_STATUS])} seq={h[BL_SEQ]} "
                f"result={RESULT_NAMES.get(h[BL_RESULT], h[BL_RESULT])} "
                f"copyState={STATE_NAMES.get(h[BL_COPY_STATE], h[BL_COPY_STATE])} "
                f"attempts={h[BL_ATTEMPTS]} runValid={h[BL_RUN_VALID]}")

    def _expect(self, seq_before: int, what: str) -> None:
        h = self.head()
        if h[BL_SEQ] != (seq_before + 1) & 0xFFFF:
            raise SystemExit(f"{what}: blSeq did not move ({seq_before} -> {h[BL_SEQ]}); {self.describe()}")
        if h[BL_RESULT] != 0:
            raise SystemExit(f"{what}: result {RESULT_NAMES.get(h[BL_RESULT], h[BL_RESULT])}; {self.describe()}")

    def _params(self, write, what: str) -> None:
        """A parameter write (slot, length, CRC) sets no command register, so
        the bootloader never acts on it and blSeq does not move. Sending the
        same values again is a no-op; retry it flat."""
        last: ModbusError | None = None
        for attempt in range(WRITE_ATTEMPTS):
            try:
                write()
                return
            except ExceptionResponse:
                raise
            except ModbusError as e:
                last = e
                if attempt + 1 < WRITE_ATTEMPTS:
                    self.retries += 1
                    time.sleep(RETRY_PAUSE)
        raise SystemExit(f"{what}: {WRITE_ATTEMPTS} attempts, last: {last}")

    def _commit(self, write, what: str) -> None:
        """Send one blSeq-advancing transaction, and retry it when the wire
        eats a frame.

        blSeq is what makes this safe rather than reckless. The bootloader
        increments it exactly once per command it executes, so after a loss the
        counter says WHICH HALF was lost:

          unchanged     the request never arrived, or arrived mangled and was
                        dropped on its CRC. Nothing happened on the board.
                        Re-send it.
          advanced by 1 the command ran and the REPLY was lost. Re-sending
                        would run it a SECOND time -- harmless for a WRITE of
                        the same bytes to the same address, wrong for ERASE,
                        very wrong for APPLY. Accept it as done and let
                        _expect judge blResult.
          anything else something is driving this bus that is not us. Stop.

        So the decision is made on blSeq for every command alike, never on
        which function code happens to be idempotent."""
        seq = self.head()[BL_SEQ]
        for attempt in range(WRITE_ATTEMPTS):
            try:
                write()
                break
            except ExceptionResponse:
                raise
            except ModbusError as e:
                try:
                    # Generous: a lost reply to ERASE or APPLY can leave the
                    # board stalled in flash for seconds yet.
                    now = self.head(timeout=2.0)[BL_SEQ]
                except ModbusError as e2:
                    raise SystemExit(f"{what}: lost the reply ({e}) and then could not read blSeq "
                                     f"back either ({e2})") from e2
                if now == (seq + 1) & 0xFFFF:
                    self.recovered += 1
                    print(f"  {what}: reply lost but blSeq moved {seq} -> {now}; the command ran ({e})")
                    break
                if now != seq:
                    raise SystemExit(f"{what}: blSeq jumped {seq} -> {now} across a lost frame; "
                                     f"another master on the bus? {self.describe()}")
                if attempt + 1 >= WRITE_ATTEMPTS:
                    raise SystemExit(f"{what}: {WRITE_ATTEMPTS} attempts, blSeq never moved from "
                                     f"{seq}; last: {e}")
                self.retries += 1
                print(f"  {what}: no reply, blSeq still {seq}; resending "
                      f"({attempt + 1}/{WRITE_ATTEMPTS - 1})")
                time.sleep(RETRY_PAUSE)
        self._expect(seq, what)

    def command(self, cmd: int, what: str, timeout: float) -> None:
        if self.dry_run:
            print(f"  dry-run: would send {what}")
            return
        self._commit(lambda: self.bus.write_one(BL_BASE + BL_COMMAND, cmd, timeout=timeout), what)

    def erase(self) -> None:
        if not self.dry_run:
            self._params(lambda: self.bus.write_one(BL_BASE + BL_SLOT, SLOT_STAGING),
                         "ERASE staging (slot register)")
        # A 128 KB sector erase stalls the whole chip for 1-4 s; the reply
        # comes after it. 10 s is the tolerance the task set.
        self.command(CMD_ERASE, "ERASE staging", timeout=10.0)

    def write_chunk(self, addr: int, data: bytes, image_len: int, image_crc: int) -> None:
        assert len(data) % 4 == 0 and 0 < len(data) <= CHUNK_BYTES
        regs = [CMD_WRITE, addr & 0xFFFF, addr >> 16, image_len & 0xFFFF, image_len >> 16,
                image_crc & 0xFFFF, image_crc >> 16, SLOT_STAGING, 0, len(data), 0, 0, 0]
        words = list(struct.unpack(f"<{len(data) // 2}H", data))
        words += [0xFFFF] * (BL_DATA_REGS - len(words))
        regs += words
        if self.dry_run:
            return
        self._commit(lambda: self.bus.write_many(BL_BASE + BL_COMMAND, regs, timeout=1.0),
                     f"WRITE at 0x{addr:08x}")

    def verify(self, image_len: int, image_crc: int) -> None:
        if not self.dry_run:
            self._params(lambda: self.bus.write_many(
                BL_BASE + BL_LEN_LO,
                [image_len & 0xFFFF, image_len >> 16, image_crc & 0xFFFF, image_crc >> 16,
                 SLOT_STAGING]), "VERIFY staging (parameters)")
        self.command(CMD_VERIFY, "VERIFY staging", timeout=3.0)

    def apply(self) -> None:
        # Two erases, two copies, four journal records: budget 15 s.
        self.command(CMD_APPLY, "APPLY (backup + copy)", timeout=15.0)
        if not self.dry_run:
            h = self.head()
            if h[BL_COPY_STATE] != 3 or h[BL_RUN_VALID] != 1:
                raise SystemExit(f"APPLY left the board not ready: {self.describe()}")

    def jump(self) -> None:
        """DELIBERATELY NOT RETRIED, and not through _commit.

        JUMP is the one command whose reply is expected to go missing: the
        board leaves immediately after sending it, and the blSeq read that
        _commit would make to reconcile a loss is answered by whatever is
        running afterwards -- the application, which does not serve the
        bootloader window at all and answers exception 2 there. There is
        nothing to reconcile against, so a lost reply is simply accepted, as
        it always was, and the caller settles it by watching idStage."""
        if self.dry_run:
            print("  dry-run: would send JUMP")
            return
        seq = self.head()[BL_SEQ]
        self.bus.write_one(BL_BASE + BL_COMMAND, CMD_JUMP, timeout=2.0)
        # The reply comes before the jump; the board leaves right after it.
        try:
            self._expect(seq, "JUMP")
        except SystemExit:
            raise
        except ModbusError:
            pass  # already gone: fine


# --- top-level flows -----------------------------------------------------------

def enter_bootloader(bus: Rtu, ident: Identity, dry_run: bool) -> Identity:
    if ident.stage == ID_STAGE_BOOTLOADER:
        return ident
    reg = APP_BOOT_COMMAND_REG.get(ident.app_protocol)
    if reg is None:
        raise SystemExit(f"application protocolVersion {ident.app_protocol} has no known bootCommand "
                         f"register (known: {sorted(APP_BOOT_COMMAND_REG)}); flash over SWD this once.")
    if dry_run:
        print(f"  dry-run: would write bootCommand=1 at register {reg} and wait for the bootloader")
        return ident
    print(f"  application {ident.rev_str}: requesting reboot into the bootloader (register {reg})")
    try:
        bus.write_one(reg, BOOT_CMD_BOOTLOADER)
    except ModbusError as e:
        # the reset can land before the reply is out
        print(f"  (no reply to the reboot command: {e})")
    ident = wait_for_stage(bus, ID_STAGE_BOOTLOADER, timeout=10.0)
    print(f"  bootloader {ident.rev_str} answered")
    return ident


def flash(bus: Rtu, image_path: str, dry_run: bool) -> int:
    with open(image_path, "rb") as f:
        data = f.read()
    try:
        hdr = ri.validate(data)
    except ValueError as e:
        raise SystemExit(f"{image_path}: not a valid slotted image: {e}")
    image = data[:hdr.image_length]
    print(f"image {image_path}: rev {hdr.rev_str}, {hdr.image_length} bytes, crc32 0x{hdr.crc32:08x}")

    ident = read_identity(bus)
    print(f"board: {ident}")
    if ident.stage == ID_STAGE_APP and ident.build_rev == hdr.build_rev and not hdr.dirty and not ident.dirty:
        print("note: the board already reports this revision; continuing anyway")
    ident = enter_bootloader(bus, ident, dry_run)
    if dry_run and ident.stage != ID_STAGE_BOOTLOADER:
        print("dry-run: stopping before any write (board is still in the application)")
        return 0

    bl = Bootloader(bus, dry_run)
    print(f"bootloader: {bl.describe()}")
    t0 = time.monotonic()
    print("  erasing staging (up to 4 s)...")
    bl.erase()
    print(f"  streaming {len(image)} bytes in {(len(image) + CHUNK_BYTES - 1) // CHUNK_BYTES} chunks...")
    for off in range(0, len(image), CHUNK_BYTES):
        chunk = image[off:off + CHUNK_BYTES]
        bl.write_chunk(ri.STAGING_SLOT_BASE + off, chunk, hdr.image_length, hdr.crc32)
    print("  verifying...")
    bl.verify(hdr.image_length, hdr.crc32)
    print("  applying (backup old image, copy new image to the run slot)...")
    bl.apply()
    print(f"  {bl.describe()}")
    print("  jumping...")
    bl.jump()
    if dry_run:
        print(f"VERDICT: dry-run complete, nothing written ({time.monotonic() - t0:.1f}s)")
        return 0
    ident = wait_for_stage(bus, ID_STAGE_APP, timeout=15.0, rev=hdr.build_rev)
    # Say the retry count out loud even when it is zero. A silent retry layer
    # is how a link that has quietly started losing a tenth of its frames goes
    # on looking healthy for months.
    print(f"  link: {bus.retries} read retries, {bl.retries} commands resent, "
          f"{bl.recovered} replies lost after the command had run")
    print(f"VERDICT: OK -- application {ident.rev_str} is running, protocolVersion {ident.app_protocol} "
          f"({time.monotonic() - t0:.1f}s)")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("image", nargs="?", help="slotted reflex-fw.bin (built with REFLEX_APP_BASE=0x08020000)")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--address", type=int, default=17)
    ap.add_argument("--identity", action="store_true", help="read and print the identity window, exit")
    ap.add_argument("--dry-run", action="store_true", help="everything except writes")
    ap.add_argument("--enter-bootloader", action="store_true", help="reboot into the bootloader and stay")
    ap.add_argument("--boot-app", action="store_true", help="tell a resident bootloader to JUMP")
    args = ap.parse_args(argv[1:])

    bus = Rtu(args.port, args.baud, args.address)
    try:
        if args.identity:
            ident = read_identity(bus)
            print(ident)
            if ident.stage == ID_STAGE_BOOTLOADER:
                print("bootloader:", Bootloader(bus, True).describe())
            return 0
        if args.enter_bootloader:
            ident = read_identity(bus)
            print(f"board: {ident}")
            ident = enter_bootloader(bus, ident, args.dry_run)
            print(f"now: {ident}")
            if ident.stage == ID_STAGE_BOOTLOADER:
                print("bootloader:", Bootloader(bus, True).describe())
            return 0
        if args.boot_app:
            ident = read_identity(bus)
            if ident.stage != ID_STAGE_BOOTLOADER:
                print(f"board is already in the {ident.stage_name} ({ident})")
                return 0
            Bootloader(bus, args.dry_run).jump()
            if args.dry_run:
                return 0
            ident = wait_for_stage(bus, ID_STAGE_APP, timeout=15.0)
            print(f"VERDICT: OK -- application {ident.rev_str} is running")
            return 0
        if not args.image:
            ap.error("an image, --identity, --enter-bootloader or --boot-app is required")
        return flash(bus, args.image, args.dry_run)
    finally:
        bus.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
