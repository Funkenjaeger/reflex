"""Tests for reflex.utils.paths.config_dir.

This helper exists so the persisted-settings directory can be moved OUT of a
privileged home. On the real lathe the service runs as root (for DRM/KMS and
/var/log writes), which put the commissioned machine config — geometry, servo
polarity, calibration — in /root/.config/reflex, unreadable by the operator
account and therefore un-diffable, un-backupable, and invisible if it drifted.

The alternatives were a directory-mode change on /root or a standing sudo rule
to read one directory; an explicit, overridable path is neither.
"""
import os
from pathlib import Path

from reflex.utils.paths import config_dir


def test_default_is_the_xdg_style_home_path(monkeypatch):
    monkeypatch.delenv("REFLEX_CONFIG_DIR", raising=False)
    assert config_dir() == Path.home() / ".config" / "reflex"


def test_override_is_honored(monkeypatch, tmp_path):
    target = tmp_path / "reflex-config"
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(target))
    assert config_dir() == target


def test_override_expands_a_tilde(monkeypatch):
    monkeypatch.setenv("REFLEX_CONFIG_DIR", "~/somewhere/reflex")
    assert config_dir() == Path.home() / "somewhere" / "reflex"


def test_empty_override_falls_back_to_the_default(monkeypatch):
    """An env var set to "" is the shape a misconfigured unit file produces
    (Environment=REFLEX_CONFIG_DIR= with nothing after it). Treat it as unset
    rather than resolving to the process CWD, which is where Path("") leads."""
    monkeypatch.setenv("REFLEX_CONFIG_DIR", "")
    assert config_dir() == Path.home() / ".config" / "reflex"


def test_override_is_read_per_call_not_cached(monkeypatch, tmp_path):
    """The value must not be frozen at import time — deployments set it in the
    launcher, and tests/tools change it between calls."""
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / "first"))
    first = config_dir()
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / "second"))
    assert config_dir() != first
    assert config_dir() == tmp_path / "second"


def test_saving_dispatcher_uses_the_override(monkeypatch, tmp_path):
    """End-to-end: the persisted-settings path is what actually has to move.
    A helper nothing consumes would be a no-op fix."""
    from reflex.dispatchers.saving_dispatcher import SavingDispatcher

    target = tmp_path / "cfg"
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(target))

    class _Probe(SavingDispatcher):
        _save_class_name = "Probe"

    probe = _Probe()
    resolved = Path(probe.filename)
    assert resolved.parent == target, (
        f"SavingDispatcher wrote outside the override: {resolved}"
    )
    assert target.is_dir(), "filename should create the settings dir"
    assert os.fspath(resolved).endswith(".yaml")
