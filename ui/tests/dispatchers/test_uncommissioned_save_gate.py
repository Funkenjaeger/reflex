"""An uncommissioned machine records nothing; a commissioned one is untouched.

WHAT IS BEING PROTECTED. ``write_settings``'s own comment says a file that did
not exist before is "the commissioning event for that dispatcher". On a card
that has never been restored onto, every value a dispatcher holds is an in-code
default, so letting that event fire writes down numbers nobody measured as this
lathe's commissioning baseline -- and, because ~19 default files clear the
restore contract's 15-file bar, the next boot would find them and agree.

TWO HALVES, AND THE SECOND IS THE ONE THAT IS EASY TO GET WRONG:

  (D) while uncommissioned, a save writes NO ledger line AND NO yaml file. A
      gate on the ledger alone leaves the card self-certifying on reboot, which
      is why :class:`TestTheGateIsAlsoOnTheFile` exists.
  (E) while commissioned, nothing changed. Proven rather than asserted: the
      same scenario is run twice into two temporary directories -- once with
      the latch never armed (today's code path, since `latched()` defaults to
      True) and once armed against a commissioned directory -- and every byte
      of every file the run produced is compared. Clocks and version strings
      are frozen so that "different" can only mean a behavior difference.

SEEN-RED -- mutations applied to reflex/dispatchers/saving_dispatcher.py or
reflex/utils/commissioning_state.py alone, then reverted (``git diff
--exit-code`` clean after every one). Measured 2026-09-22 against this file:

  * the whole ``if not commissioning_state.latched(): ... return False`` block
    removed from ``write_settings`` (7 failed, 4 passed): every test in
    :class:`TestNothingIsRecordedWhileUncommissioned` plus
    ``TestTheGateIsAlsoOnTheFile::test_a_session_of_saves_cannot_promote_the_card_to_commissioned``
    and ``::test_the_file_on_disk_is_not_touched``.
  * THE PARTIAL FIX -- gate narrowed to skip only the ledger, file still
    written (5 failed, 6 passed): ``test_no_yaml_file_is_created``,
    ``test_an_explicit_property_change_writes_no_commissioning_event``,
    ``test_write_settings_reports_that_it_wrote_nothing``,
    ``test_a_session_of_saves_cannot_promote_the_card_to_commissioned``,
    ``test_the_file_on_disk_is_not_touched``. The pure-ledger tests
    (``test_constructing_a_dispatcher_writes_no_commissioning_event``,
    ``test_no_ledger_observer_is_notified``) stayed GREEN -- which is exactly
    why :class:`TestTheGateIsAlsoOnTheFile` exists.
  * ``commissioning_state.latched()`` made to return ``False`` unconditionally,
    i.e. the gate firing on a commissioned machine (2 failed, 9 passed):
    ``TestCommissionedBehaviourIsUnchanged::test_the_run_actually_produced_the_things_being_compared``
    and ``::test_the_commissioning_event_still_fires_for_a_new_dispatcher``.
    ``test_every_byte_of_every_file_is_identical`` did NOT fail, and that is
    the whole reason its sibling exists: with the gate closed both runs write
    nothing, so a byte comparison of two empty trees passes and measures
    nothing. Naming the signal and branching on it is the only thing that
    makes the comparison mean anything.
"""
import json

import pytest
from kivy.properties import BooleanProperty, NumericProperty, StringProperty

from reflex.dispatchers.saving_dispatcher import SavingDispatcher
from reflex.utils import (commissioning_bundle, commissioning_ledger,
                          commissioning_state)
from reflex.utils.commissioning_state import ANCHOR_FILE, MIN_YAML_FILES


class Thing(SavingDispatcher):
    """One commissioning key of each persisted type. `backlash` is the one the
    ledger cares about -- see utils/commissioning_scope.py for the tiering."""
    backlash = NumericProperty(0.04)
    axis_name = StringProperty("Z")
    spindleMode = BooleanProperty(False)

    _skip_save = []


