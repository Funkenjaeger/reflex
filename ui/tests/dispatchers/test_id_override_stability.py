"""Settings must be keyed by something stable, and the key must not be an input.

The defect: SavingDispatcher falls back to `f"{self.uid}"` -- a Kivy widget uid
-- so a widget without an explicit id_override stores its settings in a file
named by how many widgets happened to be constructed before it. elspi
accumulated six ElsAdvancedBar-<uid>.yaml files that disagreed with each other
about `enable_retract`, i.e. about which of the three stop modes the operator
had selected. Any change to the widget tree moved the bar to a different file,
or to none, silently reverting the choice.

The second half is subtler and is what makes fixing the first half safe:
`id_override` is a StringProperty, so it was written to the file AND read back
out of it -- and it is the key in `filename`. A stored value therefore lets a
file rename itself back.
"""
import io

import pytest
import yaml

from reflex.dispatchers.saving_dispatcher import SavingDispatcher


class _Widget(SavingDispatcher):
    """Minimal dispatcher; the behaviour under test is all in the base."""
    from kivy.properties import BooleanProperty, StringProperty
    enable_retract = BooleanProperty(False)
    label = StringProperty("")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path))
    return tmp_path


# --- the key must not be loadable from the thing it keys ------------------

def test_id_override_is_not_read_back_from_the_file(cfg):
    """A file that names a different id must not redirect the next save."""
    (cfg / "_Widget-0.yaml").write_text(
        yaml.safe_dump({"id_override": "2164", "enable_retract": True}),
        encoding="utf-8")

    w = _Widget(id_override="0")

    # The real setting came through...
    assert w.enable_retract is True
    # ...but the key did not move.
    assert w.id_override == "0"
    assert w.filename.name == "_Widget-0.yaml"


def test_a_migrated_file_does_not_rename_itself_back(cfg):
    """End to end: the exact shape of the elspi migration."""
    (cfg / "_Widget-0.yaml").write_text(
        yaml.safe_dump({"id_override": "2164", "enable_retract": True}),
        encoding="utf-8")

    w = _Widget(id_override="0")
    w.label = "touched"          # triggers a save

    assert not (cfg / "_Widget-2164.yaml").exists(), "save escaped to the old key"
    written = yaml.safe_load((cfg / "_Widget-0.yaml").read_text(encoding="utf-8"))
    assert written["id_override"] == "0"
    assert written["enable_retract"] is True


def test_id_override_is_still_written_for_legibility(cfg):
    """Write-only, not absent -- somebody reading the file should see the key."""
    w = _Widget(id_override="7")
    w.label = "x"
    written = yaml.safe_load((cfg / "_Widget-7.yaml").read_text(encoding="utf-8"))
    assert written["id_override"] == "7"


# --- the advanced bar in particular ---------------------------------------

def test_advanced_bar_is_constructed_with_a_stable_key():
    """Source assertion: the uid fallback is what produced six files, and a
    behavioural test would need the whole widget tree to catch its return."""
    import inspect

    from reflex.components.home import els_mode_layout

    src = inspect.getsource(els_mode_layout)
    assert 'ElsAdvancedBar(els_bar=els_bar, id_override="0")' in src
    # The bare form is the regression.
    assert "ElsAdvancedBar(els_bar=els_bar)" not in src


def test_uid_fallback_still_exists_for_widgets_that_want_it():
    """Not removing the fallback -- only refusing to rely on it where the
    settings matter. A dispatcher given no id still gets a working file."""
    w = _Widget()
    assert w.id_override != ""
    assert w.filename.name == f"_Widget-{w.id_override}.yaml"
