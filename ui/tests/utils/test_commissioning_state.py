"""The commissioned/uncommissioned predicate, pinned at the restore contract's bar.

THE DEFECT THIS EXISTS TO CATCH. elspi's seam amendment (2026-09-21) starts the
app on first boot, so an unrestored card now reaches ``SavingDispatcher``.
``read_settings`` returns ``None`` for an absent file and the dispatcher keeps
its in-code defaults, so the UI looks configured on geometry nobody measured.
:func:`reflex.utils.commissioning_state.is_commissioned` is the one gate that
tells those two situations apart, and it is only worth anything if its bar is
the SAME bar the restore script refuses on -- otherwise a card the provisioning
run would refuse to restore onto could still call itself commissioned.

So these cases deliberately mirror elspi ``deltas/tests/test-restore-contract.sh``
one for one: no ``Els-0.yaml``, a zero-byte ``Els-0.yaml``, a partial capture,
and a good one.

SEEN-RED -- each mutation applied to reflex/utils/commissioning_state.py alone,
then reverted (``git diff --exit-code`` clean after every one). Measured
2026-09-22 against this file:

  * ``MIN_YAML_FILES = 15`` -> ``0`` (4 failed, 13 passed):
    ``test_exactly_the_minimum_is_commissioned``,
    ``test_fourteen_files_is_a_partial_capture``,
    ``test_other_extensions_do_not_count``,
    ``test_yaml_under_a_subdirectory_does_not_count``.
    NOT ``test_an_empty_directory_is_not_commissioned`` -- the anchor half
    refuses that one first, which is the correct division of labour and worth
    knowing before reading a future failure list.
  * the ``st_size <= 0`` half of the anchor check deleted (1 failed, 16
    passed): ``test_a_zero_byte_anchor_is_not_a_restore_point``.
  * ``path.glob("*.yaml")`` -> ``path.rglob("*.yaml")`` (1 failed, 16 passed):
    ``test_yaml_under_a_subdirectory_does_not_count``.
  * ``latched()`` made to re-derive the answer instead of returning the stored
    one (1 failed, 16 passed):
    ``test_latching_a_commissioned_directory_opens_the_gate``.
"""
import pytest

from reflex.utils import commissioning_state
from reflex.utils.commissioning_state import (ANCHOR_FILE, MIN_YAML_FILES,
                                              is_commissioned)

ANCHOR_BODY = "els_backlash_steps: 450\nels_cal_last_measured_steps: 375\n"


def _make_config(root, *, yaml_files=MIN_YAML_FILES, anchor=ANCHOR_BODY):
    """Build a config directory with `yaml_files` .yaml files in total.

    `anchor` of None leaves Els-0.yaml out entirely; "" writes it zero-byte.
    The filler files are named like real dispatcher stems so a reader can see
    what the count is counting.
    """
    root.mkdir(parents=True, exist_ok=True)
    written = 0
    if anchor is not None:
        (root / ANCHOR_FILE).write_text(anchor)
        written = 1
    for i in range(yaml_files - written):
        (root / f"Filler-{i}.yaml").write_text(f"value: {i}\n")
    return root


@pytest.fixture(autouse=True)
def _no_latch_leaks():
    """The latch is process-global; never let one test's answer reach another."""
    commissioning_state.clear_latch()
    yield
    commissioning_state.clear_latch()


