"""Register-map layout contract test (Task 15 of the emulator system-test plan).

Independent, emulator-free check that reflex-ui's hand-maintained register
definitions (``reflex/utils/devices.py``) still agree, byte-for-byte, with the
firmware's authoritative struct layout (reflex-fw ``Core/Inc/Ramps.h``).

Why this exists
---------------
The Modbus register map is duplicated by hand on both sides of the RS-485 link:
the firmware owns the C structs in ``Ramps.h``; reflex-ui mirrors them as the
``definition`` strings in ``devices.py``. Nothing else checks the two still
match. Drift here is silent and nasty -- reads land at the wrong offset and come
back as plausible-looking garbage (exactly the class of the wrong-base-address
gotcha the emulator spike hit). The emulator system suite catches gross drift
only implicitly and slowly; this catches it precisely, instantly, and WITHOUT a
firmware build, so it runs in the DEFAULT test suite (it is NOT marked
``system``). It only skips when reflex-fw isn't checked out.

The subtlety this test handles
------------------------------
reflex-ui's parser (``BaseDevice.parse_addresses_from_definition``) packs fields
sequentially with NO C-alignment awareness. The firmware's real on-the-wire
layout is what the C compiler produces, *with* natural alignment and implicit
trailing padding. reflex-ui bridges the gap by hand-placing explicit ``_pad``
fields in its definitions. So a correct contract is NOT a field-name diff -- it
is: does reflex-ui's packed layout (with its manual pads) reproduce the firmware
struct's true C layout? We compute the true C offsets here as the oracle and
compare reflex-ui's actual computed offsets against them.
"""

import os
import re
from pathlib import Path

import pytest

from reflex.utils.base_device import BaseDevice
from reflex.utils.communication import ConnectionManager

REFLEX_FW_DIR = Path(os.environ.get("REFLEX_FW_DIR", "/mnt/c/projects/embedded/reflex-fw"))
RAMPS_H = REFLEX_FW_DIR / "Core" / "Inc" / "Ramps.h"
SCALES_H = REFLEX_FW_DIR / "Core" / "Inc" / "Scales.h"

pytestmark = pytest.mark.skipif(
    not RAMPS_H.exists(),
    reason=f"reflex-fw not found ({RAMPS_H}); set REFLEX_FW_DIR to run the register-map contract test",
)

# C fixed-width scalar types used in the shared structs. For these, alignment
# equals size (natural alignment), which is target-independent. `bool` is not
# used by any struct under test but is listed for completeness.
SCALAR_SIZE = {
    "float": 4, "int32_t": 4, "uint32_t": 4,
    "int16_t": 2, "uint16_t": 2, "bool": 1,
}

ROOT_STRUCT = "rampsSharedData_t"

# Oracle self-check anchors: known-good facts about the firmware layout that
# guard the hand-rolled alignment model below against silent bugs. If the model
# ever miscomputes padding, these fixed numbers diverge and fail loudly.
KNOWN_SERVO_DIR_OFFSET = 32      # servo_t.servoDir sits after 8x 4-byte fields
KNOWN_SERVO_T_SIZE = 36          # 34 bytes of fields + 2 trailing pad -> 4-align
KNOWN_ROOT_SIZE = 432            # sizeof(rampsSharedData_t); see module test
                                 # 264 -> 304 on 2026-08-08: the closed-loop
                                 # backlash calibration block appended 40 bytes
                                 # to elsStop_t (56 -> 96), packed uint16s-first
                                 # so nothing pads. Bump this ONLY together with
                                 # devices.py and elsStop.protocolVersion.


# ── firmware C-struct parsing + true-C-layout model ──────────────────────────

def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _load_macros() -> dict:
    macros = {}
    for hdr in (SCALES_H, RAMPS_H):
        if hdr.exists():
            for m in re.finditer(r"#define\s+(\w+)\s+(\d+)", hdr.read_text()):
                macros[m.group(1)] = int(m.group(2))
    return macros


