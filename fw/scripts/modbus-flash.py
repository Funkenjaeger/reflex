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
    modbus-flash.py --revert [--expect-rev R]  put the previous image back (see revert())

BENCH DIAGNOSTICS (read-only, or one deliberately damaged READ; never a write):
    modbus-flash.py --link-probe N [--probe-interval S]
        N single-try status reads (bootloader: the 16-register status block;
        application: the identity window), S seconds apart (default 0.25).
        One mark per read -- `.` answered, `x` no answer, `E` exception --
        in rows of 50, then: answered/total, the longest run of misses, and
        whether the misses ALTERNATE with answers. In the bootloader it also
        prints the link counters at register 2420 before and after, when the
        bootloader has them (see DIAG_BASE). Exit 0 when every read answered.
    modbus-flash.py --inject KIND [--link-probe N] [--inject-seed S]
        Bootloader only; refused in the application. Sends ONE damaged frame
        and prints its bytes, then a short link probe (default 40 reads) so
        its effect is visible. KIND: garbage (20 random bytes, none of which
        forms a frame with a valid CRC), truncated (a status-block READ
        request cut after 3 bytes), badcrc (that READ with its CRC inverted),
        burst (that READ twice back to back, no inter-frame gap). Nothing it
        sends can reach a write or a command register.

THE SEQUENCE (decisions/els-modbus-register-map.md, Implemented):
  1. read the identity window at 2048 FIRST, ALWAYS; refuse on any idMagic
     mismatch -- nothing else is known to be safe to read;
  2. if idStage == 2 (application): write bootCommand = 1 and wait for
     idStage == 1; a refusal (job live) shows as idStage staying 2;
  3. ERASE staging; stream the image 200 bytes per FC16 (command + address +
     length + CRC + data in one frame; the bootloader finishes the write
     before replying); VERIFY against the header and the host's own numbers;
     APPLY (backup + copy, journaled in flash); JUMP;
  4. poll the identity window until idStage == 2 and idBuildRev matches the
     image header; print one verdict line;
  5. on that verdict, and only on it, append a record to the flash manifest
     (flash_manifest.py; default ~/firmware/flashed.json, --manifest to say
     where, --no-manifest to skip). It records what the board was SEEN
     running, not merely what was sent.

WHEN IT FAILS (Open Loops 6aae7131 / 6aae7135, after 2026-09-19 07:12 on the
lathe: a transfer lost one chunk four times running and the flasher exited 1
with the board parked in the bootloader and the old application intact):
  * a chunk that will not go through is RESYNCED and RESUMED, not fatal --
    see stream(). Bounded by TRANSFER_BUDGET_S and MAX_RESYNCS;
  * a failure BEFORE APPLY never leaves the board in the bootloader when it
    was running an application: the run slot has not been written (the
    transfer goes to staging), so return_to_app() jumps back and PROVES the
    previous rev is running before saying "NOTHING CHANGED". Exit is still
    non-zero -- the update did not happen. It keeps looking for a board that
    has gone quiet for up to RECOVERY_TOTAL_S (2026-09-23: the lathe's
    bootloader was silent for the old 30 s and answered minutes later), and
    when it still cannot get back its verdict leads with the power-cycle an
    operator without a terminal can do;
  * a failure once APPLY may have run is REPORTED, never answered with a
    blind jump -- see after_apply_report().

Every operation is edge-detected on blSeq and judged on blResult, never on
blCommand (the firmware clears that the instant it consumes the command).

blSeq is also what makes retry safe: on a lost frame the counter says whether
the command ran, so a resend only happens when it demonstrably did not. See
the retry notes above READ_ATTEMPTS.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flash_manifest  # noqa: E402
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
CMD_REVERT = 7
SLOT_RUN, SLOT_STAGING, SLOT_BACKUP = 0, 1, 2

STATUS_NAMES = {0: "IDLE", 1: "ERASING", 2: "WRITING", 3: "VERIFYING", 4: "BAD_IMAGE",
                5: "STAGED", 6: "APPLYING", 7: "READY_TO_JUMP", 8: "STRUCK_OUT"}
RESULT_NAMES = {0: "OK", 1: "BAD_COMMAND", 2: "SLOT", 3: "ADDR_RANGE", 4: "WRITE_LEN",
                5: "FLASH_ERASE", 6: "FLASH_PROG", 7: "FLASH_VERIFY", 8: "HDR_MAGIC",
                9: "HDR_VERSION", 10: "HDR_LENGTH", 11: "HDR_CRC", 12: "HOST_LEN",
                13: "HOST_CRC", 14: "NOT_STAGED", 15: "NO_RUN_IMAGE", 16: "VECTORS",
                17: "JOURNAL", 18: "BACKUP_FAILED", 19: "COPY_FAILED",
                20: "NO_BACKUP"}
STATE_NAMES = {0: "IDLE", 1: "BACKUP", 2: "COPY", 3: "TRIAL", 4: "REVERT", 5: "REVERTED"}

# The app-side command register lives in rampsSharedData_t and so moves with
# protocolVersion; the identity window tells us which layout is running.
# Register index = byte offset / 2 (elsStop.bootCommand, Ramps.h).
# 9 (2026-09-07) keeps 232: the trigger-instant snapshot was appended at the
# TAIL of elsStop_t, behind bootCommand/bootSeq, so every offset that existed
# under protocolVersion 8 is unmoved. Same value, listed separately rather than
# inferred -- the table is a whitelist of layouts that have been CHECKED, and a
# guess about an unlisted one is how a flash lands on the wrong register.
# 10 (2026-09-07) MOVES it to 168. The hot/cold remap reordered the whole block,
# so unlike 8 -> 9 this is a real move and the old value would land on
# stopTriggerZSpeed. The number is not typed by hand: it is generated into
# registers/offsets.json by tools/genregs.py from the same schema that lays out
# the struct, and copied here. This table stays a WHITELIST of layouts that have
# been checked -- an unlisted protocolVersion makes the client refuse and tell
# the operator to flash over SWD once, which is the correct answer to "I do not
# know where this register is".
# 11 (2026-09-18) keeps 168: stopOffset and stopTriggerOffset were placed in two
# of protocolVersion 10's alignment pads, so no group grew and nothing moved.
# Listed separately for the whitelist reason above; genregs --check verifies it.
APP_BOOT_COMMAND_REG = {8: 232, 9: 232, 10: 168, 11: 168}
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

# --- resync and resume (Open Loops 6aae7135) -----------------------------------
# 2026-09-19 07:12, on the lathe: a 226-chunk transfer lost the WRITE at
# 0x08043840 four times running while every status read in between WAS
# answered ("blSeq never moved from 73"), and the flasher gave up. The same
# transfer went through minutes later in 13 s. So a chunk that will not go
# through is not the end of the transfer: resync (read status until the
# bootloader answers), take from blSeq how many chunks it has accepted, and
# carry on from there. Bounded twice, so a link that is really gone still
# ends -- and then return_to_app() applies.
TRANSFER_BUDGET_S = 180.0   # the whole stream; a clean one is ~13 s
MAX_RESYNCS = 16
RESYNC_WAIT_S = 20.0        # one resync: status reads with backoff, at most this long
RESYNC_PAUSE_FIRST = 0.1
RESYNC_PAUSE_MAX = 2.0
PAD_READ_TIMEOUT = 0.15     # see Bootloader.pad
PAD_AFTER_TROUBLED = 3      # consecutive commands that lost a frame before pad mode

