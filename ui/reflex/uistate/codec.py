"""Binary codec for UI state snapshots.

A snapshot is a fixed, ordered list of field values (see :mod:`reflex.uistate.schema`).
This module turns that list into a short textual **code** and back again:

    values  ->  packed binary  ->  zlib  ->  Crockford base32  ->  "R1.C5K2..."

WHY CROCKFORD BASE32 AND NOT BASE64. The code has to survive being read off a
1024x600 touchscreen log viewer and typed back into a terminal. Crockford's
alphabet is case-insensitive and has no ``0``/``O`` or ``1``/``I``/``l``
confusion, and it is safe in filenames and URLs without escaping. That costs
about 20% over base64 and is worth it for a diagnostic whose whole point is that
a human can move it around by hand.

INTEGRITY IS THE ZLIB WRAPPER'S JOB. ``zlib.compress`` emits an Adler-32 trailer
and ``zlib.decompress`` raises on a corrupted stream, so a mistyped code fails
loudly at decompression rather than decoding into plausible nonsense. There is
deliberately no second checksum on top.

BOOLEANS ARE BIT-PACKED. Most of the declared fields are flags -- every banner,
every enable, every validity light -- so they are collected into one leading
bitfield rather than costing a byte each. The field ORDER is what makes this
work, which is why a schema's field order is frozen once its id is issued.
"""

import struct
import zlib

# Crockford base32: no I, L, O, U -- so no character can be confused with
# another when read off a screen or dictated.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}
# Crockford's documented aliases, plus lowercase.
_DECODE.update({"I": 1, "L": 1, "O": 0})
_DECODE.update({c.lower(): v for c, v in list(_DECODE.items()) if c.isalpha()})


class CodecError(ValueError):
    """A code could not be decoded. Never raised for a code this UI produced."""


# ── primitives ─────────────────────────────────────────────────────────────

def put_uvarint(out: bytearray, value: int) -> None:
    if value < 0:
        raise ValueError(f"uvarint cannot encode {value}")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return


