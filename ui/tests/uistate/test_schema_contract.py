"""The schema registry contract.

The rules enforced here are the ones that keep old captures replayable, and
each has a specific failure it prevents -- see ``reflex/uistate/schema.py``.
"""

import pytest

from reflex.uistate import schema as schema_mod
from reflex.uistate.schema import Field, Kind, Schema
import reflex.uistate.schema_v1 as v1  # noqa: F401 - registers schema 1


def test_v1_is_registered_and_live():
    assert 1 in schema_mod.KNOWN_SCHEMAS
    assert schema_mod.current_schema().id >= 1


def test_field_keys_are_unique():
    for schema in schema_mod.KNOWN_SCHEMAS.values():
        keys = schema.keys
        assert len(set(keys)) == len(keys), f"duplicate keys in schema {schema.id}"


def test_every_field_declares_both_directions():
    """A field that can be captured but not applied produces a confident, wrong
    screenshot -- the exact failure this feature exists to prevent."""
    for schema in schema_mod.KNOWN_SCHEMAS.values():
        for field in schema.fields:
            assert callable(field.get), f"{field.key} has no getter"
            assert callable(field.apply), f"{field.key} has no applier"


def test_every_field_default_matches_its_kind():
    checkers = {
        Kind.BOOL: lambda v: isinstance(v, bool),
        Kind.UINT: lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
        Kind.INT: lambda v: isinstance(v, int) and not isinstance(v, bool),
        Kind.FLOAT: lambda v: isinstance(v, (int, float)),
        Kind.STR: lambda v: isinstance(v, str),
    }
    for schema in schema_mod.KNOWN_SCHEMAS.values():
        for field in schema.fields:
            assert checkers[field.kind](field.default), \
                f"{field.key} default {field.default!r} does not match its kind"


def test_registering_a_used_id_is_refused():
    """Reissuing an id would make an old capture decode under a new shape."""
    with pytest.raises(ValueError, match="already registered"):
        schema_mod.register(Schema(id=1, name="clash", fields=()))


def test_duplicate_keys_are_refused_at_registration():
    dupe = Field("x", Kind.BOOL, lambda a: False, lambda a, v: None, False)
    with pytest.raises(ValueError, match="duplicate keys"):
        schema_mod.register(Schema(id=9999, name="dupe", fields=(dupe, dupe)))


def test_unknown_schema_is_refused_not_guessed():
    with pytest.raises(schema_mod.UnknownSchema, match="does not recognise"):
        schema_mod.get_schema(4242)


def test_unknown_schema_in_a_code_is_refused():
    """A code from a newer UI carries fields in an order this one cannot know."""
    from reflex.uistate import codec
    code = codec.encode(77, [codec.KIND_BOOL], [True], {})
    with pytest.raises(schema_mod.UnknownSchema):
        schema_mod.decode(code)


def test_declaration_order_is_stable():
    """Order IS the wire format: values are packed positionally.

    If this list needs to change, the change is a new schema id, and updating
    this expectation instead of issuing one is the mistake it guards against.
    """
    keys = schema_mod.KNOWN_SCHEMAS[1].keys
    assert keys[:6] == [
        "app_version", "win_w", "win_h", "use_case",
        "els_z_index", "els_x_index",
    ]
    assert keys.index("use_case") < keys.index("mode"), \
        "use_case gates allowed_modes and must be applied before the mode"
    assert keys.index("screen") < keys.index("mode")
    assert keys.index("theme") < keys.index("fmt_display_color"), \
        "assigning the theme re-seeds the operator colors, which must then win"
    for name in ("els_z_index", "els_x_index", "els_spindle_index"):
        assert keys.index(name) < keys.index("mode"), \
            f"{name} decides which axis bars the ELS layout builds"


def test_snapshot_survives_a_missing_widget_tree():
    """DRO mode genuinely has no ELS widgets; a snapshot must still be takeable."""
    class Bare:
        version = "vtest"

    values = schema_mod.snapshot(Bare(), schema_mod.KNOWN_SCHEMAS[1])
    schema = schema_mod.KNOWN_SCHEMAS[1]
    assert set(values) == set(schema.keys)
    for field in schema.fields:
        if field.key != "app_version":
            assert values[field.key] == field.default


def test_snapshot_of_defaults_encodes_and_decodes():
    class Bare:
        version = "vtest"

    schema = schema_mod.KNOWN_SCHEMAS[1]
    values = schema_mod.snapshot(Bare(), schema)
    code = schema_mod.encode(values, {"all": 1}, schema)
    _schema, back, digests = schema_mod.decode(code)
    assert digests == {"all": 1}
    for key, value in values.items():
        if isinstance(value, float):
            assert back[key] == pytest.approx(value)
        else:
            assert back[key] == value


def test_apply_reports_failures_instead_of_aborting():
    """One unreachable field must not stop the rest of the frame replaying."""
    class Bare:
        version = "vtest"

    schema = schema_mod.KNOWN_SCHEMAS[1]
    values = schema_mod.snapshot(Bare(), schema)
    failed = schema_mod.apply(Bare(), values, schema)
    # Bare() has no manager/formats/els, so these cannot land -- the point is
    # that apply() returns them rather than raising.
    assert "screen" in failed
    assert isinstance(failed, list)
