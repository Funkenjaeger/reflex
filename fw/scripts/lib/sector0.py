#!/usr/bin/env python3
"""Classify a dump of the legacy application region, 0x08000000..0x0801FFFF.

Used by scripts/flash.sh (through lib/sector0.sh) to decide whether writing the
LEGACY layout over this board is safe. Stdlib only: it runs on the probe host,
which for this project is the Pi, whose system python has no extras.

    sector0.py classify <dump>      print a verdict, exit with its code
    sector0.py constants            print the values derived from the headers

THE QUESTION IT ANSWERS. flash.sh writes the application linked at 0x08000000.
That is only safe on a board whose sector 0 holds nothing worth keeping, and
the thing most worth keeping is the field bootloader, which lives there. Until
2026-09-11 the guard asked "is the Reflex bootloader's signature in sector 0?"
and proceeded on NO. That is a DENYLIST of one byte pattern, and it failed
dangerous three ways: a bump of ELS_ID_WINDOW_VERSION hid the bootloader from
it, anything else in sector 0 (a bootloader without this identity window, a
half-written image) read as "safe", and so did a dump that was not this
board's at all.

IT IS NOW AN ALLOWLIST. Writing proceeds only on a region positively
recognized as one of two things; everything else refuses:

  LEGACY      the legacy application is at 0x08000000. ALL of:
                (a) its vector table is plausible for an image linked at
                    0x08000000 -- initial MSP inside SRAM and word-aligned,
                    reset handler Thumb and inside this region. The same test
                    the bootloader applies before it jumps
                    (blImageVectorsPlausible, bootloader/core/bl_image.c);
                (b) an APPLICATION identity window (magic, stage 2, any
                    window version) is in the region -- the image here is a
                    Reflex application, and one linked below the RUN slot;
                (c) sector 0 has no run of ERASED_RUN_LIMIT or more 0xFF
                    bytes: it is the head of an image that fills the sector,
                    as the ~41 KB legacy app does, not a small image followed
                    by erased flash, which is what any bootloader that fits in
                    16 KB looks like.
  ERASED      sector 0 is entirely 0xFF. Nothing there to destroy -- a virgin
              board, or one whose sector 0 has been mass-erased.

  BOOTLOADER  a BOOTLOADER identity window (magic, stage 1) is in sector 0,
              whatever its window version. Refuse, and say what to do instead.
  UNKNOWN     anything else. Refuse. This is the verdict the old guard could
              not reach, and reaching it is the point.

WHY ANY WINDOW VERSION. The version word describes the window's layout, and it
will change. Matching it exactly meant a new bootloader was invisible to the
guard -- and deriving it from the header at build time would not fix that, it
would move the hole: a board still carrying a version-1 bootloader, checked
from a checkout where the header says 2, would be missed the same way. Magic
and stage identify a Reflex bootloader; the version does not need to.

WHAT IT STILL CANNOT SEE, stated so it is not mistaken for coverage: a foreign
bootloader that fills more than 16 KB minus ERASED_RUN_LIMIT of sector 0,
sitting on a board whose sectors 1-4 still hold a legacy Reflex application
(so (a), (b) and (c) all pass). That needs a bootloader of ~15 KB with no
Reflex identity window written over a legacy board without erasing sectors
1-4. It is not a state this project can produce by any documented path.

THE CONSTANTS ARE DERIVED, never retyped. Magic, stage values, flash geometry
and the SRAM bounds are parsed out of Core/Inc/els_identity.h and
bootloader/core/bl_image.c each run, on the precedent of provision.sh reading
ELS_RUN_SLOT_BASE: a shell or Python copy that drifted from the C would
classify against numbers the firmware no longer uses. If any cannot be parsed
the answer is exit 31, which the caller treats as a refusal.

EXIT CODES. The caller proceeds on 0 and 10 ONLY; any other status, including
one this file never returns (python missing is 127), is a refusal.

   0  LEGACY        10  ERASED
  20  BOOTLOADER    21  UNKNOWN
  30  dump unreadable or not exactly the region's length
  31  constants could not be derived from the headers
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

FW = Path(__file__).resolve().parents[2]          # lib -> scripts -> fw
IDENTITY_H = FW / "Core" / "Inc" / "els_identity.h"
BL_IMAGE_C = FW / "bootloader" / "core" / "bl_image.c"

# A run of this many 0xFF bytes inside sector 0 means the sector is NOT the
# head of an image larger than itself. The legacy application is one
# contiguous image of ~41 KB; its first 16 KB is code, vectors and constants.
# A kilobyte of 0xFF inside that would need a 1 KB all-0xFF constant table.
# If a legacy build ever does carry one, the result is a refusal (the safe
# direction), and lib/sector0-test.sh's real-artifact check of the legacy
# binary goes red in CI before it ships.
ERASED_RUN_LIMIT = 1024

EXIT = {"LEGACY": 0, "ERASED": 10, "BOOTLOADER": 20, "UNKNOWN": 21}
EXIT_UNREADABLE = 30
EXIT_UNDERIVABLE = 31


class Underivable(Exception):
    pass


def _define(text: str, name: str, source: Path) -> int:
    m = re.search(rf"^\s*#\s*define\s+{name}\s+(0[xX][0-9A-Fa-f]+|\d+)[uUlL]*\b",
                  text, re.MULTILINE)
    if not m:
        raise Underivable(f"cannot read {name} from {source}")
    return int(m.group(1), 0)


def constants() -> dict[str, int]:
    try:
        ident = IDENTITY_H.read_text()
        image = BL_IMAGE_C.read_text()
    except OSError as e:
        raise Underivable(str(e)) from e
    c = {name: _define(ident, name, IDENTITY_H) for name in (
        "ELS_ID_MAGIC", "ELS_ID_STAGE_BOOTLOADER", "ELS_ID_STAGE_APP",
        "ELS_FLASH_BASE", "ELS_BL_SECTOR_BASE", "ELS_BL_SECTOR_SIZE",
        "ELS_RUN_SLOT_BASE")}
    c.update({name: _define(image, name, BL_IMAGE_C)
              for name in ("SRAM_BASE_ADDR", "SRAM_SIZE")})
    # The classification below assumes the bootloader sector IS the start of
    # flash and the legacy region is what lies below the RUN slot. Assert it
    # rather than trust it: a geometry change must stop this, not reinterpret.
    if c["ELS_BL_SECTOR_BASE"] != c["ELS_FLASH_BASE"]:
        raise Underivable("ELS_BL_SECTOR_BASE != ELS_FLASH_BASE; geometry changed")
    if not c["ELS_BL_SECTOR_SIZE"] < c["ELS_RUN_SLOT_BASE"] - c["ELS_FLASH_BASE"]:
        raise Underivable("RUN slot does not lie beyond sector 0; geometry changed")
    c["REGION_SIZE"] = c["ELS_RUN_SLOT_BASE"] - c["ELS_FLASH_BASE"]
    return c


def windows(data: bytes, magic: int, limit: int | None = None):
    """(offset, stage, window_version) for every 2-byte-aligned identity window
    prefix in data[:limit]. The window is a uint16_t array, so the compiler
    aligns it to at least 2; an odd-offset match is not a window."""
    end = len(data) if limit is None else min(limit, len(data))
    needle = struct.pack("<H", magic)
    i = data.find(needle, 0, end)
    while i != -1:
        if i % 2 == 0 and i + 6 <= len(data):
            _, stage, version = struct.unpack_from("<HHH", data, i)
            yield i, stage, version
        i = data.find(needle, i + 1, end)


def longest_ff_run(data: bytes) -> int:
    best = run = 0
    for b in data:
        run = run + 1 if b == 0xFF else 0
        if run > best:
            best = run
    return best


def classify(data: bytes, c: dict[str, int]) -> tuple[str, list[str]]:
    base = c["ELS_FLASH_BASE"]
    sector0 = data[:c["ELS_BL_SECTOR_SIZE"]]

    bl = [(o, v) for o, s, v in windows(data, c["ELS_ID_MAGIC"], len(sector0))
          if s == c["ELS_ID_STAGE_BOOTLOADER"]]
    if bl:
        o, v = bl[0]
        return "BOOTLOADER", [f"bootloader identity window at 0x{base + o:08x} "
                              f"(window version {v})"]

    if sector0 == b"\xff" * len(sector0):
        return "ERASED", ["sector 0 is entirely 0xFF"]

    reasons, failed = [], False
    msp, reset = struct.unpack_from("<II", data, 0)
    sram_lo, sram_hi = c["SRAM_BASE_ADDR"], c["SRAM_BASE_ADDR"] + c["SRAM_SIZE"]
    region_hi = base + c["REGION_SIZE"]
    if sram_lo <= msp <= sram_hi and msp % 4 == 0 and reset & 1 and base <= reset < region_hi:
        reasons.append(f"vectors plausible: MSP 0x{msp:08x}, reset 0x{reset:08x}")
    else:
        failed = True
        reasons.append(f"vectors NOT an image linked at 0x{base:08x}: "
                       f"MSP 0x{msp:08x}, reset 0x{reset:08x}")

    app = [o for o, s, _ in windows(data, c["ELS_ID_MAGIC"])
           if s == c["ELS_ID_STAGE_APP"]]
    if app:
        reasons.append(f"application identity window at 0x{base + app[0]:08x}")
    else:
        failed = True
        reasons.append("NO application identity window in the region")

    run = longest_ff_run(sector0)
    if run < ERASED_RUN_LIMIT:
        reasons.append(f"sector 0 is filled (longest 0xFF run {run} bytes)")
    else:
        failed = True
        reasons.append(f"sector 0 holds a {run}-byte run of 0xFF: a small image "
                       f"followed by erased flash, not the head of the legacy app")

    return ("UNKNOWN" if failed else "LEGACY"), reasons


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "constants":
        try:
            for k, v in constants().items():
                print(f"{k}=0x{v:x}")
        except Underivable as e:
            print(f"UNDERIVABLE {e}", file=sys.stderr)
            return EXIT_UNDERIVABLE
        return 0
    if len(argv) != 3 or argv[1] != "classify":
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        c = constants()
    except Underivable as e:
        print(f"UNDERIVABLE {e}")
        return EXIT_UNDERIVABLE
    try:
        data = Path(argv[2]).read_bytes()
    except OSError as e:
        print(f"UNREADABLE {e}")
        return EXIT_UNREADABLE
    if len(data) != c["REGION_SIZE"]:
        print(f"UNREADABLE dump is {len(data)} bytes, expected {c['REGION_SIZE']}")
        return EXIT_UNREADABLE
    verdict, reasons = classify(data, c)
    print(verdict)
    for r in reasons:
        print(f"  {r}")
    return EXIT[verdict]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
