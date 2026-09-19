"""End to end through a real SavingDispatcher: a property changes, and the
ledger line plus the snapshot appear on the card.

WHY A REAL DISPATCHER AND NOT A write_settings() CALL. The value of the hook is
that it fires on the path the machine actually uses -- `bind_settings` wires
every persisted property to `save_settings`, so a touch on the ELS setup screen
lands in `write_settings` with a `triggering_property`. A test that called
`write_settings` directly would pass even if the hook were wired somewhere the
app never reaches.

SEEN-RED. The hook was temporarily neutralized (the
`commissioning_ledger.record(...)` line in `write_settings` commented out) and
this module was rerun: `test_changing_a_property_writes_a_ledger_line` and
`test_a_commissioning_change_snapshots_the_whole_machine` both failed on the
empty ledger, and `test_creating_a_dispatcher_logs_its_commissioning_keys`
failed too. The line was then restored.
"""
import json

import pytest
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, StringProperty

from reflex.dispatchers.saving_dispatcher import SavingDispatcher
from reflex.utils import commissioning_bundle, commissioning_ledger


class CommissionedThing(SavingDispatcher):
    """A minimal dispatcher carrying one key of each tier.

    `offsets` needs `_force_save` for the same reason axis.py's does:
    `get_our_properties` only auto-includes Numeric/String/Boolean by exact
    type, so a ListProperty is invisible to it otherwise.
    """
    backlash = NumericProperty(0.04)      # commissioning
    axis_name = StringProperty("Z")       # commissioning
    spindleMode = BooleanProperty(False)  # commissioning (which axis IS the spindle)
    syncRatioNum = NumericProperty(1000)  # tier depends on spindleMode
    offsets = ListProperty([0, 0])        # operational, always

    _skip_save = []
    _force_save = ["offsets"]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "config" / "reflex"
    root.mkdir(parents=True)
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
    return root


def _lines():
    path = commissioning_ledger.ledger_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _snapshots():
    return sorted(commissioning_bundle.snapshots_dir().glob("*.yaml"))


# ── the hook ─────────────────────────────────────────────────────────

def test_creating_a_dispatcher_logs_its_commissioning_keys(cfg):
    """First creation with no file on disk IS the commissioning event."""
    CommissionedThing(id_override="0")

    lines = _lines()
    assert sorted(line["key"] for line in lines) == [
        "axis_name", "backlash", "spindleMode", "syncRatioNum"]
    assert all(line["old"] is None for line in lines)
    assert all(line["file"] == "CommissionedThing-0" for line in lines)


def test_changing_a_property_writes_a_ledger_line(cfg):
    thing = CommissionedThing(id_override="0")
    before = len(_lines())

    thing.backlash = 0.062

    lines = _lines()
    assert len(lines) == before + 1
    latest = lines[-1]
    assert latest["file"] == "CommissionedThing-0"
    assert latest["key"] == "backlash"
    assert latest["old"] == 0.04
    assert latest["new"] == 0.062
    # write_settings already receives the triggering property name; the hook
    # carries it through rather than inventing attribution.
    assert latest["trigger"] == "backlash"


def test_a_commissioning_change_snapshots_the_whole_machine(cfg):
    thing = CommissionedThing(id_override="0")
    thing.axis_name = "W"

    snaps = _snapshots()
    assert snaps, "a commissioning change must leave a recoverable copy"
    import yaml
    doc = yaml.safe_load(snaps[-1].read_text())
    assert doc["CommissionedThing-0"]["axis_name"] == "W"
    assert doc["meta"]["schema"] == 1


def test_an_operational_change_leaves_the_ledger_alone(cfg):
    thing = CommissionedThing(id_override="0")
    before = len(_lines())

    thing.offsets = [0, 1.5]

    assert (cfg / "CommissionedThing-0.yaml").exists()
    assert len(_lines()) == before, "work offsets are job state, not identity"


def test_the_spindle_rule_survives_the_round_trip(cfg):
    """The tier of syncRatioNum follows the flag IN THE SAVED FILE.

    Not a filename and not an in-memory guess: the data written alongside it is
    what commissioning_scope reads.
    """
    thing = CommissionedThing(id_override="0")
    thing.spindleMode = True
    before = len(_lines())

    thing.syncRatioNum = 360

    assert len(_lines()) == before, \
        "a spindle's sync ratio is the operator picking a feed"


def test_the_write_still_happens_when_the_ledger_cannot(cfg):
    """The guarantee that makes the hook safe to run on a machine tool.

    A plain file where the ledger directory has to go, so every ledger write
    fails -- and the config save must be untouched.
    """
    (cfg / "ledger").write_text("not a directory\n")

    thing = CommissionedThing(id_override="0")
    thing.backlash = 0.062

    import yaml
    saved = yaml.safe_load((cfg / "CommissionedThing-0.yaml").read_text())
    assert saved["backlash"] == 0.062
    assert _lines() == []


def test_write_settings_still_returns_its_result(cfg):
    """The hook must not change the write path's return value."""
    from reflex.dispatchers.saving_dispatcher import write_settings

    assert write_settings(cfg / "Axis-9.yaml", {"backlash": 1}, "backlash") is True
    assert write_settings(cfg / "no-such-dir" / "Axis-9.yaml", {"backlash": 1}) is False
