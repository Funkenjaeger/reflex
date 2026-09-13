"""The whole machine configuration as ONE document, plus point-in-time snapshots.

WHY ONE DOCUMENT. The machine's configuration is ~19 YAML files in
:func:`reflex.utils.paths.config_dir`, one per ``SavingDispatcher`` instance.
Nineteen files is fine for the app and wrong for everything that has to MOVE
the configuration: a USB export the operator carries to a replacement card, a
later opt-in cloud sync, and the snapshots written here all want a single
object they can hash, diff, transport and version. So this module defines that
object once and everything else carries it, rather than each transport
inventing its own directory walk.

THE SHAPE::

    meta:
      schema: 1
      ts: 2026-09-13T19:04:11+00:00   # UTC, ISO-8601, seconds
      machine_id: 5a2f...             # /etc/machine-id, else platform.node()
      hostname: elspi
      app: 1.2.0rc3                   # installed reflex version, or "unknown"
      fw: 1.2.0                       # caller-supplied, may be null
    config_ini:
      device:
        use_case: lathe
        current_mode: '2'
    Axis-0: {...}                     # one key per YAML stem, verbatim,
    Axis-1: {...}                     # in sorted stem order
    CoordBar-0: {...}
    Els-0: {...}

``meta`` is first in the dumped text (``sort_keys=False``) so a human opening
an export or a snapshot sees what machine and what moment it came from before
anything else. ``config_ini`` is ``{section: {key: value}}`` -- the honest
parse of an INI file, which has sections -- and is ``None`` when ``config.ini``
is absent, so the document's shape is the same either way and two documents can
be compared field by field without a missing-key special case.

WHAT THIS MODULE DOES NOT DO. There is no import/apply. :func:`split` is the
inverse of the per-stem part of :func:`build` and exists so a future import can
be written against a tested decomposition, but writing a bundle back onto a
machine is a separate change with its own safety questions (which keys may be
overwritten on a machine that is not the one exported, what happens to a stem
the running app has no dispatcher for, whether the app must be stopped first).
"""
import configparser
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

import yaml
from kivy.logger import Logger

from reflex.utils.paths import config_dir

log = Logger.getChild(__name__)

#: Bump when the document's shape changes incompatibly. A consumer that does
#: not recognise the schema should refuse the document, not guess at it.
SCHEMA = 1

#: Top-level keys of a bundle that are NOT a config file stem.
NON_STEM_KEYS = ("meta", "config_ini")

MACHINE_ID_PATH = "/etc/machine-id"


def ledger_dir() -> Path:
    """The directory the ledger and snapshots share. Under
    :func:`config_dir` deliberately: on the machine that directory is already
    placed outside ``/root`` so an unprivileged operator can read it, and a
    commissioning record nobody can copy off the card is no record at all."""
    return config_dir() / "ledger"


def snapshots_dir() -> Path:
    return ledger_dir() / "snapshots"


def utc_now() -> str:
    """UTC, ISO-8601, second resolution. Shared with the ledger so a ledger
    line and the snapshot it triggered carry the same stamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _file_stamp() -> str:
    """The same instant, filesystem-safe and lexicographically sortable --
    which is what makes "newest snapshot" a plain ``max()`` over names."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def app_version() -> str:
    """The installed ``reflex`` version, or ``"unknown"``.

    Never raises. A snapshot from a machine whose package metadata is missing
    is still worth keeping -- it is the calibration that matters, and refusing
    to record it because the version string is unavailable would lose the thing
    this module exists to keep.
    """
    try:
        import importlib.metadata
        return importlib.metadata.version("reflex")
    except Exception:  # pragma: no cover - metadata is present in dev and on the card
        return "unknown"


def machine_id() -> str:
    """``/etc/machine-id`` if readable, else :func:`platform.node`.

    The machine-id is what distinguishes two cards in the same lathe, and it
    survives a hostname change. It does not exist on the Windows development
    machine, hence the fallback rather than an error.
    """
    try:
        text = Path(MACHINE_ID_PATH).read_text().strip()
        if text:
            return text
    except OSError:
        pass
    return platform.node()


def config_ini_path() -> str | None:
    """Where the app's ``config.ini`` lives, or ``None`` if that is unknowable.

    Resolved through ``reflex.components.appsettings.config_path`` -- the same
    constant the app itself reads -- so the bundle cannot describe a
    ``config.ini`` the running app is not using. Imported lazily: that module
    pulls in ``kivy.uix.settings``, and a bundle built in a test has no reason
    to. A seam rather than a direct import so a test can aim it somewhere
    else; the file is gitignored, so there is nothing to aim at in a checkout.
    """
    try:
        from reflex.components.appsettings import config_path
        return config_path
    except Exception as e:  # pragma: no cover - only if the app package breaks
        log.error(f"commissioning bundle: cannot locate config.ini ({e})")
        return None


