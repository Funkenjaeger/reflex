"""Dismissal opens the write gate, persists, and is WITHDRAWN after an import.

WHAT THIS FILE IS FOR. ``test_uncommissioned_save_gate.py`` proves the gate
refuses writes on a card nobody measured. That is correct and it is also a
dead end for the operator with no capture to restore: the app they are standing
in front of will not save anything they type, ever. Dismissal is the way out
for exactly that person, and a dismissal that only hid the strip would be the
wrong feature -- it would leave the operator hand-entering geometry into an app
that silently discards it. So every case here is about the GATE, not the paint:
the widget half lives in ``tests/components/test_uncommissioned_details.py``.

THE FOUR PROPERTIES, and the third is the one that catches a marker that games
its own gate:

  (A) an uncommissioned directory plus a present marker -> ``write_settings``
      SUCCEEDS and the file lands on disk;
  (B) the same directory with NO marker -> still refuses BOTH the yaml write
      and the ledger event, i.e. nothing about the never-dismissed path moved;
  (C) the marker is not a ``.yaml`` file -- created on a directory one file
      SHORT of the bar, ``is_commissioned`` is still False. A marker that
      counted toward ``MIN_YAML_FILES`` would be a gate voting in its own
      election, and on the next boot the card would read as commissioned on
      the strength of the dismissal alone;
  (D) after ``commissioning_bundle.apply`` has run in this process, dismissal
      is gone. Driven through the REAL import function rather than a flag,
      because the property is "the files on disk moved", and a test that sets
      a boolean would pass against an implementation that never looks at the
      directory.

SEEN-RED -- each mutation applied alone, then reverted (``git diff
--exit-code`` clean after every one). Measured 2026-09-22; the observed counts
and names are in the build report.

  * dismissal made to hide the banner WITHOUT opening the gate
    (``dismiss()`` writes the marker but does not set ``_latched``, and
    ``latch()`` ignores the marker) -> case A goes red.
  * ``DISMISSAL_MARKER`` given a ``.yaml`` extension -> case C goes red.
  * ``dismissal_available()`` stops consulting
    ``config_changed_since_latch()`` -> case D goes red.
"""
from pathlib import Path

import pytest

from reflex.dispatchers.saving_dispatcher import write_settings
from reflex.utils import (commissioning_bundle, commissioning_ledger,
                          commissioning_state)
from reflex.utils.commissioning_state import (ANCHOR_FILE, DISMISSAL_MARKER,
                                              MIN_YAML_FILES, is_commissioned)


@pytest.fixture(autouse=True)
def _no_latch_leaks():
    """The latch is process-global; never let one test's answer reach another."""
    commissioning_state.clear_latch()
    yield
    commissioning_state.clear_latch()


def _blank_card(tmp_path, monkeypatch, name="reflex-config"):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
    return root


def _commissioned_card(tmp_path, monkeypatch, *, yaml_files=17, name="reflex-config"):
    """A directory meeting the restore contract. 17 is what the live machine
    carries (see commissioning_state's module docstring) -- two files of slack
    over the bar, not four."""
    root = _blank_card(tmp_path, monkeypatch, name)
    (root / ANCHOR_FILE).write_text("els_backlash_steps: 450\n")
    for i in range(yaml_files - 1):
        (root / f"Filler-{i}.yaml").write_text(f"value: {i}\n")
    return root


def _ledger_lines(root):
    path = root / "ledger" / "commissioning.jsonl"
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line.strip()]