def _parse_structs(text: str, macros: dict) -> dict:
    """Return {struct_name: [(c_type, field_name, count), ...]} for every
    ``typedef struct { ... } NAME;`` in the header. Bodies here contain no
    nested braces, so a non-greedy brace match is sufficient."""
    text = _strip_comments(text)
    structs = {}
    for m in re.finditer(r"typedef\s+struct\s*\{(.*?)\}\s*(\w+)\s*;", text, flags=re.S):
        body, name = m.group(1), m.group(2)
        fields = []
        for stmt in body.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            parts = stmt.split(None, 1)  # first token = type, remainder = declarators
            if len(parts) != 2:
                continue
            ctype, rest = parts
            rest = rest.replace("*", "")
            for decl in rest.split(","):  # e.g. "syncRatioNum, syncRatioDen"
                decl = decl.strip()
                if not decl:
                    continue
                arr = re.match(r"(\w+)\s*\[\s*(\w+)\s*\]", decl)
                if arr:
                    fname = arr.group(1)
                    tok = arr.group(2)
                    count = int(tok) if tok.isdigit() else macros[tok]
                else:
                    fname = decl
                    count = 1
                fields.append((ctype, fname, count))
        structs[name] = fields
    return structs


def _align_up(off: int, align: int) -> int:
    return (off + align - 1) // align * align


def _type_align(ctype: str, structs: dict) -> int:
    if ctype in SCALAR_SIZE:
        return SCALAR_SIZE[ctype]
    return max(_type_align(t, structs) for t, _n, _c in structs[ctype])


def _type_size(ctype: str, structs: dict) -> int:
    if ctype in SCALAR_SIZE:
        return SCALAR_SIZE[ctype]
    off = 0
    for t, _n, count in structs[ctype]:
        off = _align_up(off, _type_align(t, structs)) + _type_size(t, structs) * count
    return _align_up(off, _type_align(ctype, structs))


def _flatten(ctype: str, structs: dict, prefix: str = "", base: int = 0) -> list:
    """Flatten a struct to leaf scalars: [(path, c_type, offset_bytes, size_bytes)],
    applying real C alignment + implicit padding."""
    leaves = []
    off = 0
    for t, name, count in structs[ctype]:
        align = _type_align(t, structs)
        size = _type_size(t, structs)
        off = _align_up(off, align)
        for i in range(count):
            path = f"{prefix}{name}" if count == 1 else f"{prefix}{name}.{i}"
            eoff = base + off + i * size
            if t in SCALAR_SIZE:
                leaves.append((path, t, eoff, size))
            else:
                leaves.extend(_flatten(t, structs, prefix=path + ".", base=eoff))
        off += size * count
    return leaves


# ── reflex-ui side: walk the REAL device tree the UI uses ─────────────────────

def _reflex_leaves() -> list:
    """Flatten reflex-ui's actual ConnectionManager device tree to leaf scalars:
    [(path, c_type_name, offset_bytes, size_bytes)]. No connection is opened --
    ConnectionManager.__init__ parses the definitions offline."""
    cm = ConnectionManager()
    root = cm["Global"]
    return _walk_device(root, prefix="", base_bytes=0)


def _walk_device(dev, prefix: str, base_bytes: int) -> list:
    leaves = []
    for var in dev.variables:
        rf = var.type.read_function
        is_struct = isinstance(rf, type) and issubclass(rf, BaseDevice)
        elem_bytes = var.type.length * 2  # reflex-ui `length` is in 16-bit registers
        for i in range(var.count):
            eoff = base_bytes + var.address * 2 + i * elem_bytes
            path = f"{prefix}{var.name}" if var.count == 1 else f"{prefix}{var.name}.{i}"
            if is_struct:
                sub = rf(dev.dm, 0)  # parse-only sub-device; offsets computed here
                leaves.extend(_walk_device(sub, prefix=path + ".", base_bytes=eoff))
            else:
                leaves.append((path, var.type.name, eoff, elem_bytes))
    return leaves


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def firmware_structs():
    macros = _load_macros()
    return _parse_structs(RAMPS_H.read_text(), macros)