# --- getting back after a failure (Open Loops 6aae7131) -------------------------
RECOVERY_WAIT_S = 30.0      # apply_never_ran's look for blSeq after a lost APPLY
REPORT_WAIT_S = 10.0        # how long to keep asking a flaky link, for the last look before reporting
JUMP_ATTEMPTS = 3           # each gated on an identity read showing the bootloader
# PATIENT RECOVERY (2026-09-23). The lathe's in-app update to v1.2.0-rc.5 spent
# its transfer budget in resyncs, then return_to_app got no answer to its
# identity looks for RECOVERY_WAIT_S (30 s), which was then also the whole of
# its patience, and it gave up: "the board does not answer", bootloader
# resident, dead DRO, operator with no terminal. Minutes later -- the UI
# polling again -- the bootloader answered, and `--boot-app` worked on the first
# try. On 2026-09-19 the same bootloader answered every OTHER request, idle
# host, until it was re-entered. So after a bad frame it is intermittent, not
# dead, and the host is what gave up. Why it goes quiet is NOT known (a
# bootloader fix is being built separately and is unverified), so this does
# not model it: it keeps asking for RECOVERY_TOTAL_S from the moment recovery
# starts. The spacing is jittered between RECOVERY_PAUSE_MIN and _MAX rather
# than fixed or doubling, because a fixed rhythm can phase-lock onto whatever
# periodic state the bootloader is in and miss every window it answers in;
# frame-for-frame alternation answers any spacing, and 30 s of total silence
# on 09-23 says that was not all that was going on. The JUMP gates are
# untouched; only how long the host keeps looking changes.
RECOVERY_TOTAL_S = 150.0
RECOVERY_PAUSE_MIN = 0.3
RECOVERY_PAUSE_MAX = 2.0
RECOVERY_PROGRESS_S = 10.0  # a "still looking" line this often, for the Update screen's status box
_rng = random.Random()      # the jitter; the tests reseed it

# --- bootloader link diagnostics (2026-09-23) ------------------------------------
# A READ-ONLY window the bootloader adds after its 116-register block: eight
# uint16 counters of what its receive path saw, then (2026-09-24) clockHse, 1
# when it runs on the 8 MHz crystal -- 0 means it fell back to the internal
# RC, whose baud error stalled every transfer until then. Older bootloaders
# (5a5ee43 and before: none; the 09-23 bench build d325bac: 8 registers)
# answer this 9-register read with exception 2 (illegal data address) --
# which means "no diagnostics", never a failure: read_diag returns None for
# it, as for silence. It is read only right after the BOOTLOADER window has
# answered, never against an application, whose register map is not the
# bootloader's.
DIAG_BASE = BL_BASE + 116   # 2420
DIAG_NAMES = ("framesTaken", "crcErrors", "badFrames", "overflowDrops",
              "errOre", "errFe", "errNe", "dmaRestarts", "clockHse")
DIAG_SIZE = len(DIAG_NAMES)

# --- bench link probe and fault injection (2026-09-23) ---------------------------
PROBE_INTERVAL_S = 0.25     # --probe-interval default: the pause after each probe read
PROBE_READ_TIMEOUT = 0.3    # one probe read; a 16-register reply is ~3 ms at 115200
PROBE_ROW = 50
ALTERNATING_MIN = 8         # reads of strict answered/missed alternation to call it alternating
INJECT_KINDS = ("garbage", "truncated", "badcrc", "burst")
INJECT_PROBE_READS = 40     # --inject's probe when --link-probe does not say
INJECT_LISTEN_S = 0.5       # how long to collect whatever answers the injected frame
INJECT_GARBAGE_BYTES = 20


class ModbusError(Exception):
    pass


class LinkLost(SystemExit):
    """A command that could not be got through the link: its resends were
    spent with blSeq unmoved, or blSeq could not be read back at all.

    A SystemExit, so every caller that handled the plain SystemExit these
    used to be still does; stream() catches this class by name to resync
    instead. Other SystemExits -- blSeq jumping, a bad blResult -- are the
    board SAYING something is wrong, and are not resynced."""


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

    def send_raw(self, data: bytes, listen: float) -> bytes:
        """--inject only: put ``data`` on the wire EXACTLY as given -- no CRC
        added, no framing -- in one write, so a burst leaves with no
        inter-frame gap; then collect whatever comes back for ``listen``
        seconds, unparsed. The caller builds ``data`` (inject_frame) and is
        what guarantees it cannot be a write."""
        self.ser.reset_input_buffer()
        self.ser.write(data)
        self.ser.flush()
        deadline = time.monotonic() + listen
        buf = b""
        while time.monotonic() < deadline:
            buf += self.ser.read(256)
        return buf


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


# How long to wait for the APPLICATION to answer after a jump. Longer than one
# IWDG period (~32 s, LSI/256/4096), on purpose: an image whose first start
# hangs is reset by the watchdog and gets a second attempt from the
# bootloader, and a trial that comes good on that attempt is still a good
# image -- it must not be reported as a failed flash. Measured 2026-09-13:
# the first in-app update to v1.2.0-rc.2 answered about 30 s after the jump
# and the old 15 s wait declared it failed while the update had in fact
# completed. A late answer is SAID, below, so it is never silent.
APP_START_WAIT_S = 45.0
APP_START_NORMAL_S = 5.0


def wait_for_stage(bus: Rtu, stage: int, timeout: float, rev: int | None = None) -> Identity:
    t0 = time.monotonic()
    deadline = t0 + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            regs = bus.read(ID_BASE, ID_SIZE, timeout=0.3, attempts=1)  # ditto
            ident = Identity(regs)
            if ident.magic == ID_MAGIC and ident.stage == stage and (rev is None or ident.build_rev == rev):
                took = time.monotonic() - t0
                if stage == ID_STAGE_APP and took > APP_START_NORMAL_S:
                    print(f"  NOTE: the application answered {took:.1f} s after the jump; a normal start"
                          f" is under {APP_START_NORMAL_S:.0f} s and one IWDG period is ~32 s. It is"
                          f" running, but its first start may have hung and been reset.")
                return ident
            last = str(ident)
        except ModbusError as e:
            last = str(e)
        time.sleep(0.2)
    raise SystemExit(f"timed out after {timeout:.0f}s waiting for stage {stage}"
                     f"{'' if rev is None else f' rev {rev:07x}'}; last: {last}")


# --- bootloader control ------------------------------------------------------

def describe_head(h: list[int], diag: list[int] | None = None) -> str:
    """The status block in words; with ``diag`` (read_diag), the bootloader's
    link counters appended -- the failure reports pass it, so a failure on a
    bootloader that has the window says what its receive path saw."""
    s = (f"status={STATUS_NAMES.get(h[BL_STATUS], h[BL_STATUS])} seq={h[BL_SEQ]} "
         f"result={RESULT_NAMES.get(h[BL_RESULT], h[BL_RESULT])} "
         f"copyState={STATE_NAMES.get(h[BL_COPY_STATE], h[BL_COPY_STATE])} "
         f"attempts={h[BL_ATTEMPTS]} runValid={h[BL_RUN_VALID]}")
    return s + (f"; diag {describe_diag(diag)}" if diag else "")


def describe_diag(d: list[int]) -> str:
    return " ".join(f"{name}={v}" for name, v in zip(DIAG_NAMES, d))


