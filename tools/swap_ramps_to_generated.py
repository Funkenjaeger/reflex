#!/usr/bin/env python3
"""One-shot: replace the hand-written elsStop_t in Ramps.h with the generated one.

The field-level prose has already moved into the schema (migrate_field_prose.py)
and comes back out in Ramps_generated.h. What is NOT in the schema is the
SECTION-level rationale interleaved between the fields -- why the block appends
at the tail, why the diagnostic scratchpad is reserved in every build, why an
implicit pad is a phantom register. That is design history, it explains the
shape rather than any one field, and deleting it would be the real cost of this
swap. So it is preserved verbatim above the include.

Run with --dry to see what would happen.

GATES: the region must be located by both anchors; every block comment in it
must be carried over (counted before and after); the field declarations removed
must all exist in the generated header; and Ramps.h must still contain
rampsSharedData_t with an elsStop member afterwards.
"""
import io
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
RAMPS = ROOT / "fw" / "Core" / "Inc" / "Ramps.h"
GEN = ROOT / "fw" / "Core" / "Inc" / "Ramps_generated.h"
DRY = "--dry" in sys.argv

text = io.open(RAMPS, encoding="utf-8", errors="replace").read()
lines = text.splitlines(keepends=True)

end_i = next((i for i, l in enumerate(lines) if l.startswith("} elsStop_t")), None)
if end_i is None:
    sys.exit("ANCHOR FAILED: '} elsStop_t' line not found")
start_i = max((i for i, l in enumerate(lines[:end_i]) if l.strip().startswith("typedef struct")),
              default=None)
if start_i is None:
    sys.exit("ANCHOR FAILED: opening 'typedef struct' not found")

region = "".join(lines[start_i:end_i + 1])

# Every /* ... */ block inside the region, in order. These are the section
# comments; the per-field // comments are NOT carried (they live in the schema).
blocks = re.findall(r"[ \t]*/\*.*?\*/", region, re.S)
if not blocks:
    sys.exit("ANCHOR FAILED: no block comments found in the region -- refusing "
             "to proceed, because that means the extraction is broken rather "
             "than that the prose is absent")

gen = io.open(GEN, encoding="utf-8", errors="replace").read()
decls = re.findall(r"^\s*(?:u?int(?:8|16|32)_t|float)\s+(\w+)\s*(?:\[[^\]]*\])?\s*;", region, re.M)
# The ONLY fields allowed to vanish, named individually so that a third one
# disappearing is still a failure. These are the hand-placed alignment pads; the
# generator emits its own (_pad1/_pad2) at the same offsets, which is the whole
# point of not making a human compute padding.
DROPPED_PADS = {"machineModeReserved", "stopTriggerReserved"}
missing = [d for d in decls
           if d not in DROPPED_PADS and not re.search(r"\b%s\b" % re.escape(d), gen)]
if missing:
    sys.exit(f"GATE FAILED: fields in Ramps.h absent from the generated header: {missing}")
pads = sorted(d for d in decls if d in DROPPED_PADS)

banner = (
    "/* ---------------------------------------------------------------------------\n"
    " * elsStop_t IS GENERATED. Its definition, its per-field documentation and the\n"
    " * _Static_asserts that prove its layout live in Ramps_generated.h, emitted by\n"
    " * tools/genregs.py from registers/els_stop.yaml. Do not add a field here --\n"
    " * add it to the schema and regenerate, or CI's `genregs.py --check` fails.\n"
    " *\n"
    " * WHAT FOLLOWS IS DESIGN HISTORY, kept verbatim from the hand-written struct\n"
    " * this replaced. It explains the SHAPE of the block -- the append-at-tail\n"
    " * convention, the reserved diagnostic scratchpad, why an implicit pad is a\n"
    " * phantom register -- rather than any one field, so it has no home in the\n"
    " * schema and would otherwise have been lost in the swap.\n"
    " * ---------------------------------------------------------------------------\n"
    " */\n\n")

carried = "\n\n".join(b.strip("\n") for b in blocks) + "\n"
replacement = banner + carried + '\n#include "Ramps_generated.h"\n'

new_text = "".join(lines[:start_i]) + replacement + "".join(lines[end_i + 1:])

kept = len(re.findall(r"/\*.*?\*/", replacement, re.S))
if kept < len(blocks):
    sys.exit(f"GATE FAILED: {len(blocks)} block comments in, {kept} out")
if not re.search(r"elsStop_t\s+elsStop\s*;", new_text):
    sys.exit("GATE FAILED: rampsSharedData_t no longer declares an elsStop member")

print(f"region: lines {start_i + 1}..{end_i + 1} ({end_i - start_i + 1} lines)")
print(f"  {len(decls)} field declarations removed; {len(decls)-len(pads)} present in the generated header, {len(pads)} hand-placed pads now generator-emitted: {pads}")
print(f"  {len(blocks)} block comments carried over verbatim")
print(f"  Ramps.h {len(lines)} lines -> {len(new_text.splitlines())} lines")
if DRY:
    print("\n(dry run -- nothing written)")
    sys.exit(0)
io.open(RAMPS, "w", encoding="utf-8", newline="\n").write(new_text)
print("\nwritten")
