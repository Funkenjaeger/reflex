"""fastData.stepsToGo decodes SIGNED (protocolVersion 11, 2026-09-18).

servo.stepsToGo is int32 and goes negative on reverse moves; its fastData
mirror was declared uint32 from upstream fd251ea8 (2024) until 11, so the
flight and phase recorders would have logged a reverse backlog of -5 as
4294967291. The fix is one word in registers/fast_data.yaml; this pins that
the generated UI decoder -- the one every reader goes through -- honours it.

SEEN-RED (2026-09-18): with the schema type put back to uint32 and the map
regenerated, test_a_negative_backlog_decodes_negative failed with
4294967291 != -5.
"""
import struct

from reflex.utils import fast_data_map


def _wire_frame(steps_to_go: int):
    """The fastData block AS THE FIRMWARE SENDS IT: all registers zero except
    stepsToGo, whose two registers carry the little-endian bytes of the int32
    the firmware copies out of servo.stepsToGo. Built from the register offset,
    NOT from the decoder's format string, so it is the same frame whatever the
    decoder believes the type to be."""
    regs = [0] * fast_data_map.TOTAL_REGISTERS
    lo, hi = struct.unpack("<2H", struct.pack("<i", steps_to_go))
    at = fast_data_map.OFFSETS["stepsToGo"]
    regs[at], regs[at + 1] = lo, hi
    return regs


def test_a_negative_backlog_decodes_negative():
    assert fast_data_map.decode_all(_wire_frame(-5))["stepsToGo"] == -5


def test_a_positive_backlog_is_unchanged():
    assert fast_data_map.decode_all(_wire_frame(478))["stepsToGo"] == 478


def test_the_c_definition_the_device_parses_is_signed():
    """BaseDevice reads the C-style DEFINITION, not ALL_FORMAT; both are
    generated from the same schema line and must agree."""
    assert "int32_t stepsToGo;" in fast_data_map.DEFINITION
    assert "uint32_t stepsToGo;" not in fast_data_map.DEFINITION
