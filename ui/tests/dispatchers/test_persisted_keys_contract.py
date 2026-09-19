"""Contract: the set of keys each SavingDispatcher persists is pinned.

WHY THIS EXISTS. A dispatcher's persisted keys ARE the on-card YAML schema
(``<Stem>-<id>.yaml`` in the config dir) and, since 2026-09-13, the
commissioning bundle's schema too: the bundle carries each stem's mapping
verbatim and USB import writes it back for the dispatchers to read. Renaming
or repurposing a property therefore silently strands the value already on
every card -- ``read_settings`` logs "unknown to this class" at DEBUG and the
machine comes back on the default. The register map has the same hazard and
the same answer (test_register_map_contract.py): one pinned source of truth
and a test that goes red.

THE DISCIPLINE THIS ENFORCES (Open Loops 6aa7318d, guardrail 1, 2026-09-13).
When this test fails because a key was renamed, repurposed or dropped, the fix
is TWO things in the SAME commit:
  1. regenerate ``persisted_keys.json`` (run this module as a script, below),
  2. add a read-time migration in the dispatcher's ``read_settings`` path so a
     card carrying the OLD key keeps its value (precedent: DeviceDispatcher's
     config.ini -> Device-0 migration, 70e1b25).
A pure ADDITION needs only (1): an old file simply lacks the new key and the
dispatcher's default applies. The test cannot see (2); the failure message says
so, and review is where it is caught.

WHAT IS PINNED. Exactly what ``SavingDispatcher.save_settings`` writes: the
names ``get_our_properties`` returns (Numeric/String/Boolean properties on the
class, minus ``_skip_save``, plus ``_force_save``), keyed by the file stem
(``_save_class_name`` or the class name). That includes Kivy layout properties
inherited by the widget dispatchers (``size_hint_x``, ``spacing``, ...): they
really are written to the card, so a Kivy upgrade that changes them is drift
this test SHOULD report. The set is computed by calling the real
``get_our_properties`` on an un-initialised instance (``cls.__new__``), so
``read_settings`` never runs and nothing touches a config dir.

TRUSTWORTHINESS -- how this avoids being a check that cannot fail:
  * the production class set is cross-checked against the id_override guard's
    source-derived allow-list, so a new subclass in a module this file does not
    import fails here instead of escaping the contract;
  * keys written by an override of ``_get_extra_save_data`` (outside the
    property set) are pinned too, via a per-class probe in EXTRA_SAVE_PROBES --
    today AxisDispatcher's ``transform_config``, the axis's scale mapping,
    with every TransformType string it can hold. An override with no probe
    fails the suite, so new extras cannot escape the pin;
  * the ``test_the_diff_*`` tests aim the comparator at inputs whose answer is
    known, and ``test_the_key_set_follows_skip_and_force_save`` proves the
    helper reads the real rule rather than a copy of it.

SEEN-RED (2026-09-17, on 587fc9a). Three mutations of production code, each
run alone and reverted, each turned ``test_persisted_keys_match_the_contract``
red with the stem named:
  * ``els_backlash_steps`` -> ``els_backlash`` in ElsDispatcher: stem ``Els``,
    DROPPED ['els_backlash_steps'], added ['els_backlash'];
  * ``transform_type`` -> ``kind`` in AxisTransform.to_dict: stem ``Axis``,
    DROPPED both ``transform_config.transform_type=...`` paths;
  * DeviceDispatcher's ``_save_class_name`` "Device" -> "DeviceSettings":
    ``Device`` pinned but no longer saved, ``DeviceSettings`` new.
And ``test_no_dispatcher_writes_keys_outside_the_contract`` went red on its
first run, before any probe existed, naming AxisDispatcher.

GROUND TRUTH. The fixture's key sets for Els, Device, Axis and ServoBar were
compared against the live card's /var/lib/reflex-config yaml on elspi
(2026-09-17): identical, except Axis-3.yaml predating ``diameter_mode`` -- an
addition, which a default covers.

Regenerate the fixture (deliberately, per the discipline above):

    cd ui && python -m tests.dispatchers.test_persisted_keys_contract > tests/dispatchers/persisted_keys.json
"""
import importlib
import json
import sys
from pathlib import Path

import pytest
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, StringProperty

