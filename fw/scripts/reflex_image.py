#!/usr/bin/env python3
"""reflex_image.py -- the host side of the reflex application image format.

The format is defined ONCE, in fw/Core/Inc/els_identity.h (elsImageHeader_t
and the ELS_IMAGE_* constants); this module is its Python rendering and is
imported by scripts/modbus-flash.py. Two things live here:

  * stm32_crc32(words) -- the STM32 CRC unit's variant of CRC-32: polynomial
    0x04C11DB7, initial value 0xFFFFFFFF, 32-bit words fed MSB-first, NO
    input/output reflection, NO final XOR. It is NOT zlib.crc32 and the two
    agree on nothing. Words are the image's bytes taken as little-endian
    uint32, which is how the CRC->DR register sees a word stored in memory.
  * the header patch: the firmware compiles imageLength and crc32 as zero
    placeholders (Core/Src/image_header.c); `patch` fills them into the .bin
    and then re-reads the file to prove the write landed.

Usage:
    reflex_image.py patch  <image.bin>      fill length + CRC in place
    reflex_image.py info   <image.bin>      parse and validate, exit 1 if bad
    reflex_image.py crc    <file>           print the STM32 CRC32 of a file
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass

# --- mirrors of els_identity.h ------------------------------------------------
IMAGE_HEADER_OFFSET = 0x200
IMAGE_HEADER_SIZE = 32
IMAGE_MAGIC = 0x584C4652          # "RFLX" as little-endian bytes
IMAGE_HEADER_VERSION = 1
IMAGE_FLAG_DIRTY = 0x0001
IMAGE_MIN_LENGTH = IMAGE_HEADER_OFFSET + IMAGE_HEADER_SIZE
SLOT_SIZE = 0x20000
IMAGE_MAX_LENGTH = SLOT_SIZE

RUN_SLOT_BASE = 0x08020000
STAGING_SLOT_BASE = 0x08040000
BACKUP_SLOT_BASE = 0x08060000

# Header layout: magic u32, headerVersion u16, flags u16, imageLength u32,
# crc32 u32, buildRev u32, reserved u32[3]; little-endian, 32 bytes.
_HEADER_FMT = "<IHHIII3I"
assert struct.calcsize(_HEADER_FMT) == IMAGE_HEADER_SIZE
CRC_FIELD_OFFSET = IMAGE_HEADER_OFFSET + 12   # byte offset of crc32 in the image

_POLY = 0x04C11DB7


def _make_table() -> list[int]:
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ _POLY) if (c & 0x80000000) else (c << 1)
            c &= 0xFFFFFFFF
        table.append(c)
    return table


_TABLE = _make_table()


def stm32_crc32_words(words, init: int = 0xFFFFFFFF) -> int:
    """CRC over an iterable of 32-bit words, the way the STM32 CRC unit does it."""
    crc = init
    for w in words:
        w &= 0xFFFFFFFF
        for shift in (24, 16, 8, 0):
            b = (w >> shift) & 0xFF
            crc = ((crc << 8) & 0xFFFFFFFF) ^ _TABLE[((crc >> 24) ^ b) & 0xFF]
    return crc


def stm32_crc32_bitwise(words, init: int = 0xFFFFFFFF) -> int:
    """Independent bit-serial implementation, kept only to cross-check the
    table version (tests compare the two on random input)."""
    crc = init
    for w in words:
        crc ^= (w & 0xFFFFFFFF)
        for _ in range(32):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ _POLY) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc


def bytes_to_words(data: bytes) -> list[int]:
    if len(data) % 4:
        raise ValueError(f"length {len(data)} is not a multiple of 4")
    return list(struct.unpack("<%dI" % (len(data) // 4), data))


def image_crc(data: bytes) -> int:
    """CRC of a whole image with the crc32 header field taken as zero."""
    words = bytes_to_words(data)
    words[CRC_FIELD_OFFSET // 4] = 0
    return stm32_crc32_words(words)


@dataclass
class ImageHeader:
    magic: int
    header_version: int
    flags: int
    image_length: int
    crc32: int
    build_rev: int

    @property
    def dirty(self) -> bool:
        return bool(self.flags & IMAGE_FLAG_DIRTY)

    @property
    def rev_str(self) -> str:
        return f"{self.build_rev:07x}" + ("-dirty" if self.dirty else "")


def parse_header(data: bytes) -> ImageHeader:
    if len(data) < IMAGE_MIN_LENGTH:
        raise ValueError(f"image is {len(data)} bytes, shorter than the header at +0x200")
    fields = struct.unpack_from(_HEADER_FMT, data, IMAGE_HEADER_OFFSET)
    return ImageHeader(*fields[:6])


def validate(data: bytes) -> ImageHeader:
    """Raise ValueError naming the first thing wrong, else return the header.
    The checks and their order mirror blImageValidate() in the bootloader."""
    h = parse_header(data)
    if h.magic != IMAGE_MAGIC:
        raise ValueError(f"bad magic 0x{h.magic:08x} (want 0x{IMAGE_MAGIC:08x})")
    if h.header_version != IMAGE_HEADER_VERSION:
        raise ValueError(f"header version {h.header_version} (want {IMAGE_HEADER_VERSION})")
    if (h.image_length < IMAGE_MIN_LENGTH or h.image_length > IMAGE_MAX_LENGTH
            or h.image_length % 4):
        raise ValueError(f"bad image length {h.image_length}")
    if h.image_length > len(data):
        raise ValueError(f"header length {h.image_length} exceeds file length {len(data)}")
    calc = image_crc(data[:h.image_length])
    if calc != h.crc32:
        raise ValueError(f"CRC mismatch: header 0x{h.crc32:08x}, computed 0x{calc:08x}")
    return h


def pad4(data: bytes) -> bytes:
    return data + b"\xff" * ((4 - len(data) % 4) % 4)


def patch(data: bytes) -> bytes:
    """Return the image with imageLength and crc32 filled in."""
    data = pad4(bytes(data))
    h = parse_header(data)
    if h.magic != IMAGE_MAGIC:
        raise ValueError(f"no image header magic at +0x{IMAGE_HEADER_OFFSET:x}: "
                         "was this built with REFLEX_APP_BASE=0x08020000?")
    if len(data) > IMAGE_MAX_LENGTH:
        raise ValueError(f"image {len(data)} bytes exceeds the {IMAGE_MAX_LENGTH}-byte slot")
    buf = bytearray(data)
    struct.pack_into("<I", buf, IMAGE_HEADER_OFFSET + 8, len(buf))   # imageLength
    struct.pack_into("<I", buf, CRC_FIELD_OFFSET, 0)
    crc = image_crc(bytes(buf))
    struct.pack_into("<I", buf, CRC_FIELD_OFFSET, crc)
    return bytes(buf)


def _cmd_patch(path: str) -> int:
    with open(path, "rb") as f:
        before = f.read()
    out = patch(before)
    with open(path, "wb") as f:
        f.write(out)
    # GATE ON THE SIGNAL: re-read the file and validate it. A patch that did
    # not land, or landed wrong, fails here rather than at the bootloader.
    with open(path, "rb") as f:
        after = f.read()
    if after != out:
        print(f"reflex_image: {path}: readback differs from what was written", file=sys.stderr)
        return 1
    try:
        h = validate(after)
    except ValueError as e:
        print(f"reflex_image: {path}: patched image does not validate: {e}", file=sys.stderr)
        return 1
    print(f"reflex_image: {path}: rev {h.rev_str}, {h.image_length} bytes, "
          f"crc32 0x{h.crc32:08x}")
    return 0


def _cmd_info(path: str) -> int:
    with open(path, "rb") as f:
        data = f.read()
    try:
        h = validate(data)
    except ValueError as e:
        print(f"{path}: INVALID: {e}")
        return 1
    print(f"{path}: rev {h.rev_str}, {h.image_length} bytes "
          f"(file {len(data)}), crc32 0x{h.crc32:08x}, header v{h.header_version}")
    return 0


def _cmd_crc(path: str) -> int:
    with open(path, "rb") as f:
        data = pad4(f.read())
    print(f"0x{stm32_crc32_words(bytes_to_words(data)):08x}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("patch", "info", "crc"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return {"patch": _cmd_patch, "info": _cmd_info, "crc": _cmd_crc}[argv[1]](argv[2])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