def read_diag(bus: Rtu, tries: int = 2) -> list[int] | None:
    """The bootloader's link counters at DIAG_BASE, or None.

    None covers BOTH "this bootloader has no such window" (exception 2 --
    every bootloader in the field on 2026-09-23) and "no answer": the counters
    are extra information and never a reason to fail. Single tries, not
    Rtu.read's retries, so a missing window costs one frame and the retry
    count in the link line and the manifest stays the flash's own. Call it
    only right after the bootloader window answered (see DIAG_BASE)."""
    for _ in range(tries):
        try:
            d = bus.read(DIAG_BASE, DIAG_SIZE, timeout=PROBE_READ_TIMEOUT, attempts=1)
        except ExceptionResponse:
            return None
        except ModbusError:
            continue
        return d if len(d) == DIAG_SIZE else None
    return None


class Bootloader:
    def __init__(self, bus: Rtu, dry_run: bool):
        self.bus = bus
        self.dry_run = dry_run
        self.retries = 0        # commands re-sent because they never landed
        self.recovered = 0      # commands that HAD landed; only the reply was lost
        self.resyncs = 0        # times stream() had to resync (see stream)
        self.resumed = 0        # ... and carried on from the confirmed chunk
        self.seq: int | None = None         # blSeq as last confirmed by _expect / resync
        self.last_head: list[int] | None = None
        # PAD MODE, switched on by the first resync and left on. The failure
        # on 2026-09-19 had a shape: every WRITE lost, every status read in
        # between answered, and afterwards the bootloader answered only every
        # OTHER request. Against a link that answers alternate frames, the
        # retry loop's read-then-resend rhythm puts every resend on a lost
        # slot, four times running. In pad mode each command is preceded by
        # one throwaway single-try read, which moves the command onto the
        # other slot. On a healthy link it costs one short read per command.
        # Whether the real desync is strictly alternate is UNKNOWN (it is a
        # firmware task of its own); the resync bound holds either way.
        self.pad = False
        self.troubled = 0       # commands in a row that lost a frame on the way

    def head(self, timeout: float = 0.5) -> list[int]:
        h = self.bus.read(BL_BASE, 16, timeout=timeout)
        self.last_head = h
        return h

    def describe(self, h: list[int] | None = None) -> str:
        return describe_head(self.head() if h is None else h)

    def _sacrifice(self) -> None:
        """Pad mode's throwaway read: one try, short timeout, answer unused."""
        try:
            self.bus.read(BL_BASE + BL_SEQ, 1, timeout=PAD_READ_TIMEOUT, attempts=1)
        except ExceptionResponse:
            raise
        except ModbusError:
            pass

    def _expect(self, seq_before: int, what: str) -> None:
        h = self.head(timeout=PAD_READ_TIMEOUT if self.pad else 0.5)
        if h[BL_SEQ] != (seq_before + 1) & 0xFFFF:
            raise SystemExit(f"{what}: blSeq did not move ({seq_before} -> {h[BL_SEQ]}); {self.describe()}")
        if h[BL_RESULT] != 0:
            raise SystemExit(f"{what}: result {RESULT_NAMES.get(h[BL_RESULT], h[BL_RESULT])}; {self.describe()}")
        self.seq = h[BL_SEQ]

    def resync(self, deadline: float) -> list[int]:
        """Read the status block until the bootloader answers, backing off.

        One try per read (this loop is the retry), pauses doubling from
        RESYNC_PAUSE_FIRST to RESYNC_PAUSE_MAX, for at most RESYNC_WAIT_S or
        until ``deadline``. An exception response is an ANSWER -- the
        bootloader window is gone, i.e. the board is not in the bootloader
        any more -- and is raised, not waited out."""
        t0 = time.monotonic()
        stop = min(deadline, t0 + RESYNC_WAIT_S)
        pause = RESYNC_PAUSE_FIRST
        tries = 0
        last: ModbusError | None = None
        while True:
            try:
                h = self.bus.read(BL_BASE, 16, timeout=0.5, attempts=1)
                self.last_head = h
                return h
            except ExceptionResponse:
                raise
            except ModbusError as e:
                last = e
                tries += 1
                self.bus.retries += 1
            if time.monotonic() + pause > stop:
                raise SystemExit(f"resync: the bootloader stopped answering -- {tries} status reads "
                                 f"over {time.monotonic() - t0:.1f}s went unanswered; last: {last}")
            time.sleep(pause)
            pause = min(pause * 2, RESYNC_PAUSE_MAX)

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
        # In pad mode blSeq is already known from the last _expect/resync,
        # and not re-reading it keeps the command on the answered slot.
        seq = self.seq if (self.pad and self.seq is not None) else self.head()[BL_SEQ]
        lost = False
        for attempt in range(WRITE_ATTEMPTS):
            try:
                if self.pad:
                    self._sacrifice()
                write()
                break
            except ExceptionResponse:
                raise
            except ModbusError as e:
                lost = True
                try:
                    # Generous: a lost reply to ERASE or APPLY can leave the
                    # board stalled in flash for seconds yet.
                    now = self.head(timeout=2.0)[BL_SEQ]
                except ExceptionResponse:
                    raise
                except ModbusError as e2:
                    raise LinkLost(f"{what}: lost the reply ({e}) and then could not read blSeq "
                                   f"back either ({e2})") from e2
                if now == (seq + 1) & 0xFFFF:
                    self.recovered += 1
                    print(f"  {what}: reply lost but blSeq moved {seq} -> {now}; the command ran ({e})")
                    break
                if now != seq:
                    raise SystemExit(f"{what}: blSeq jumped {seq} -> {now} across a lost frame; "
                                     f"another master on the bus? {self.describe()}")
                if attempt + 1 >= WRITE_ATTEMPTS:
                    raise LinkLost(f"{what}: {WRITE_ATTEMPTS} attempts, blSeq never moved from "
                                   f"{seq}; last: {e}")
                self.retries += 1
                print(f"  {what}: no reply, blSeq still {seq}; resending "
                      f"({attempt + 1}/{WRITE_ATTEMPTS - 1})")
                time.sleep(RETRY_PAUSE)
        self._expect(seq, what)
        # A link that loses a frame on EVERY command never needs a resync --
        # each command limps through on its own retries -- but at a second
        # or two per chunk. Pad mode is the cure for that rhythm too.
        self.troubled = self.troubled + 1 if lost else 0
        if self.troubled >= PAD_AFTER_TROUBLED and not self.pad:
            self.pad = True
            print(f"  link: {self.troubled} commands in a row lost a frame; padding every command "
                  f"with a throwaway read from here on")

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

    def revert(self) -> None:
        """Copy BACKUP -> RUN. Not idempotent -- a second REVERT has nothing
        different to go to and answers NO_BACKUP -- so it goes through _commit
        like APPLY: a lost reply is reconciled on blSeq, never resent blind."""
        # One 128 KB erase, one copy, two journal records: APPLY's budget.
        self.command(CMD_REVERT, "REVERT (backup -> run)", timeout=15.0)
        if not self.dry_run:
            h = self.head()
            if h[BL_COPY_STATE] != 5 or h[BL_RUN_VALID] != 1:
                raise SystemExit(f"REVERT left the board without a valid restored image: {self.describe()}")

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
        if self.pad:
            self._sacrifice()
        try:
            self.bus.write_one(BL_BASE + BL_COMMAND, CMD_JUMP, timeout=2.0)
        except ExceptionResponse:
            raise
        except ModbusError as e:
            # What the docstring above always said and the code did not do:
            # until 2026-09-19 this propagated as a traceback. Whether the
            # request or only the reply was lost, the caller's idStage wait
            # is what settles it.
            print(f"  JUMP: no reply ({e}); watching idStage to see whether it ran")
            return
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


def link_stats(bus: Rtu, bl: Bootloader) -> dict:
    """The link's record for this run, as the manifest keys it lands under."""
    return {"link_read_retries": bus.retries, "link_commands_resent": bl.retries,
            "link_replies_recovered": bl.recovered, "link_resyncs": bl.resyncs,
            "link_chunks_resumed": bl.resumed}


