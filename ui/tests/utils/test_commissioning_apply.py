"""``commissioning_bundle.apply`` -- writing a bundle back onto a config dir.

See ``apply``'s own docstring for the two refusals (a too-new schema, a
section with no commissioning-tier data) and why a real export's sections
never trip the second one.
"""
import os

import pytest
import yaml

from reflex.utils import commissioning_bundle


@pytest.fixture
def source_dir(tmp_path, monkeypatch):
    """A config dir with real files, wired the same way as
    ``test_commissioning_bundle.py``'s ``cfg`` fixture."""
    root = tmp_path / "source"
    root.mkdir()
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
    (root / "Axis-0.yaml").write_text(
        "axis_name: C\nspindleMode: true\nsyncRatioNum: 360\n")
    (root / "Axis-1.yaml").write_text("axis_name: Z\nbacklash: 0.04\n")
    (root / "Els-0.yaml").write_text(
        "spindle_axis_index: 0\nz_axis_index: 1\n")
    return root


@pytest.fixture
def dest_dir(tmp_path):
    d = tmp_path / "dest"
    d.mkdir()
    return d


# ── (1) round trip ───────────────────────────────────────────────────────

def test_round_trip_is_byte_identical(source_dir, dest_dir):
    doc = commissioning_bundle.build(fw_rev="1.2.0")

    report = commissioning_bundle.apply(doc, dest_dir)

    assert report.ok
    assert sorted(report.written) == ["Axis-0", "Axis-1", "Els-0"]
    assert report.skipped == []
    for stem in ["Axis-0", "Axis-1", "Els-0"]:
        source_text = (source_dir / f"{stem}.yaml").read_text()
        dest_text = (dest_dir / f"{stem}.yaml").read_text()
        assert dest_text == source_text, f"{stem}.yaml was not byte-identical"


def test_round_trip_preserves_the_operational_key(source_dir, dest_dir):
    """The whole point of writing sections verbatim: syncRatioNum on the
    spindle axis is operational (see commissioning_scope), and a filtered
    write would silently drop it -- and fail the byte-identical assertion
    above. This pins the reason, not just the symptom."""
    doc = commissioning_bundle.build()
    commissioning_bundle.apply(doc, dest_dir)
    written = yaml.safe_load((dest_dir / "Axis-0.yaml").read_text())
    assert written["syncRatioNum"] == 360


def test_config_ini_is_not_restored(source_dir, dest_dir):
    """A legacy config_ini section is never written back as an ini or as a
    stem of its own."""
    doc = commissioning_bundle.build()
    doc["config_ini"] = {"device": {"use_case": "lathe", "current_mode": "2"}}
    commissioning_bundle.apply(doc, dest_dir)
    assert not (dest_dir / "config.ini").exists()
    assert not (dest_dir / "config_ini.yaml").exists()


# ── legacy bundles: config_ini.device.use_case -> Device-0 ───────────────

def _legacy_bundle(use_case="lathe"):
    doc = commissioning_bundle.build()
    assert "config_ini" not in doc
    doc["config_ini"] = {"device": {"use_case": use_case, "current_mode": "2"}}
    return doc


def test_a_legacy_use_case_is_written_to_the_device_stem(source_dir, dest_dir):
    """(e) A pre-migration export, which carried use_case only in config_ini,
    still restores a lathe."""
    report = commissioning_bundle.apply(_legacy_bundle("lathe"), dest_dir)
    assert report.ok, report.reason
    assert "Device-0" in report.written
    written = yaml.safe_load((dest_dir / "Device-0.yaml").read_text())
    assert written == {"use_case": "lathe"}


def test_a_legacy_use_case_does_not_override_a_device_stem(source_dir, dest_dir):
    """A bundle that has both: the stem is the newer truth."""
    doc = _legacy_bundle("lathe")
    doc["Device-0"] = {"use_case": "rotary_table", "current_mode": 1}
    commissioning_bundle.apply(doc, dest_dir)
    written = yaml.safe_load((dest_dir / "Device-0.yaml").read_text())
    assert written["use_case"] == "rotary_table"


def test_a_legacy_config_ini_without_use_case_writes_no_device_stem(
        source_dir, dest_dir):
    doc = commissioning_bundle.build()
    doc["config_ini"] = None
    report = commissioning_bundle.apply(doc, dest_dir)
    assert report.ok
    assert not (dest_dir / "Device-0.yaml").exists()


def test_apply_never_mutates_the_callers_document(source_dir, dest_dir):
    doc = _legacy_bundle("lathe")
    commissioning_bundle.apply(doc, dest_dir)
    assert "Device-0" not in doc


