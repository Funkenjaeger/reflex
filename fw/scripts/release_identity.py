#!/usr/bin/env python3
"""release_identity.py -- read the build identity a firmware image carries, and
refuse an image that does not name the release commit cleanly.

Every reflex firmware stage -- the application in either link layout, and the
bootloader -- compiles the identity window of fw/Core/Inc/els_identity.h into
flash as a `const uint16_t[8]`:

    magic 0x454C, stage (1 bootloader / 2 application), window version 1,
    build rev low 16, build rev high 16, dirty (0/1), app protocol, reserved 0

That array is what the board answers at Modbus address 2048 and what
modbus-flash.py prints as `rev=<7 hex>[-dirty]`. The rev and the dirty flag
come from cmake/BuildRev.cmake, which runs `git rev-parse --short=7 HEAD` and
`git diff --quiet` / `git diff --cached --quiet` at BUILD time. So an image
built with uncommitted tracked changes in the tree says `-dirty`, and the rev
it names is whatever HEAD was -- not the commit that was later tagged.

This script finds the window in the .bin -- the bytes that ship, not the build
log or the generated header -- and prints the same string the board would.

Usage:
    release_identity.py show  <image.bin>
    release_identity.py check <image.bin> --stage app|bootloader --rev <sha> [--slotted]
    release_identity.py self-test

`check` exits 1 unless the image carries EXACTLY ONE identity window, of the
expected stage, naming <sha> (first 7 hex digits; the window holds 28 bits),
with the dirty flag clear. With --slotted it additionally requires a valid RFLX
image header whose buildRev and dirty flag say the same thing. Not finding a
window is a failure, never a pass: a check that cannot see the identity cannot
vouch for it.

`self-test` runs `check` against synthetic images -- one clean, and one for
each way an image can be wrong -- and exits 1 unless every case comes out the
way it must. The release workflow runs it before the real check, so every
release proves the guard can still fail.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reflex_image  # noqa: E402  (same directory; the RFLX header format)

# --- mirrors of els_identity.h ------------------------------------------------
ID_MAGIC = 0x454C
ID_WINDOW_VERSION = 1
ID_SIZE = 8
STAGE_BOOTLOADER = 1
STAGE_APP = 2
STAGES = {"bootloader": STAGE_BOOTLOADER, "app": STAGE_APP}
STAGE_NAMES = {STAGE_BOOTLOADER: "bootloader", STAGE_APP: "application"}
REV_MASK = 0x0FFFFFFF          # 28 bits: 7 hex digits

_WINDOW_FMT = "<%dH" % ID_SIZE
_WINDOW_BYTES = struct.calcsize(_WINDOW_FMT)
_MAGIC_BYTES = struct.pack("<H", ID_MAGIC)


@dataclass
class Window:
    offset: int
    stage: int
    build_rev: int
    dirty: bool
    app_protocol: int

    @property
    def stage_name(self) -> str:
        return STAGE_NAMES.get(self.stage, f"unknown({self.stage})")

    @property
    def rev_str(self) -> str:
        return f"{self.build_rev:07x}" + ("-dirty" if self.dirty else "")


def find_windows(data: bytes) -> list[Window]:
    """Every 2-byte-aligned run of 8 little-endian halfwords shaped like an
    identity window. The shape is strict on the fields that are constants
    (magic, window version, reserved) and on the ranges of the others (stage
    1/2, rev high <= 0x0FFF, dirty 0/1), so arbitrary code or data does not
    match it by accident -- and more than one match is reported, not guessed
    between."""
    found = []
    pos = data.find(_MAGIC_BYTES)
    while pos != -1:
        if pos % 2 == 0 and pos + _WINDOW_BYTES <= len(data):
            w = struct.unpack_from(_WINDOW_FMT, data, pos)
            if (w[1] in STAGE_NAMES and w[2] == ID_WINDOW_VERSION
                    and w[4] <= 0x0FFF and w[5] in (0, 1) and w[7] == 0):
                found.append(Window(offset=pos, stage=w[1], build_rev=w[3] | (w[4] << 16),
                                    dirty=bool(w[5]), app_protocol=w[6]))
        pos = data.find(_MAGIC_BYTES, pos + 1)
    return found


def rev_of(sha: str) -> int:
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        raise ValueError(f"'{sha}' is not a lowercase hex git sha of at least 7 digits")
    return int(sha[:7], 16) & REV_MASK


def check(data: bytes, stage: int, rev: int, slotted: bool) -> tuple[list[str], str]:
    """Return (problems, description). No problems means the image is a clean
    build of `rev`. The description names what was read, for the log."""
    problems: list[str] = []
    windows = find_windows(data)
    if not windows:
        return (["no identity window found -- this check cannot see what the image claims to be, "
                 "so it cannot vouch for it"], "no window")
    if len(windows) > 1:
        where = ", ".join(f"0x{w.offset:x} ({w.stage_name} rev {w.rev_str})" for w in windows)
        return ([f"{len(windows)} identity windows found ({where}); refusing to pick one"],
                "ambiguous")
    w = windows[0]
    desc = f"{w.stage_name} rev {w.rev_str} (window at +0x{w.offset:x})"
    if w.stage != stage:
        problems.append(f"stage is {w.stage_name}, expected {STAGE_NAMES[stage]}")
    if w.dirty:
        problems.append(f"identity is {w.rev_str}: built from a tree with uncommitted tracked changes")
    if w.build_rev != rev:
        problems.append(f"identity names {w.build_rev:07x}, not the release commit {rev:07x}")
    if slotted:
        try:
            h = reflex_image.validate(data)
        except ValueError as e:
            problems.append(f"--slotted, but the RFLX header does not validate: {e}")
        else:
            desc += f"; header rev {h.rev_str}"
            if h.dirty:
                problems.append(f"image header is {h.rev_str}: dirty flag set")
            if h.build_rev != rev:
                problems.append(f"image header names {h.build_rev:07x}, not the release commit {rev:07x}")
    return problems, desc


# --- self-test ----------------------------------------------------------------

def _window_bytes(stage: int, rev: int, dirty: int) -> bytes:
    return struct.pack(_WINDOW_FMT, ID_MAGIC, stage, ID_WINDOW_VERSION,
                       rev & 0xFFFF, (rev >> 16) & 0xFFFF, dirty, 10 if stage == STAGE_APP else 0, 0)


def _image(*windows: bytes) -> bytes:
    """Erased-flash filler with the given windows placed at even offsets."""
    buf = bytearray(b"\xff" * 4096)
    for i, w in enumerate(windows):
        at = 0x400 + 0x300 * i
        buf[at:at + len(w)] = w
    return bytes(buf)


def self_test() -> int:
    good, stale = 0x0a85a5d1, 0x05da4a81      # v1.2.0-rc.1's stamp commit, and its parent
    app = STAGE_APP
    cases = [
        # name, image, expected stage, expected rev, must pass
        ("clean image of the release commit", _image(_window_bytes(app, good, 0)), app, good, True),
        ("clean bootloader of the release commit",
         _image(_window_bytes(STAGE_BOOTLOADER, good, 0)), STAGE_BOOTLOADER, good, True),
        ("dirty image of the release commit", _image(_window_bytes(app, good, 1)), app, good, False),
        ("the v1.2.0-rc.1 shape: dirty, and naming the commit BEFORE the stamp",
         _image(_window_bytes(app, stale, 1)), app, good, False),
        ("clean, but naming another commit", _image(_window_bytes(app, stale, 0)), app, good, False),
        ("bootloader where the application was expected",
         _image(_window_bytes(STAGE_BOOTLOADER, good, 0)), app, good, False),
        ("no identity window at all", _image(), app, good, False),
        ("two identity windows", _image(_window_bytes(app, good, 0), _window_bytes(app, good, 0)),
         app, good, False),
    ]
    wrong = 0
    for name, data, stage, rev, must_pass in cases:
        problems, desc = check(data, stage, rev, slotted=False)
        passed = not problems
        verdict = "ok " if passed == must_pass else "BAD"
        outcome = "PASS" if passed else "FAIL (" + "; ".join(problems) + ")"
        print(f"self-test {verdict} expected {'PASS' if must_pass else 'FAIL'}, got {outcome} -- {name}")
        wrong += passed != must_pass
    if wrong:
        print(f"self-test: {wrong} case(s) came out wrong -- the identity guard cannot be trusted")
        return 1
    print(f"self-test: all {len(cases)} cases came out as they must")
    return 0


# --- CLI ------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_show = sub.add_parser("show")
    p_show.add_argument("image")
    p_check = sub.add_parser("check")
    p_check.add_argument("image")
    p_check.add_argument("--stage", required=True, choices=sorted(STAGES))
    p_check.add_argument("--rev", required=True)
    p_check.add_argument("--slotted", action="store_true")
    sub.add_parser("self-test")
    args = ap.parse_args(argv[1:])

    if args.cmd == "self-test":
        return self_test()

    with open(args.image, "rb") as f:
        data = f.read()

    if args.cmd == "show":
        windows = find_windows(data)
        if not windows:
            print(f"{args.image}: no identity window found")
            return 1
        for w in windows:
            print(f"{args.image}: {w.stage_name} rev {w.rev_str} (window at +0x{w.offset:x})")
        return 0 if len(windows) == 1 else 1

    try:
        rev = rev_of(args.rev)
    except ValueError as e:
        print(f"FAIL {args.image}: {e}")
        return 1
    problems, desc = check(data, STAGES[args.stage], rev, args.slotted)
    if problems:
        for p in problems:
            print(f"FAIL {args.image}: {p}")
        print(f"     read: {desc}")
        return 1
    print(f"ok   {args.image}: {desc}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