def get_uvarint(buf: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise CodecError("truncated varint")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise CodecError("varint too long")


def _zigzag(value: int) -> int:
    return (value << 1) ^ (value >> 63) if value < 0 else value << 1


def _unzigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


# ── base32 ─────────────────────────────────────────────────────────────────

def b32_encode(data: bytes) -> str:
    if not data:
        return ""
    bits = int.from_bytes(data, "big")
    width = len(data) * 8
    # Left-align into whole 5-bit groups; the trailing pad bits are zero, and
    # b32_decode recovers the byte count from the character count.
    pad = (-width) % 5
    bits <<= pad
    chars = [_ALPHABET[(bits >> shift) & 0x1F]
             for shift in range((width + pad) - 5, -5, -5)]
    return "".join(chars)


def b32_decode(text: str) -> bytes:
    # Crockford allows "-" as a visual separator; whitespace and newlines creep
    # in whenever a code is copied out of a log or wrapped in an email.
    text = "".join(c for c in text if c != "-" and not c.isspace())
    if not text:
        return b""
    bits = 0
    for char in text:
        try:
            bits = (bits << 5) | _DECODE[char]
        except KeyError:
            raise CodecError(f"invalid character {char!r} in code") from None
    nbytes = (len(text) * 5) // 8
    bits >>= (len(text) * 5) - (nbytes * 8)
    return bits.to_bytes(nbytes, "big")


# ── value packing ──────────────────────────────────────────────────────────
#
# Kind values are the ints declared in schema.Kind; imported lazily-by-value to
# keep this module free of a circular import back to the schema.

KIND_BOOL = 1
KIND_UINT = 2
KIND_INT = 3
KIND_FLOAT = 4
KIND_STR = 5


def pack(kinds: list[int], values: list) -> bytes:
    """Pack values (in declaration order) against their kinds."""
    if len(kinds) != len(values):
        raise ValueError(f"{len(kinds)} kinds but {len(values)} values")

    flags = [bool(v) for k, v in zip(kinds, values) if k == KIND_BOOL]
    out = bytearray()
    for start in range(0, len(flags), 8):
        byte = 0
        for offset, flag in enumerate(flags[start:start + 8]):
            if flag:
                byte |= 1 << offset
        out.append(byte)

    for kind, value in zip(kinds, values):
        if kind == KIND_BOOL:
            continue
        if kind == KIND_UINT:
            put_uvarint(out, int(value))
        elif kind == KIND_INT:
            put_uvarint(out, _zigzag(int(value)))
        elif kind == KIND_FLOAT:
            out.extend(struct.pack("<f", float(value)))
        elif kind == KIND_STR:
            encoded = str(value).encode("utf-8")
            put_uvarint(out, len(encoded))
            out.extend(encoded)
        else:
            raise ValueError(f"unknown kind {kind}")
    return bytes(out)


def unpack(kinds: list[int], buf: bytes, pos: int = 0) -> tuple[list, int]:
    """Inverse of :func:`pack`. Returns (values, position after the record)."""
    n_bools = sum(1 for k in kinds if k == KIND_BOOL)
    n_flag_bytes = (n_bools + 7) // 8
    if pos + n_flag_bytes > len(buf):
        raise CodecError("truncated flag field")
    flag_bytes = buf[pos:pos + n_flag_bytes]
    pos += n_flag_bytes
    flags = [bool(flag_bytes[i // 8] & (1 << (i % 8))) for i in range(n_bools)]

    values = []
    flag_index = 0
    for kind in kinds:
        if kind == KIND_BOOL:
            values.append(flags[flag_index])
            flag_index += 1
        elif kind == KIND_UINT:
            value, pos = get_uvarint(buf, pos)
            values.append(value)
        elif kind == KIND_INT:
            value, pos = get_uvarint(buf, pos)
            values.append(_unzigzag(value))
        elif kind == KIND_FLOAT:
            if pos + 4 > len(buf):
                raise CodecError("truncated float")
            values.append(struct.unpack("<f", buf[pos:pos + 4])[0])
            pos += 4
        elif kind == KIND_STR:
            length, pos = get_uvarint(buf, pos)
            if pos + length > len(buf):
                raise CodecError("truncated string")
            try:
                values.append(buf[pos:pos + length].decode("utf-8"))
            except UnicodeDecodeError as e:
                raise CodecError(f"bad utf-8 in string field: {e}") from None
            pos += length
        else:
            raise ValueError(f"unknown kind {kind}")
    return values, pos


# ── digests ────────────────────────────────────────────────────────────────

def pack_digests(digests: dict[str, int]) -> bytes:
    """Pack the hierarchical drift digests: name -> 32-bit value.

    Sorted by name so the same tree always produces the same bytes.
    """
    out = bytearray()
    put_uvarint(out, len(digests))
    for name in sorted(digests):
        encoded = name.encode("utf-8")
        put_uvarint(out, len(encoded))
        out.extend(encoded)
        out.extend(struct.pack("<I", digests[name] & 0xFFFFFFFF))
    return bytes(out)


def unpack_digests(buf: bytes, pos: int = 0) -> tuple[dict[str, int], int]:
    count, pos = get_uvarint(buf, pos)
    digests = {}
    for _ in range(count):
        length, pos = get_uvarint(buf, pos)
        if pos + length + 4 > len(buf):
            raise CodecError("truncated digest block")
        name = buf[pos:pos + length].decode("utf-8")
        pos += length
        digests[name] = struct.unpack("<I", buf[pos:pos + 4])[0]
        pos += 4
    return digests, pos


# ── the code itself ────────────────────────────────────────────────────────

PREFIX = "R"


def encode(schema_id: int, kinds: list[int], values: list,
           digests: dict[str, int]) -> str:
    """Build the textual code for one snapshot."""
    body = bytearray()
    put_uvarint(body, schema_id)
    body.extend(pack(kinds, values))
    body.extend(pack_digests(digests))
    return f"{PREFIX}{schema_id}.{b32_encode(zlib.compress(bytes(body), 9))}"


def peek_schema_id(code: str) -> int:
    """Read the schema id from a code's prefix without decompressing it.

    The id is in the prefix precisely so an unknown schema can be rejected --
    and named in the log line -- without trusting the payload.
    """
    head, _, _ = code.strip().partition(".")
    head = head.upper()
    if not head.startswith(PREFIX) or not head[len(PREFIX):].isdigit():
        raise CodecError(f"malformed code prefix {head!r}")
    return int(head[len(PREFIX):])


def decode(code: str, kinds_for: "callable") -> tuple[int, list, dict[str, int]]:
    """Decode a code. ``kinds_for(schema_id)`` supplies the kind list.

    Returns (schema_id, values, digests).
    """
    schema_id = peek_schema_id(code)
    _, _, payload = code.partition(".")
    if not payload:
        raise CodecError("code has no payload")
    try:
        body = zlib.decompress(b32_decode(payload))
    except zlib.error as e:
        raise CodecError(f"corrupt code: {e}") from None

    inner_id, pos = get_uvarint(body, 0)
    if inner_id != schema_id:
        raise CodecError(
            f"code prefix says schema {schema_id} but payload says {inner_id}")

    kinds = kinds_for(schema_id)
    values, pos = unpack(kinds, body, pos)
    digests, pos = unpack_digests(body, pos)
    return schema_id, values, digests