# ── (A) a dismissed card saves ───────────────────────────────────────────────
class TestDismissalOpensTheWriteGate:
    """The half that makes this a feature rather than a way to hide a warning."""

    def test_a_present_marker_opens_the_gate_at_latch_and_the_file_lands(
            self, tmp_path, monkeypatch):
        """The LATER BOOT. The marker was written in some previous session;
        this process only finds it -- and the file has to reach the disk, not
        merely be attempted."""
        root = _blank_card(tmp_path, monkeypatch)
        (root / DISMISSAL_MARKER).write_text("dismissed by hand\n")

        assert is_commissioned(root) is False, (
            "the directory itself must still be uncommissioned, or this test "
            "is measuring the restore contract instead of the marker")
        assert commissioning_state.latch(root) is True

        target = root / "Axis-0.yaml"
        assert write_settings(str(target), {"backlash": 0.04}) is True
        assert target.exists()
        assert "backlash" in target.read_text()

    def test_the_ledger_records_the_write_too(self, tmp_path, monkeypatch):
        """Both halves of the refusal lift together. A dismissed machine that
        wrote yaml but no ledger line would have a commissioning history with
        a hole in it exactly where the hand-entered values are."""
        root = _blank_card(tmp_path, monkeypatch)
        (root / DISMISSAL_MARKER).touch()
        commissioning_state.latch(root)

        write_settings(str(root / "Axis-0.yaml"), {"backlash": 0.04})
        assert _ledger_lines(root) != []

    def test_dismissing_in_this_session_opens_the_gate_immediately(
            self, tmp_path, monkeypatch):
        """THE IN-SESSION PATH. The operator does not restart to start saving;
        that is the entire point of offering the choice at the machine."""
        root = _blank_card(tmp_path, monkeypatch)
        assert commissioning_state.latch(root) is False
        assert write_settings(str(root / "Axis-0.yaml"), {"backlash": 0.04}) is False

        assert commissioning_state.dismiss() is True
        assert commissioning_state.latched() is True
        assert write_settings(str(root / "Axis-0.yaml"), {"backlash": 0.04}) is True
        assert (root / "Axis-0.yaml").exists()

    def test_the_decision_persists_as_a_file_in_the_config_directory(
            self, tmp_path, monkeypatch):
        """PERSISTED, not remembered: the next boot is a different process and
        the operator is not going to dismiss it again every morning."""
        root = _blank_card(tmp_path, monkeypatch)
        commissioning_state.latch(root)
        commissioning_state.dismiss()

        marker = commissioning_state.dismissal_marker_path(root)
        assert marker.is_file()
        assert marker.parent == root

        # A fresh process: forget everything and latch again.
        commissioning_state.clear_latch()
        assert commissioning_state.latch(root) is True

    def test_deleting_the_marker_puts_the_warning_back(self, tmp_path, monkeypatch):
        """The documented undo, and the only one. If this ever stops working
        an operator has no way to re-arm a gate they opened by mistake."""
        root = _blank_card(tmp_path, monkeypatch)
        commissioning_state.latch(root)
        commissioning_state.dismiss()

        commissioning_state.dismissal_marker_path(root).unlink()
        commissioning_state.clear_latch()
        assert commissioning_state.latch(root) is False


# ── (B) nothing about the never-dismissed path moved ─────────────────────────
class TestTheUndismissedPathStillRefusesBothHalves:
    """2026-09-21#1's property, re-asserted against the code that can now open
    the gate. The gate is not weakened; a door was added beside it."""

    @pytest.fixture
    def cfg(self, tmp_path, monkeypatch):
        root = _blank_card(tmp_path, monkeypatch)
        assert commissioning_state.latch(root) is False
        return root

    def test_no_marker_means_no_yaml_file(self, cfg):
        assert write_settings(str(cfg / "Axis-0.yaml"), {"backlash": 0.04}) is False
        assert list(cfg.glob("*.yaml")) == []

    def test_no_marker_means_no_ledger_event(self, cfg):
        write_settings(str(cfg / "Axis-0.yaml"), {"backlash": 0.04})
        assert _ledger_lines(cfg) == []

    def test_no_ledger_observer_is_notified(self, cfg):
        seen = []
        commissioning_ledger.on_change(seen.append)
        try:
            write_settings(str(cfg / "Axis-0.yaml"), {"backlash": 0.04})
        finally:
            commissioning_ledger.clear_change_observers()
        assert seen == []

    def test_merely_asking_whether_dismissal_is_available_creates_nothing(
            self, tmp_path, monkeypatch):
        """Asking must not answer -- the same property `latch` has."""
        missing = tmp_path / "never-created"
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(missing))
        commissioning_state.latch(missing)
        assert commissioning_state.dismissal_available() is True
        assert not missing.exists()


