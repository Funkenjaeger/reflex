"""MainApp decides the commissioning state, once, before any dispatcher exists.

THE ORDER IS THE MECHANISM, not a tidiness preference. Every SavingDispatcher
built in ``MainApp.build`` reads its file in its constructor and -- when the
file is absent -- saves its in-code defaults. If the latch were taken after
``FormatsDispatcher(id_override="0")``, the first dispatcher would already have
written its commissioning event before anything knew the card was blank.

WHY NOT CALL build() IN A TEST. It constructs a Window, a theme with real GL
textures, a Board, the ELS controller and the whole screen manager; under this
suite's mock GL backend that segfaults, and with a real backend it costs
minutes. So ``latch_commissioning_state`` is its own method and this file runs
that -- the decision -- against real temporary config directories, while
``test_the_latch_is_taken_before_any_dispatcher_is_built`` holds the ORDER by
reading build()'s source, which is the only way left to check it.

SEEN-RED -- each mutation applied alone, then reverted (``git diff
--exit-code`` clean after every one). Measured 2026-09-22 against this file:

  * ``self.uncommissioned = not commissioned`` -> ``= False`` (3 failed, 4
    passed):
    ``test_an_empty_config_directory_puts_the_app_in_the_uncommissioned_state``,
    ``test_a_partial_directory_is_uncommissioned``,
    ``test_the_app_state_and_the_module_latch_agree``.
  * ``self.latch_commissioning_state()`` moved below
    ``self.formats = FormatsDispatcher(id_override="0")`` in build() (1 failed,
    6 passed): ``test_the_latch_is_taken_before_any_dispatcher_is_built``.
"""
import inspect
from pathlib import Path

import pytest

from reflex.app import MainApp
from reflex.utils import commissioning_state
from reflex.utils.commissioning_state import ANCHOR_FILE, MIN_YAML_FILES


@pytest.fixture(autouse=True)
def _no_latch_leaks():
    commissioning_state.clear_latch()
    yield
    commissioning_state.clear_latch()


def _config(root, *, yaml_files=MIN_YAML_FILES, anchor="els_backlash_steps: 450\n"):
    root.mkdir(parents=True, exist_ok=True)
    written = 0
    if anchor is not None:
        (root / ANCHOR_FILE).write_text(anchor)
        written = 1
    for i in range(yaml_files - written):
        (root / f"Filler-{i}.yaml").write_text(f"value: {i}\n")
    return root


class TestTheAppExposesTheState:
    def test_an_empty_config_directory_puts_the_app_in_the_uncommissioned_state(
            self, tmp_path, monkeypatch):
        """(A) The freshly flashed card, asked of the app rather than the
        predicate: this is the property the kv binds and the operator sees."""
        blank = tmp_path / "reflex-config"
        blank.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(blank))

        app = MainApp()
        assert app.latch_commissioning_state() is False
        assert app.uncommissioned is True

    def test_a_restored_card_comes_up_in_the_ordinary_state(self, tmp_path,
                                                            monkeypatch):
        """(B) Nothing about a commissioned machine's startup changes."""
        monkeypatch.setenv("REFLEX_CONFIG_DIR",
                           str(_config(tmp_path / "reflex-config", yaml_files=19)))

        app = MainApp()
        assert app.latch_commissioning_state() is True
        assert app.uncommissioned is False

    def test_a_partial_directory_is_uncommissioned(self, tmp_path, monkeypatch):
        """(C) 14 files, or a zero-byte anchor -- refused, not accepted as a
        subset, exactly as the restore script refuses it."""
        partial = _config(tmp_path / "partial", yaml_files=MIN_YAML_FILES - 1)
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(partial))
        app = MainApp()
        app.latch_commissioning_state()
        assert app.uncommissioned is True

        empty_anchor = _config(tmp_path / "empty-anchor", yaml_files=19, anchor="")
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(empty_anchor))
        app = MainApp()
        app.latch_commissioning_state()
        assert app.uncommissioned is True

    def test_deciding_the_state_creates_nothing(self, tmp_path, monkeypatch):
        """Asking must not answer. Refusing to invent commissioned data is the
        property being protected."""
        missing = tmp_path / "never-created"
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(missing))
        MainApp().latch_commissioning_state()
        assert not missing.exists()

    def test_the_app_state_and_the_module_latch_agree(self, tmp_path, monkeypatch):
        """One answer, two readers: the banner reads the app property, the save
        gate reads the module latch. They must never disagree."""
        blank = tmp_path / "reflex-config"
        blank.mkdir()
        monkeypatch.setenv("REFLEX_CONFIG_DIR", str(blank))
        app = MainApp()
        app.latch_commissioning_state()
        assert app.uncommissioned is not commissioning_state.latched()


class TestTheOrderInBuild:
    def test_the_latch_is_taken_before_any_dispatcher_is_built(self):
        """A dispatcher constructed first has already written its commissioning
        event; the gate would then be closing a door behind the horse."""
        source = inspect.getsource(MainApp.build)
        latch_at = source.index("latch_commissioning_state()")
        for first_dispatcher in ("FormatsDispatcher(", "_load_device_settings()",
                                 "Board(", "ElsDispatcher("):
            assert latch_at < source.index(first_dispatcher), (
                f"build() constructs {first_dispatcher} before latching the "
                f"commissioning state")

    def test_build_latches_at_all(self):
        assert "self.latch_commissioning_state()" in inspect.getsource(MainApp.build)
