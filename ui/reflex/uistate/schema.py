"""Field registry for UI state snapshots -- the contract that encode and replay
both read from.

WHY A DECLARED REGISTRY AND NOT REFLECTION. ``SavingDispatcher`` enumerates its
properties by reflection (``get_our_properties``), which is right for a settings
file that is rewritten every time it is read. It is wrong here. A snapshot's
field list IS a wire format: if adding a property to a dispatcher silently
changed it, every code recorded before that day would decode into the wrong
shape -- readable, plausible, and wrong. So fields are declared explicitly, in a
frozen order, under a schema id.

This mirrors ``reflex/fsms/els_diag.py`` deliberately, and inherits its rules:

* Every record carries its own schema id, so a capture stays readable forever.
* ``KNOWN_SCHEMAS`` gates decoding. An id this UI does not know is REFUSED, not
  guessed at.
* **Retired ids stay in the registry and are never reissued.** Old captures must
  keep replaying. This is the same reason retired firmware probe schemas are
  never deleted.
* Adding, removing, reordering or repurposing a field is a NEW SCHEMA ID. There
  is no such thing as a compatible edit to a live schema.

ONE DECLARATION DRIVES BOTH DIRECTIONS. Each ``Field`` carries its own ``get``
and ``apply``, so the capture side and the replay side cannot drift apart -- the
failure mode where a field is recorded faithfully and then restored to the wrong
place, which would produce a confident, wrong screenshot.

INPUTS ARE CAPTURED, OUTPUTS ARE HASHED. A field here must be an *input* to the
render -- something set, which KV then reacts to. Computed geometry (``pos``,
``size``), resolved text and resolved colors are *outputs*: they cannot be
pushed back in (the next layout pass or binding fire overwrites them), so they
belong to the drift digest in ``digest.py``, never to a Field.
"""

from dataclasses import dataclass, field as dc_field
from typing import Any, Callable

from kivy.logger import Logger

from reflex.uistate import codec

log = Logger.getChild(__name__)


class Kind:
    """Wire kinds. Values are shared with :mod:`reflex.uistate.codec`."""
    BOOL = codec.KIND_BOOL
    UINT = codec.KIND_UINT
    INT = codec.KIND_INT
    FLOAT = codec.KIND_FLOAT
    STR = codec.KIND_STR


@dataclass(frozen=True)
class Field:
    """One captured value, with the two halves of its contract.

    ``get(app)`` reads it off the live app; ``apply(app, value)`` puts it back
    on a replaying one. Both are wrapped by the driver functions below, so a
    field whose widget does not exist in the current mode (the ELS bar in DRO
    mode, say) degrades to ``default`` instead of raising.
    """
    key: str
    kind: int
    get: Callable[[Any], Any]
    apply: Callable[[Any, Any], None]
    default: Any
    doc: str = ""
    volatile: bool = False
    """Changes on its own, forever, with nothing happening on screen.

    The COM led's blink phase and the frame counter are the whole population.
    They are still captured and applied -- they ARE drawn, so leaving them out
    would make every replay differ -- but the recorder must not treat them as
    evidence that the UI is still moving, or it would never see a settled frame
    to record.
    """


@dataclass(frozen=True)
class Schema:
    id: int
    name: str
    fields: tuple[Field, ...]
    retired: bool = False

    @property
    def kinds(self) -> list[int]:
        return [f.kind for f in self.fields]

    @property
    def keys(self) -> list[str]:
        return [f.key for f in self.fields]

    @property
    def stable_keys(self) -> list[str]:
        """Keys whose stability means the UI has stopped changing."""
        return [f.key for f in self.fields if not f.volatile]


# Populated by register(); see schema_v1.py for the live declaration.
KNOWN_SCHEMAS: dict[int, Schema] = {}

# Ids that have ever been issued, so a contract test can catch a reissue even
# if the schema itself was later dropped from the module.
ISSUED_IDS: set[int] = set()


class UnknownSchema(codec.CodecError):
    """A code names a schema id this UI does not recognise.

    Deliberately fatal to the decode. A code from a newer UI carries fields in
    an order this one does not know, and guessing would produce a confident,
    wrong screenshot -- the one outcome this whole feature exists to prevent.
    """


def register(schema: Schema) -> Schema:
    if schema.id in KNOWN_SCHEMAS:
        raise ValueError(f"schema id {schema.id} already registered")
    keys = schema.keys
    if len(set(keys)) != len(keys):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        raise ValueError(f"schema {schema.id} has duplicate keys: {dupes}")
    KNOWN_SCHEMAS[schema.id] = schema
    ISSUED_IDS.add(schema.id)
    return schema


def get_schema(schema_id: int) -> Schema:
    try:
        return KNOWN_SCHEMAS[schema_id]
    except KeyError:
        raise UnknownSchema(
            f"UI state code carries schema {schema_id}, which this UI does not "
            f"recognise (known: {sorted(KNOWN_SCHEMAS)}). Refusing to decode."
        ) from None


def current_schema() -> Schema:
    """The schema new captures are written under: the highest live id."""
    live = [s for s in KNOWN_SCHEMAS.values() if not s.retired]
    if not live:
        raise RuntimeError("no live UI state schema is registered")
    return max(live, key=lambda s: s.id)


# ── drivers ────────────────────────────────────────────────────────────────

def snapshot(app, schema: Schema | None = None) -> dict[str, Any]:
    """Read every declared field off the live app.

    A getter that raises falls back to the field's default. That is not
    defensive padding: in DRO mode the ELS widgets genuinely do not exist, and a
    snapshot must still be takeable. The drift digest is what catches the case
    where a default silently stands in for something that WAS on screen.
    """
    schema = schema or current_schema()
    values = {}
    for f in schema.fields:
        try:
            value = f.get(app)
        except Exception as e:  # noqa: BLE001 - a getter must never break capture
            log.debug(f"uistate: field {f.key!r} unreadable ({e}); using default")
            value = f.default
        values[f.key] = f.default if value is None else value
    return values


def apply(app, values: dict[str, Any], schema: Schema | None = None) -> list[str]:
    """Push a snapshot onto a booted app. Returns the keys that failed.

    Fields are applied in declaration order, which is why the schema puts screen
    and mode first: the widgets a later field addresses have to exist before it
    runs.
    """
    schema = schema or current_schema()
    failed = []
    for f in schema.fields:
        if f.key not in values:
            continue
        try:
            f.apply(app, values[f.key])
        except Exception as e:  # noqa: BLE001 - report, never abort the replay
            log.warning(f"uistate: could not apply field {f.key!r}: {e}")
            failed.append(f.key)
    return failed


def encode(values: dict[str, Any], digests: dict[str, int],
           schema: Schema | None = None) -> str:
    schema = schema or current_schema()
    ordered = [values[f.key] for f in schema.fields]
    return codec.encode(schema.id, schema.kinds, ordered, digests)


def decode(code: str) -> tuple[Schema, dict[str, Any], dict[str, int]]:
    schema_id = codec.peek_schema_id(code)
    schema = get_schema(schema_id)
    _, ordered, digests = codec.decode(code, lambda _id: schema.kinds)
    return schema, dict(zip(schema.keys, ordered)), digests
