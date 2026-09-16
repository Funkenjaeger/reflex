"""``usb.py`` -- finding removable media via /proc/mounts, without shelling
out, and reading/writing a commissioning bundle there.

See the module docstring for why /proc/mounts and why mount-point prefix is
what "removable" means here.
"""
import os
from pathlib import Path

import pytest

from reflex.utils import usb


def _write_proc_mounts(tmp_path, lines: list[str]) -> str:
    path = tmp_path / "fake-proc-mounts"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


# ── list_removable: mount-point-prefix classification ─────────────────────

def test_a_removable_and_a_fixed_mount_only_the_removable_is_offered(tmp_path):
    proc_mounts = _write_proc_mounts(tmp_path, [
        "/dev/sda1 / ext4 rw,relatime 0 0",
        "/dev/sdb1 /media/operator/STICK vfat rw,relatime,uid=1000 0 0",
    ])

    devices = usb.list_removable(proc_mounts)

    assert len(devices) == 1
    assert devices[0].device == "/dev/sdb1"
    assert devices[0].mountpoint == Path("/media/operator/STICK")
    assert devices[0].fstype == "vfat"


def test_run_media_also_counts_as_removable(tmp_path):
    proc_mounts = _write_proc_mounts(tmp_path, [
        "/dev/sda1 / ext4 rw,relatime 0 0",
        "/dev/sdc1 /run/media/pi/STICK vfat rw 0 0",
    ])
    devices = usb.list_removable(proc_mounts)
    assert [d.device for d in devices] == ["/dev/sdc1"]


def test_no_mounts_at_all_yields_nothing(tmp_path):
    proc_mounts = _write_proc_mounts(tmp_path, [
        "/dev/sda1 / ext4 rw,relatime 0 0",
        "/dev/sda2 /boot vfat rw 0 0",
    ])
    assert usb.list_removable(proc_mounts) == []


def test_unreadable_proc_mounts_yields_nothing_not_an_exception(tmp_path):
    missing = str(tmp_path / "does-not-exist")
    assert usb.list_removable(missing) == []


def test_a_mountpoint_containing_a_space_is_unescaped(tmp_path):
    proc_mounts = _write_proc_mounts(tmp_path, [
        r"/dev/sdb1 /media/operator/MY\040STICK vfat rw 0 0",
    ])
    devices = usb.list_removable(proc_mounts)
    assert str(devices[0].mountpoint) == "/media/operator/MY STICK"


# ── find_bundles ────────────────────────────────────────────────────────

def test_find_bundles_only_matches_the_naming_convention(tmp_path):
    stick = tmp_path / "stick"
    stick.mkdir()
    (stick / "reflex-commissioning-elspi-20260913T190411Z.yaml").write_text("meta: {}\n")
    (stick / "not-a-bundle.yaml").write_text("meta: {}\n")
    (stick / "reflex-commissioning-elspi-20260101T000000Z.yaml").write_text("meta: {}\n")
    device = usb.RemovableDevice(device="/dev/sdb1", mountpoint=stick, fstype="vfat")

    bundles = usb.find_bundles(devices=[device])

    assert [p.name for p in bundles] == [
        "reflex-commissioning-elspi-20260101T000000Z.yaml",
        "reflex-commissioning-elspi-20260913T190411Z.yaml",
    ], "sorted so the newest (highest stamp) bundle is last"


def test_find_bundles_with_no_removable_media_is_empty(tmp_path):
    missing = str(tmp_path / "does-not-exist")
    assert usb.find_bundles(proc_mounts=missing) == []


def test_find_bundles_across_two_sticks(tmp_path):
    stick_a = tmp_path / "a"
    stick_a.mkdir()
    (stick_a / "reflex-commissioning-elspi-20260101T000000Z.yaml").write_text("x: 1\n")
    stick_b = tmp_path / "b"
    stick_b.mkdir()
    (stick_b / "reflex-commissioning-elspi-20260913T190411Z.yaml").write_text("x: 2\n")
    devices = [
        usb.RemovableDevice(device="/dev/sdb1", mountpoint=stick_a, fstype="vfat"),
        usb.RemovableDevice(device="/dev/sdc1", mountpoint=stick_b, fstype="vfat"),
    ]

    bundles = usb.find_bundles(devices=devices)

    assert [p.name for p in bundles] == [
        "reflex-commissioning-elspi-20260101T000000Z.yaml",
        "reflex-commissioning-elspi-20260913T190411Z.yaml",
    ]


# ── (5) RED: no mounts at all -> the refusal message ──────────────────────

def test_export_with_no_removable_media_raises_no_removable_media(tmp_path):
    missing = str(tmp_path / "does-not-exist")
    with pytest.raises(usb.NoRemovableMedia):
        usb.export_bundle("meta: {}\n", "reflex-commissioning-x-1.yaml",
                           proc_mounts=missing)


# ── export_bundle ──────────────────────────────────────────────────────

def test_export_bundle_writes_to_the_first_removable_device(tmp_path, monkeypatch):
    monkeypatch.setattr(usb, "REMOVABLE_ROOTS", (str(tmp_path),))
    stick = tmp_path / "stick"
    stick.mkdir()
    proc_mounts = _write_proc_mounts(tmp_path, [
        "/dev/sda1 / ext4 rw 0 0",
        f"/dev/sdb1 {stick} vfat rw 0 0",
    ])

    written = usb.export_bundle("meta:\n  schema: 1\n",
                                 "reflex-commissioning-elspi-x.yaml",
                                 proc_mounts=proc_mounts)

    assert written == stick / "reflex-commissioning-elspi-x.yaml"
    assert written.read_text() == "meta:\n  schema: 1\n"


def test_export_bundle_prefers_the_first_device_in_mount_order(tmp_path, monkeypatch):
    monkeypatch.setattr(usb, "REMOVABLE_ROOTS", (str(tmp_path),))
    stick_a = tmp_path / "a"
    stick_a.mkdir()
    stick_b = tmp_path / "b"
    stick_b.mkdir()
    proc_mounts = _write_proc_mounts(tmp_path, [
        f"/dev/sdb1 {stick_a} vfat rw 0 0",
        f"/dev/sdc1 {stick_b} vfat rw 0 0",
    ])

    written = usb.export_bundle("x: 1\n", "reflex-commissioning-x.yaml",
                                 proc_mounts=proc_mounts)

    assert written.parent == stick_a


def test_export_bundle_fsyncs(tmp_path, monkeypatch):
    """The whole reason export uses os.fdopen/os.fsync instead of a plain
    ``Path.write_text``: an operator pulling the stick the instant success is
    reported must not race the page cache."""
    monkeypatch.setattr(usb, "REMOVABLE_ROOTS", (str(tmp_path),))
    stick = tmp_path / "stick"
    stick.mkdir()
    proc_mounts = _write_proc_mounts(tmp_path, [f"/dev/sdb1 {stick} vfat rw 0 0"])

    calls = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real_fsync(fd))[1])

    usb.export_bundle("x: 1\n", "reflex-commissioning-x.yaml", proc_mounts=proc_mounts)

    assert len(calls) == 1


# ── bundle_filename ────────────────────────────────────────────────────

def test_bundle_filename_matches_the_glob_it_is_found_by():
    import fnmatch
    name = usb.bundle_filename("elspi", "20260913T190411Z")
    assert name == "reflex-commissioning-elspi-20260913T190411Z.yaml"
    assert fnmatch.fnmatch(name, usb.BUNDLE_GLOB)
