"""Seen-red check for the protocolVersion <-> layout binding in genregs.py.

The gap it guards (2026-09-18 register-map sweep): --check compares generated
files against the schema, so a deliberate schema edit that is REGENERATED passed
every gate while protocol_version stayed put. registers/layout-fingerprints.json
now pins one layout per version. This proves the pin can fail and can be
satisfied, on a scratch copy of the tree -- the real repo is never touched:

  0. the unmodified copy regenerates and --checks green (else the rest is moot)
  1. reorder two fields, regenerate, --check    -> RED, naming the missing bump
  2. --pin over the existing version            -> REFUSED
  3. same edit + bump + flash-table entry + pin -> GREEN

Run: python tools/test_layout_fingerprint_seen_red.py   (exit 0 = all as expected)
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# fw/Core/Inc whole: the schemas resolve constants out of several headers there.
COPY = ["tools/genregs.py", "registers", "fw/Core/Inc", "fw/scripts/modbus-flash.py"]

tmp = tempfile.mkdtemp(prefix="genregs_fp_red_")
for rel in COPY:
    src, dst = os.path.join(ROOT, rel), os.path.join(tmp, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    (shutil.copytree if os.path.isdir(src) else shutil.copy)(src, dst)

results = []


def run(*args):
    r = subprocess.run([sys.executable, os.path.join(tmp, "tools", "genregs.py"), *args],
                       capture_output=True, text=True, cwd=tmp)
    return r.returncode, r.stdout + r.stderr


def expect(label, ok, out):
    results.append(ok)
    print("[%s] %s" % ("ok  " if ok else "FAIL", label))
    for line in out.strip().splitlines()[-2:]:
        print("       " + line.strip()[:200])


def edit(rel, pattern, repl, count=1):
    p = os.path.join(tmp, rel)
    text = io.open(p, encoding="utf-8").read()
    new, n = re.subn(pattern, repl, text, count=count, flags=re.M)
    if n != count:  # anchor gate: an edit that matched nothing measured nothing
        sys.exit(f"ANCHOR MISSING in {rel}: {pattern!r} matched {n}, wanted {count}")
    io.open(p, "w", encoding="utf-8", newline="\n").write(new)


try:
    rc, out = run()
    expect("0. unmodified copy regenerates green", rc == 0, out)
    rc, out = run("--check")
    expect("0. unmodified copy --check green", rc == 0, out)

    # 1. A pure reorder of the first two uint16 fields: same size, every
    #    downstream consumer regenerated, static_asserts rewritten to agree.
    #    Exactly the edit nothing but the fingerprint can catch.
    edit("registers/els_stop.yaml",
         r"^(  - \{id: 1,  name: enable,.*\n)(  - \{id: 2,  name: scaleIndex,.*\n)", r"\2\1")
    rc, out = run()
    expect("1. reorder without bump: regenerate exits nonzero naming the bump",
           rc != 0 and "WITHOUT A protocol_version BUMP" in out, out)
    rc, out = run("--check")
    expect("1. reorder without bump: --check RED naming the bump",
           rc != 0 and "WITHOUT A protocol_version BUMP" in out, out)

    # 2. Re-pinning the existing version must be refused.
    rc, out = run("--pin")
    expect("2. --pin over an existing version is REFUSED",
           rc != 0 and "bump protocol_version instead of re-pinning" in out, out)

    # 3. The legitimate path: bump, teach modbus-flash the new version, pin.
    ver = int(re.search(r"^protocol_version:\s*(\d+)",
                        io.open(os.path.join(tmp, "registers/els_stop.yaml"),
                                encoding="utf-8").read(), re.M).group(1))
    boot = json.load(io.open(os.path.join(ROOT, "registers/offsets.json"),
                             encoding="utf-8"))["protocol_versions"][str(ver)]["bootCommand"]
    edit("registers/els_stop.yaml", r"^protocol_version:\s*%d\b" % ver,
         "protocol_version: %d" % (ver + 1))
    edit("fw/scripts/modbus-flash.py", r"^(APP_BOOT_COMMAND_REG\s*=\s*\{[^}]*)\}",
         r"\1, %d: %d}" % (ver + 1, boot))
    rc, out = run()
    expect("3. bumped but not yet pinned: regenerate still nonzero (no pin)",
           rc != 0 and "no pinned layout fingerprint" in out, out)
    rc, out = run("--pin")
    expect("3. --pin of the NEW version accepted", rc == 0, out)
    rc, out = run("--check")
    expect("3. bump + pin: --check GREEN", rc == 0, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if not all(results):
    print("SEEN-RED FAILED: %d of %d expectations not met" % (results.count(False), len(results)))
    sys.exit(1)
print("all %d expectations met" % len(results))