# ── (C) the marker cannot vote in its own election ───────────────────────────
class TestTheMarkerIsNotAYamlFile:

    def test_the_name_carries_no_yaml_extension(self):
        assert not DISMISSAL_MARKER.endswith(".yaml")
        assert not DISMISSAL_MARKER.endswith(".yml")

    def test_a_marker_on_a_directory_one_file_short_does_not_make_the_bar(
            self, tmp_path, monkeypatch):
        """THE ARM THAT CATCHES A MARKER THAT GAMES ITS OWN GATE.

        One file short of MIN_YAML_FILES, so a marker that the ``*.yaml`` glob
        counted would tip the directory over the restore contract's bar -- and
        the NEXT boot would find a machine that calls itself commissioned on
        the strength of somebody having dismissed a warning.
        """
        root = _commissioned_card(tmp_path, monkeypatch,
                                  yaml_files=MIN_YAML_FILES - 1)
        assert len(list(root.glob("*.yaml"))) == MIN_YAML_FILES - 1
        assert is_commissioned(root) is False

        commissioning_state.latch(root)
        assert commissioning_state.dismiss() is True

        assert commissioning_state.dismissal_marker_path(root).is_file(), (
            "the marker was never created, so this test would pass against "
            "an implementation that does nothing")
        assert len(list(root.glob("*.yaml"))) == MIN_YAML_FILES - 1
        assert is_commissioned(root) is False, (
            "the dismissal marker is being counted as configuration")

    def test_the_marker_is_not_exported_in_a_bundle(self, tmp_path, monkeypatch):
        """Dismissal is a decision about THIS card. A bundle that carried it
        would hand another machine somebody else's decision to skip measuring.

        Asserted on the marker's STEM, which is how ``commissioning_bundle``
        keys a section, and not on its filename: keyed on the filename this
        check passes trivially the moment the marker is renamed to something
        that WOULD be exported, which is the exact mutation it exists to
        catch.
        """
        root = _commissioned_card(tmp_path, monkeypatch)
        (root / DISMISSAL_MARKER).write_text("dismissed\n")
        doc = commissioning_bundle.build(fw_rev="1.2.0")
        assert Path(DISMISSAL_MARKER).stem not in doc
        assert DISMISSAL_MARKER not in doc
        assert "Els-0" in doc, "the bundle is empty; this comparison proves nothing"


