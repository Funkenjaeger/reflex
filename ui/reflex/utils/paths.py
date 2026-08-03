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
