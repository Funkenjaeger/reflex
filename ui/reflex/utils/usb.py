"""Find mounted removable media and read/write a commissioning bundle there.

WHY /proc/mounts, NOT lsblk/udisksctl/pyudev. The operator's card runs the UI
as an unprivileged service user with no guarantee a display manager's
automounter, udisks, or even ``lsblk`` is installed on a minimal image.
``/proc/mounts`` is a kernel-maintained file every process can read for free:
no subprocess, no polkit prompt, no dependency on $PATH. "Without shelling
out" is not a style preference here -- ``NetworkScreen`` already shows what an
unprivileged-user permission refusal from a shelled-out tool costs (see its
module docstring); this module has no reason to risk the same failure mode
for something a plain file read answers.

WHAT COUNTS AS "REMOVABLE". ``/proc/mounts`` has no removable/fixed column --
that lives in ``/sys/block/*/removable``, a second file per device this module
does not read. Instead it uses the convention actually in force on the elspi
image: anything auto-mounted for an inserted USB stick lands under
``/run/media`` or ``/media`` (the udisks/session automounter's target roots),
while the machine's own filesystems (``/``, ``/boot``, ``/var/lib/reflex-config``,
...) never do. So a mount point under one of those two roots IS "a removable
stick" for every function below -- a path-prefix rule, not a hardware query,
and it works the same whether the process is root or the reflex service user.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from kivy.logger import Logger

log = Logger.getChild(__name__)

PROC_MOUNTS = "/proc/mounts"

#: Mount-point roots the elspi desktop auto-mounts removable media under. A
#: mount whose target does not fall under one of these is a fixed filesystem.
REMOVABLE_ROOTS = ("/run/media", "/media")

#: Matches the export filename this module writes (see :func:`bundle_filename`)
#: and what :func:`find_bundles` looks for on a stick.
BUNDLE_GLOB = "reflex-commissioning-*.yaml"


class NoRemovableMedia(Exception):
    """Raised by :func:`export_bundle` when nothing is mounted under a
    removable root. The Setup screen turns this into the "no stick found"
    refusal message rather than letting it become a traceback on the
    operator's screen."""


@dataclass(frozen=True)
class RemovableDevice:
    device: str
    mountpoint: Path
    fstype: str


def _unescape_mount_field(field: str) -> str:
    """Undo the octal escaping the kernel applies to ``/proc/mounts`` fields
    that contain whitespace or a backslash (see ``proc(5)``)."""
    return (field.replace("\\040", " ")
                 .replace("\\011", "\t")
                 .replace("\\012", "\n")
                 .replace("\\134", "\\"))


def _iter_mounts(proc_mounts: str = PROC_MOUNTS):
    """Yield ``(device, mountpoint, fstype)`` for every line of ``proc_mounts``.

    Never raises: a card that cannot read ``/proc/mounts`` (should not happen,
    but this is reached from a button press, not a boot path) reports "no
    media" rather than taking the Setup screen down with it.
    """
    try:
        with open(proc_mounts, "r") as f:
            lines = f.readlines()
    except OSError as e:
        log.warning(f"usb: cannot read {proc_mounts} ({e})")
        return
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mountpoint, fstype = parts[0], parts[1], parts[2]
        yield device, _unescape_mount_field(mountpoint), fstype


def _is_removable(mountpoint: str) -> bool:
    return any(mountpoint == root or mountpoint.startswith(root + "/")
               for root in REMOVABLE_ROOTS)


def list_removable(proc_mounts: str = PROC_MOUNTS) -> list[RemovableDevice]:
    """Every currently-mounted removable device, by the mount-point
    convention documented at the top of this module.

    :param proc_mounts: override for tests; production callers use the
        default.
    """
    return [RemovableDevice(device=dev, mountpoint=Path(mp), fstype=fstype)
            for dev, mp, fstype in _iter_mounts(proc_mounts)
            if _is_removable(mp)]


def find_bundles(devices: "list[RemovableDevice] | None" = None,
                  proc_mounts: str = PROC_MOUNTS) -> list[Path]:
    """Every ``reflex-commissioning-*.yaml`` on any removable device, sorted.

    Sorted rather than in mount order: :func:`bundle_filename` stamps names so
    that lexicographic order is chronological (the same convention
    :func:`newest_snapshot_path` relies on), so ``find_bundles()[-1]`` is the
    newest bundle across every stick that happens to be plugged in.

    :param devices: override for a test; production callers omit it and get
        :func:`list_removable`.
    """
    if devices is None:
        devices = list_removable(proc_mounts)
    bundles = []
    for dev in devices:
        try:
            bundles.extend(dev.mountpoint.glob(BUNDLE_GLOB))
        except OSError as e:
            log.warning(f"usb: cannot list {dev.mountpoint} ({e})")
    return sorted(bundles)


def bundle_filename(hostname: str, stamp: str) -> str:
    """The conventional export name, matching :data:`BUNDLE_GLOB`.

    ``stamp`` is expected to be filesystem-safe and sortable -- the same shape
    ``commissioning_bundle._file_stamp()`` produces -- so exporting twice in a
    row adds a second file instead of overwriting the first.
    """
    return f"reflex-commissioning-{hostname}-{stamp}.yaml"


def export_bundle(text: str, filename: str, proc_mounts: str = PROC_MOUNTS) -> Path:
    """Write ``text`` to ``filename`` on the first removable device found.

    fsync'd before this returns, so an operator who pulls the stick the
    instant the Setup screen reports success is not racing a page-cache
    flush.

    :raises NoRemovableMedia: nothing is mounted under a removable root.
    """
    devices = list_removable(proc_mounts)
    if not devices:
        raise NoRemovableMedia("no removable media is mounted")
    target = devices[0].mountpoint / filename
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    return target