from reflex.dispatchers.saving_dispatcher import SavingDispatcher
from tests.dispatchers.test_id_override_guard import SAVING_DISPATCHER_SUBCLASSES

FIXTURE = Path(__file__).with_name("persisted_keys.json")

# Every production module that defines a SavingDispatcher subclass. Importing
# them is what makes the subclasses visible to __subclasses__();
# test_every_production_subclass_is_covered proves this list is complete.
PRODUCTION_MODULES = (
    "reflex.components.home.els_advbar",
    "reflex.components.home.elsbar",
    "reflex.dispatchers.axis",
    "reflex.dispatchers.circle_pattern",
    "reflex.dispatchers.device",
    "reflex.dispatchers.els",
    "reflex.dispatchers.formats",
    "reflex.dispatchers.input",
    "reflex.dispatchers.line_pattern",
    "reflex.dispatchers.rect_pattern",
    "reflex.dispatchers.servo",
)


def _all_subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_subclasses(sub)


def production_subclasses():
    """SavingDispatcher subclasses defined under reflex/ -- test-local
    subclasses (other test modules define several) are excluded by module."""
    for name in PRODUCTION_MODULES:
        importlib.import_module(name)
    return sorted(
        {c for c in _all_subclasses(SavingDispatcher) if c.__module__.startswith("reflex.")},
        key=lambda c: c.__name__,
    )


def stem(cls) -> str:
    return cls._save_class_name or cls.__name__


def persisted_keys(cls) -> list[str]:
    """The keys save_settings writes for ``cls``, via the REAL rule."""
    instance = cls.__new__(cls)  # no __init__: read_settings must not run
    return sorted(p.name for p in SavingDispatcher.get_our_properties(instance))


def _axis_extra_samples(cls):
    """One save's extra data per TransformType, so the contract pins every
    ``transform_type`` string a card can hold -- AxisTransform.from_dict is the
    read-time migration for that field, and a value it stops recognising is
    exactly the stranding this contract exists to catch."""
    from reflex.dispatchers.axis_transform import AxisTransform, TransformType
    for tt in TransformType:
        inst = cls.__new__(cls)
        inst._transform = AxisTransform(
            contributions=(0, 1) if tt is TransformType.SUM else (0,), transform_type=tt)
        yield inst._get_extra_save_data()


# Dispatchers that override _get_extra_save_data, keyed by class name, with a
# function yielding sample extra-data dicts. The extras land in the SAME file
# as the properties, so they are pinned in the same list, as dotted paths
# (``transform_config.contributions``) and, for string leaves, path=value
# (``transform_config.transform_type=sum``).
EXTRA_SAVE_PROBES = {
    "AxisDispatcher": _axis_extra_samples,
}


def _flatten(d: dict, prefix: str = "") -> set[str]:
    out = set()
    for k, v in d.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            out |= _flatten(v, path + ".")
        elif isinstance(v, str):
            out.add(f"{path}={v}")
        else:
            out.add(path)
    return out


def extra_keys(cls) -> list[str]:
    probe = EXTRA_SAVE_PROBES.get(cls.__name__)
    if probe is None:
        return []
    keys = set()
    for sample in probe(cls):
        keys |= _flatten(sample)
    return sorted(keys)


def current_contract() -> dict[str, list[str]]:
    contract = {}
    for cls in production_subclasses():
        s = stem(cls)
        assert s not in contract, f"two dispatchers save to the same stem {s!r}"
        props, extras = persisted_keys(cls), extra_keys(cls)
        clash = {e.split(".")[0].split("=")[0] for e in extras} & set(props)
        assert not clash, f"{s}: extra save data overwrites properties {sorted(clash)}"
        contract[s] = sorted(props + extras)
    return dict(sorted(contract.items()))


def diff(pinned: dict, actual: dict) -> list[str]:
    """Human-readable differences, empty when the contract holds."""
    problems = []
    for s in sorted(pinned.keys() - actual.keys()):
        problems.append(f"stem {s!r}: pinned but no dispatcher saves it any more "
                        f"(renamed _save_class_name or class?) -- every card's {s}-*.yaml is stranded")
    for s in sorted(actual.keys() - pinned.keys()):
        problems.append(f"stem {s!r}: new dispatcher, not pinned yet")
    for s in sorted(pinned.keys() & actual.keys()):
        dropped = sorted(set(pinned[s]) - set(actual[s]))
        added = sorted(set(actual[s]) - set(pinned[s]))
        if dropped:
            problems.append(f"stem {s!r}: DROPPED {dropped} -- values on existing cards "
                            f"are ignored from now on unless read_settings migrates them")
        if added:
            problems.append(f"stem {s!r}: added {added}")
    return problems


