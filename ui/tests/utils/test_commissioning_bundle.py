"""The bundle document: the single-document form of the whole machine config
that USB export, a later cloud sync, and the snapshots all carry."""
import yaml

import pytest

from reflex.utils import commissioning_bundle


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "config" / "reflex"
    root.mkdir(parents=True)
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
    (root / "Axis-0.yaml").write_text(
        "axis_name: C\nspindleMode: true\nsyncRatioNum: 360\n")
    (root / "Axis-1.yaml").write_text("axis_name: Z\nbacklash: 0.04\n")
    (root / "Els-0.yaml").write_text("spindle_axis_index: 0\nz_axis_index: 1\n")
    return root


# ── shape ────────────────────────────────────────────────────────────

def test_meta_carries_the_provenance_of_the_capture(cfg):
    doc = commissioning_bundle.build(fw_rev="1.2.0")
    meta = doc["meta"]
    assert meta["schema"] == 1
    assert meta["ts"].endswith("+00:00")
    assert meta["machine_id"]
    assert meta["hostname"]
    assert meta["app"]
    assert meta["fw"] == "1.2.0"


def test_fw_is_null_when_the_board_could_not_be_asked(cfg):
    """A bundle built with the board offline is still a valid bundle; it says
    so rather than guessing a firmware revision."""
    assert commissioning_bundle.build()["meta"]["fw"] is None


def test_one_top_level_key_per_yaml_stem_in_sorted_order(cfg):
    doc = commissioning_bundle.build()
    stems = [k for k in doc if k not in commissioning_bundle.NON_STEM_KEYS]
    assert stems == ["Axis-0", "Axis-1", "Els-0"]
    assert doc["Axis-1"] == {"axis_name": "Z", "backlash": 0.04}


def test_build_emits_the_device_stem_and_no_config_ini(cfg):
    """(d) config_ini is retired: use_case travels as the Device-0 stem,
    written here by the real dispatcher rather than by hand."""
    from reflex.dispatchers.device import DeviceDispatcher
    dev = DeviceDispatcher(id_override="0")
    dev.use_case = "lathe"
    doc = commissioning_bundle.build()
    assert "config_ini" not in doc
    assert doc[commissioning_bundle.DEVICE_STEM]["use_case"] == "lathe"


def test_the_bundle_device_stem_is_the_dispatchers_file(cfg):
    """The bundle names the stem without importing the dispatcher; pin the
    two spellings together."""
    from reflex.dispatchers import device
    from reflex.dispatchers.device import DeviceDispatcher
    assert commissioning_bundle.DEVICE_STEM == device.DEVICE_STEM
    assert DeviceDispatcher(id_override="0").filename.stem == device.DEVICE_STEM


def test_the_ledger_subtree_is_not_swallowed_into_the_bundle(cfg):
    """A snapshot that contained the previous snapshots would grow without
    bound; the config-dir walk is deliberately non-recursive."""
    snaps = commissioning_bundle.snapshots_dir()
    snaps.mkdir(parents=True)
    (snaps / "20260101T000000Z-startup.yaml").write_text("meta: {}\n")
    doc = commissioning_bundle.build()
    assert [k for k in doc if k not in commissioning_bundle.NON_STEM_KEYS] == [
        "Axis-0", "Axis-1", "Els-0"]


# ── dump / split ─────────────────────────────────────────────────────

def test_meta_comes_first_in_the_dumped_text(cfg, tmp_path):
    out = tmp_path / "bundle.yaml"
    commissioning_bundle.dump(commissioning_bundle.build(), out)
    text = out.read_text()
    assert text.startswith("meta:")
    # default_flow_style=False: nested mappings are block, not `{a: 1}`.
    assert "\nAxis-1:\n  axis_name: Z\n" in text


def test_dump_creates_parent_directories(cfg, tmp_path):
    out = tmp_path / "a" / "b" / "bundle.yaml"
    commissioning_bundle.dump(commissioning_bundle.build(), out)
    assert out.exists()