def _config_ini() -> dict | None:
    """The app's ``config.ini``, parsed as ``{section: {key: value}}``, or
    ``None`` when it is absent.

    Parsed with the stdlib rather than Kivy's ConfigParser because Kivy's
    subclass writes defaults back into the file on read, and a reader that
    mutates what it reads has no place in a capture path.
    """
    config_path = config_ini_path()
    if config_path is None or not os.path.exists(config_path):
        return None

    parser = configparser.ConfigParser()
    try:
        parser.read(config_path)
    except (OSError, configparser.Error) as e:
        log.error(f"commissioning bundle: cannot parse config.ini ({e})")
        return None
    return {section: dict(parser[section]) for section in parser.sections()}


def build(fw_rev: str | None = None) -> dict:
    """The whole machine configuration as one mapping. See the module docstring.

    :param fw_rev: the firmware revision to stamp into ``meta.fw``. Passed in
        rather than read here because the firmware version is known to the
        board dispatcher, not to a config-directory walk, and a bundle built
        with the board offline is still a valid bundle -- it just says ``fw:
        null`` instead of guessing.
    """
    doc: dict = {
        "meta": {
            "schema": SCHEMA,
            "ts": utc_now(),
            "machine_id": machine_id(),
            "hostname": platform.node(),
            "app": app_version(),
            "fw": fw_rev,
        },
        "config_ini": _config_ini(),
    }

    # Non-recursive glob on purpose: ledger/ and snapshots/ live under
    # config_dir(), and a snapshot that contained the previous snapshots would
    # grow without bound.
    for path in sorted(config_dir().glob("*.yaml"), key=lambda p: p.stem):
        data = None
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f.read())
        except (OSError, yaml.YAMLError) as e:
            log.error(f"commissioning bundle: cannot read {path} ({e})")
            continue
        doc[path.stem] = data
    return doc


def dump(doc: dict, path) -> None:
    """Write ``doc`` as YAML. Creates parent directories.

    ``sort_keys=False`` so ``meta`` stays first; ``default_flow_style=False``
    so the result is block YAML a person can read on a phone screen over SSH.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False)


def split(doc: dict) -> dict[str, dict]:
    """``{stem: mapping}`` for every config file in the bundle.

    The inverse of :func:`build`'s per-file half, and the decomposition a
    future import will apply. It does NOT write anything.
    """
    return {k: v for k, v in doc.items() if k not in NON_STEM_KEYS}


def _without_meta(doc: dict) -> dict:
    """A bundle with ``meta`` removed, which is what "did anything change?"
    means. ``meta.ts`` differs on every build, so comparing whole documents
    would report a change every time and snapshot the card on every startup."""
    return {k: v for k, v in doc.items() if k != "meta"}


def newest_snapshot_path() -> Path | None:
    """The most recent snapshot file, by name. :func:`_file_stamp` is
    zero-padded and UTC, so lexicographic order IS chronological order --
    mtime would not survive a copy off the card."""
    try:
        paths = sorted(snapshots_dir().glob("*.yaml"))
    except OSError:
        return None
    return paths[-1] if paths else None


def _load_snapshot(path: Path) -> dict | None:
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f.read())
    except (OSError, yaml.YAMLError) as e:
        log.error(f"commissioning bundle: cannot read snapshot {path} ({e})")
        return None
    return data if isinstance(data, dict) else None


def snapshot(reason: str) -> Path:
    """Write :func:`build` to ``<config_dir>/ledger/snapshots/<ts>-<reason>.yaml``.

    Unconditional. Second-resolution stamps mean several snapshots for the same
    reason inside one second collapse onto one file, which is the wanted
    behavior when a slider drag fires a run of saves.

    Raises on an unwritable directory. The two callers that must never fail --
    :func:`reflex.utils.commissioning_ledger.record` and
    :func:`snapshot_if_changed` -- swallow it themselves.
    """
    path = snapshots_dir() / f"{_file_stamp()}-{reason}.yaml"
    dump(build(), path)
    return path


def snapshot_if_changed(reason: str = "startup") -> Path | None:
    """Snapshot only if the configuration differs from the newest snapshot.

    THE POINT IS THE WRITES THE LEDGER CANNOT SEE. The ledger hooks
    ``write_settings``, so it records what the APP changed. A value edited by
    hand over SSH, restored from a backup, or written by a tool that bypasses
    the dispatcher leaves no ledger line at all -- and the first evidence would
    otherwise be the next app-driven save, which attributes the whole delta to
    whatever property happened to move.

    Called once at startup. Never raises: a card that cannot write its ledger
    directory must still boot into a working lathe.
    """
    try:
        current = build()
        newest = newest_snapshot_path()
        if newest is not None:
            previous = _load_snapshot(newest)
            if previous is not None and _without_meta(previous) == _without_meta(current):
                return None
        path = snapshots_dir() / f"{_file_stamp()}-{reason}.yaml"
        dump(current, path)
        log.info(f"commissioning snapshot: {path}")
        return path
    except Exception as e:
        log.error(f"commissioning snapshot failed ({reason}): {e}")
        return None
