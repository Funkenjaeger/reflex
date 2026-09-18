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
    Axis-0: {...}                     # one key per YAML stem, verbatim,
    Axis-1: {...}                     # in sorted stem order
    CoordBar-0: {...}
    Device-0: {...}                   # use_case, current_mode
    Els-0: {...}

``meta`` is first in the dumped text (``sort_keys=False``) so a human opening
an export or a snapshot sees what machine and what moment it came from before
anything else.

THE RETIRED ``config_ini`` SECTION. Until 2026-09-16 the document also carried
``config_ini:``, the parsed ``ui/config.ini``, because ``use_case`` and
``current_mode`` still lived there. They now live in the ``Device-0`` stem
(``reflex/dispatchers/device.py``), so :func:`build` no longer emits it. A
bundle exported before that still has it, and :func:`apply` accepts such a
bundle: it logs and ignores the section, except that a
``config_ini.device.use_case`` in a bundle with NO ``Device-0`` stem is
carried into ``Device-0``, so a pre-migration export still restores a lathe.
``meta.schema`` did not change: see :data:`SCHEMA`.

WHAT :func:`apply` IS. It writes the per-stem half of a bundle back onto a
config directory -- the inverse of :func:`split`, which is why it takes the
same document shape.
"""
import os
import platform
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from kivy.logger import Logger

from reflex.utils.commissioning_scope import COMMISSIONING, tier
from reflex.utils.paths import config_dir

log = Logger.getChild(__name__)

#: Bump when the document's shape changes incompatibly. A consumer that does
#: not recognise the schema should refuse the document, not guess at it.
#:
#: NOT bumped when ``config_ini`` was retired for the ``Device-0`` stem
#: (2026-09-16). :func:`apply` refuses only a schema NEWER than its own, so a
#: bump would make every older app refuse every new export outright. The
#: change is not one an older reader misreads: a new bundle simply lacks
#: ``config_ini`` (which older ``split``/``apply`` already skipped) and has one
#: more stem (``Device-0``), which older ``apply`` writes like any other and
#: whose ``use_case`` passes its commissioning gate. An older app ignores that
#: file rather than corrupting anything.
SCHEMA = 1

#: Top-level keys of a bundle that are NOT a config file stem. ``config_ini``
#: is no longer emitted but still recognised, so an older bundle's section is
#: never written out as a ``config_ini.yaml`` stem.
LEGACY_CONFIG_INI_KEY = "config_ini"
NON_STEM_KEYS = ("meta", LEGACY_CONFIG_INI_KEY)

#: The stem that now holds ``use_case`` / ``current_mode``. Mirrors
#: ``reflex.dispatchers.device.DEVICE_STEM`` (pinned equal by a test) without
#: importing the Kivy dispatcher stack into this module.
DEVICE_STEM = "Device-0"

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


def recorded_fw_rev() -> str | None:
    """The firmware revision the flash manifest last recorded, or ``None``.

    What the flashing tools RECORDED (``~/firmware/flashed.json``, see
    :func:`reflex.utils.updater.last_flashed_rev`), not a live read of the
    board: the identity window is only readable through ``modbus-flash.py``,
    which needs the serial port a running UI owns. Never raises -- a desktop,
    or a card nobody flashed from, gets ``None`` and a bundle that says so.
    """
    try:
        from reflex.utils import updater  # lazy: POSIX-only helpers inside
        return updater.last_flashed_rev(
            updater.manifest_path_for(updater.resolve_checkout()))
    except Exception as e:
        log.info(f"commissioning bundle: no recorded firmware revision ({e})")
        return None


#: ``build``'s default: look the revision up with :func:`recorded_fw_rev`.
RECORDED = object()


def build(fw_rev=RECORDED) -> dict:
    """The whole machine configuration as one mapping. See the module docstring.

    :param fw_rev: the firmware revision for ``meta.fw``. By default it is
        looked up with :func:`recorded_fw_rev`, HERE, so every producer of a
        bundle carries it -- USB export, gist sync and the startup snapshot.
        Until 2026-09-17 the default was ``None`` and each caller had to pass
        it; none did, every bundle said ``fw: null``, and fixing one caller
        (the USB export) left the gist sync still writing null. Pass ``None``
        explicitly for a bundle that must not claim a revision.
    """
    if fw_rev is RECORDED:
        fw_rev = recorded_fw_rev()
    doc: dict = {
        "meta": {
            "schema": SCHEMA,
            "ts": utc_now(),
            "machine_id": machine_id(),
            "hostname": platform.node(),
            "app": app_version(),
            "fw": fw_rev,
        },
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


@dataclass
class ApplyReport:
    """The result of :func:`apply`.

    :param ok: ``False`` means the WHOLE document was refused -- nothing was
        written, and ``reason`` says why. ``True`` covers both a real write
        and a ``dry_run`` (which never writes but still validates and lists
        what it would have done).
    :param reason: set only when ``ok`` is ``False``.
    :param written: stems actually written (or, under ``dry_run``, that would
        have been).
    :param skipped: ``(stem, reason)`` for a stem whose own write failed --
        writing the OTHERS still went ahead; see :func:`apply`'s docstring.
    """
    ok: bool
    reason: str | None = None
    written: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _atomic_dump(data: dict, path: Path) -> None:
    """Write ``data`` to ``path`` as YAML, never leaving ``path`` half-written.

    Writes to a temp file IN THE SAME DIRECTORY (so the final ``os.replace``
    is a same-filesystem rename, which POSIX guarantees is atomic), fsyncs it,
    then renames it onto ``path``. A failure at any point before the rename
    -- a full disk, a killed process -- leaves the temp file orphaned (removed
    in the ``except``) and ``path`` exactly as it was; a reader can never
    observe a partially-written ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def apply(doc: dict, config_dir, *, dry_run: bool = False) -> ApplyReport:
    """Write a bundle's per-stem sections back onto ``config_dir``.

    The inverse of :func:`split`'s decomposition: every non-``meta``,
    non-``config_ini`` top-level key of ``doc`` becomes ``<stem>.yaml`` under
    ``config_dir``, written verbatim (see :func:`_atomic_dump`) and byte-for-
    byte round-trippable -- this does NOT filter out operational keys like
    ``offsets`` from a section it decides to write, because a real export
    always carries them and dropping them would silently lose the machine's
    current job state, not just its identity.

    Two refusals happen BEFORE anything is written, and both leave every file
    under ``config_dir`` untouched:

    * ``meta.schema`` newer than this app's :data:`SCHEMA` -- a document whose
      shape this code was never taught to read. Guessing at an unknown shape
      is how a newer bundle silently corrupts an older machine's config; a
      named refusal is the alternative.
    * A section that carries NO commissioning-tier data at all (every one of
      its keys classifies as ``operational`` or ``ignored`` per
      :mod:`reflex.utils.commissioning_scope`). A section like that is not
      what a commissioning import exists to restore -- see the scope module's
      own docstring for why the tier split exists in the first place -- and
      accepting it as machine identity would be importing job-state noise (or
      a hand-edited/corrupted section) under that name. An ordinary export's
      sections always mix in identity keys (``axis_name``, ``backlash``, ...)
      alongside any operational ones, so this does not fire on a real bundle;
      it fires on one that is not one.

    Past that gate, each stem is written independently and a per-file failure
    (e.g. an unwritable destination) is caught, logged, and recorded in
    ``report.skipped`` -- one bad file does not stop the others, and
    :func:`_atomic_dump`'s temp+rename means the failed file is left exactly
    as it was found (nothing "half written").

    A legacy ``config_ini`` section is logged and ignored, with one
    exception: if it has ``device.use_case`` and the bundle has no
    :data:`DEVICE_STEM`, a ``{use_case: ...}`` section is added under that stem
    (see the module docstring). Only ``use_case`` is carried; ``current_mode``
    is job state and the app falls back to a mode valid for the use case.

    :param dry_run: validate and report, but write nothing. ``report.written``
        lists what WOULD be written.
    """
    config_dir = Path(config_dir)
    meta = doc.get("meta") or {}
    schema = meta.get("schema")
    if not isinstance(schema, int) or schema > SCHEMA:
        return ApplyReport(ok=False, reason=(
            f"bundle schema {schema!r} is newer than this app understands "
            f"(schema {SCHEMA}); refusing rather than guessing at its shape"))

    sections = split(doc)
    _carry_legacy_use_case(doc, sections)
    for stem, data in sections.items():
        if not isinstance(data, dict):
            return ApplyReport(ok=False, reason=(
                f"section {stem!r} is not a mapping; refusing"))
        if data and all(tier(stem, key, data) != COMMISSIONING for key in data):
            return ApplyReport(ok=False, reason=(
                f"section {stem!r} carries no commissioning-tier data "
                f"(commissioning_scope.py); refusing"))

    if dry_run:
        return ApplyReport(ok=True, written=sorted(sections))

    written: list[str] = []
    skipped: list[tuple[str, str]] = []
    for stem in sorted(sections):
        path = config_dir / f"{stem}.yaml"
        try:
            _atomic_dump(sections[stem], path)
            written.append(stem)
        except OSError as e:
            log.error(f"commissioning apply: cannot write {path} ({e})")
            skipped.append((stem, str(e)))
    return ApplyReport(ok=True, written=written, skipped=skipped)


def _carry_legacy_use_case(doc: dict, sections: dict) -> None:
    """Fold a pre-2026-09-16 bundle's ``config_ini.device.use_case`` into
    ``sections`` as :data:`DEVICE_STEM`, if that stem is absent. Mutates
    ``sections``; never touches ``doc``."""
    if LEGACY_CONFIG_INI_KEY not in doc:
        return
    legacy = doc.get(LEGACY_CONFIG_INI_KEY)
    device = legacy.get("device") if isinstance(legacy, dict) else None
    use_case = device.get("use_case") if isinstance(device, dict) else None
    if use_case and DEVICE_STEM not in sections:
        sections[DEVICE_STEM] = {"use_case": str(use_case)}
        log.info(f"commissioning apply: legacy config_ini section ignored, "
                 f"except device.use_case={use_case!r} -> {DEVICE_STEM}")
    else:
        log.info("commissioning apply: legacy config_ini section ignored")


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