def print_link(bus: Rtu, bl: Bootloader) -> None:
    # Say the retry count out loud even when it is zero. A silent retry layer
    # is how a link that has quietly started losing a tenth of its frames goes
    # on looking healthy for months. The resync line is added only when there
    # was one, so a clean flash prints exactly what it always printed.
    print(f"  link: {bus.retries} read retries, {bl.retries} commands resent, "
          f"{bl.recovered} replies lost after the command had run")
    if bl.resyncs:
        print(f"  link: {bl.resyncs} resyncs, {bl.resumed} transfers resumed from the chunk "
              f"the bootloader confirmed")


def record_flash(manifest, image_path: str, data: bytes, hdr, ident,
                 variant: str, tag: str | None, link: dict | None = None) -> None:
    """Append the manifest record for a flash the board has CONFIRMED.

    A failure here is reported, not raised: the board is already running the
    new image, and an exit code saying the flash failed would be a lie that
    sends someone to reflash a good board. ot-state reporting a stale rev is
    the loud signal that the record did not land.

    ``link`` (link_stats) adds the link_* keys, so a link that is getting
    worse shows in the record of every flash, not only in scrollback. New
    keys only: readers take the last line's ``rev``/``tag`` and ignore the
    rest (updater.last_flashed_rev).
    """
    extra = {"image": os.path.basename(image_path), "protocol": ident.app_protocol}
    if tag:
        extra["tag"] = tag
    if link:
        extra.update(link)
    rec = flash_manifest.record(
        variant=variant, probe=None if variant == "release" else "unknown",
        rev=f"{hdr.build_rev:07x}", dirty=hdr.dirty,
        md5=hashlib.md5(data).hexdigest(), via="modbus", **extra)
    try:
        where = flash_manifest.append(manifest, rec)
        print(f"  recorded in {where}")
    except OSError as e:
        print(f"WARNING: the flash succeeded but was NOT recorded in {manifest}: {e}")


def stream(bl: Bootloader, image: bytes, hdr) -> None:
    """Send the image to staging, chunk by chunk, RESUMING across a link that
    drops out (Open Loops 6aae7135).

    HOW PROGRESS IS KNOWN. The bootloader keeps no "bytes received" count;
    what it has is blSeq, bumped exactly once per command it executes
    (bl_core.c blCoreService), and the target-address registers, which hold
    the address of the last WRITE it executed. Every command between the
    ERASE and the VERIFY is one of our WRITEs, in order, so after a resync

        (blSeq - blSeq just after the ERASE) = chunks the bootloader has run

    and that count can only be the failing chunk's index (its request never
    got there: send it again) or one more (it ran and every reply after it
    was lost: its target address must then be the failing chunk's, its
    result OK, and it is NOT written again). Any other count means something
    other than this transfer moved the counter -- a reset, another master --
    and the transfer stops rather than guess. On the lathe on 2026-09-19 the
    arithmetic held: blSeq 60 at 0x08042e18, chunk 59, one ERASE before it.

    Bounded by TRANSFER_BUDGET_S over the whole stream (checked before every
    chunk, so a transfer that limps without ever needing a resync is bounded
    too) and MAX_RESYNCS; past
    either, or when a resync gets no answer, it raises and the caller's
    return_to_app() takes over."""
    n = (len(image) + CHUNK_BYTES - 1) // CHUNK_BYTES
    deadline = time.monotonic() + TRANSFER_BUDGET_S
    base = bl.seq                               # blSeq after the ERASE
    i = 0
    while i < n:
        off = i * CHUNK_BYTES
        addr = ri.STAGING_SLOT_BASE + off
        if time.monotonic() >= deadline:
            raise SystemExit(f"the {TRANSFER_BUDGET_S:.0f}s transfer budget is spent with {i} of {n} "
                             f"chunks written; giving up")
        try:
            bl.write_chunk(addr, image[off:off + CHUNK_BYTES], hdr.image_length, hdr.crc32)
            i += 1
            continue
        except ExceptionResponse:
            raise
        except (LinkLost, ModbusError) as e:
            failure = e
        if bl.resyncs >= MAX_RESYNCS:
            raise SystemExit(f"{failure}; giving up: all {MAX_RESYNCS} resyncs spent")
        if time.monotonic() >= deadline:
            raise SystemExit(f"{failure}; giving up: the {TRANSFER_BUDGET_S:.0f}s transfer budget is spent")
        bl.resyncs += 1
        print(f"  chunk {i + 1}/{n} at 0x{addr:08x} will not go through; resync "
              f"{bl.resyncs}/{MAX_RESYNCS} ({failure})")
        h = bl.resync(deadline)
        done = (h[BL_SEQ] - base) & 0xFFFF
        if done == i + 1:
            last = h[BL_TARGET_LO] | (h[BL_TARGET_HI] << 16)
            if last != addr or h[BL_RESULT] != 0:
                raise SystemExit(f"resync: blSeq says chunk {i + 1} ran, but the bootloader's last write "
                                 f"was at 0x{last:08x} with result "
                                 f"{RESULT_NAMES.get(h[BL_RESULT], h[BL_RESULT])}; {describe_head(h)}")
            bl.recovered += 1
            i += 1
        elif done != i:
            raise SystemExit(f"resync: blSeq {h[BL_SEQ]} counts {done} commands since the ERASE "
                             f"(blSeq {base}), but chunk {i + 1} of {n} was next; something other than "
                             f"this transfer moved it. Not resuming against a count that does not add "
                             f"up. {describe_head(h)}")
        bl.seq = h[BL_SEQ]
        bl.pad = True
        bl.resumed += 1
        print(f"    resumed: the bootloader confirms {i} of {n} chunks written"
              + (f"; continuing at 0x{ri.STAGING_SLOT_BASE + i * CHUNK_BYTES:08x}" if i < n else ""))


def poll_identity(bus: Rtu, wait: float) -> Identity | None:
    """read_identity for a link that may be flaky: single tries with backoff
    for up to ``wait`` seconds; None when nothing answered."""
    t_end = time.monotonic() + wait
    pause = RESYNC_PAUSE_FIRST
    while True:
        try:
            ident = Identity(bus.read(ID_BASE, ID_SIZE, timeout=0.3, attempts=1))
            if ident.magic == ID_MAGIC:
                return ident
        except ModbusError:
            pass
        if time.monotonic() + pause > t_end:
            return None
        time.sleep(pause)
        pause = min(pause * 2, RESYNC_PAUSE_MAX)


def poll_head(bus: Rtu, wait: float) -> list[int] | None:
    """The bootloader status block, same terms. None when nothing answered,
    or when the answer was that there is no bootloader window (exception)."""
    t_end = time.monotonic() + wait
    pause = RESYNC_PAUSE_FIRST
    while True:
        try:
            return bus.read(BL_BASE, 16, timeout=0.5, attempts=1)
        except ExceptionResponse:
            return None
        except ModbusError:
            pass
        if time.monotonic() + pause > t_end:
            return None
        time.sleep(pause)
        pause = min(pause * 2, RESYNC_PAUSE_MAX)


def apply_never_ran(bus: Rtu, seq_at_apply: int | None) -> bool:
    """True only when the bootloader PROVES that nothing ran after VERIFY:
    blSeq unmoved and blStatus still STAGED (a reset would have cleared the
    staged flag and restarted blSeq). Anything less is treated as 'APPLY may
    have run', which is the case that must not be answered with a jump."""
    if seq_at_apply is None:
        return False
    h = poll_head(bus, RECOVERY_WAIT_S)
    return h is not None and h[BL_SEQ] == seq_at_apply and h[BL_STATUS] == 5   # STAGED


