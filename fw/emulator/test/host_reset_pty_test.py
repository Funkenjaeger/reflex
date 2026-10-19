#!/usr/bin/env python3
"""host_reset_pty_test.py -- host resets END TO END, over a real wire.

Launches the real lathe-emulator (real mb_app.c with the generated access
tables, real Ramps.c) in serve mode on a PTY, twice:

  1. the isr-gap probe build (lathe-emulator-isr-gap): the real host reader,
     scripts/isr-gap.py, run as the bench runs it, must open a fresh window
     with --reset (window 0 -> 1); a write of 1 to diagEndReason, the
     request value before the reset became a 0, is exception 2;
  2. the release build (lathe-emulator): each host-resettable counter
     (executionCyclesPeak, stepPulseMinCycles, stepPulseRuntCount) takes a 0
     and refuses a nonzero value with exception 2; a 0 to a firmware-owned
     register without the flag (commandSeq) is exception 2; isr-gap.py
     refuses the board because no probe is compiled in.

Register addresses come from the generated map (ui/reflex/utils/
els_stop_map.py), not from literals here.

Exit 77 (ctest SKIP_RETURN_CODE) when pyserial is not installed.

Usage: host_reset_pty_test.py <lathe-emulator-isr-gap> <lathe-emulator>
                              <isr-gap.py> <modbus-flash.py>
"""

import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))
    spec.loader.exec_module(mod)
    return mod


class Emulator:
    def __init__(self, exe):
        env = dict(os.environ, EMU_SCENARIO="serve")
        self.proc = subprocess.Popen([exe, "config/lathe.toml"], env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.pty = None
        deadline = time.time() + 20
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            m = re.search(r"Modbus serial: (/dev/pts/\d+)", line)
            if m:
                self.pty = m.group(1)
                break
        time.sleep(0.5)

    def close(self):
        self.proc.kill()
        self.proc.wait()


def main(argv):
    probe_exe, release_exe, isr_gap, client_path = argv[1:5]
    repo = Path(client_path).resolve().parents[2]
    try:
        import serial  # noqa: F401
    except ImportError:
        print("SKIP: pyserial not installed")
        return 77

    mf = load(client_path, "modbus_flash")
    regmap = load(str(repo / "ui" / "reflex" / "utils" / "els_stop_map.py"), "els_stop_map")

    def reg(name):
        return regmap.ELS_STOP_BASE + regmap.OFFSETS[name]

    failures = 0

    def check(ok, label):
        nonlocal failures
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            failures += 1

    def refused(bus, addr, value):
        try:
            bus.write_one(addr, value)
            return False
        except mf.ExceptionResponse as e:
            return e.code == 2

    def run_isr_gap(pty, *args):
        r = subprocess.run([sys.executable, isr_gap, "--port", pty, *args],
                           capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout, r.stderr

    # ---- 1. the isr-gap probe build ---------------------------------------
    emu = Emulator(probe_exe)
    try:
        if emu.pty is None:
            print("FAIL: the probe emulator never printed its PTY path")
            return 1
        bus = mf.Rtu(emu.pty, 115200, 17)
        check(bus.read(reg("diagSchema"), 1) == [8], "probe build: diagSchema reads 8")
        check(bus.read(reg("diagEndReason"), 1) == [1],
              "probe build: diagEndReason holds 1 while the window is open")
        check(refused(bus, reg("diagEndReason"), 1),
              "probe build: a write of 1 to diagEndReason is exception 2")
        bus.close()

        rc, out, err = run_isr_gap(emu.pty, "--reset")
        print(out.rstrip())
        if err:
            print(err.rstrip())
        check(rc == 0 and "reset acknowledged, window 0 -> 1" in out,
              "probe build: isr-gap.py --reset opens window 1")
        rc, out, err = run_isr_gap(emu.pty, "--reset")
        check(rc == 0 and "reset acknowledged, window 1 -> 2" in out,
              "probe build: a second --reset opens window 2")
        rc, out, err = run_isr_gap(emu.pty)
        check(rc == 0 and "isr-gap window 2:" in out and "RESET REQUESTED" not in out,
              "probe build: a plain read shows window 2 with no reset pending")
    finally:
        emu.close()

    # ---- 2. the release build ---------------------------------------------
    emu = Emulator(release_exe)
    try:
        if emu.pty is None:
            print("FAIL: the release emulator never printed its PTY path")
            return 1
        bus = mf.Rtu(emu.pty, 115200, 17)
        for name in ("executionCyclesPeak", "stepPulseMinCycles", "stepPulseRuntCount"):
            for k in range(2):
                r = reg(name) + k
                check(refused(bus, r, 0x1234), f"release: FC6 of 0x1234 to {name} word {k} is exception 2")
                try:
                    bus.write_one(r, 0)
                    ok = True
                except mf.ExceptionResponse:
                    ok = False
                check(ok, f"release: FC6 of 0 to {name} word {k} lands")
        check(refused(bus, reg("commandSeq"), 0),
              "release: FC6 of 0 to commandSeq (no host reset) is exception 2")
        bus.close()

        rc, out, err = run_isr_gap(emu.pty, "--reset")
        check(rc == 2 and "release build" in err, "release: isr-gap.py refuses a board with no probe")
    finally:
        emu.close()

    print("all passed" if failures == 0 else f"{failures} FAILURES")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
