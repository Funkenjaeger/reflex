"""Round-trip and robustness of the UI state code wire format."""

import pytest

from reflex.uistate import codec


KINDS = [codec.KIND_BOOL, codec.KIND_BOOL, codec.KIND_UINT, codec.KIND_INT,
         codec.KIND_FLOAT, codec.KIND_STR, codec.KIND_STR]


def roundtrip(values, digests=None):
    digests = digests or {"all": 1}
    code = codec.encode(1, KINDS, values, digests)
    schema_id, out, out_digests = codec.decode(code, lambda _id: KINDS)
    assert schema_id == 1
    assert out_digests == digests
    return out


def test_roundtrip_exact():
    values = [True, False, 4096, -17, 1.5, "Cutting...", ""]
    assert roundtrip(values) == values


def test_roundtrip_unicode():
    # The spindle direction icons are Font Awesome private-use glyphs, and
    # instruction text can carry anything the operator's locale produces.
    values = [False, True, 0, 0, 0.0, "", "é ✓ 日本語"]
    assert roundtrip(values) == values


def test_roundtrip_extremes():
    values = [True, True, 2 ** 32, -(2 ** 31), -0.5, "x" * 4000, "\x00"]
    assert roundtrip(values) == values


@pytest.mark.parametrize("count", range(0, 33))
def test_bitfield_packs_every_boolean_count(count):
    kinds = [codec.KIND_BOOL] * count
    values = [bool(i % 3) for i in range(count)]
    code = codec.encode(1, kinds, values, {})
    assert codec.decode(code, lambda _id: kinds)[1] == values


@pytest.mark.parametrize("size", range(0, 40))
def test_base32_roundtrip(size):
    data = bytes((i * 37 + size) % 256 for i in range(size))
    assert codec.b32_decode(codec.b32_encode(data)) == data


def test_code_is_case_insensitive_and_survives_wrapping():
    """A code has to survive being read off the lathe's log viewer and retyped."""
    values = [True, False, 12345, -1, 2.25, "Ready to cut", "mm"]
    code = codec.encode(1, KINDS, values, {"all": 7})
    for variant in (code.lower(), code.upper(),
                    code[:20] + "\n" + code[20:], f"  {code}  "):
        assert codec.decode(variant, lambda _id: KINDS)[1] == values


def test_crockford_confusable_aliases_decode():
    """O->0 and I/L->1, so a hand-transcribed code still works."""
    values = [True, True, 1, 1, 1.0, "a", "b"]
    code = codec.encode(1, KINDS, values, {})
    munged = code.partition(".")[2].replace("0", "O").replace("1", "I")
    assert codec.decode("R1." + munged, lambda _id: KINDS)[1] == values


@pytest.mark.parametrize("bad", [
    "", "R1", "R1.", "nonsense", "X1.ABC", "R.ABC", "R1.!!!!", "Ra.ABC",
])
def test_malformed_codes_raise_cleanly(bad):
    with pytest.raises(codec.CodecError):
        codec.decode(bad, lambda _id: KINDS)


def test_corrupted_payload_is_detected_not_guessed():
    """zlib's Adler-32 is what stops a mistyped code decoding into nonsense."""
    code = codec.encode(1, KINDS, [True, False, 5, 5, 5.0, "x", "y"], {})
    corrupt = code[:-6] + "ZZZZZ" + code[-1]
    with pytest.raises(codec.CodecError):
        codec.decode(corrupt, lambda _id: KINDS)


def test_truncated_code_does_not_crash_the_decoder():
    code = codec.encode(1, KINDS, [True, False, 5, 5, 5.0, "x", "y"], {})
    for cut in range(4, len(code)):
        with pytest.raises(codec.CodecError):
            codec.decode(code[:cut], lambda _id: KINDS)


def test_prefix_id_must_match_payload_id():
    code = codec.encode(1, KINDS, [True, False, 1, 1, 1.0, "a", "b"], {})
    with pytest.raises(codec.CodecError, match="payload says"):
        codec.decode("R2." + code.partition(".")[2], lambda _id: KINDS)


def test_digests_roundtrip_with_names():
    digests = {"all": 0xFFFFFFFF, "ElsBar": 0, "ElsAdvancedBar": 123456}
    packed = codec.pack_digests(digests)
    assert codec.unpack_digests(packed)[0] == digests