RECOVER_BY_HAND = ("Recover by hand: `modbus-flash.py --identity` to look; `modbus-flash.py --boot-app` "
                   "starts whatever the run slot holds; if the board does not answer at all, power-cycle "
                   "it (the stay-in-bootloader request was consumed on entry, so with a valid run slot the "
                   "bootloader starts the application by itself); last resort SWD (fw/scripts/flash.sh).")

# What the verdict leads with when return_to_app cannot get the board back
# (2026-09-23). The in-app updater runs this script on a machine whose
# operator usually has NO terminal, and the verdict above it told him to run
# modbus-flash.py. The step he CAN take comes first, in plain words; the SSH
# path stays, after it. The claim underneath -- power-on with a valid run slot
# starts the application, because the stay request was consumed on entry --
# is what bl_core is written to do, but on 2026-09-23 it had not been tried
# on the bench after a failed transfer, so the text says it is being verified
# rather than promise it. Update it (and the updater's copy) once it has been.
POWER_CYCLE_STEP = (
    "WHAT TO DO NOW, no terminal needed: turn the machine OFF, wait 10 seconds, and turn it back ON. "
    "Nothing was applied, so the previous firmware is still in the controller, and when its run slot "
    "is valid the bootloader starts it by itself at power-on. Then check that the controller reads "
    "normally: the position displays show and follow the machine. This power-cycle recovery is still "
    "being bench-verified, so it is expected to work but not proven; if the controller does not read "
    "normally afterwards, it needs the terminal recovery below.")


class _Patience:
    """return_to_app's looks at a board that may have gone quiet: single-try
    reads, jittered spacing, one absolute deadline for all of them, and a
    progress line every RECOVERY_PROGRESS_S (see RECOVERY_TOTAL_S).

    A look always makes at least one read, even past the deadline, so a
    look after a JUMP attempt that ran long still asks once."""

    def __init__(self, bus: Rtu):
        self.bus = bus
        self.t0 = time.monotonic()
        self.deadline = self.t0 + RECOVERY_TOTAL_S
        self.missed = 0             # reads that got no answer, all looks together
        self.silent_since: float | None = None
        self.next_note = self.t0 + RECOVERY_PROGRESS_S

    def time_left(self) -> bool:
        return time.monotonic() < self.deadline

    def _miss(self) -> bool:
        """Count a miss, say so now and then, pause. False: out of time."""
        self.missed += 1
        now = time.monotonic()
        if self.silent_since is None:
            self.silent_since = now
        pause = _rng.uniform(RECOVERY_PAUSE_MIN, RECOVERY_PAUSE_MAX)
        if now + pause > self.deadline:
            return False
        if now >= self.next_note:
            print(f"  recovery: still looking for the board -- {now - self.t0:.0f} s of "
                  f"{RECOVERY_TOTAL_S:.0f} s, {self.missed} reads unanswered so far. Please wait.",
                  flush=True)
            self.next_note = now + RECOVERY_PROGRESS_S
        time.sleep(pause)
        return True

    def _answered(self) -> None:
        if self.silent_since is not None:
            quiet = time.monotonic() - self.silent_since
            if quiet >= RECOVERY_PROGRESS_S:
                print(f"  recovery: the board answered again after {quiet:.0f} s without an answer",
                      flush=True)
            self.silent_since = None

    def identity(self) -> Identity | None:
        """poll_identity's job, patiently. None: nothing answered in time."""
        while True:
            try:
                ident = Identity(self.bus.read(ID_BASE, ID_SIZE, timeout=0.3, attempts=1))
                if ident.magic == ID_MAGIC:
                    self._answered()
                    return ident
            except ModbusError:
                pass
            if not self._miss():
                return None

    def head(self) -> tuple[list[int] | None, bool]:
        """(status block, False), or (None, gone): gone is True when the
        bootloader window answered with an EXCEPTION -- the board is no longer
        in the bootloader -- and False when nothing answered in time."""
        while True:
            try:
                h = self.bus.read(BL_BASE, 16, timeout=0.5, attempts=1)
                self._answered()
                return h, False
            except ExceptionResponse:
                return None, True
            except ModbusError:
                pass
            if not self._miss():
                return None, False


def return_to_app(bus: Rtu, bl: Bootloader, before: Identity | None, start: list[int] | None,
                  reason, where: str):
    """A failure BEFORE APPLY: put the board back in the application it was
    running and PROVE it, then exit non-zero. Always raises SystemExit.
    (Open Loops 6aae7131.)

    Safe because nothing before APPLY writes the run slot: ERASE, WRITE and
    VERIFY all address staging. Still checked, not assumed, before each jump:
    runValid must be 1, no copy may be in flight, and copyState must be what
    it was when this run found the bootloader. Each JUMP after the first is
    gated on an identity read showing the board is STILL in the bootloader,
    so a jump is never sent to a board that already left.

    Proven by an identity read: stage = application AND the rev (and dirty
    flag) the board reported before this run. Nothing less is reported as
    'nothing changed'.

    Patient since 2026-09-23: a board that stops answering is asked again,
    jittered, for up to RECOVERY_TOTAL_S across all the looks (see there),
    not given up on after one 30 s look. What is gated, and how, is exactly
    what it was."""
    print(f"FAILED {where}: {reason}", flush=True)
    print_link(bus, bl)
    if before is None:
        look = poll_head(bus, REPORT_WAIT_S)
        raise SystemExit(
            f"VERDICT: FAILED {where}; the board is in the bootloader "
            f"({describe_head(look, read_diag(bus)) if look else 'status unreadable'}). It was already in "
            f"the bootloader when this run began, so there is no previous application rev to prove a return "
            f"against, and it is not jumped blind. The run slot was not written by this run. {RECOVER_BY_HAND}")
    print(f"  the run slot was not written (the transfer goes to staging); returning to the "
          f"previous application {before.rev_str}", flush=True)
    start_copy = start[BL_COPY_STATE] if start else None
    state = "never read"
    look = _Patience(bus)        # every look below shares its RECOVERY_TOTAL_S
    for attempt in range(1, JUMP_ATTEMPTS + 1):
        h = None
        while h is None:
            ident = look.identity()
            if ident is None:
                state = (f"the board did not answer its identity window in "
                         f"{time.monotonic() - look.t0:.0f} s of looking ({look.missed} single-try reads "
                         f"unanswered, {RECOVERY_PAUSE_MIN}-{RECOVERY_PAUSE_MAX:.0f} s apart)")
                break
            if ident.stage == ID_STAGE_APP:
                _settled(before, ident, where, reason)               # raises
            h, gone = look.head()
            if h is None and not (gone and look.time_left()):
                # gone = the bootloader window answered with an exception
                # between the identity read and this one, i.e. the board has
                # just left the bootloader: look at the identity again, while
                # there is time. Silence is the other case, and final.
                state = ("the bootloader answered its identity window but not its status block"
                         + (" (it answered there with an exception)" if gone else ""))
                break
        if h is None:
            break
        state = f"bootloader: {describe_head(h)}"
        refuse = None
        if h[BL_RUN_VALID] != 1:
            refuse = "runValid is not 1: the bootloader does not consider the run slot bootable"
        elif h[BL_COPY_STATE] in (1, 2, 4) or h[BL_STATUS] == 6:
            refuse = "a copy is in flight"
        elif start_copy is not None and h[BL_COPY_STATE] != start_copy:
            refuse = (f"copyState moved from {STATE_NAMES.get(start_copy, start_copy)} during this run, "
                      f"so something other than the transfer happened")
        if refuse:
            # The counters are read only here, at the verdict: a read before
            # a JUMP would shift the request rhythm the jump rides on.
            raise SystemExit(f"VERDICT: FAILED {where}, and the board is left in the BOOTLOADER: "
                             f"not jumping, because {refuse}. bootloader: "
                             f"{describe_head(h, read_diag(bus))}. {RECOVER_BY_HAND}")
        print(f"  JUMP to the application (attempt {attempt}/{JUMP_ATTEMPTS}); {state}", flush=True)
        try:
            bl.jump()
        except (SystemExit, ModbusError) as e:
            print(f"  JUMP: {e}")
        try:
            ident = wait_for_stage(bus, ID_STAGE_APP, timeout=APP_START_WAIT_S)
        except SystemExit as e:
            print(f"  the application did not answer: {e}")
            continue
        _settled(before, ident, where, reason)                       # raises
    else:
        look = poll_identity(bus, REPORT_WAIT_S)
        if look is not None and look.stage == ID_STAGE_APP:
            _settled(before, look, where, reason)                    # raises
        h = poll_head(bus, REPORT_WAIT_S) if look is not None else None
        state = (f"bootloader: {describe_head(h, read_diag(bus))}" if h else
                 "the board does not answer" if look is None else f"identity: {look}")
    # The operator's step first: the in-app updater shows this to someone
    # with no terminal (see POWER_CYCLE_STEP). Then the facts, then SSH.
    was_valid = (" runValid was 1 when this run found the bootloader."
                 if start and start[BL_RUN_VALID] == 1 else "")
    raise SystemExit(f"VERDICT: FAILED {where}, and the board could NOT be returned to the application "
                     f"{before.rev_str}.\n"
                     f"  {POWER_CYCLE_STEP}\n"
                     f"  Board state: {state}. Nothing was applied: the previous image should still be "
                     f"intact in the run slot.{was_valid}\n"
                     f"  With a terminal (SSH to the Pi): {RECOVER_BY_HAND}")