def test_split_round_trips_to_the_on_disk_mappings(cfg):
    """split(build()) is the decomposition a future import applies, so it has
    to be exactly what is on the card -- verbatim, not normalized."""
    parts = commissioning_bundle.split(commissioning_bundle.build())
    on_disk = {p.stem: yaml.safe_load(p.read_text())
               for p in cfg.glob("*.yaml")}
    assert parts == on_disk


def test_split_drops_meta_and_config_ini(cfg):
    """A legacy bundle's config_ini is still not a stem."""
    doc = commissioning_bundle.build()
    doc["config_ini"] = {"device": {"use_case": "lathe"}}
    parts = commissioning_bundle.split(doc)
    assert "meta" not in parts
    assert "config_ini" not in parts


def test_a_dumped_bundle_reloads_to_the_same_config(cfg, tmp_path):
    """The config half of the document survives a dump/load exactly.

    Only the config half is asserted: `meta.ts` is an ISO-8601 string, and
    YAML's own timestamp resolver hands it back as a `datetime` rather than
    the string that was written. That is why "has anything changed?" is
    computed over the document MINUS meta (see `_without_meta`) -- comparing
    whole reloaded documents would report a change on every read.
    """
    doc = commissioning_bundle.build(fw_rev="1.2.0")
    out = tmp_path / "bundle.yaml"
    commissioning_bundle.dump(doc, out)
    reloaded = yaml.safe_load(out.read_text())
    assert commissioning_bundle.split(reloaded) == commissioning_bundle.split(doc)


# ── snapshots ────────────────────────────────────────────────────────

def test_snapshot_writes_a_named_timestamped_file(cfg):
    path = commissioning_bundle.snapshot("export")
    assert path.parent == commissioning_bundle.snapshots_dir()
    assert path.name.endswith("-export.yaml")
    assert yaml.safe_load(path.read_text())["meta"]["schema"] == 1


def test_snapshot_if_changed_writes_on_the_first_call(cfg):
    path = commissioning_bundle.snapshot_if_changed("startup")
    assert path is not None
    assert path.name.endswith("-startup.yaml")


def test_snapshot_if_changed_is_silent_when_nothing_moved(cfg):
    """meta.ts differs on every build, so comparing whole documents would
    snapshot the card on every startup forever."""
    first = commissioning_bundle.snapshot_if_changed("startup")
    assert first is not None
    assert commissioning_bundle.snapshot_if_changed("startup") is None
    assert len(list(commissioning_bundle.snapshots_dir().glob("*.yaml"))) == 1


def test_snapshot_if_changed_catches_a_hand_edit(cfg):
    """The whole reason it exists: a write the ledger never saw.

    Asserted on the CONTENT of the newest snapshot, not on a file count. Stamps
    are second-resolution, so two snapshots for the same reason inside one
    second land on one filename -- the wanted behavior when a run of saves
    fires, and it makes a count assertion a coin flip on a fast machine.
    """
    first = commissioning_bundle.snapshot_if_changed("startup")
    assert yaml.safe_load(first.read_text())["Axis-1"]["backlash"] == 0.04

    (cfg / "Axis-1.yaml").write_text("axis_name: Z\nbacklash: 0.062\n")

    second = commissioning_bundle.snapshot_if_changed("startup")
    assert second is not None
    assert yaml.safe_load(second.read_text())["Axis-1"]["backlash"] == 0.062


def test_snapshot_if_changed_never_raises(cfg):
    """It is called from App.build(); a card that cannot write its snapshot
    must still boot into a working lathe."""
    (cfg / "ledger").write_text("not a directory\n")
    assert commissioning_bundle.snapshot_if_changed("startup") is None


def test_newest_snapshot_is_the_latest_timestamp(cfg):
    snaps = commissioning_bundle.snapshots_dir()
    snaps.mkdir(parents=True)
    for name in ["20260101T000000Z-startup.yaml",
                 "20260913T190411Z-change.yaml",
                 "20260301T120000Z-change.yaml"]:
        (snaps / name).write_text("meta: {}\n")
    assert commissioning_bundle.newest_snapshot_path().name == \
        "20260913T190411Z-change.yaml"
