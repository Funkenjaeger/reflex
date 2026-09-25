#!/usr/bin/env python3
"""modbus-flash-recovery-seen-red.py -- prove modbus-flash-recovery-test.py
can FAIL, by removing each of the behaviours it exists to pin and
watching the scenario that pins it go red.

  M1 "no return to app": flash()'s call to return_to_app() for a failure
     before APPLY becomes a bare re-raise -- the behaviour until 2026-09-19,
     which left the board in the bootloader. Scenario "return" must go red.
  M2 "no resume": stream()'s resync/resume branch becomes a bare re-raise,
     so the first chunk that will not go through ends the transfer. Scenario
     "drops" must go red.
  M3 "impatient recovery": RECOVERY_TOTAL_S back to the 30 s return_to_app
     had on 2026-09-23. Scenario "silent60" must go red.
  M4 "diag exception is fatal": read_diag lets a field bootloader's
     exception at register 2420 out. Scenario "diag" must go red.
  M5 "no operator step": the power-cycle step dropped from the could-not-
     return verdict. Scenario "dead" must go red.
  M6 "no refusal check": enter_bootloader's recognition of a REFUSED reboot
     (bootCommand consumed, bootSeq unmoved -- an ELS job engaged, 2026-09-25)
     never matches, so the flasher waits out its 10 s and says only "timed
     out". Scenario "engaged" must go red.
  M7 "refusal without the ack": the refusal check armed even when the reboot
     request went unacknowledged, so a request that may never have arrived is
     reported as refused. Scenario "engagedunacked" must go red.

Each mutation is made on a scratch copy of fw/scripts (never on the tree),
its anchor is asserted present exactly once BEFORE the edit and the edit is
re-read AFTER it; the unmutated copy is run first and must pass, so a red
mutant cannot be red for some unrelated reason. Prints the [FAIL] lines each
mutant produced. Exit 0 when every mutant went red, 1 otherwise. Stdlib only.

    python3 scripts/lib/modbus-flash-recovery-seen-red.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parent
SCRIPTS = LIB.parent
TEST = LIB / "modbus-flash-recovery-test.py"

MUTANTS = [
    ("M1 no return to app", "return",
     '        return_to_app(bus, bl, before, start, e, "before APPLY")      # always raises\n',
     "        raise\n"),
    ("M2 no resume", "drops",
     "        except (LinkLost, ModbusError) as e:\n            failure = e\n",
     "        except (LinkLost, ModbusError) as e:\n            raise\n"),
    # 2026-09-23: the 30 s of patience the lathe's return_to_app had that day.
    ("M3 impatient recovery", "silent60",
     "RECOVERY_TOTAL_S = 150.0\n",
     "RECOVERY_TOTAL_S = 30.0\n"),
    # A field bootloader's exception at the diag window treated as a failure.
    ("M4 diag exception is fatal", "diag",
     "        except ExceptionResponse:\n            return None\n        except ModbusError:\n"
     "            continue\n",
     "        except ExceptionResponse:\n            raise\n        except ModbusError:\n"
     "            continue\n"),
    # The operator's power-cycle step dropped from the verdict.
    ("M5 no operator step", "dead",
     '                     f"  {POWER_CYCLE_STEP}\\n"\n',
     '                     f""\n'),
    # 2026-09-25: the ELS-engaged refusal is not recognised.
    ("M6 no refusal check", "engaged",
     "        if cmd == 0 and seq == seq_before:\n",
     "        if False:\n"),
    # ... or is claimed on an unacknowledged request.
    ("M7 refusal without the ack", "engagedunacked",
     "    if acked and seq_before is not None:\n",
     "    if seq_before is not None:\n"),
]


def run(client: Path, only: str):
    p = subprocess.run([sys.executable, str(TEST), "--client", str(client), "--only", only],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
    return p.returncode, p.stdout


def main() -> int:
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("modbus-flash.py", "flash_manifest.py", "reflex_image.py"):
            shutil.copy(SCRIPTS / name, Path(tmp) / name)
        client = Path(tmp) / "modbus-flash.py"
        pristine = client.read_text()

        for label, only, anchor, replacement in MUTANTS:
            client.write_text(pristine)
            rc, out = run(client, only)
            if rc != 0:
                print(f"UNUSABLE: {label}: the UNMUTATED copy already fails scenario {only!r}:\n{out}")
                bad += 1
                continue
            n = pristine.count(anchor)
            if n != 1:
                print(f"UNUSABLE: {label}: anchor found {n} times, expected exactly 1:\n{anchor}")
                bad += 1
                continue
            client.write_text(pristine.replace(anchor, replacement))
            mutated = client.read_text()
            if anchor in mutated or replacement not in mutated:
                print(f"UNUSABLE: {label}: the mutation did not land in {client}")
                bad += 1
                continue
            rc, out = run(client, only)
            fails = [l for l in out.splitlines() if l.startswith("[FAIL]")]
            if rc != 0 and fails:
                print(f"RED (good): {label} -> scenario {only!r}, {len(fails)} failing checks:")
                for l in fails:
                    print(f"    {l}")
            else:
                print(f"GREEN (bad): {label} -> scenario {only!r} still passes (rc {rc}); "
                      f"the test does not pin this behaviour")
                bad += 1
    print("seen-red: all mutants went red" if not bad else f"seen-red: {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
