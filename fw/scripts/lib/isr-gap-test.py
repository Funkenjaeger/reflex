#!/usr/bin/env python3
"""isr-gap-test.py -- scripts/isr-gap.py against the firmware it reads.

Stdlib only. Four things, each of which would otherwise fail silently on the
lathe as a plausible wrong number:

  1. the script's diagTrace word map equals ELS_DIAG_GAP_W_* in the probe
     header (a drifted index reads a neighbour's value);
  2. its absolute register addresses equal docs/reference/register-map.md;
  3. decode() of a known block;
  4. the read/reset flow against a fake board: refusals, the reset handshake.

    python3 scripts/lib/isr-gap-test.py      (from fw/)
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
import types
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

FW = Path(__file__).resolve().parents[2]
REPO = FW.parent
HEADER = FW / "Core/Inc/els_diag_isr_gap.h"
RAMPS_H = FW / "Core/Inc/Ramps.h"
REGMAP = REPO / "docs/reference/register-map.md"

failures = 0


def check(cond: bool, what: str) -> None:
    global failures
    print(f"   {what:<66} {'ok' if cond else 'FAIL'}")
    if not cond:
        failures += 1


def load_script():
    spec = importlib.util.spec_from_file_location("isr_gap", FW / "scripts/isr-gap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def header_words() -> dict[str, int]:
    text = HEADER.read_text()
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"^#define\s+ELS_DIAG_GAP_W_([A-Z_]+)\s+(\d+)", text, re.M)}


def regmap_abs(name: str) -> int:
    for line in REGMAP.read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 4 and cells[4] == f"`{name}`":
            return int(cells[2])
    raise KeyError(name)


class FakeBoard:
    """The application's holding registers, with the ISR's reset handshake."""

    def __init__(self, gs, schema=8, protocol=12, stage=2, ack=True):
        self.gs = gs
        self.regs = {}
        self.ack = ack
        self.writes = []
        self.regs[gs.REG_BLOCK + gs.B_SCHEMA] = schema
        self.regs[gs.REG_BLOCK + gs.B_TRACE + gs.W_NOMINAL] = 2000
        self.regs[gs.REG_BLOCK + gs.B_TRACE + gs.W_WINDOW] = 0
        self.regs[gs.REG_BLOCK + gs.B_WORST] = 4100
        self.regs[gs.REG_DIAG_SEQ] = 5
        self.ident = types.SimpleNamespace(magic=0x454C, stage=stage, app_protocol=protocol,
                                               rev_str="abcdef0")

    def read(self, addr, count, **_):
        return [self.regs.get(addr + i, 0) for i in range(count)]

    def write_one(self, addr, value, **_):
        self.writes.append((addr, value))
        gs = self.gs
        if addr == gs.REG_RESET and self.ack:
            # The ISR's next tick: clear, bump the window, acknowledge.
            for i in range(gs.BLOCK_REGS):
                if i not in (gs.B_SCHEMA,):
                    self.regs.pop(gs.REG_BLOCK + i, None)
            self.regs[gs.REG_BLOCK + gs.B_TRACE + gs.W_NOMINAL] = 2000
            self.regs[gs.REG_BLOCK + gs.B_TRACE + gs.W_WINDOW] = 1
            self.regs[gs.REG_DIAG_SEQ] += 1
        elif addr == gs.REG_RESET:
            self.regs[addr] = value

    def close(self):
        pass


def run_main(gs, board, argv):
    fake = types.SimpleNamespace(
        Rtu=lambda *a, **k: board,
        read_identity=lambda bus: board.ident,
        ID_MAGIC=0x454C, ID_STAGE_APP=2)
    gs._load_flash_module = lambda: fake
    gs.time.sleep = lambda s: None
    old = sys.argv
    sys.argv = ["isr-gap.py"] + argv
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = gs.main()
    finally:
        sys.argv = old
    return rc, out.getvalue(), err.getvalue()


def main() -> int:
    gs = load_script()
    print("=== isr-gap.py ===")

    print("-- 1. word map matches the probe header --")
    hw = header_words()
    check(len(hw) >= 12, f"parsed {len(hw)} ELS_DIAG_GAP_W_* defines")
    for name, val in sorted(hw.items(), key=lambda kv: kv[1]):
        check(getattr(gs, "W_" + name, None) == val, f"W_{name} == {val}")
    words = re.search(r"^#define\s+ELS_DIAG_GAP_WORDS\s+(\d+)", HEADER.read_text(), re.M)
    check(words is not None and gs.WORDS == int(words.group(1)), "WORDS matches")
    sid = re.search(r"^#define\s+ELS_DIAG_SCHEMA_ISR_GAP\s+(\d+)", RAMPS_H.read_text(), re.M)
    check(sid is not None and gs.SCHEMA_ISR_GAP == int(sid.group(1)), "schema id matches Ramps.h")
    req = re.search(r"^#define\s+ELS_DIAG_GAP_RESET_REQ\s+(\d+)u?", HEADER.read_text(), re.M)
    check(req is not None and gs.RESET_REQ == int(req.group(1)), "reset request value matches")

    print("-- 2. absolute addresses match the register map --")
    check(gs.REG_DIAG_SEQ == regmap_abs("diagSeq"), "diagSeq")
    check(gs.REG_BLOCK == regmap_abs("diagSchema"), "diagSchema (block start)")
    check(gs.REG_RESET == regmap_abs("diagEndReason"), "diagEndReason (reset request)")
    check(gs.REG_BLOCK + gs.B_WORST == regmap_abs("diagSettleTicks"), "diagSettleTicks (worst gap)")
    check(gs.REG_BLOCK + gs.B_TICKS == regmap_abs("diagNetCounts"), "diagNetCounts (ticks)")
    check(gs.REG_BLOCK + gs.B_TRACE == regmap_abs("diagTrace"), "diagTrace")
    check(gs.REG_BLOCK + gs.B_PEAK == regmap_abs("diagCaptureTicks"), "diagCaptureTicks (run peak)")
    check(regmap_abs("diagReserved") + 3 == gs.REG_BLOCK + gs.BLOCK_REGS - 1,
          "block is 64 registers ending at diagReserved[3]")

    print("-- 3. decode --")
    blk = [0] * gs.BLOCK_REGS
    blk[gs.B_SCHEMA] = 8
    blk[gs.B_BUCKET_COUNT] = gs.WORDS
    blk[gs.B_WORST], blk[gs.B_WORST + 1] = 0x2345, 0x0001          # 74565
    blk[gs.B_TICKS], blk[gs.B_TICKS + 1] = 0x86A0, 0x0001          # 100000
    blk[gs.B_PEAK] = 1500
    t = gs.B_TRACE
    blk[t + gs.W_MIN] = 1800
    for i, n in enumerate((70000, 12, 3, 1)):
        blk[t + gs.W_OVER + 2 * i] = n & 0xFFFF
        blk[t + gs.W_OVER + 2 * i + 1] = n >> 16
    for i, v in enumerate((2100, 2500, 3000, 4000)):
        blk[t + gs.W_THRESH + i] = v
    blk[t + gs.W_NOMINAL] = 2000
    blk[t + gs.W_WORST_TICK] = 4242
    blk[t + gs.W_WORST_PREV] = 1999
    blk[t + gs.W_WORST_MMODE] = 6
    blk[t + gs.W_WORST_SERVO] = 1
    blk[t + gs.W_WORST_ENABLE] = 1
    blk[t + gs.W_WORST_TAKEUP] = 1
    blk[t + gs.W_WINDOW] = 3
    d = gs.decode(blk)
    check(d["worst_gap_cycles"] == 74565, "worst gap is a full uint32, low word first")
    check(d["ticks"] == 100000, "tick count is a full uint32")
    check(d["over_counts"] == [70000, 12, 3, 1], "late-tick counts")
    check(d["thresholds_cycles"] == [2100, 2500, 3000, 4000], "thresholds")
    check(d["worst"] == {"tick": 4242, "prev_tick_cycles": 1999, "machine_mode": 6,
                         "servo_mode": 1, "enable": 1, "active": 0, "takeup_pending": 1},
          "snapshot at the worst gap")
    check(d["window"] == 3 and d["min_gap_cycles"] == 1800 and d["isr_peak_cycles"] == 1500,
          "window, min gap, run peak")
    check("37.28x nominal" in gs.render(d), "render states the worst gap against nominal")
    try:
        gs.decode([0] * gs.BLOCK_REGS)
        check(False, "decode refuses schema 0")
    except ValueError:
        check(True, "decode refuses schema 0")

    print("-- 4. read / reset against a fake board --")
    rc, out, err = run_main(gs, FakeBoard(gs), [])
    check(rc == 0 and "worst gap     4100 cycles" in out, "read prints the window")
    b = FakeBoard(gs)
    rc, out, err = run_main(gs, b, ["--reset"])
    check(rc == 0 and b.writes == [(gs.REG_RESET, 1)], "reset writes 1 to diagEndReason, once")
    check("window 0 -> 1" in out and "worst gap     0 cycles" in out,
          "reset waits for the acknowledgement and prints the fresh window")
    rc, out, err = run_main(gs, FakeBoard(gs, ack=False), ["--reset"])
    check(rc == 1 and "not acknowledged" in err, "an unacknowledged reset is a failure")
    b = FakeBoard(gs, schema=0)
    rc, out, err = run_main(gs, b, ["--reset"])
    check(rc == 2 and b.writes == [] and "release build" in err,
          "schema 0 is refused before any write")
    b = FakeBoard(gs, schema=7)
    rc, out, err = run_main(gs, b, ["--reset"])
    check(rc == 2 and b.writes == [], "another probe's schema is refused before any write")
    b = FakeBoard(gs, protocol=13)
    rc, out, err = run_main(gs, b, ["--reset"])
    check(rc == 2 and b.writes == [], "an unknown register layout is refused before any write")
    b = FakeBoard(gs, stage=1)
    rc, out, err = run_main(gs, b, [])
    check(rc == 2 and "not a running application" in err, "the bootloader is refused")

    print(f"=== {'ALL PASS' if failures == 0 else 'FAILURES'} ===")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