def _settled(before: Identity, ident: Identity, where: str, reason):
    if ident.build_rev == before.build_rev and ident.dirty == before.dirty:
        raise SystemExit(f"VERDICT: FAILED -- NOTHING CHANGED. The flash failed {where} ({reason}); the "
                         f"previous firmware {ident.rev_str} is running again, protocolVersion "
                         f"{ident.app_protocol}, confirmed by an identity read.")
    raise SystemExit(f"VERDICT: FAILED {where}, and the board came back running application "
                     f"{ident.rev_str}, which is NOT the {before.rev_str} it ran before this flash. "
                     f"Treat the controller's firmware as unknown until checked. ({reason})")


AFTER_APPLY_MEANING = {
    0: "IDLE: no copy recorded (an APPLY that failed before touching the run slot ends here, as does "
       "a trial the application has confirmed)",
    3: "TRIAL: the NEW image is in the run slot, unconfirmed. --boot-app starts it; if it fails to "
       "start three times the bootloader puts the previous image back by itself",
    5: "REVERTED: the previous image was copied back into the run slot",
}


def after_apply_report(bus: Rtu, before: Identity | None, hdr, reason) -> str:
    """A failure once APPLY may have run. Looks, reports, and does NOT jump:
    from here the run slot may hold the new image, the old one, or a torn
    copy the bootloader is part-way through repairing, and only the
    bootloader's journal knows which. Returns the message to exit with."""
    look = poll_identity(bus, REPORT_WAIT_S)
    if look is None:
        state = f"the board does not answer (identity window silent for {REPORT_WAIT_S:.0f}s)"
    elif look.stage == ID_STAGE_APP:
        which = ("the NEW image" if look.build_rev == hdr.build_rev else
                 "the PREVIOUS image" if before and look.build_rev == before.build_rev else
                 "neither the new nor the previous image")
        state = f"application {look.rev_str} is running -- {which}"
    else:
        h = poll_head(bus, REPORT_WAIT_S)
        if h is None:
            state = "in the bootloader; its status block did not answer"
        else:
            state = (f"in the bootloader: {describe_head(h, read_diag(bus))}. copyState "
                     + AFTER_APPLY_MEANING.get(h[BL_COPY_STATE],
                                               f"{STATE_NAMES.get(h[BL_COPY_STATE], h[BL_COPY_STATE])}: a "
                                               f"copy is in flight; the bootloader finishes or undoes it "
                                               f"at its next start"))
    prev = (f" To go back to the previous firmware deliberately: `modbus-flash.py --revert "
            f"--expect-rev {before.rev_str}`." if before else "")
    return (f"VERDICT: FAILED after APPLY started -- not jumping blind ({reason}).\n"
            f"  board now: {state}.\n  {RECOVER_BY_HAND}{prev}")


def flash(bus: Rtu, image_path: str, dry_run: bool, manifest=None,
          variant: str = "unknown", tag: str | None = None) -> int:
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
    # What return_to_app must prove came back, if anything goes wrong before
    # APPLY. None when the board was already in the bootloader: there is then
    # no running application to go back to, and no rev to prove.
    before = ident if ident.stage == ID_STAGE_APP else None
    ident = enter_bootloader(bus, ident, dry_run)
    if dry_run and ident.stage != ID_STAGE_BOOTLOADER:
        print("dry-run: stopping before any write (board is still in the application)")
        return 0

    bl = Bootloader(bus, dry_run)
    print(f"bootloader: {bl.describe()}")
    t0 = time.monotonic()
    start = bl.last_head          # the bootloader as this run found it
    try:
        print("  erasing staging (up to 4 s)...")
        bl.erase()
        print(f"  streaming {len(image)} bytes in {(len(image) + CHUNK_BYTES - 1) // CHUNK_BYTES} chunks...")
        stream(bl, image, hdr)
        print("  verifying...")
        bl.verify(hdr.image_length, hdr.crc32)
    except (SystemExit, ModbusError) as e:
        if dry_run:
            raise
        return_to_app(bus, bl, before, start, e, "before APPLY")      # always raises
    print("  applying (backup old image, copy new image to the run slot)...")
    seq_at_apply = bl.seq
    try:
        bl.apply()
        print(f"  {bl.describe()}")
        print("  jumping...")
        bl.jump()
        if dry_run:
            print(f"VERDICT: dry-run complete, nothing written ({time.monotonic() - t0:.1f}s)")
            return 0
        ident = wait_for_stage(bus, ID_STAGE_APP, timeout=APP_START_WAIT_S, rev=hdr.build_rev)
    except (SystemExit, ModbusError) as e:
        if dry_run:
            raise
        if apply_never_ran(bus, seq_at_apply):
            return_to_app(bus, bl, before, start, e,
                          "at APPLY, which blSeq proves never ran")    # always raises
        print_link(bus, bl)
        raise SystemExit(after_apply_report(bus, before, hdr, e)) from None
    print_link(bus, bl)
    print(f"VERDICT: OK -- application {ident.rev_str} is running, protocolVersion {ident.app_protocol} "
          f"({time.monotonic() - t0:.1f}s)")
    if manifest:
        record_flash(manifest, image_path, data, hdr, ident, variant, tag, link_stats(bus, bl))
    return 0


