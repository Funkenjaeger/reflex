"""Quieten third-party loggers that drown the app's own output.

WHY THIS EXISTS. On 2026-08-16, a machine-test session's log was 1324 lines, of
which roughly 365 were the `transitions` state-machine library narrating its own
internals -- "Executed callbacks before conditions", "Executed machine finalize
callbacks", "Executed callback before transition" and so on, several per state
change. Another ~120 were one INFO line per Modbus register write. Against that,
the lines that actually mattered at the machine -- take-up outcomes, protocol
version, the diagnostic recorder's state -- were a handful, and finding them
meant grepping.

That is a real cost, not an aesthetic one: at the lathe the touchscreen log
viewer IS the diagnostic instrument, and a log you have to grep is a log you
cannot read while standing at a machine with the spindle running.

NOTHING IS DELETED, ONLY DEMOTED. Every one of these is genuinely useful when
you are debugging the thing it describes, so each is recoverable:

    REFLEX_LOG_TRANSITIONS=debug   FSM internals back at full volume
    REFLEX_LOG_TRANSITIONS=info    transition-level only

The default is WARNING: you still hear about it when the state machine is
unhappy, which is the part worth interrupting for.
"""

import logging
import os

# Third-party loggers and the level they run at unless overridden. The env var
# name is derived from the key, uppercased: transitions -> REFLEX_LOG_TRANSITIONS.
NOISY_LOGGERS = {
    "transitions": logging.WARNING,
}

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def resolve_level(name: str, default: int) -> int:
    """Level for `name`, honouring REFLEX_LOG_<NAME> if it is set and valid.

    An unrecognised value falls back to the default rather than raising. A typo
    in an env var should not stop the machine from starting; it should just fail
    to make the log louder.
    """
    raw = os.environ.get(f"REFLEX_LOG_{name.upper()}")
    if not raw:
        return default
    return _LEVELS.get(raw.strip().lower(), default)


def apply_log_levels():
    """Apply the quiet defaults. Safe to call more than once."""
    for name, default in NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(resolve_level(name, default))
