"""The flash manifest: one JSON line per flash, in ~/firmware/flashed.json.

Written by every path that puts firmware on the controller -- scripts/flash.sh
and scripts/provision.sh (in shell, printf'd, the same shape) and
scripts/modbus-flash.py (through this module), which is also what the in-app
updater runs. Read by the estate's ot-state on elspi, which takes the LAST line
as "what firmware this lathe runs" and compares its `rev`.

WHY THIS FILE EXISTS. From 2026-09-07 the paths actually in use -- over-the-wire
flashing and the touchscreen updater -- wrote nothing, so the only record went
stale and ot-state reported UNKNOWN every night. A record that only the least
used path writes is a record that answers the wrong question.

THE FILE IS JSONL, not one document. json.load() on it raises "Extra data";
read it line by line.

WHOSE HOME. The manifest belongs to the machine's login user -- `default` on
elspi, the owner of the checkout -- because that is where ot-state reads it
(/home/default/firmware/flashed.json) and where flash.sh, run by that user,
appends. The in-app updater runs inside reflex-ui, which runs as ROOT, so a
bare `~` there is /root. The caller must therefore say WHERE (the updater
passes the checkout owner's home), and when root writes into a user's home,
append() hands the file -- and a directory it had to create -- back to that
home's owner, so the next flash.sh run by the user can still append to it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT_PATH = "~/firmware/flashed.json"


def record(*, variant: str, rev: str, dirty: bool, md5: str, via: str,
           probe: str | None = None, utc: str | None = None, **extra) -> dict:
    """One manifest record, keys in flash.sh's order, extras after.

    `probe` is null for a release build -- flash.sh's convention, an absent
    probe stated rather than a missing key -- and a caller that cannot know
    whether its image carries one says "unknown" rather than null.
    """
    rec = {
        "utc": utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "variant": variant,
        "probe": probe,
        "rev": rev,
        "dirty": bool(dirty),
        "md5": md5,
        "via": via,
    }
    rec.update(extra)
    return rec


def append(path, rec: dict, *, euid: int | None = None, chown=os.chown,
           stat=os.stat) -> Path:
    """Append `rec` as one line. Returns the resolved path.

    `euid`, `chown` and `stat` are injectable so the root branch is testable
    without being root -- and `stat` has to be, because a non-root test can
    only create directories it owns itself, which makes "the new directory's
    owner" and "the home's owner" the same answer and the choice between them
    untestable.
    """
    path = Path(path).expanduser()
    d = path.parent
    created_dir = not d.exists()
    d.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    if (os.geteuid() if euid is None else euid) == 0:
        # The directory's owner is the user this manifest belongs to. When the
        # directory was just created, by root, its owner is root -- so go one
        # level up, to the home it sits in.
        owner = stat(d.parent if created_dir else d)
        if created_dir:
            chown(d, owner.st_uid, owner.st_gid)
        chown(path, owner.st_uid, owner.st_gid)
    return path
