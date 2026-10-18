#!/usr/bin/env python3
"""isr-gap.py -- read or reset the isr-gap probe (diagnostic schema 8).

Runs ON THE PROBE HOST with the UI STOPPED: the UI owns the serial port. Uses
modbus-flash.py's RTU master (pyserial only), so it needs nothing from the
UI's venv.

    isr-gap.py                   read the current window and print it
    isr-gap.py --reset           start a fresh window, then print it
    isr-gap.py --json            print the decoded window as JSON

It refuses unless the identity window shows a running application whose
register layout this script knows, and the scratchpad names schema 8. Every
read also reports whether an ELS job is engaged (elsStop.enable): the revert
to release that ends the bench procedure is refused while one is, and with
the UI stopped the job cannot be seen any other way. The
probe and its registers are documented in fw/DIAG.md and
Core/Inc/els_diag_isr_gap.h; the word map below mirrors that header, and
scripts/lib/isr-gap-test.py fails if the two drift.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

SCHEMA_ISR_GAP = 8

# Absolute holding registers. elsStop starts at register 104 (ui
# els_stop_map.ELS_STOP_BASE); the offsets are the elsStop-relative ones in
# docs/reference/register-map.md. Valid for the layouts listed below and no
# other: a protocolVersion bump may move them.
KNOWN_APP_PROTOCOLS = {12}
ELS_STOP_BASE = 104
REG_ENABLE = ELS_STOP_BASE + 0          # elsStop.enable: an ELS job is engaged
REG_DIAG_SEQ = ELS_STOP_BASE + 40
REG_BLOCK = ELS_STOP_BASE + 78          # diagSchema, first register of the block
BLOCK_REGS = 64
REG_RESET = ELS_STOP_BASE + 137         # diagEndReason: the reset request
RESET_REQ = 1                           # ELS_DIAG_GAP_RESET_REQ

# Block-relative register indexes.
B_SCHEMA, B_BUCKET_TICKS, B_BUCKET_COUNT = 0, 1, 2
B_WORST = 4            # diagSettleTicks, int32, low word first
B_TICKS = 6            # diagNetCounts, uint32
B_TRACE = 8            # diagTrace[0]
B_PEAK = 58            # diagCaptureTicks
B_RESET = 59           # diagEndReason

# diagTrace word map: ELS_DIAG_GAP_W_* in Core/Inc/els_diag_isr_gap.h.
W_MIN = 0
W_OVER = 2
W_THRESH = 10
W_NOMINAL = 14
W_WORST_TICK = 15
W_WORST_PREV = 17
W_WORST_MMODE = 18
W_WORST_SERVO = 19
W_WORST_ENABLE = 20
W_WORST_ACTIVE = 21
W_WORST_TAKEUP = 22
W_WINDOW = 23
WORDS = 24
THRESHOLDS = 4

CORE_HZ = 100_000_000   # cycles to microseconds, for display only


def _load_flash_module():
    spec = importlib.util.spec_from_file_location("modbus_flash", HERE / "modbus-flash.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(mod)
    return mod


def decode(block: list[int]) -> dict:
    """Decode the 64 raw block registers. Pure: no I/O, no assumptions about
    the rate beyond what the block publishes."""
    if len(block) != BLOCK_REGS:
        raise ValueError(f"expected {BLOCK_REGS} registers, got {len(block)}")
    if block[B_SCHEMA] != SCHEMA_ISR_GAP:
        raise ValueError(f"diagSchema is {block[B_SCHEMA]}, not {SCHEMA_ISR_GAP}")

    def u32(i: int) -> int:
        return block[i] | (block[i + 1] << 16)

    def w(i: int) -> int:
        return block[B_TRACE + i]

    def w32(i: int) -> int:
        return w(i) | (w(i + 1) << 16)

    thresholds = [w(W_THRESH + i) for i in range(THRESHOLDS)]
    return {
        "schema": block[B_SCHEMA],
        "window": w(W_WINDOW),
        "ticks": u32(B_TICKS),
        "nominal_cycles": w(W_NOMINAL),
        "worst_gap_cycles": u32(B_WORST),
        "min_gap_cycles": w32(W_MIN),
        "isr_peak_cycles": block[B_PEAK],
        "thresholds_cycles": thresholds,
        "over_counts": [w32(W_OVER + 2 * i) for i in range(THRESHOLDS)],
        "worst": {
            "tick": w32(W_WORST_TICK),
            "prev_tick_cycles": w(W_WORST_PREV),
            "machine_mode": w(W_WORST_MMODE),
            "servo_mode": w(W_WORST_SERVO),
            "enable": w(W_WORST_ENABLE),
            "active": w(W_WORST_ACTIVE),
            "takeup_pending": w(W_WORST_TAKEUP),
        },
        "reset_pending": block[B_RESET] != 0,
    }


def render(d: dict) -> str:
    nom = d["nominal_cycles"] or 1
    us = 1e6 / CORE_HZ

    def cyc(c: int) -> str:
        return f"{c} cycles ({c * us:.2f} us, {c / nom:.2f}x nominal)"

    wst = d["worst"]
    lines = [
        f"isr-gap window {d['window']}: {d['ticks']} ticks "
        f"({d['ticks'] / (CORE_HZ / nom):.1f} s at the nominal rate)",
        f"  worst gap     {cyc(d['worst_gap_cycles'])}",
        f"  shortest gap  {cyc(d['min_gap_cycles'])}",
        f"  ISR run peak  {cyc(d['isr_peak_cycles'])}",
        "  late ticks:",
    ]
    for t, n in zip(d["thresholds_cycles"], d["over_counts"]):
        lines.append(f"    gap > {t:5d} cycles ({t / nom:.2f}x): {n}")
    lines += [
        f"  at the worst gap: window tick {wst['tick']}, previous tick ran "
        f"{wst['prev_tick_cycles']} cycles",
        f"    machineMode={wst['machine_mode']} servoMode={wst['servo_mode']} "
        f"enable={wst['enable']} active={wst['active']} "
        f"takeupPending={wst['takeup_pending']}",
    ]
    if d["reset_pending"]:
        lines.append("  RESET REQUESTED, not yet acknowledged")
    if d.get("els_job_engaged"):
        lines.append(f"  {ENGAGED_NOTE}")
    return "\n".join(lines)


# Printed with the window when elsStop.enable is set. The firmware refuses to
# reboot into its bootloader while it is (Ramps.c elsBootCommandTick), so the
# revert to release that ends the bench procedure would be refused: it was on
# 2026-10-17, with the stop and retract cycle still armed after the last pass.
# This script only says so. Disengaging ends the operator's job, so it is done
# at the touchscreen, never by writing the register from here.
ENGAGED_NOTE = ("ELS JOB ENGAGED (elsStop.enable is set): a revert to release is refused "
                "until it is cleared. Start the UI, press Disengage at the touchscreen, "
                "and stop the UI again before reverting.")


def read_stable(bus, attempts: int = 5) -> list[int]:
    """Read the block until two reads agree on everything the ISR writes only
    at a new worst gap, with diagSeq unchanged across them. The tick and
    late-tick counters still move between reads; the second read is used."""
    for _ in range(attempts):
        s0 = bus.read(REG_DIAG_SEQ, 1)[0]
        a = bus.read(REG_BLOCK, BLOCK_REGS)
        b = bus.read(REG_BLOCK, BLOCK_REGS)
        s1 = bus.read(REG_DIAG_SEQ, 1)[0]
        fixed = [B_WORST, B_WORST + 1] + [B_TRACE + i for i in range(W_WORST_TICK, WORDS)]
        if s0 == s1 and all(a[i] == b[i] for i in fixed):
            return b
        time.sleep(0.05)
    raise RuntimeError("block kept changing under the read; try again")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", default="/dev/serial0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--address", type=int, default=17)
    ap.add_argument("--reset", action="store_true", help="start a fresh window, then read it")
    ap.add_argument("--json", action="store_true", help="print the decoded window as JSON")
    args = ap.parse_args()

    mf = _load_flash_module()
    bus = mf.Rtu(args.port, args.baud, args.address)
    try:
        ident = mf.read_identity(bus)
        if ident.magic != mf.ID_MAGIC or ident.stage != mf.ID_STAGE_APP:
            print(f"isr-gap: not a running application ({ident}); refusing", file=sys.stderr)
            return 2
        if ident.app_protocol not in KNOWN_APP_PROTOCOLS:
            print(f"isr-gap: appProtocol {ident.app_protocol} is not one this script's "
                  f"register addresses are valid for ({sorted(KNOWN_APP_PROTOCOLS)}); refusing",
                  file=sys.stderr)
            return 2
        schema = bus.read(REG_BLOCK, 1)[0]
        if schema != SCHEMA_ISR_GAP:
            print(f"isr-gap: diagSchema is {schema}; this board is not running the "
                  f"isr-gap probe (0 = release build). Refusing.", file=sys.stderr)
            return 2

        if args.reset:
            before = bus.read(REG_BLOCK + B_TRACE + W_WINDOW, 1)[0]
            try:
                bus.write_one(REG_RESET, RESET_REQ)
            except mf.ExceptionResponse as e:
                # diagEndReason is classed ro_firmware in the register schema;
                # firmware that enforces access classes refuses the write. A
                # power cycle also opens a window (the probe starts one at boot).
                print(f"isr-gap: the board refused the reset write ({e}); "
                      f"power-cycle to open a window instead", file=sys.stderr)
                return 1
            deadline = time.monotonic() + 2.0
            while bus.read(REG_RESET, 1)[0] != 0:
                if time.monotonic() > deadline:
                    print("isr-gap: reset not acknowledged within 2 s", file=sys.stderr)
                    return 1
                time.sleep(0.02)
            after = bus.read(REG_BLOCK + B_TRACE + W_WINDOW, 1)[0]
            print(f"isr-gap: reset acknowledged, window {before} -> {after}")

        d = decode(read_stable(bus))
        d["board_rev"] = ident.rev_str
        d["els_job_engaged"] = bus.read(REG_ENABLE, 1)[0] != 0
        print(json.dumps(d, indent=2) if args.json else render(d))
        if args.json and d["els_job_engaged"]:
            # The JSON is usually redirected to a file; the warning must not go with it.
            print(f"isr-gap: {ENGAGED_NOTE}", file=sys.stderr)
        return 0
    finally:
        bus.close()


if __name__ == "__main__":
    sys.exit(main())
