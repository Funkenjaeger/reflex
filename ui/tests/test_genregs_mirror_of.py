"""The `mirror_of:` gate in tools/genregs.py (check_mirrors()).

WHAT THIS GUARDS. fastData_t is a publishing copy of fields that LIVE in
servo_t and input_t, and until build/2026-09-12-5 (d7de6de, never merged to
integration) that fact was carried only in `doc:` prose: "mirror of
servo.stepsToGo". The prose was true and the TYPE was not -- fastData.stepsToGo
was declared uint32 over an int32 source -- so a 1287-step reverse indexing
move published as 4294966009. NOTHING could catch that: the register-map
contract test (ui/tests/test_register_map_contract.py) compares the two SIDES
of the RS-485 link, and both sides mirrored the wrong type faithfully.

registers/fast_data.yaml now declares `mirror_of:` on its five publishing
fields, and genregs.check_mirrors() resolves each one against the schema that
owns the mirrored field, at generation time. These tests are the proof that the
resolution actually happens and actually refuses.

DIVERGES FROM d7de6de's ORIGINAL GATE, PORTED HERE, because the generator it
targeted no longer exists in this shape:

* d7de6de's check_mirrors() re-parsed fw/Core/Inc/Ramps.h as TEXT, because
  servo_t and input_t were still hand-maintained there. Since 2026-09-18
  (Open Loops 6a9f3106, integration 11ebe41) servo_t and input_t are schemas
  too -- registers/servo.yaml, registers/input.yaml -- so this port resolves a
  mirror against the sibling Schema object genregs already loaded, not against
  header text. There is no `meta.mirror_source` any more and no C-struct
  parser to test; TestUnresolvableMirrorRefuses below exercises the new
  resolution path (unknown parent_member / unknown field / malformed spec /
  array-ness mismatch) instead.
* d7de6de's tests also pinned the DEFINITION generated for the ORIGINAL
  fastData_t->schema conversion against the hand-written struct it replaced
  (`BASE_SHA_HAND_DEFINITION`) and asserted genregs.SCHEMAS/outputs shape. That
  conversion already happened on integration (11ebe41, byte-identical, its own
  proof); it is not this gate's job to re-prove it, so those classes are
  dropped here rather than re-targeted at a conversion this port did not do.
* The CLI end (`genregs.py` / `genregs.py --check` refusing on a scratch copy
  of the real tree) was proven by hand for this build rather than as a
  checked-in tools/ script -- the build order's bound restricts this port to
  tools/genregs.py, registers/*.yaml and ui/tests/, and tools/ is not a test
  directory pytest collects anyway. These tests instead exercise
  check_mirrors()/load_schemas() directly, with both the real schemas
  (control) and synthetic ones (the refusal paths) -- tools/ is not a
  package, so genregs.py is imported by path.
"""
import importlib.util
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GENREGS_PY = REPO_ROOT / "tools" / "genregs.py"