def revert(bus: Rtu, dry_run: bool, manifest=None, expect_rev: str | None = None) -> int:
    """Put back the image the last APPLY displaced. The in-app updater's
    rollback when its gate refuses the firmware it just flashed.

    APPLY copies RUN into BACKUP before overwriting it, so the previous image
    is already on the board however it got there -- nothing is read over the
    wire and nothing has to be kept on disk. One step back only: after a
    REVERT, BACKUP and RUN hold the same image and another is refused.

    Getting INTO the bootloader goes through the running application's
    bootCommand register, whose address depends on its protocolVersion. When
    the image being reverted speaks a layout this checkout has never seen,
    enter_bootloader refuses rather than guess, and the rollback cannot start.
    That is the correct answer to 'I do not know where this register is'.

    ``expect_rev`` (the rev the board ran before the update) makes the wait
    for the application also a check that the right image came back."""
    want = int(expect_rev.split("-")[0], 16) if expect_rev else None
    ident = read_identity(bus)
    print(f"board: {ident}")
    reverted_from = ident.rev_str if ident.stage == ID_STAGE_APP else None
    ident = enter_bootloader(bus, ident, dry_run)
    if dry_run and ident.stage != ID_STAGE_BOOTLOADER:
        print("dry-run: stopping before any write (board is still in the application)")
        return 0

    bl = Bootloader(bus, dry_run)
    print(f"bootloader: {bl.describe()}")
    t0 = time.monotonic()
    print("  reverting (copy the backup image into the run slot)...")
    bl.revert()
    print(f"  {bl.describe()}")
    print("  jumping...")
    bl.jump()
    if dry_run:
        print(f"VERDICT: dry-run complete, nothing written ({time.monotonic() - t0:.1f}s)")
        return 0
    ident = wait_for_stage(bus, ID_STAGE_APP, timeout=APP_START_WAIT_S, rev=want)
    print(f"  link: {bus.retries} read retries, {bl.retries} commands resent, "
          f"{bl.recovered} replies lost after the command had run")
    print(f"VERDICT: OK -- reverted; application {ident.rev_str} is running, "
          f"protocolVersion {ident.app_protocol} ({time.monotonic() - t0:.1f}s)")
    if manifest:
        record_revert(manifest, ident, reverted_from)
    return 0


def record_revert(manifest, ident, reverted_from: str | None) -> None:
    """ot-state takes the manifest's LAST line as what the lathe runs, so a
    revert that wrote nothing would leave it reporting the refused image.
    No image file was sent, so md5 is stated null rather than invented, and
    whether the restored image carries a probe is unknown here. Reported,
    not raised, for the reason record_flash gives."""
    rec = flash_manifest.record(
        variant="revert", probe="unknown", rev=f"{ident.build_rev:07x}",
        dirty=ident.dirty, md5=None, via="modbus",
        protocol=ident.app_protocol, reverted_from=reverted_from)
    try:
        where = flash_manifest.append(manifest, rec)
        print(f"  recorded in {where}")
    except OSError as e:
        print(f"WARNING: the revert succeeded but was NOT recorded in {manifest}: {e}")


# --- bench diagnostics (2026-09-23) ------------------------------------------------
# Two bench tools for the question 2026-09-19 and 2026-09-23 left open: after a
# bad frame the bootloader answered every other request (09-19) or nothing at
# all for over 30 s (09-23), and both times came good later. --link-probe
# measures that pattern instead of inferring it from a failed flash;
# --inject makes one bad frame of a known kind on purpose, so the pattern
# can be tied to what caused it. Both are read-only: every request they
# make is an FC3 read, and the one damaged frame is built from a READ (or
# is garbage proven to contain no frame with a valid CRC) -- see inject_frame.

def probe_marks(bus: Rtu, n: int, interval: float, bootloader: bool) -> str:
    """n single-try reads, one mark each, printed live in rows of PROBE_ROW."""
    addr, count = (BL_BASE, 16) if bootloader else (ID_BASE, ID_SIZE)
    marks = []
    for k in range(n):
        if k % PROBE_ROW == 0:
            print(f"  {k:5d} ", end="", flush=True)
        try:
            bus.read(addr, count, timeout=PROBE_READ_TIMEOUT, attempts=1)
            m = "."
        except ExceptionResponse:
            m = "E"
        except ModbusError:
            m = "x"
        marks.append(m)
        print(m, end="\n" if (k + 1) % PROBE_ROW == 0 or k + 1 == n else "", flush=True)
        if k + 1 < n:
            time.sleep(interval)
    return "".join(marks)


def summarize_marks(marks: str) -> list[str]:
    """The probe's verdict lines. ALTERNATING means a stretch of at least
    ALTERNATING_MIN reads that go strictly answered/missed/answered/... --
    the 2026-09-19 afterstate -- as opposed to misses in runs."""
    total, ok, exc = len(marks), marks.count("."), marks.count("E")
    lines = [f"probe: {ok}/{total} answered, {marks.count('x')} missed, {exc} exceptions"]
    run = best = best_at = 0
    for i, m in enumerate(marks):
        run = run + 1 if m == "x" else 0
        if run > best:
            best, best_at = run, i - run + 1
    lines.append(f"probe: longest run of misses: {best}" + (f" (from read {best_at})" if best else ""))
    alt = alt_best = alt_at = 0
    for i, m in enumerate(marks):
        if m in ".x" and i and alt and marks[i - 1] in ".x" and marks[i - 1] != m:
            alt += 1
        else:
            alt = 1 if m in ".x" else 0
        if alt > alt_best:
            alt_best, alt_at = alt, i - alt + 1
    if "x" not in marks:
        lines.append("probe: no misses, so nothing to alternate")
    elif alt_best >= ALTERNATING_MIN:
        lines.append(f"probe: misses ALTERNATE with answers: reads {alt_at}-{alt_at + alt_best - 1} "
                     f"({alt_best} reads) go strictly answered/missed")
    else:
        lines.append(f"probe: misses do not alternate (longest strictly alternating stretch: "
                     f"{alt_best} reads; {ALTERNATING_MIN} would count)")
    return lines


def _print_diag_change(d0: list[int] | None, d1: list[int] | None, sent: str) -> None:
    if d0 is None:
        return
    if d1 is None:
        print("diag after:  no answer (the window answered before the probe)")
        return
    print(f"diag after:  {describe_diag(d1)}")
    print("diag change: " + " ".join(f"{name}+{(b - a) & 0xFFFF}"
                                     for name, a, b in zip(DIAG_NAMES, d0, d1))
          + f"  (between the two diag reads this run sent {sent})")


def _diag_before(bus: Rtu) -> list[int] | None:
    d = read_diag(bus)
    print(f"diag before: {describe_diag(d)}" if d is not None else
          f"diag: register {DIAG_BASE} not served (a bootloader without the diagnostics window, "
          f"or no answer) -- probing without counters")
    return d


def link_probe(bus: Rtu, n: int, interval: float) -> int:
    """--link-probe: see the module docstring. Reads only. Exit 0 when every
    read answered, 1 otherwise, so a bench loop can count bad runs."""
    ident = read_identity(bus)
    print(f"board: {ident}")
    bootloader = ident.stage == ID_STAGE_BOOTLOADER
    d0 = _diag_before(bus) if bootloader else None
    if not bootloader:
        print("application: probing the identity window; the diagnostics window is the bootloader's "
              "and is not read here")
    what = ("bootloader status block (16 registers at 2304)" if bootloader else
            f"identity window ({ID_SIZE} registers at {ID_BASE})")
    print(f"probe: {n} single-try reads of the {what}, {interval:.2f} s apart, "
          f"{PROBE_READ_TIMEOUT} s timeout each ('.' answered, 'x' no answer, 'E' exception)")
    marks = probe_marks(bus, n, interval, bootloader)
    for line in summarize_marks(marks):
        print(line)
    if bootloader:
        _print_diag_change(d0, read_diag(bus) if d0 is not None else None, f"{n} probe reads")
    return 0 if marks.count(".") == len(marks) else 1