class TestThePredicate:
    # ── (A) nothing there ────────────────────────────────────────────────────
    def test_an_empty_directory_is_not_commissioned(self, tmp_path):
        """The freshly flashed card. This is the whole point of the module."""
        empty = tmp_path / "reflex-config"
        empty.mkdir()
        assert is_commissioned(empty) is False

    def test_a_missing_directory_is_not_commissioned(self, tmp_path):
        assert is_commissioned(tmp_path / "never-created") is False

    def test_a_file_where_the_directory_should_be_is_not_commissioned(self, tmp_path):
        impostor = tmp_path / "reflex-config"
        impostor.write_text("not a directory\n")
        assert is_commissioned(impostor) is False

    def test_a_directory_with_no_yaml_at_all_is_not_commissioned(self, tmp_path):
        root = tmp_path / "reflex-config"
        root.mkdir()
        (root / "README.txt").write_text("hello\n")
        assert is_commissioned(root) is False

    # ── (B) a real capture ───────────────────────────────────────────────────
    def test_a_directory_meeting_the_restore_contract_is_commissioned(self, tmp_path):
        root = _make_config(tmp_path / "reflex-config", yaml_files=19)
        assert is_commissioned(root) is True

    def test_exactly_the_minimum_is_commissioned(self, tmp_path):
        """15 is the bar, not one past it -- the shell uses `-ge`."""
        root = _make_config(tmp_path / "reflex-config", yaml_files=MIN_YAML_FILES)
        assert len(list(root.glob("*.yaml"))) == MIN_YAML_FILES
        assert is_commissioned(root) is True

    # ── (C) partial, in each of the three ways it can be partial ─────────────
    def test_fourteen_files_is_a_partial_capture(self, tmp_path):
        root = _make_config(tmp_path / "reflex-config",
                            yaml_files=MIN_YAML_FILES - 1)
        assert len(list(root.glob("*.yaml"))) == MIN_YAML_FILES - 1
        assert is_commissioned(root) is False

    def test_a_zero_byte_anchor_is_not_a_restore_point(self, tmp_path):
        """A touched placeholder is not measured geometry. Plenty of files,
        so the count half passes and only the anchor half can refuse."""
        root = _make_config(tmp_path / "reflex-config", yaml_files=19, anchor="")
        assert (root / ANCHOR_FILE).stat().st_size == 0
        assert len(list(root.glob("*.yaml"))) == 19
        assert is_commissioned(root) is False

    def test_plenty_of_files_but_no_anchor_is_not_commissioned(self, tmp_path):
        root = _make_config(tmp_path / "reflex-config", yaml_files=19, anchor=None)
        assert not (root / ANCHOR_FILE).exists()
        assert is_commissioned(root) is False

    # ── the count counts the right things ────────────────────────────────────
    def test_yaml_under_a_subdirectory_does_not_count(self, tmp_path):
        """`ledger/snapshots/*.yaml` is the app's OWN record-keeping. A
        recursive count would let it vote itself commissioned, which is the
        exact self-certification this module exists to prevent."""
        root = _make_config(tmp_path / "reflex-config", yaml_files=3)
        snapshots = root / "ledger" / "snapshots"
        snapshots.mkdir(parents=True)
        for i in range(30):
            # DISTINCT names. An earlier draft of this test numbered them
            # `i % 10` and wrote ten files while claiming thirty -- 3 + 10 is
            # under the bar, so the test passed against a `rglob` mutant and
            # measured nothing. A count test whose count is wrong is a check
            # that cannot fail.
            (snapshots / f"20260921T0000{i:02d}Z-startup.yaml").write_text("meta: {}\n")
        assert len(list(snapshots.glob("*.yaml"))) == 30
        assert len(list(root.rglob("*.yaml"))) > MIN_YAML_FILES
        assert is_commissioned(root) is False

    def test_other_extensions_do_not_count(self, tmp_path):
        """`.yml`, `.ini` and `.json` are not what the restore script counts."""
        root = _make_config(tmp_path / "reflex-config", yaml_files=3)
        for i in range(30):
            (root / f"other-{i}.yml").write_text("value: 1\n")
            (root / f"other-{i}.ini").write_text("[x]\n")
        assert is_commissioned(root) is False

    def test_the_default_directory_is_this_machines_config_dir(self, tmp_path,
                                                               monkeypatch):
        """Called with no argument it must ask `paths.config_dir()`, which is
        what REFLEX_CONFIG_DIR redirects on the card."""
        root = _make_config(tmp_path / "reflex-config")
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
        assert is_commissioned() is True
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / "elsewhere"))
        assert is_commissioned() is False


class TestTheLatch:
    def test_unlatched_reads_as_commissioned(self):
        """Nothing outside the app boots a card, so nothing outside the app has
        a machine to judge -- and answering "uncommissioned" there would
        silently disable config persistence for every caller that never asked."""
        assert commissioning_state.latched() is True

    def test_latch_remembers_the_answer(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert commissioning_state.latch(empty) is False
        assert commissioning_state.latched() is False

    def test_the_latch_does_not_move_when_the_directory_fills_up(self, tmp_path):
        """THE GATE MUST NOT OPEN UNDER ITS OWN FEET. If the answer were
        re-derived per save, writes 13..15 would push the count past the bar and
        the app would promote its own defaults to commissioned mid-session."""
        root = tmp_path / "reflex-config"
        root.mkdir()
        commissioning_state.latch(root)
        assert commissioning_state.latched() is False
        _make_config(root, yaml_files=19)
        assert is_commissioned(root) is True        # the directory changed...
        assert commissioning_state.latched() is False  # ...the latched answer did not

    def test_latching_a_commissioned_directory_opens_the_gate(self, tmp_path):
        root = _make_config(tmp_path / "reflex-config")
        assert commissioning_state.latch(root) is True
        assert commissioning_state.latched() is True

    def test_the_predicate_never_creates_anything(self, tmp_path):
        """Refusing to invent commissioned data is the property being
        protected: asking the question must not answer it."""
        missing = tmp_path / "never-created"
        assert is_commissioned(missing) is False
        commissioning_state.latch(missing)
        assert not missing.exists()