# ── (D) after a real import, dismissal is gone ───────────────────────────────
class TestDismissalIsWithdrawnAfterAnImport:
    """THE HARD CONSTRAINT, and it is correctness rather than caution.

    ``commissioning_bundle.apply`` writes restored YAML to disk with its own
    ``_atomic_dump`` while every dispatcher in memory still holds its in-code
    defaults. Open the gate at that moment and the next property change writes
    the defaults over the values just restored -- the operator's own import
    destroys their configuration. The remedy is a restart and nothing else.
    """

    def _bundle(self, tmp_path, monkeypatch):
        """A real document, from the real producer, off a real directory."""
        source = tmp_path / "source"
        source.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(source))
        (source / "Axis-1.yaml").write_text("axis_name: Z\nbacklash: 0.04\n")
        (source / "Els-0.yaml").write_text(
            "spindle_axis_index: 0\nz_axis_index: 1\n")
        return commissioning_bundle.build(fw_rev="1.2.0")

    @pytest.fixture
    def imported(self, tmp_path, monkeypatch):
        doc = self._bundle(tmp_path, monkeypatch)
        card = _blank_card(tmp_path, monkeypatch, name="card")
        assert commissioning_state.latch(card) is False
        assert commissioning_state.dismissal_available() is True, (
            "dismissal was already unavailable BEFORE the import, so this "
            "fixture cannot tell whether the import is what withdrew it")

        report = commissioning_bundle.apply(doc, card)
        assert report.ok and report.written, (
            f"the import wrote nothing ({report.reason}); there is no hazard "
            f"to test")
        return card

    def test_dismissal_is_unavailable(self, imported):
        assert commissioning_state.dismissal_available() is False

    def test_dismiss_refuses_and_leaves_no_marker(self, imported):
        """Belt and braces: the module refuses on its own account, so a caller
        that built the dialog wrong still cannot open the gate."""
        assert commissioning_state.dismiss() is False
        assert not commissioning_state.dismissal_marker_path(imported).exists()

    def test_the_gate_is_still_shut(self, imported):
        assert commissioning_state.latched() is False
        assert write_settings(str(imported / "Axis-1.yaml"),
                              {"backlash": 0.999}) is False

    def test_the_restored_values_are_intact(self, imported):
        """The thing actually being protected, stated as bytes rather than as
        a flag: the file the import wrote still holds the imported value."""
        assert "backlash: 0.04" in (imported / "Axis-1.yaml").read_text()

    def test_a_refused_import_leaves_dismissal_available(self, tmp_path,
                                                         monkeypatch):
        """The converse, so the rule is 'configuration arrived', not 'apply was
        called'. A bundle apply() refuses writes nothing, so there is no
        hazard and no reason to take the operator's way out away."""
        card = _blank_card(tmp_path, monkeypatch, name="card")
        commissioning_state.latch(card)
        report = commissioning_bundle.apply(
            {"meta": {"schema": commissioning_bundle.SCHEMA + 99}}, card)
        assert report.ok is False
        assert commissioning_state.dismissal_available() is True

    def test_a_dry_run_import_leaves_dismissal_available(self, tmp_path,
                                                         monkeypatch):
        doc = self._bundle(tmp_path, monkeypatch)
        card = _blank_card(tmp_path, monkeypatch, name="card")
        commissioning_state.latch(card)
        assert commissioning_bundle.apply(doc, card, dry_run=True).ok
        assert commissioning_state.dismissal_available() is True

    def test_a_restore_over_ssh_is_caught_by_the_same_rule(self, tmp_path,
                                                           monkeypatch):
        """Not an apply() flag: the rule is about the DIRECTORY, so a file
        dropped in over SSH while the app runs carries the same hazard and
        gets the same answer."""
        card = _blank_card(tmp_path, monkeypatch, name="card")
        commissioning_state.latch(card)
        assert commissioning_state.dismissal_available() is True
        (card / "Axis-1.yaml").write_text("axis_name: Z\nbacklash: 0.04\n")
        assert commissioning_state.dismissal_available() is False


# ── (E) a commissioned machine is untouched ──────────────────────────────────
class TestACommissionedMachineIsUnaffected:
    """The no-regression arm. ``test_uncommissioned_save_gate.py``'s
    ``TestCommissionedBehaviourIsUnchanged`` already diffs every byte the two
    runs produce and still does; what is added here is that the DISMISSAL
    machinery specifically is inert on a machine that never needed it."""

    def test_no_marker_is_created_on_a_commissioned_card(self, tmp_path,
                                                          monkeypatch):
        root = _commissioned_card(tmp_path, monkeypatch)
        before = {p.name for p in root.iterdir()}
        assert commissioning_state.latch(root) is True
        write_settings(str(root / "Axis-0.yaml"), {"backlash": 0.04})
        assert DISMISSAL_MARKER not in {p.name for p in root.iterdir()}
        assert "Axis-0.yaml" not in before and (root / "Axis-0.yaml").exists(), (
            "the run wrote nothing, so the absence of a marker proves nothing")

    def test_dismissal_is_not_offered_on_a_commissioned_card(self, tmp_path,
                                                             monkeypatch):
        root = _commissioned_card(tmp_path, monkeypatch)
        commissioning_state.latch(root)
        assert commissioning_state.dismissal_available() is False

    def test_dismissal_is_not_offered_when_nothing_ever_latched(self):
        """Off the machine -- tests, previews, tools/ -- there is no card to
        make a decision about, and `latched()` already defaults to open."""
        assert commissioning_state.latched() is True
        assert commissioning_state.dismissal_available() is False
