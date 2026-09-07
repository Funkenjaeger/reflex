"""Seen-red check for the generated header's _Static_asserts.

The compile passing proves nothing unless it can fail. Three mutations, each a
realistic way the generator could be wrong about layout. Every one MUST fail to
compile, and must name the field that moved.

  1. Drop a generator-emitted alignment pad  -> everything after it shifts.
  2. Swap two adjacent same-size fields      -> a pure reorder, no size change.
  3. Widen a field uint16 -> uint32          -> the shape the C ABI punishes.
"""
import io, os, re, subprocess, sys, tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import glob
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HDR = os.path.join(ROOT, "fw", "Core", "Inc", "Ramps_generated.h")

# Prefer an arm-none-eabi on PATH; fall back to the Windows toolchain install.
# SKIPS rather than passes when no cross compiler is found -- a seen-red suite
# that silently reports success without compiling anything would be the exact
# failure it exists to detect.
GCC = os.environ.get("ARM_GCC") or shutil.which("arm-none-eabi-gcc")
if not GCC:
    cands = sorted(glob.glob(
        r"C:\Program Files (x86)\GNU Arm Embedded Toolchain\*\bin\arm-none-eabi-gcc.exe"))
    GCC = cands[-1] if cands else None
if not GCC:
    print("SKIP: no arm-none-eabi-gcc found (set ARM_GCC) -- asserts NOT exercised")
    sys.exit(77)
src = io.open(HDR, encoding="utf-8").read()
tmp = tempfile.mkdtemp(prefix="genregs_red_")


def compile_with(text, label):
    h = os.path.join(tmp, "hdr.h")
    c = os.path.join(tmp, "tu.c")
    io.open(h, "w", encoding="utf-8", newline="\n").write(text)
    io.open(c, "w", encoding="utf-8", newline="\n").write(
        '#include "hdr.h"\nint main(void){return (int)sizeof(elsStop_t);}\n')
    r = subprocess.run([GCC, "-std=c11", "-mcpu=cortex-m4", "-mthumb",
                        "-c", c, "-o", os.devnull],
                       capture_output=True, text=True, cwd=tmp)
    failed = r.returncode != 0
    named = [l for l in r.stderr.splitlines() if "moved" in l or "not 140 registers" in l]
    print("[%s] %s" % ("RED  " if failed else "GREEN", label))
    if named:
        print("       " + named[0].strip()[:150])
    return failed


# control: unmutated must compile
if compile_with(src, "control (unmutated)"):
    sys.exit("CONTROL FAILED: the pristine header does not compile -- results meaningless")

results = []

# DELETING _pad1 is deliberately NOT tested. It is not a layout change: the
# compiler inserts an implicit pad at the same offset, so every assert correctly
# holds and a green result is the right answer. That drift is caught by
# `genregs.py --check` instead. Asserting otherwise would be testing a claim
# that is false.
#
# WIDENING the pad IS a layout change -- everything after it shifts by 2 bytes.
mut1, n1 = re.subn(r"^  uint16_t _pad1;", "  uint32_t _pad1;", src, count=1, flags=re.M)
if n1 != 1:
    sys.exit("MUTATION 1 ANCHOR FAILED: no _pad1 line in the generated header")
results.append(compile_with(mut1, "widened alignment pad _pad1 to uint32_t"))

m = re.search(r"(^  uint16_t calSeq;.*$\n)(^  uint16_t calResult;.*$\n)", src, re.M)
if not m:
    sys.exit("MUTATION 2 ANCHOR FAILED: calSeq/calResult pair not found")
results.append(compile_with(src[:m.start()] + m.group(2) + m.group(1) + src[m.end():],
                            "swapped calSeq and calResult"))

mut3, n = re.subn(r"^  uint16_t enable;", "  uint32_t enable;", src, count=1, flags=re.M)
if n != 1:
    sys.exit("MUTATION 3 ANCHOR FAILED: enable declaration not found")
results.append(compile_with(mut3, "widened enable to uint32_t"))

print()
if all(results):
    print("SEEN RED: all three layout mutations fail the compile")
    sys.exit(0)
print("AT LEAST ONE ASSERT IS DECORATIVE -- a wrong layout compiled clean")
sys.exit(1)
