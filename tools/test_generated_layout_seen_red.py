"""Seen-red check for the generated header's static_asserts.

The compile passing proves nothing unless it can fail. Each mutation below is a
realistic way the generator could be wrong about layout; every one MUST fail to
compile, and must name the field or struct that moved.

elsStop_t (since 2026-09-07):
  1. Widen a generator-emitted alignment pad -> everything after it shifts.
  2. Swap two uint16 fields                  -> a pure reorder, no size change.
  3. Widen a field uint16 -> uint32          -> the shape the C ABI punishes.

servo_t, input_t, fastData_t (since 2026-09-18), each:
  4. Swap the first two fields (same size)   -> a pure reorder.
  5. Drop the first field                    -> every later field shifts.
     (NOT the last: dropping input_t's trailing int16 leaves sizeof at 24 via
     a new implicit tail pad, so no offset or size moves and green would be the
     right answer -- that drift is --check's job, as with elsStop's _pad1.)
"""
import glob
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
    named = [l for l in r.stderr.splitlines()
             if "moved" in l or re.search(r"is not \d+ registers", l)]
    print("[%s] %s" % ("RED  " if failed else "GREEN", label))
    for line in named[:3]:
        print("       " + line.strip()[:170])
    return failed


def struct_span(text, name):
    """(start, end) of `typedef struct { ... } name;` -- found by its banner."""
    banner = text.index(f"/* ======== {name} -- from ")
    start = text.index("typedef struct {", banner)
    end = text.index(f"}} {name};", start)
    return start, end


def decls(text, name):
    """Field declarations inside one struct, in order, as regex matches."""
    s, e = struct_span(text, name)
    return [m for m in re.finditer(r"^  (\w+) (\w+)(\[\w+\])?;", text[s:e], re.M)
            if not m.group(2).startswith("_pad")], s


# control: unmutated must compile
if compile_with(src, "control (unmutated)"):
    sys.exit("CONTROL FAILED: the pristine header does not compile -- results meaningless")

results = []

# ---- elsStop_t -------------------------------------------------------------
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
results.append(compile_with(mut1, "elsStop_t: widened alignment pad _pad1 to uint32_t"))

# A PURE REORDER: two uint16 fields exchange places. No size changes, so this
# is the mutation a size check cannot see and only per-field offsets catch.
#
# Found structurally rather than by name. Naming a pair made this brittle
# against the layout it is testing -- calSeq/calResult stopped being adjacent
# when they moved to the cold group, and takeupSeq/takeupResult stopped being
# adjacent when migrated prose put a block comment between every declaration.
# Both times the anchor failed loudly, which is the right behaviour, but a
# self-test that needs re-anchoring whenever the map moves is a self-test that
# will eventually be switched off.
els, base = decls(src, "elsStop_t")
u16 = [m for m in els if m.group(1) == "uint16_t"]
if len(u16) < 2:
    sys.exit("MUTATION 2 ANCHOR FAILED: fewer than two uint16 fields to swap")
a, b = u16[0], u16[1]
mut2 = (src[:base + a.start(2)] + b.group(2) + src[base + a.end(2):base + b.start(2)]
        + a.group(2) + src[base + b.end(2):])
if mut2 == src:
    sys.exit("MUTATION 2 CHANGED NOTHING")
results.append(compile_with(mut2, f"elsStop_t: swapped {a.group(2)} and {b.group(2)}"))

mut3, n = re.subn(r"^  uint16_t enable;", "  uint32_t enable;", src, count=1, flags=re.M)
if n != 1:
    sys.exit("MUTATION 3 ANCHOR FAILED: enable declaration not found")
results.append(compile_with(mut3, "elsStop_t: widened enable to uint32_t"))

# ---- the structs moved onto the generator on 2026-09-18 ----------------------
SIZE = {"uint16_t": 2, "int16_t": 2, "uint32_t": 4, "int32_t": 4, "float": 4}
for name in ("servo_t", "input_t", "fastData_t"):
    fields, base = decls(src, name)
    if len(fields) < 2:
        sys.exit(f"{name}: ANCHOR FAILED -- fewer than two fields found")
    a, b = fields[0], fields[1]
    if SIZE[a.group(1)] != SIZE[b.group(1)] or a.group(3) or b.group(3):
        sys.exit(f"{name}: ANCHOR FAILED -- first two fields are not same-size scalars")
    mut = (src[:base + a.start(2)] + b.group(2) + src[base + a.end(2):base + b.start(2)]
           + a.group(2) + src[base + b.end(2):])
    if mut == src:
        sys.exit(f"{name}: SWAP CHANGED NOTHING")
    results.append(compile_with(mut, f"{name}: swapped {a.group(2)} and {b.group(2)}"))

    first = fields[0]
    mut = src[:base + first.start(0)] + src[base + first.end(0):]
    # the offsetof assert for the deleted field would fail on the missing
    # member, which is a DIFFERENT error; drop it so only layout can go red
    mut, n = re.subn(r"^static_assert\(offsetof\(%s, %s\).*\n" % (name, first.group(2)),
                     "", mut, count=1, flags=re.M)
    if n != 1:
        sys.exit(f"{name}: ANCHOR FAILED -- no offsetof assert for {first.group(2)}")
    results.append(compile_with(mut, f"{name}: dropped first field {first.group(2)}"))

print()
if all(results):
    print("SEEN RED: all %d layout mutations fail the compile" % len(results))
    sys.exit(0)
print("AT LEAST ONE ASSERT IS DECORATIVE -- a wrong layout compiled clean")
sys.exit(1)
