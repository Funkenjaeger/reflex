import os
from pathlib import Path


def config_dir() -> Path:
    """Return the directory reflex-ui reads/writes its persisted settings in
    (axis/servo/input calibration YAML, user theme INIs, profiler output).

    Defaults to ``~/.config/reflex`` (unchanged behavior). Set
    ``REFLEX_CONFIG_DIR`` to override -- e.g. to move the directory out of
    ``/root`` when the service runs as root, so it can be made readable by an
    unprivileged operator without a directory-mode change or sudo rule.
    """
    override = os.environ.get("REFLEX_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "reflex"


def diag_dir() -> Path:
    """Return the directory firmware diagnostic captures are appended to.

    Sits under :func:`config_dir` deliberately: on the machine that directory is
    already placed outside ``/root`` precisely so an unprivileged operator can
    read it, and a capture is no use if it lands somewhere you need sudo to
    fetch. Set ``REFLEX_DIAG_DIR`` to override independently.

    Captures are only ever written when the firmware was built with
    ``ELS_DIAG_SCRATCH``; against a release build this directory is never
    created.
    """
    override = os.environ.get("REFLEX_DIAG_DIR")
    if override:
        return Path(override).expanduser()
    return config_dir() / "diag"


def flight_dir() -> Path:
    """Return the directory the flight recorder rotates its poll log through.

    A SIBLING of :func:`diag_dir`, not a child, and the difference is the
    retention policy rather than tidiness. Everything under ``diag`` is written
    once and kept until somebody copies it off the machine; everything here is
    AUTOMATICALLY DELETED when the byte budget is reached. Nesting an
    auto-pruned tree inside the directory people hand-copy captures out of is a
    way to lose a capture -- and the operator has no terminal, so they would
    never see it go.

    Set ``REFLEX_FLIGHT_DIR`` to override independently (a USB stick, a tmpfs,
    or somewhere with more room than the SD card).
    """
    override = os.environ.get("REFLEX_FLIGHT_DIR")
    if override:
        return Path(override).expanduser()
    return config_dir() / "flight"