def _crc_frame(body: bytes) -> bytes:
    return body + struct.pack("<H", crc16(body))


def holds_valid_frame(data: bytes) -> bool:
    """True when ANY contiguous slice of 4 bytes or more ends in a valid
    Modbus CRC of the bytes before it -- i.e. whatever framing the receiver
    lands on inside ``data``, could it see one good frame?"""
    for i in range(len(data)):
        for j in range(i + 4, len(data) + 1):
            if struct.unpack("<H", data[j - 2:j])[0] == crc16(data[i:j - 2]):
                return True
    return False


def inject_frame(kind: str, address: int, rng: random.Random) -> bytes:
    """The one damaged frame --inject sends. Every kind is built from the
    status-block READ (FC3, 16 registers at 2304) or is garbage that
    holds_valid_frame proves has no valid frame anywhere in it, so nothing
    sent can execute as a write or reach the command register."""
    read = _crc_frame(struct.pack(">BBHH", address, 3, BL_BASE, 16))
    if kind == "truncated":
        return read[:3]
    if kind == "badcrc":
        return read[:-2] + bytes(b ^ 0xFF for b in read[-2:])
    if kind == "burst":
        return read + read
    if kind == "garbage":
        while True:
            data = bytes(rng.randrange(256) for _ in range(INJECT_GARBAGE_BYTES))
            if not holds_valid_frame(data):
                return data
    raise ValueError(f"unknown --inject kind {kind!r}; one of {', '.join(INJECT_KINDS)}")


def inject(bus: Rtu, kind: str, n: int, interval: float, seed: int | None) -> int:
    """--inject: see the module docstring. Refused unless the board SAYS it
    is in the bootloader: the application's receive path is not what is
    being studied, and a bad frame into a running lathe controller is not a
    bench experiment."""
    if kind not in INJECT_KINDS:
        raise SystemExit(f"--inject {kind!r}: one of {', '.join(INJECT_KINDS)}")
    ident = read_identity(bus)
    print(f"board: {ident}")
    if ident.stage != ID_STAGE_BOOTLOADER:
        raise SystemExit(f"REFUSING --inject: the board is in the {ident.stage_name}, and fault "
                         f"injection is for the bootloader only. `--enter-bootloader` first, on a bench.")
    if seed is None:
        seed = int.from_bytes(os.urandom(4), "big")
    frame = inject_frame(kind, bus.address, random.Random(seed))
    d0 = _diag_before(bus)
    print(f"inject {kind}: sending {len(frame)} bytes: {frame.hex(' ')}"
          + (f"  (seed {seed}; --inject-seed {seed} repeats it)" if kind == "garbage" else ""), flush=True)
    heard = bus.send_raw(frame, INJECT_LISTEN_S)
    print(f"inject {kind}: heard back within {INJECT_LISTEN_S} s: {heard.hex(' ') if heard else 'nothing'}")
    print(f"probe: {n} single-try reads of the bootloader status block, {interval:.2f} s apart "
          f"('.' answered, 'x' no answer, 'E' exception)")
    marks = probe_marks(bus, n, interval, True)
    for line in summarize_marks(marks):
        print(line)
    _print_diag_change(d0, read_diag(bus) if d0 is not None else None,
                       f"the injected {kind} bytes and {n} probe reads")
    return 0 if marks.count(".") == len(marks) else 1


def main(argv: list[str]) -> int:
    # The in-app updater reads this script's output through a pipe and shows
    # it line by line in the Update screen's status box (updater.py
    # subprocess_runner). Through a pipe Python block-buffers stdout, so
    # unless the service's environment sets PYTHONUNBUFFERED (nothing in this
    # repo does, 2026-09-23) the progress lines -- recovery's "still looking"
    # among them -- reached the box only when the flasher exited.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):   # pragma: no cover -- a replaced stdout
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("image", nargs="?", help="slotted reflex-fw.bin (built with REFLEX_APP_BASE=0x08020000)")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--address", type=int, default=17)
    ap.add_argument("--identity", action="store_true", help="read and print the identity window, exit")
    ap.add_argument("--dry-run", action="store_true", help="everything except writes")
    ap.add_argument("--enter-bootloader", action="store_true", help="reboot into the bootloader and stay")
    ap.add_argument("--boot-app", action="store_true", help="tell a resident bootloader to JUMP")
    ap.add_argument("--revert", action="store_true",
                    help="copy the backup image (the one the last update displaced) back into the run slot")
    ap.add_argument("--expect-rev", default=None,
                    help="with --revert: the rev that must come back (the board's rev before the update)")
    ap.add_argument("--manifest", default=flash_manifest.DEFAULT_PATH,
                    help="flash manifest to append to on a confirmed flash (default: "
                         "%(default)s -- as ROOT that is /root; pass the login user's)")
    ap.add_argument("--no-manifest", action="store_true", help="do not record this flash")
    ap.add_argument("--record-variant", default="unknown",
                    help="what the image is, for the record: 'release' for a published "
                         "release asset (implies no diagnostic probe)")
    ap.add_argument("--record-tag", default=None, help="release tag, for the record")
    bench = ap.add_argument_group(
        "bench diagnostics", "read-only link measurements for a board on the bench; never a write "
        "(see the module docstring)")
    bench.add_argument("--link-probe", type=int, default=None, metavar="N",
                       help="N single-try status reads, one mark each ('.' answered, 'x' no answer, "
                            "'E' exception) in rows of 50, then answered/total, longest run of misses, "
                            "whether misses alternate, and the bootloader's diag counters (register "
                            f"{DIAG_BASE}) before and after when it has them. With --inject: the probe's "
                            f"length (default {INJECT_PROBE_READS})")
    bench.add_argument("--probe-interval", type=float, default=PROBE_INTERVAL_S, metavar="S",
                       help="pause after each probe read (default %(default)s s)")
    bench.add_argument("--inject", choices=INJECT_KINDS, default=None, metavar="KIND",
                       help="BOOTLOADER ONLY: send one damaged frame (" + ", ".join(INJECT_KINDS) +
                            "), print its bytes, then a short link probe")
    bench.add_argument("--inject-seed", type=int, default=None, metavar="S",
                       help="with --inject garbage: the random seed, to repeat a run's bytes")
    args = ap.parse_args(argv[1:])
    if args.link_probe is not None and args.link_probe < 1:
        ap.error("--link-probe needs N >= 1")
    if args.probe_interval < 0:
        ap.error("--probe-interval must be >= 0")

    bus = Rtu(args.port, args.baud, args.address)
    try:
        if args.inject:
            return inject(bus, args.inject, args.link_probe or INJECT_PROBE_READS,
                          args.probe_interval, args.inject_seed)
        if args.link_probe is not None:
            return link_probe(bus, args.link_probe, args.probe_interval)
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
            ident = wait_for_stage(bus, ID_STAGE_APP, timeout=APP_START_WAIT_S)
            print(f"VERDICT: OK -- application {ident.rev_str} is running")
            return 0
        if args.revert:
            return revert(bus, args.dry_run,
                          manifest=None if args.no_manifest else args.manifest,
                          expect_rev=args.expect_rev)
        if not args.image:
            ap.error("an image, --identity, --enter-bootloader, --boot-app, --revert, --link-probe or "
                     "--inject is required")
        return flash(bus, args.image, args.dry_run,
                     manifest=None if args.no_manifest else args.manifest,
                     variant=args.record_variant, tag=args.record_tag)
    finally:
        bus.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
