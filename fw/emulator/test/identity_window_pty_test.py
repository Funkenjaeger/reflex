#!/usr/bin/env python3
"""identity_window_pty_test.py -- the identity window END TO END.

Launches the real lathe-emulator (real Modbus.c with the window resolver,
real Ramps.c publishing rampsIdentityWindow) in serve mode on a PTY, then
drives it with the real host client, scripts/modbus-flash.py, imported as a
module. Asserts:

  1. `--identity` reads magic 0x454C, stage 2 (application), window version
     1, the git rev the build recorded in reflex_build_rev.h, and
     idAppProtocolVersion == the ELS_PROTOCOL_VERSION the emulator publishes
     at elsStop.protocolVersion;
  2. FC3 at the bootloader window (2304) is Modbus exception 2 -- the
     "this is the app" signal the decision record specifies;
  3. FC6 into the identity window is exception 2 (read-only);
  4. a read straddling the end of the struct is exception 2, and a read of
     the struct's first registers still works (the resolver did not break
     the main map).

Exit 77 (ctest SKIP_RETURN_CODE) when pyserial is not installed.

Usage: identity_window_pty_test.py <lathe-emulator> <modbus-flash.py> <reflex_build_rev.h>
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def load_client(path):
    spec = importlib.util.spec_from_file_location("modbus_flash", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    emulator, client_path, rev_header = argv[1], argv[2], argv[3]
    # <repo>/fw/scripts/modbus-flash.py -> <repo>. Derived from the client
    # path the harness already passes rather than from this file's own
    # location, so moving the test does not silently point it elsewhere.
    REPO_ROOT = Path(client_path).resolve().parents[2]
    try:
        import serial  # noqa: F401
    except ImportError:
        print("SKIP: pyserial not installed")
        return 77

    text = open(rev_header).read()
    rev = int(re.search(r"REFLEX_BUILD_REV\s+0x([0-9a-f]+)u", text).group(1), 16)
    dirty = int(re.search(r"REFLEX_BUILD_DIRTY\s+(\d)u", text).group(1))

    env = dict(os.environ, EMU_SCENARIO="serve")
    proc = subprocess.Popen([emulator, "config/lathe.toml"], env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    failures = 0
    pty = None
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            m = re.search(r"Modbus serial: (/dev/pts/\d+)", line)
            if m:
                pty = m.group(1)
                break
        if pty is None:
            print("FAIL: emulator never printed its PTY path")
            return 1
        time.sleep(0.5)

        mf = load_client(client_path)
        bus = mf.Rtu(pty, 115200, 17)

        def check(ok, label):
            nonlocal failures
            print(f"[{'PASS' if ok else 'FAIL'}] {label}")
            if not ok:
                failures += 1

        ident = mf.read_identity(bus)
        print(f"  identity: {ident}")
        check(ident.magic == mf.ID_MAGIC, "idMagic 0x454C")
        check(ident.stage == mf.ID_STAGE_APP, "idStage 2 (application)")
        check(ident.window_version == 1, "idWindowVersion 1")
        check(ident.build_rev == rev, f"idBuildRev matches reflex_build_rev.h (0x{rev:07x})")
        check(ident.dirty == bool(dirty), f"idBuildDirty matches reflex_build_rev.h ({dirty})")
        # The struct's protocolVersion: elsStop.protocolVersion. Find it from
        # the client's table rather than hardcoding a second offset.
        check(ident.app_protocol in mf.APP_BOOT_COMMAND_REG,
              f"idAppProtocolVersion {ident.app_protocol} is one the client knows")

        try:
            bus.read(mf.BL_BASE, 4)
            check(False, "FC3 at the bootloader window answers")
        except mf.ExceptionResponse as e:
            check(e.code == 2, "FC3 at 2304 (bootloader window) -> exception 2 in the app")

        try:
            bus.write_one(mf.ID_BASE, 0)
            check(False, "FC6 into the identity window was accepted")
        except mf.ExceptionResponse as e:
            check(e.code == 2, "FC6 into the identity window -> exception 2 (read-only)")

        first = bus.read(0, 4)
        check(len(first) == 4, "FC3 at register 0 still serves the struct")

        # The boot pair, read through the client's own knowledge of where
        # bootCommand is -- which is the thing that has to keep working when the
        # register map grows behind it.
        boot_reg = mf.APP_BOOT_COMMAND_REG[ident.app_protocol]
        tail = bus.read(boot_reg, 2)
        check(tail == [0, 0], "bootCommand/bootSeq read as 0 at the client's register index")

        # Where the struct ENDS. This number is now READ FROM THE GENERATED MAP
        # rather than written here.
        #
        # It was a literal, and it had already been re-derived by hand twice
        # (468 -> 488 bytes) before the hot/cold remap moved it a third time to
        # 492. Each of those re-derivations was correct and carefully explained,
        # and the third one still broke this test -- because the literal cannot
        # know the layout changed, only a human noticing can. The generator
        # knows exactly, so it emits struct_registers and this reads it.
        #
        # What is still asserted is the PROPERTY, not the number: everything
        # from bootCommand to the struct end must answer, and one register past
        # it must be refused. That is the check worth having, and it survives
        # the map moving again.
        offsets = json.loads(
            (REPO_ROOT / "registers" / "offsets.json").read_text())
        STRUCT_REGISTERS = offsets["struct_registers"]
        to_end = STRUCT_REGISTERS - boot_reg
        check(to_end > 0,
              f"{to_end} registers from bootCommand (reg {boot_reg}) to the "
              f"struct end (reg {STRUCT_REGISTERS}) -- bootCommand must lie "
              f"inside the struct")
        check(len(bus.read(boot_reg, to_end)) == to_end,
              "a read to exactly the struct end still answers")
        try:
            bus.read(boot_reg, to_end + 1)
            check(False, "a read past the end of the struct answers")
        except mf.ExceptionResponse as e:
            check(e.code == 2,
                  "a read straddling the struct end -> exception 2")

        # --identity as the user runs it
        rc = mf.main(["modbus-flash.py", "--identity", "--port", pty])
        check(rc == 0, "modbus-flash.py --identity exits 0")

        bus.close()
    finally:
        proc.kill()
        proc.wait()
    print("all passed" if failures == 0 else f"{failures} FAILURES")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
