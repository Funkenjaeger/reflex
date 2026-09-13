#!/usr/bin/env python3
"""Tests for the flash manifest: scripts/flash_manifest.py, and the call in
scripts/modbus-flash.py that writes it after a confirmed over-the-wire flash.

WHAT MUST HOLD, each pinned below:
  * a record is written on a CONFIRMED flash -- after the board reports the
    image's rev -- and never on a dry run, never when the board did not come
    back, never with --no-manifest;
  * the record carries the image header's rev in the form ot-state compares
    (seven hex digits, dirty as its own field), and the md5 of the file;
  * a record that cannot be written does not turn a good flash into a failed
    one;
  * written as ROOT into a user's home, the file ends up owned by that home's
    user, so the user's own flash.sh can still append to it.

No board, no serial port: pyserial is stubbed, and modbus-flash.py's board
conversation is replaced at the four functions flash() calls.

    python3 scripts/lib/flash-manifest-test.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.modules.setdefault("serial", types.ModuleType("serial"))   # pyserial stub

import flash_manifest  # noqa: E402
import reflex_image as ri  # noqa: E402

_spec = importlib.util.spec_from_file_location("modbus_flash", SCRIPTS / "modbus-flash.py")
mf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mf)

REV = 0x8483D77


def slotted_image(rev=REV, dirty=False) -> bytes:
    buf = bytearray(0x400)
    struct.pack_into(ri._HEADER_FMT, buf, ri.IMAGE_HEADER_OFFSET, ri.IMAGE_MAGIC,
                     ri.IMAGE_HEADER_VERSION, ri.IMAGE_FLAG_DIRTY if dirty else 0,
                     0, 0, rev, 0, 0, 0)
    data = ri.patch(bytes(buf))
    ri.validate(data)
    return data


class FakeIdent:
    def __init__(self, stage, rev=REV, protocol=10):
        self.stage, self.build_rev, self.app_protocol = stage, rev, protocol
        self.dirty = False
        self.rev_str = f"{rev:07x}"

    def __str__(self):
        return f"stage={self.stage} rev={self.rev_str}"


class FakeBootloader:
    def __init__(self, bus, dry_run):
        self.retries = self.recovered = 0

    def describe(self):
        return "fake"

    def erase(self): pass
    def write_chunk(self, *a): pass
    def verify(self, *a): pass
    def apply(self): pass
    def jump(self): pass


class FakeBus:
    retries = 0


class ModbusFlashRecords(unittest.TestCase):
    """flash() writes the manifest on the verdict, and only on it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.image = self.tmp / "reflex-app-1.2.0.bin"
        self.image.write_bytes(slotted_image())
        self.manifest = self.tmp / "home" / "firmware" / "flashed.json"
        self.saved = {n: getattr(mf, n) for n in
                      ("read_identity", "enter_bootloader", "Bootloader", "wait_for_stage")}
        mf.read_identity = lambda bus: FakeIdent(mf.ID_STAGE_APP, rev=0x1111111)
        mf.enter_bootloader = lambda bus, ident, dry: FakeIdent(mf.ID_STAGE_BOOTLOADER)
        mf.Bootloader = FakeBootloader
        mf.wait_for_stage = lambda bus, stage, timeout, rev=None: FakeIdent(stage, rev=rev)

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(mf, n, v)

    def flash(self, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = mf.flash(FakeBus(), str(self.image), kw.pop("dry_run", False), **kw)
        return rc, out.getvalue()

    def records(self):
        if not self.manifest.exists():
            return []
        return [json.loads(line) for line in self.manifest.read_text().splitlines()]

    def test_confirmed_flash_is_recorded(self):
        rc, out = self.flash(manifest=self.manifest, variant="release", tag="v1.2.0")
        self.assertEqual(rc, 0)
        self.assertIn("VERDICT: OK", out)
        [rec] = self.records()
        self.assertEqual(rec["rev"], "8483d77")          # the form ot-state compares
        self.assertIs(rec["dirty"], False)
        self.assertEqual(rec["via"], "modbus")
        self.assertEqual(rec["variant"], "release")
        self.assertIsNone(rec["probe"])                   # release implies no probe
        self.assertEqual(rec["tag"], "v1.2.0")
        self.assertEqual(rec["protocol"], 10)
        self.assertEqual(rec["md5"], hashlib.md5(self.image.read_bytes()).hexdigest())
        self.assertEqual(list(rec)[:6], ["utc", "variant", "probe", "rev", "dirty", "md5"])

    def test_record_follows_the_verdict_not_precedes_it(self):
        _, out = self.flash(manifest=self.manifest)
        self.assertLess(out.index("VERDICT: OK"), out.index("recorded in"))

    def test_unknown_variant_says_so_rather_than_claiming_release(self):
        self.flash(manifest=self.manifest)
        [rec] = self.records()
        self.assertEqual(rec["variant"], "unknown")
        self.assertEqual(rec["probe"], "unknown")

    def test_dirty_image_records_dirty_as_its_own_field(self):
        self.image.write_bytes(slotted_image(dirty=True))
        self.flash(manifest=self.manifest)
        [rec] = self.records()
        self.assertEqual(rec["rev"], "8483d77")
        self.assertIs(rec["dirty"], True)

    def test_dry_run_records_nothing(self):
        rc, _ = self.flash(manifest=self.manifest, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.records(), [])

    def test_board_that_never_came_back_records_nothing(self):
        def timeout(bus, stage, timeout, rev=None):
            raise SystemExit("timed out waiting for the application")
        mf.wait_for_stage = timeout
        with self.assertRaises(SystemExit):
            self.flash(manifest=self.manifest)
        self.assertEqual(self.records(), [])

    def test_no_manifest_records_nothing(self):
        self.flash(manifest=None)
        self.assertEqual(self.records(), [])

    def test_an_unwritable_manifest_does_not_fail_a_good_flash(self):
        blocker = self.tmp / "not-a-dir"
        blocker.write_text("a file where the directory should be")
        rc, out = self.flash(manifest=blocker / "flashed.json")
        self.assertEqual(rc, 0)
        self.assertIn("WARNING: the flash succeeded but was NOT recorded", out)

    def test_cli_default_is_the_invoking_users_home(self):
        # And --manifest / --no-manifest reach flash(). Parsed, not run.
        seen = {}
        mf.flash = (lambda bus, image, dry, **kw: seen.update(kw) or 0)
        mf.Rtu = lambda *a: types.SimpleNamespace(close=lambda: None)
        try:
            mf.main(["x", str(self.image)])
            self.assertEqual(seen["manifest"], "~/firmware/flashed.json")
            mf.main(["x", str(self.image), "--no-manifest"])
            self.assertIsNone(seen["manifest"])
            mf.main(["x", str(self.image), "--manifest", "/home/default/firmware/flashed.json",
                     "--record-variant", "release", "--record-tag", "v9"])
            self.assertEqual(seen, {"manifest": "/home/default/firmware/flashed.json",
                                    "variant": "release", "tag": "v9"})
        finally:
            _spec.loader.exec_module(mf)   # restore the real flash/Rtu


class AppendOwnership(unittest.TestCase):
    """As root, the file goes to the home's owner; otherwise nobody chowns."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.chowns = []

    def fake_chown(self, p, uid, gid):
        self.chowns.append((Path(p), uid, gid))

    def test_jsonl_appends(self):
        path = self.home / "firmware" / "flashed.json"
        for rev in ("aaaaaaa", "bbbbbbb"):
            flash_manifest.append(path, flash_manifest.record(
                variant="unknown", rev=rev, dirty=False, md5="0", via="modbus"))
        lines = path.read_text().splitlines()
        self.assertEqual([json.loads(l)["rev"] for l in lines], ["aaaaaaa", "bbbbbbb"])

    def test_non_root_never_chowns(self):
        flash_manifest.append(self.home / "firmware" / "flashed.json", {"rev": "x"},
                              euid=1000, chown=self.fake_chown)
        self.assertEqual(self.chowns, [])

    # Ownership as it would be on elspi: the home and an existing ~/firmware
    # belong to `default` (1000); anything root just created belongs to root.
    # Faked, because a non-root test can only create directories it owns, and
    # then every owner is the same answer.
    def fake_stat(self, created_by_root=()):
        def stat(p):
            uid = 0 if Path(p) in created_by_root else 1000
            return types.SimpleNamespace(st_uid=uid, st_gid=uid)
        return stat

    def test_root_into_existing_dir_gives_file_to_the_dirs_owner(self):
        d = self.home / "firmware"
        d.mkdir()
        flash_manifest.append(d / "flashed.json", {"rev": "x"}, euid=0,
                              chown=self.fake_chown, stat=self.fake_stat())
        self.assertEqual(self.chowns, [(d / "flashed.json", 1000, 1000)])

    def test_root_creating_the_dir_gives_both_to_the_homes_owner(self):
        """The directory root just made is ROOT's; the owner to hand it to is
        the home's. Taking the new directory's owner would chown everything to
        root -- a no-op that looks like a chown."""
        d = self.home / "firmware"
        flash_manifest.append(d / "flashed.json", {"rev": "x"}, euid=0,
                              chown=self.fake_chown, stat=self.fake_stat(created_by_root={d}))
        self.assertEqual(self.chowns, [(d, 1000, 1000), (d / "flashed.json", 1000, 1000)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