# --- the contract ----------------------------------------------------------

def test_persisted_keys_match_the_contract():
    pinned = json.loads(FIXTURE.read_text())
    problems = diff(pinned, current_contract())
    assert not problems, (
        "The persisted-key contract changed:\n  " + "\n  ".join(problems) +
        "\n\nIf this is deliberate: regenerate persisted_keys.json (see this module's "
        "docstring) AND, for any DROPPED or renamed key, add a read-time migration in "
        "the same commit so cards carrying the old key keep their value.")


def test_every_production_subclass_is_covered():
    found = {c.__name__ for c in production_subclasses()}
    assert found == SAVING_DISPATCHER_SUBCLASSES, (
        f"PRODUCTION_MODULES misses {sorted(SAVING_DISPATCHER_SUBCLASSES - found)}; "
        f"unexpected {sorted(found - SAVING_DISPATCHER_SUBCLASSES)}")


def test_no_dispatcher_writes_keys_outside_the_contract():
    """_get_extra_save_data adds keys save_settings writes but get_our_properties
    never reports. Every override needs a probe, or its keys escape the pin.
    (This went red on first run: AxisDispatcher's transform_config.)"""
    overriders = {c.__name__ for c in production_subclasses()
                  if c._get_extra_save_data is not SavingDispatcher._get_extra_save_data}
    assert overriders == set(EXTRA_SAVE_PROBES), (
        f"override _get_extra_save_data with no probe: {sorted(overriders - set(EXTRA_SAVE_PROBES))}; "
        f"probe for a class that no longer overrides it: {sorted(set(EXTRA_SAVE_PROBES) - overriders)}")


def test_the_flattener_pins_nested_paths_and_string_values():
    sample = {"transform_config": {"transform_type": "sum", "contributions": [0, 1]}}
    assert _flatten(sample) == {"transform_config.transform_type=sum",
                                "transform_config.contributions"}


# --- proof the comparator and the helper can go red -------------------------

PINNED = {"Els": ["els_backlash_steps", "id_override"], "Device": ["id_override", "use_case"]}


def test_the_diff_passes_an_identical_contract():
    assert diff(PINNED, json.loads(json.dumps(PINNED))) == []


def test_the_diff_flags_a_renamed_key_as_dropped_and_added():
    actual = {**PINNED, "Els": ["els_backlash", "id_override"]}
    out = diff(PINNED, actual)
    assert any("DROPPED ['els_backlash_steps']" in p for p in out)
    assert any("added ['els_backlash']" in p for p in out)


def test_the_diff_flags_a_renamed_stem_and_a_new_one():
    actual = {"Els": PINNED["Els"], "DeviceSettings": PINNED["Device"]}
    out = diff(PINNED, actual)
    assert any("'Device': pinned but no dispatcher" in p for p in out)
    assert any("'DeviceSettings': new dispatcher" in p for p in out)


def test_the_key_set_follows_skip_and_force_save():
    class Probe(SavingDispatcher):
        kept = NumericProperty(1)
        skipped = StringProperty("x")
        flag = BooleanProperty(False)
        listed = ListProperty([])  # invisible unless forced
        _skip_save = ["skipped"]
        _force_save = ["listed"]
        _save_class_name = "ProbeStem"

    assert persisted_keys(Probe) == ["flag", "id_override", "kept", "listed"]
    assert stem(Probe) == "ProbeStem"
    assert Probe not in production_subclasses()


def test_the_fixture_is_sorted_and_canonical():
    """So a regenerated fixture diffs cleanly against the old one."""
    text = FIXTURE.read_text()
    data = json.loads(text)
    assert list(data) == sorted(data)
    assert all(v == sorted(v) for v in data.values())
    assert text == json.dumps(data, indent=2) + "\n"


if __name__ == "__main__":
    sys.stdout.write(json.dumps(current_contract(), indent=2) + "\n")