@pytest.fixture(scope="module")
def firmware_map(firmware_structs):
    return {p: (t, o, s) for (p, t, o, s) in _flatten(ROOT_STRUCT, firmware_structs)}


@pytest.fixture(scope="module")
def reflex_map():
    # Drop reflex-ui's explicit padding fields -- they model the firmware's
    # implicit padding and have no named firmware counterpart.
    return {
        p: (t, o, s)
        for (p, t, o, s) in _reflex_leaves()
        if "_pad" not in p.rsplit(".", 1)[-1]
    }


# ── the contract ─────────────────────────────────────────────────────────────

def test_field_sets_match(firmware_map, reflex_map):
    """Every firmware field is mirrored in reflex-ui and vice versa (modulo pads)."""
    only_fw = sorted(set(firmware_map) - set(reflex_map))
    only_ui = sorted(set(reflex_map) - set(firmware_map))
    assert not only_fw and not only_ui, (
        f"register-map field-set drift:\n"
        f"  in firmware but not reflex-ui: {only_fw}\n"
        f"  in reflex-ui but not firmware: {only_ui}"
    )


def test_field_offsets_and_types_match(firmware_map, reflex_map):
    """Each shared field has the same C type, byte offset, and size on both sides."""
    diffs = []
    for path in sorted(set(firmware_map) & set(reflex_map)):
        if firmware_map[path] != reflex_map[path]:
            diffs.append(
                f"  {path}: firmware(type,offset,size)={firmware_map[path]} "
                f"!= reflex-ui={reflex_map[path]}"
            )
    assert not diffs, "register-map layout drift:\n" + "\n".join(diffs)


def test_total_size_matches(firmware_structs):
    """The whole rampsSharedData_t is the same size on both sides (catches a
    trailing-pad omission that per-field offsets alone would miss)."""
    cm = ConnectionManager()
    reflex_size_bytes = cm["Global"].size * 2
    firmware_size_bytes = _type_size(ROOT_STRUCT, firmware_structs)
    assert reflex_size_bytes == firmware_size_bytes, (
        f"rampsSharedData_t size drift: firmware={firmware_size_bytes}B, "
        f"reflex-ui={reflex_size_bytes}B"
    )


def test_layout_oracle_selfcheck(firmware_structs):
    """Guard the hand-rolled C-alignment model with fixed known-good numbers, so
    a bug in the oracle can't silently let real drift pass."""
    servo = {p: o for (p, _t, o, _s) in _flatten("servo_t", firmware_structs)}
    assert servo["servoDir"] == KNOWN_SERVO_DIR_OFFSET
    assert _type_size("servo_t", firmware_structs) == KNOWN_SERVO_T_SIZE
    assert _type_size(ROOT_STRUCT, firmware_structs) == KNOWN_ROOT_SIZE


def test_layout_engine_models_padding():
    """Unit-test the alignment engine itself on a synthetic struct with known
    tricky padding -- proves it computes interior AND trailing padding correctly,
    independent of the real firmware header (a permanent replacement for the
    plan's one-time manual perturb-and-revert check)."""
    structs = {
        # int32 @0 (4), int16 @4 (2), [pad @6], int32 @8 (4), int16 @12 (2),
        # trailing pad -> size 16, align 4
        "pad_probe_t": [("int32_t", "a", 1), ("int16_t", "b", 1),
                        ("int32_t", "c", 1), ("int16_t", "d", 1)],
    }
    leaves = {p: o for (p, _t, o, _s) in _flatten("pad_probe_t", structs)}
    assert leaves == {"a": 0, "b": 4, "c": 8, "d": 12}
    assert _type_size("pad_probe_t", structs) == 16