@pytest.fixture(autouse=True)
def _no_latch_leaks():
    commissioning_state.clear_latch()
    yield
    commissioning_state.clear_latch()


@pytest.fixture(autouse=True)
def _frozen_stamps(monkeypatch):
    """Freeze everything that would differ between two runs for reasons that
    are not behavior: the ledger timestamp, the snapshot filename stamp, and
    the installed version string. Without this the byte comparison in
    :class:`TestCommissionedBehaviourIsUnchanged` could only ever be a
    same-shape comparison, and a same-shape comparison is the kind of check
    that cannot fail."""
    monkeypatch.setattr(commissioning_bundle, "utc_now",
                        lambda: "2026-09-21T00:00:00+00:00")
    monkeypatch.setattr(commissioning_bundle, "_file_stamp",
                        lambda: "20260921T000000Z")
    monkeypatch.setattr(commissioning_bundle, "app_version", lambda: "0.0.0-test")
    monkeypatch.setattr(commissioning_bundle, "machine_id", lambda: "test-machine")


def _seed_commissioned(root):
    """A directory that meets the restore contract: non-empty Els-0.yaml and
    MIN_YAML_FILES .yaml files in total."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ANCHOR_FILE).write_text("els_backlash_steps: 450\n")
    for i in range(MIN_YAML_FILES - 1):
        (root / f"Filler-{i}.yaml").write_text(f"value: {i}\n")
    return root


def _tree(root):
    """Every file under `root`, as {relative posix path: bytes}. The comparison
    unit for the no-regression proof."""
    return {
        str(p.relative_to(root).as_posix()): p.read_bytes()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _ledger_lines(root):
    path = root / "ledger" / "commissioning.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── (D) uncommissioned: nothing is recorded ──────────────────────────────────
class TestNothingIsRecordedWhileUncommissioned:

    @pytest.fixture
    def cfg(self, tmp_path, monkeypatch):
        root = tmp_path / "reflex-config"
        root.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
        assert commissioning_state.latch(root) is False
        return root

    def test_constructing_a_dispatcher_writes_no_commissioning_event(self, cfg):
        """Construction is where it would happen: `read_settings` finds no file
        and calls `save_settings`, which on a restored machine is the first
        write and therefore THE commissioning event for that dispatcher."""
        Thing(id_override="0")
        assert _ledger_lines(cfg) == []

    def test_no_yaml_file_is_created(self, cfg):
        Thing(id_override="0")
        assert list(cfg.glob("*.yaml")) == []

    def test_an_explicit_property_change_writes_no_commissioning_event(self, cfg):
        thing = Thing(id_override="0")
        thing.backlash = 0.999          # via the property binding
        thing.save_settings()           # and again explicitly
        assert _ledger_lines(cfg) == []
        assert list(cfg.glob("*.yaml")) == []

    def test_the_dispatcher_still_holds_its_in_code_defaults(self, cfg):
        """Refusing to WRITE is not refusing to RUN. The app must still come
        up -- wearing the banner -- or the operator has no way to reach the
        Backup screen and import a capture."""
        thing = Thing(id_override="0")
        assert thing.backlash == 0.04
        assert thing.axis_name == "Z"

    def test_write_settings_reports_that_it_wrote_nothing(self, cfg):
        from reflex.dispatchers.saving_dispatcher import write_settings
        assert write_settings(str(cfg / "Thing-0.yaml"), {"backlash": 0.04}) is False

    def test_no_ledger_observer_is_notified(self, cfg):
        """gist_sync hangs off `commissioning_ledger.on_change`. An
        uncommissioned card must not push its invented defaults anywhere."""
        seen = []
        commissioning_ledger.on_change(seen.append)
        try:
            thing = Thing(id_override="0")
            thing.backlash = 0.123
        finally:
            commissioning_ledger.clear_change_observers()
        assert seen == []


class TestTheGateIsAlsoOnTheFile:
    """The half a ledger-only fix would miss.

    ~19 default yaml files is itself past the restore contract's 15-file bar.
    Skip only the ledger record and the card writes its own defaults to disk,
    the NEXT boot latches "commissioned" on them, and the uncommissioned state
    lasts exactly one session.
    """

    @pytest.fixture
    def cfg(self, tmp_path, monkeypatch):
        root = tmp_path / "reflex-config"
        root.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
        commissioning_state.latch(root)
        return root

    def test_a_session_of_saves_cannot_promote_the_card_to_commissioned(self, cfg):
        """Twenty dispatchers, each saving -- more than the live machine's 19 --
        and the directory must still fail the predicate afterwards."""
        for i in range(20):
            thing = Thing(id_override=str(i))
            thing.backlash = 0.1 + i

        # Fresh evaluation of the predicate, not the latched answer: this is
        # what the NEXT boot would compute.
        assert commissioning_state.is_commissioned(cfg) is False
        assert list(cfg.glob("*.yaml")) == []

    def test_the_file_on_disk_is_not_touched(self, cfg):
        """A stale or hand-placed file is left exactly as found. Writing over
        it would be the same act as creating one."""
        stray = cfg / "Thing-0.yaml"
        stray.write_text("backlash: 7.5\n")
        before = stray.read_bytes()
        thing = Thing(id_override="0")
        thing.backlash = 0.001
        thing.save_settings()
        assert stray.read_bytes() == before


# ── (E) commissioned: byte-for-byte what it did before ───────────────────────
class TestCommissionedBehaviourIsUnchanged:
    """PROVEN, not asserted: run the identical scenario twice and diff bytes.

    Control run = the latch never armed, which is literally the pre-change code
    path (`latched()` returns True when nothing latched, so `write_settings`
    falls straight through to the line it always had). Subject run = the latch
    armed against a directory that meets the restore contract.
    """

    def _run(self, root, monkeypatch, *, latch):
        _seed_commissioned(root)
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
        commissioning_state.clear_latch()
        if latch:
            assert commissioning_state.latch(root) is True

        thing = Thing(id_override="0")
        thing.backlash = 0.062
        thing.axis_name = "X"
        thing.spindleMode = True
        thing.save_settings()
        return _tree(root)

    def test_every_byte_of_every_file_is_identical(self, tmp_path, monkeypatch):
        control = self._run(tmp_path / "control", monkeypatch, latch=False)
        subject = self._run(tmp_path / "subject", monkeypatch, latch=True)
        assert sorted(control) == sorted(subject)
        for name in control:
            assert control[name] == subject[name], f"{name} differs"

    def test_the_run_actually_produced_the_things_being_compared(self, tmp_path,
                                                                 monkeypatch):
        """A comparison of two empty directories passes and measures nothing.
        Name the signal and branch on it: the scenario must have written the
        dispatcher's yaml, ledger lines, and a snapshot."""
        tree = self._run(tmp_path / "probe", monkeypatch, latch=True)
        assert "Thing-0.yaml" in tree
        assert "ledger/commissioning.jsonl" in tree
        assert any(n.startswith("ledger/snapshots/") for n in tree)
        lines = _ledger_lines(tmp_path / "probe")
        assert [entry["key"] for entry in lines if entry["file"] == "Thing-0"]

    def test_the_commissioning_event_still_fires_for_a_new_dispatcher(self, tmp_path,
                                                                      monkeypatch):
        """The first write of a file is still the commissioning event on a
        commissioned machine -- the gate suppresses it, it does not delete it."""
        root = _seed_commissioned(tmp_path / "reflex-config")
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
        commissioning_state.latch(root)
        Thing(id_override="0")
        stems = {entry["file"] for entry in _ledger_lines(root)}
        assert "Thing-0" in stems
        assert (root / "Thing-0.yaml").exists()
