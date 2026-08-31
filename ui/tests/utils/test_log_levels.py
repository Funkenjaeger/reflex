"""Third-party log level defaults and their env overrides.

The point of the module under test is that noise is off by default but
RECOVERABLE. A silencer with no way back is worse than the noise: it means the
first time you actually need the FSM internals, you are editing source on a
machine in a workshop.
"""

import logging

import pytest

from reflex.utils.log_levels import NOISY_LOGGERS, apply_log_levels, resolve_level


@pytest.fixture(autouse=True)
def _restore_levels():
    """Leave the real loggers as we found them -- these are process-global."""
    saved = {name: logging.getLogger(name).level for name in NOISY_LOGGERS}
    yield
    for name, lvl in saved.items():
        logging.getLogger(name).setLevel(lvl)


def test_transitions_is_quiet_by_default(monkeypatch):
    """~365 of 1324 lines in a 2026-08-16 machine-test log were this library
    narrating its own callback dispatch. It is not interesting until it breaks."""
    monkeypatch.delenv("REFLEX_LOG_TRANSITIONS", raising=False)
    apply_log_levels()
    assert logging.getLogger("transitions").level == logging.WARNING


def test_warnings_still_get_through(monkeypatch):
    """Quiet must not mean silent -- a state machine that is unhappy is worth
    interrupting for, which is why the floor is WARNING and not ERROR."""
    monkeypatch.delenv("REFLEX_LOG_TRANSITIONS", raising=False)
    apply_log_levels()
    assert logging.getLogger("transitions").isEnabledFor(logging.WARNING)
    assert not logging.getLogger("transitions").isEnabledFor(logging.INFO)


@pytest.mark.parametrize("value,expected", [
    ("debug", logging.DEBUG),
    ("INFO", logging.INFO),
    ("  Warning  ", logging.WARNING),
])
def test_env_override_restores_the_noise(monkeypatch, value, expected):
    """Case and surrounding whitespace should not defeat it -- this gets typed
    at a machine, not pasted."""
    monkeypatch.setenv("REFLEX_LOG_TRANSITIONS", value)
    apply_log_levels()
    assert logging.getLogger("transitions").level == expected


def test_a_typo_falls_back_instead_of_raising(monkeypatch):
    """A misspelled env var must not stop the app starting. The worst it should
    do is fail to make the log louder."""
    monkeypatch.setenv("REFLEX_LOG_TRANSITIONS", "verbose")   # not a level
    apply_log_levels()
    assert logging.getLogger("transitions").level == logging.WARNING


def test_resolve_level_is_pure_and_defaults_cleanly(monkeypatch):
    monkeypatch.delenv("REFLEX_LOG_NOSUCHLOGGER", raising=False)
    assert resolve_level("nosuchlogger", logging.ERROR) == logging.ERROR
