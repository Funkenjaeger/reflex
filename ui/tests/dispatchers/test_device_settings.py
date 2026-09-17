"""``use_case`` / ``current_mode`` persist in ``Device-0.yaml``, migrated once
from the legacy ``ui/config.ini``.

Driven through ``MainApp._load_device_settings`` -- the call ``build()`` makes
-- so the assertions are about the app's own properties, which is what kv
binds, and not only about the dispatcher underneath.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

import pytest
import yaml

from reflex.app import (DEFAULT_USE_CASE, MODE_DRO, MODE_ELS, MODE_INDEX,
                        MainApp)
from reflex.dispatchers import device


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "config" / "reflex"
    root.mkdir(parents=True)
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(root))
    return root


@pytest.fixture
def ini(tmp_path, monkeypatch):
    """The legacy ini, aimed at a tmp path (the real one is gitignored). The
    test writes its content; an unwritten path is "no ini"."""
    path = tmp_path / "config.ini"
    monkeypatch.setattr(device, "legacy_ini_path", lambda: str(path))
    return path


def _stem(cfg):
    return cfg / "Device-0.yaml"


def _started_app():
    app = MainApp()
    app._load_device_settings()
    return app


def test_a_legacy_lathe_is_migrated_into_the_stem(cfg, ini):
    """(a) An existing card whose ini says lathe comes up a lathe, and the
    stem now says so too."""
    ini.write_text("[device]\nuse_case = lathe\ncurrent_mode = 2\n")
    app = _started_app()
    assert app.use_case == "lathe"
    assert app.current_mode == MODE_ELS
    assert app.patterns_available is False
    stored = yaml.safe_load(_stem(cfg).read_text())
    assert stored["use_case"] == "lathe"
    assert stored["current_mode"] == MODE_ELS


def test_the_migration_leaves_the_ini_byte_identical(cfg, ini):
    text = "[device]\nuse_case = lathe\ncurrent_mode = 2\nserial_port = /dev/x\n"
    ini.write_text(text)
    _started_app()
    assert ini.read_text() == text


def test_no_ini_and_no_stem_gives_the_default(cfg, ini):
    """(b) A fresh card is a rotary table. SavingDispatcher's normal behavior
    for a missing file is to write it with the defaults at construction, so
    the stem exists after startup, not only after the first change."""
    assert not ini.exists()
    app = _started_app()
    assert app.use_case == DEFAULT_USE_CASE == "rotary_table"
    assert app.current_mode == MODE_INDEX
    stored = yaml.safe_load(_stem(cfg).read_text())
    assert stored["use_case"] == "rotary_table"


def test_an_ini_without_a_device_section_gives_the_default(cfg, ini):
    ini.write_text("[other]\nkey = value\n")
    assert _started_app().use_case == DEFAULT_USE_CASE


def test_the_stem_wins_over_the_ini(cfg, ini):
    """(c) Once the stem exists the ini is never read for these keys."""
    _stem(cfg).write_text("use_case: lathe\ncurrent_mode: 2\n")
    ini.write_text("[device]\nuse_case = rotary_table\ncurrent_mode = 1\n")
    app = _started_app()
    assert app.use_case == "lathe"
    assert app.current_mode == MODE_ELS


def test_an_app_change_is_persisted(cfg, ini):
    app = _started_app()
    app.use_case = "lathe"
    stored = yaml.safe_load(_stem(cfg).read_text())
    assert stored["use_case"] == "lathe"
    # on_use_case moved the rotary-table INDEX mode to DRO, and that is saved.
    assert app.current_mode == MODE_DRO
    assert stored["current_mode"] == MODE_DRO


def test_a_stored_mode_invalid_for_the_use_case_is_corrected_and_saved(cfg, ini):
    _stem(cfg).write_text("use_case: rotary_table\ncurrent_mode: 2\n")
    app = _started_app()
    assert app.current_mode == MODE_DRO
    assert yaml.safe_load(_stem(cfg).read_text())["current_mode"] == MODE_DRO


def test_an_unparseable_legacy_mode_is_skipped(cfg, ini):
    ini.write_text("[device]\nuse_case = lathe\ncurrent_mode = two\n")
    app = _started_app()
    assert app.use_case == "lathe"
    # The mode stays at its default (INDEX), which a lathe does not offer.
    assert app.current_mode == MODE_DRO


def test_the_real_legacy_path_is_the_apps_own_config_ini():
    """Guard the seam the fixtures patch."""
    path = device.legacy_ini_path()
    assert path is not None
    assert path.replace("\\", "/").endswith("ui/config.ini")
    from reflex.components.appsettings import config_path
    assert path == config_path