# ── (2) RED: schema too new ──────────────────────────────────────────────

def test_a_newer_schema_is_refused_and_nothing_is_written(source_dir, dest_dir):
    doc = commissioning_bundle.build()
    doc["meta"]["schema"] = commissioning_bundle.SCHEMA + 1

    report = commissioning_bundle.apply(doc, dest_dir)

    assert report.ok is False
    assert str(commissioning_bundle.SCHEMA + 1) in report.reason
    assert report.written == []
    assert list(dest_dir.iterdir()) == []


def test_a_missing_schema_is_refused(source_dir, dest_dir):
    """No schema at all is not "assume schema 1" -- it is not a bundle this
    code recognises, and the refusal path is the safer default."""
    doc = commissioning_bundle.build()
    del doc["meta"]["schema"]

    report = commissioning_bundle.apply(doc, dest_dir)

    assert report.ok is False
    assert list(dest_dir.iterdir()) == []


# ── (3) RED: a section outside the commissioning scope ───────────────────

def test_a_section_with_no_commissioning_data_is_refused(source_dir, dest_dir):
    """``offsets`` is operational in every file (commissioning_scope.py); a
    section that is ONLY that carries no machine identity at all."""
    doc = commissioning_bundle.build()
    doc["Bogus-0"] = {"offsets": [0, 0, 0]}

    report = commissioning_bundle.apply(doc, dest_dir)

    assert report.ok is False
    assert "Bogus-0" in report.reason
    assert list(dest_dir.iterdir()) == []


def test_a_mixed_section_is_not_refused(source_dir, dest_dir):
    """Axis-0 mixes a commissioning key (axis_name) with an operational one
    (syncRatioNum, because spindleMode is true) -- exactly what a real
    export looks like -- and must pass."""
    doc = commissioning_bundle.build()
    report = commissioning_bundle.apply(doc, dest_dir)
    assert report.ok is True


# ── (4) atomicity: a write failure on the second file ────────────────────

def test_a_failure_on_the_second_file_leaves_the_first_intact(
        source_dir, dest_dir, monkeypatch):
    """Simulated by making the SECOND call to ``_atomic_dump`` raise. The
    first stem's file must exist with its real content (not half-written,
    not missing), the second must be reported skipped, and no stray ``.tmp``
    file should be left behind under dest_dir."""
    doc = commissioning_bundle.build()
    stems = sorted(k for k in doc if k not in commissioning_bundle.NON_STEM_KEYS)
    assert len(stems) >= 2, "need at least two sections to test the second one failing"
    failing_stem = stems[1]

    real_atomic_dump = commissioning_bundle._atomic_dump
    calls = []

    def flaky_atomic_dump(data, path):
        calls.append(path.stem)
        if path.stem == failing_stem:
            raise OSError("simulated write failure")
        return real_atomic_dump(data, path)

    monkeypatch.setattr(commissioning_bundle, "_atomic_dump", flaky_atomic_dump)

    report = commissioning_bundle.apply(doc, dest_dir)

    assert report.ok is True  # per-file failures don't refuse the whole apply
    assert stems[0] in report.written
    assert any(stem == failing_stem for stem, _reason in report.skipped)
    assert not (dest_dir / f"{failing_stem}.yaml").exists(), (
        "a failed write must not leave a partial (or any) file behind"
    )
    # First file's content is the real, complete write -- not touched by the
    # second file's failure.
    first_content = yaml.safe_load((dest_dir / f"{stems[0]}.yaml").read_text())
    assert first_content == doc[stems[0]]
    # No orphaned temp file anywhere under dest_dir.
    leftovers = [p for p in dest_dir.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_atomic_dump_leaves_the_original_file_untouched_on_failure(
        dest_dir, monkeypatch):
    """Exercises ``_atomic_dump`` directly: a pre-existing destination file
    must survive byte-for-byte if the write to the temp file fails before
    the rename."""
    target = dest_dir / "Axis-0.yaml"
    original = "axis_name: C\n"
    target.write_text(original)

    def boom(*args, **kwargs):
        raise yaml.YAMLError("simulated dump failure")

    monkeypatch.setattr(commissioning_bundle.yaml, "safe_dump", boom)

    with pytest.raises(yaml.YAMLError):
        commissioning_bundle._atomic_dump({"axis_name": "Z"}, target)

    assert target.read_text() == original
    leftovers = [p for p in dest_dir.iterdir() if p.name.startswith(".")]
    assert leftovers == [], "the temp file must be cleaned up on failure"


# ── genregs / register-map contract is untouched by this change ──────────
# (exercised separately via `python tools/genregs.py --check` in the build
# report -- this module does not touch devices.py or Ramps.h.)
