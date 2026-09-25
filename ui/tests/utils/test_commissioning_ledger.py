"""The ledger: what it records, what it refuses to record, and that it cannot
take a config save down with it."""
import json

import pytest

from reflex.utils import commissioning_bundle, commissioning_ledger


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Point config_dir() at a tmp path, the way the dispatcher tests do."""
    root = tmp_path / "config" / "reflex"
    root.mkdir(parents=True)
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
    return root


def _lines(cfg):
    path = commissioning_ledger.ledger_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── what gets recorded ───────────────────────────────────────────────

def test_a_changed_commissioning_value_is_one_line(cfg):
    old = {"axis_name": "Z", "backlash": 0.04}
    new = {"axis_name": "Z", "backlash": 0.062}
    n = commissioning_ledger.record(cfg / "Axis-1.yaml", old, new, "backlash")

    assert n == 1
    lines = _lines(cfg)
    assert len(lines) == 1
    assert lines[0]["file"] == "Axis-1"
    assert lines[0]["key"] == "backlash"
    assert lines[0]["old"] == 0.04
    assert lines[0]["new"] == 0.062
    assert lines[0]["trigger"] == "backlash"
    assert lines[0]["ts"].endswith("+00:00")   # UTC, explicit
    assert "." not in lines[0]["ts"]           # second resolution
    assert lines[0]["app"]                     # a version or "unknown"


def test_two_changed_keys_are_two_lines(cfg):
    old = {"axis_name": "Z", "backlash": 0.04}
    new = {"axis_name": "W", "backlash": 0.062}
    assert commissioning_ledger.record(cfg / "Axis-1.yaml", old, new, "") == 2
    keys = sorted(line["key"] for line in _lines(cfg))
    assert keys == ["axis_name", "backlash"]


def test_an_unchanged_save_writes_nothing(cfg):
    data = {"axis_name": "Z", "backlash": 0.04}
    assert commissioning_ledger.record(cfg / "Axis-1.yaml", data, dict(data), "x") == 0
    assert _lines(cfg) == []
    assert not commissioning_ledger.ledger_path().exists()


def test_file_creation_logs_every_commissioning_key_with_old_null(cfg):
    new = {"axis_name": "Z", "backlash": 0.04, "offsets": [0, 0],
           "id_override": "1", "size_hint_x": 1.0}
    n = commissioning_ledger.record(cfg / "Axis-1.yaml", None, new, "")

    lines = _lines(cfg)
    assert n == 2
    assert sorted(line["key"] for line in lines) == ["axis_name", "backlash"]
    assert all(line["old"] is None for line in lines)


def test_appends_rather_than_rewrites(cfg):
    commissioning_ledger.record(cfg / "Axis-1.yaml", {"b": 1}, {"b": 2}, "b")
    commissioning_ledger.record(cfg / "Axis-1.yaml", {"b": 2}, {"b": 3}, "b")
    assert [line["new"] for line in _lines(cfg)] == [2, 3]


def test_observable_list_compares_equal_to_a_plain_list(cfg):
    """The comparison that would otherwise log a change on every single save.

    What comes off disk is a plain list; what the dispatcher holds is a Kivy
    ObservableList. Normalize both or the ledger fills with phantom changes to
    els_cal_measured_legs and transform_config.
    """
    from kivy.event import EventDispatcher
    from kivy.properties import ListProperty

    class Holder(EventDispatcher):
        legs = ListProperty([1, 2, 3])

    observable = Holder().legs
    old = {"els_cal_measured_legs": [1, 2, 3]}
    new = {"els_cal_measured_legs": observable}
    assert commissioning_ledger.record(cfg / "Els-0.yaml", old, new, "") == 0

    new_moved = {"els_cal_measured_legs": Holder(legs=[1, 2, 4]).legs}
    assert commissioning_ledger.record(cfg / "Els-0.yaml", old, new_moved, "") == 1
    assert _lines(cfg)[0]["new"] == [1, 2, 4]   # serialized as a plain list


# ── what is refused ──────────────────────────────────────────────────

def test_operational_and_ignored_changes_are_never_recorded(cfg):
    """DRO zeroing is the highest-frequency write on the machine. If it
    reached the ledger, the ledger would be unreadable and useless."""
    old = {"offsets": [0, 0, 0], "id_override": "1", "size_hint_y": 1.0,
           "spacing": 2, "opacity": 1}
    new = {"offsets": [0, 1.5, 0], "id_override": "2", "size_hint_y": 0.5,
           "spacing": 4, "opacity": 0}
    assert commissioning_ledger.record(cfg / "Axis-1.yaml", old, new, "offsets") == 0
    assert _lines(cfg) == []


def test_a_spindles_sync_ratio_is_not_recorded_but_a_linear_axis_is(cfg):
    spindle_old = {"spindleMode": True, "syncRatioNum": 360}
    spindle_new = {"spindleMode": True, "syncRatioNum": 720}
    assert commissioning_ledger.record(
        cfg / "Axis-0.yaml", spindle_old, spindle_new, "syncRatioNum") == 0

    linear_old = {"spindleMode": False, "syncRatioNum": 1000}
    linear_new = {"spindleMode": False, "syncRatioNum": 2000}
    assert commissioning_ledger.record(
        cfg / "Axis-2.yaml", linear_old, linear_new, "syncRatioNum") == 1
    assert _lines(cfg)[0]["file"] == "Axis-2"


# ── it cannot take the save down ─────────────────────────────────────

def test_an_unwritable_ledger_dir_is_swallowed(cfg):
    """A record-keeping failure must never propagate into write_settings.

    The obstruction is a plain FILE where the ledger directory has to go, so
    mkdir raises FileExistsError -- reproducible on every platform, unlike a
    chmod.
    """
    (cfg / "ledger").write_text("not a directory\n")

    assert commissioning_ledger.record(
        cfg / "Axis-1.yaml", {"backlash": 0.04}, {"backlash": 0.9}, "backlash") == 0
    assert (cfg / "ledger").is_file()   # still the obstruction, nothing clobbered


def test_a_snapshot_failure_does_not_lose_the_ledger_line(cfg, monkeypatch):
    """Ordering guarantee: the line is appended BEFORE the snapshot is
    attempted, so the cheap durable record survives the expensive one
    failing."""
    def boom(reason):
        raise OSError("disk full")
    monkeypatch.setattr(commissioning_bundle, "snapshot", boom)

    commissioning_ledger.record(
        cfg / "Axis-1.yaml", {"backlash": 0.04}, {"backlash": 0.9}, "backlash")
    assert len(_lines(cfg)) == 1


# ── the snapshot side-effect ─────────────────────────────────────────

def test_a_commissioning_change_also_writes_a_snapshot(cfg):
    (cfg / "Axis-1.yaml").write_text("backlash: 0.9\n")
    commissioning_ledger.record(
        cfg / "Axis-1.yaml", {"backlash": 0.04}, {"backlash": 0.9}, "backlash")
    assert list(commissioning_bundle.snapshots_dir().glob("*-change.yaml"))


def test_an_operational_change_writes_no_snapshot(cfg):
    commissioning_ledger.record(
        cfg / "Axis-1.yaml", {"offsets": [0]}, {"offsets": [1]}, "offsets")
    assert not commissioning_bundle.snapshots_dir().exists()
