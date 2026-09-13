"""Append-only record of every commissioning-tier config change the app makes.

THE DEFECT THIS CLOSES. On 2026-09-07 20:01 the machine's commissioning values
moved and nothing captured them; the only copy was a hand transcription made
six days later. ``SavingDispatcher`` rewrites a whole YAML file on every bound
property change and keeps no history, so the card holds exactly one state --
the current one -- and a card death loses the latest recalibration with no way
to tell what it had been.

So: one JSON object per line, appended, never rewritten::

    {"ts": "2026-09-13T19:04:11+00:00", "file": "Axis-1", "key": "backlash",
     "old": 0.04, "new": 0.062, "trigger": "backlash", "app": "1.2.0rc3"}

``jsonl`` rather than YAML because appending is the whole point: a line is
complete the moment it is written, an interrupted write costs one line instead
of the file, and ``tail`` is a working reader. One line per CHANGED KEY, not
per save -- a save that moves one value writes one line, so the file reads as a
list of what changed rather than a list of snapshots to diff by eye.

WHAT IS NOT IN HERE. Operational keys (work offsets, a spindle axis's sync
ratio) and ignored keys (``id_override``, layout geometry) are never written --
see :mod:`reflex.utils.commissioning_scope`, which is the single place that
decides. Without that filter the ledger would be ~99% DRO zeroing and useless
for the question it exists to answer.

A ledger failure never reaches the caller. :func:`record` swallows everything
and logs it, because the alternative is a config save that fails -- or an app
that crashes mid-calibration -- because a record-keeping directory was not
writable. The record is important; it is not more important than the lathe.
"""
import json
from pathlib import Path

from kivy.logger import Logger
from kivy.properties import ObservableList

from reflex.utils import commissioning_bundle
from reflex.utils.commissioning_scope import COMMISSIONING, tier

log = Logger.getChild(__name__)

LEDGER_NAME = "commissioning.jsonl"

#: Sentinel for "this key was absent from the previous file", which is
#: distinct from "it was present and held None".
_ABSENT = object()


def ledger_path() -> Path:
    return commissioning_bundle.ledger_dir() / LEDGER_NAME


def _normalize(value):
    """Make a value comparable and JSON-serializable.

    Kivy hands out ``ObservableList`` (and observable dicts) rather than plain
    containers, and two ObservableLists with equal contents do compare equal --
    but the values that come back from YAML are plain lists, so every
    comparison here is observable-vs-plain. Recursive because
    ``transform_config`` is a nested mapping.
    """
    if isinstance(value, ObservableList) or isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    return value


def _changes(stem: str, old_data: dict | None, new_data: dict) -> list[tuple]:
    """``(key, old, new)`` for every commissioning key that moved.

    A file that did not exist before (``old_data is None``) yields every
    commissioning key with ``old`` of ``None``: the first write of a config
    file IS the commissioning event for that dispatcher, and a machine
    provisioned from defaults would otherwise have an empty ledger until
    somebody changed something.

    Keys that DISAPPEARED from the file are not reported. That happens when a
    property is removed from a dispatcher class -- a software change, not a
    machine change -- and reporting it would put a line in the operator's
    calibration history for a refactor.
    """
    changes = []
    for key in sorted(new_data):
        if tier(stem, key, new_data) != COMMISSIONING:
            continue
        new = _normalize(new_data[key])
        if old_data is None:
            changes.append((key, None, new))
            continue
        old_raw = old_data.get(key, _ABSENT)
        old = None if old_raw is _ABSENT else _normalize(old_raw)
        if old != new:
            changes.append((key, old, new))
    return changes


def record(file_path, old_data: dict | None, new_data: dict, trigger: str) -> int:
    """Append a line per changed commissioning key. Returns the line count.

    :param file_path: the YAML file just written; its stem names the record.
    :param old_data: that file's contents BEFORE the write, or ``None`` when
        the file did not exist.
    :param new_data: the mapping just written.
    :param trigger: the property name that caused the save, as
        ``write_settings`` already receives it. May be ``""`` -- an explicit
        ``save_settings()`` call and the first-creation save carry no
        triggering property, and a blank is more honest than inventing one.

    Never raises. Every failure is logged and swallowed.
    """
    try:
        stem = Path(file_path).stem
        changes = _changes(stem, old_data, new_data or {})
        if not changes:
            return 0

        ts = commissioning_bundle.utc_now()
        app = commissioning_bundle.app_version()
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            for key, old, new in changes:
                f.write(json.dumps({
                    "ts": ts,
                    "file": stem,
                    "key": key,
                    "old": old,
                    "new": new,
                    "trigger": trigger or "",
                    "app": app,
                }, default=str) + "\n")
        log.info(f"commissioning ledger: {len(changes)} change(s) in {stem}")

        # The ledger says WHAT moved; the snapshot is the recoverable copy of
        # the whole machine at that moment. Only on a commissioning change --
        # snapshotting every save would mean a file per DRO zeroing.
        commissioning_bundle.snapshot("change")
        return len(changes)
    except Exception as e:
        log.error(f"commissioning ledger failed for {file_path}: {e}")
        return 0