def _load_genregs():
    spec = importlib.util.spec_from_file_location("genregs_under_test", GENREGS_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def genregs():
    # Fresh module per test: check_mirrors() runs inside load_schemas(), and a
    # shared module would leak ROOT/REGISTERS monkeypatches across tests that
    # otherwise look independent.
    return _load_genregs()


# ── synthetic schemas: a minimal servo_t/input_t/fastData_t trio, just enough
#    to exercise the resolution path without touching the real registers/ tree.
#    Literal ints throughout (no `constants:` block) -- the gate under test is
#    check_mirrors(), not resolve_constants(). ────────────────────────────────

SERVO_MINI = """\
schema_version: 1
protocol_version: 1
meta:
  struct: servo_t
  short_name: servo
  macro_prefix: SERVO
  parent: rampsSharedData_t
  parent_member: servo
  parent_count: 1
  parent_offset_registers: 0
  py_out: servo_map.py
layout:
  - {group: all, budget_registers: 125, fail_over_budget: true}
types:
  int32: {bytes: 4}
  uint32: {bytes: 4}
fields:
  - {id: 1, name: stepsToGo, type: int32, access: sw_write, group: all, doc: "x"}
  - {id: 2, name: currentSteps, type: uint32, access: ro_firmware, group: all, doc: "x"}
"""

INPUT_MINI = """\
schema_version: 1
meta:
  struct: input_t
  short_name: input
  macro_prefix: INPUT
  parent: rampsSharedData_t
  parent_member: scales
  parent_count: 4
  parent_offset_registers: 4
  py_out: input_map.py
layout:
  - {group: all, budget_registers: 125, fail_over_budget: true}
types:
  int32: {bytes: 4}
fields:
  - {id: 1, name: position, type: int32, access: ro_firmware, group: all, doc: "x"}
"""

FAST_BASE_REGISTER = 12  # past servo (0..3) and scales[4] (4..11)


def _fast_mini(field_line):
    return f"""\
schema_version: 1
meta:
  struct: fastData_t
  short_name: fastData
  macro_prefix: FAST_DATA
  parent: rampsSharedData_t
  parent_member: fastData
  parent_count: 1
  parent_offset_registers: {FAST_BASE_REGISTER}
  py_out: fast_data_map.py
layout:
  - {{group: all, budget_registers: 125, fail_over_budget: true}}
types:
  int32: {{bytes: 4}}
  uint32: {{bytes: 4}}
fields:
  {field_line}
"""


@pytest.fixture
def synth_registers(tmp_path, monkeypatch, genregs):
    """Point genregs at a scratch registers/ dir with servo_mini + input_mini
    fixed, and hand back a function that writes a given fastData_t field and
    loads all three -- raising GenError, or returning the loaded schemas."""
    regdir = tmp_path / "registers"
    regdir.mkdir()
    (regdir / "servo.yaml").write_text(SERVO_MINI, encoding="utf-8")
    (regdir / "input.yaml").write_text(INPUT_MINI, encoding="utf-8")
    monkeypatch.setattr(genregs, "ROOT", tmp_path)
    monkeypatch.setattr(genregs, "REGISTERS", regdir)

    def _load(field_line):
        (regdir / "fast_data.yaml").write_text(_fast_mini(field_line), encoding="utf-8")
        return genregs.load_schemas()

    return _load


class TestTypeDisagreementRefuses:
    """(a) THE CASE THAT motivated the gate, reproduced against synthetic schemas."""

    def test_scalar_mirror_type_disagreement_refuses(self, genregs, synth_registers):
        with pytest.raises(genregs.GenError) as exc:
            synth_registers(
                '- {id: 1, name: stepsToGo, type: uint32, access: ro_firmware, '
                'group: all, mirror_of: servo.stepsToGo, doc: "x"}')
        msg = str(exc.value)
        assert "REFUSING TO EMIT" in msg
        # Both sides and where to look -- a refusal that only says "mismatch"
        # sends the reader back to the diff that caused it.
        assert "fastData.stepsToGo is uint32" in msg
        assert "mirrors servo.stepsToGo" in msg
        assert "int32 in" in msg and "servo.yaml" in msg

    def test_matching_type_is_accepted(self, genregs, synth_registers):
        """The same field with the type it actually mirrors loads cleanly --
        so the test above is failing on the TYPE and not on the plumbing."""
        schemas = synth_registers(
            '- {id: 1, name: stepsToGo, type: int32, access: ro_firmware, '
            'group: all, mirror_of: servo.stepsToGo, doc: "x"}')
        assert len(schemas) == 3

    def test_array_mirror_type_disagreement_refuses(self, genregs, synth_registers):
        with pytest.raises(genregs.GenError) as exc:
            synth_registers(
                '- {id: 1, name: scaleCurrent, type: uint32, count: 4, '
                'access: ro_firmware, group: all, mirror_of: "scales[].position", doc: "x"}')
        msg = str(exc.value)
        assert "fastData.scaleCurrent is uint32" in msg
        assert "mirrors scales[].position" in msg

    def test_array_mirror_length_disagreement_refuses(self, genregs, synth_registers):
        """A mirror shorter than what it mirrors silently stops publishing the
        tail of the source, which reads as "that scale is dead", not as drift."""
        with pytest.raises(genregs.GenError) as exc:
            synth_registers(
                '- {id: 1, name: scaleCurrent, type: int32, count: 2, '
                'access: ro_firmware, group: all, mirror_of: "scales[].position", doc: "x"}')
        assert "is [2] but mirrors scales[].position, 4 of them" in str(exc.value)

    def test_matching_array_mirror_is_accepted(self, genregs, synth_registers):
        schemas = synth_registers(
            '- {id: 1, name: scaleCurrent, type: int32, count: 4, '
            'access: ro_firmware, group: all, mirror_of: "scales[].position", doc: "x"}')
        assert len(schemas) == 3


class TestUnresolvableMirrorRefuses:
    """(b) A mirror_of naming something that does not exist refuses too --
    never a warning, and never silently skipped as "nothing to check"."""

    def test_unknown_source_field_refuses(self, genregs, synth_registers):
        with pytest.raises(genregs.GenError) as exc:
            synth_registers(
                '- {id: 1, name: stepsToGo, type: int32, access: ro_firmware, '
                'group: all, mirror_of: servo.stepsToGone, doc: "x"}')
        msg = str(exc.value)
        assert "servo_t" in msg and "has no field 'stepsToGone'" in msg

    def test_unknown_parent_member_refuses(self, genregs, synth_registers):
        with pytest.raises(genregs.GenError) as exc:
            synth_registers(
                '- {id: 1, name: stepsToGo, type: int32, access: ro_firmware, '
                'group: all, mirror_of: spindle.stepsToGo, doc: "x"}')
        assert "no loaded schema declares parent_member 'spindle'" in str(exc.value)

    def test_malformed_mirror_spec_refuses(self, genregs, synth_registers):
        with pytest.raises(genregs.GenError) as exc:
            synth_registers(
                '- {id: 1, name: stepsToGo, type: int32, access: ro_firmware, '
                'group: all, mirror_of: servo_stepsToGo, doc: "x"}')
        assert "is not of the form member.field" in str(exc.value)

    def test_scalar_mirror_of_an_array_member_refuses(self, genregs, synth_registers):
        """`scales.position` without the `[]` is ambiguous about WHICH scale;
        the generator makes the author write it rather than guessing index 0."""
        with pytest.raises(genregs.GenError) as exc:
            synth_registers(
                '- {id: 1, name: scaleCurrent, type: int32, access: ro_firmware, '
                'group: all, mirror_of: scales.position, doc: "x"}')
        assert "write scales[].position" in str(exc.value)

    def test_array_syntax_on_a_scalar_member_refuses(self, genregs, synth_registers):
        """The mirror image of the case above: `servo[].stepsToGo` when servo
        is not an array member of the parent."""
        with pytest.raises(genregs.GenError) as exc:
            synth_registers(
                '- {id: 1, name: stepsToGo, type: int32, access: ro_firmware, '
                'group: all, mirror_of: "servo[].stepsToGo", doc: "x"}')
        assert "write servo.stepsToGo" in str(exc.value)


class TestRealSchemaAgainstTheRealTree:
    """(c) THE CONTROL. The shipped registers/fast_data.yaml against the real
    registers/servo.yaml and registers/input.yaml -- no monkeypatching."""

    def test_fast_data_declares_the_five_mirrors(self, genregs):
        doc = yaml.safe_load((REPO_ROOT / "registers" / "fast_data.yaml").read_text())
        mirrors = {f["name"]: f["mirror_of"] for f in doc["fields"] if f.get("mirror_of")}
        assert mirrors == {
            "servoCurrent": "servo.currentSteps",
            "servoDesired": "servo.desiredSteps",
            "stepsToGo": "servo.stepsToGo",
            "scaleCurrent": "scales[].position",
            "scaleSpeed": "scales[].speed",
        }

    def test_real_schemas_load_and_all_mirrors_resolve(self, genregs):
        # load_schemas() runs check_mirrors() itself; a raise here is the test
        # failure, so this is deliberately not wrapped in pytest.raises.
        schemas = genregs.load_schemas()
        fast = next(s for s in schemas if s.struct == "fastData_t")
        mirrored_names = {i["name"] for i in fast.items if i.get("mirror_of")}
        assert mirrored_names == {"servoCurrent", "servoDesired", "stepsToGo",
                                   "scaleCurrent", "scaleSpeed"}

    def test_fast_data_does_not_own_a_protocol_version(self, genregs):
        """fastData_t rides els_stop's protocol_version -- unchanged by this
        port, asserted here because check_mirrors() must not have grown a
        dependency on fastData owning one."""
        doc = yaml.safe_load((REPO_ROOT / "registers" / "fast_data.yaml").read_text())
        assert "protocol_version" not in doc

    def test_regeneration_is_byte_identical(self, genregs):
        """The port ADDS a validator; it must not change a single emitted byte.
        (Also proven by `genregs.py --check` and a manual regenerate/cmp,
        recorded in this build's report; this is the same proof from inside
        pytest.)"""
        schemas = genregs.load_schemas()
        rendered = genregs.render(schemas)
        for rel, text in rendered.items():
            p = REPO_ROOT / rel
            assert p.read_text(encoding="utf-8") == text, f"{rel} has drifted"
