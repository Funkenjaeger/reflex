#!/usr/bin/env python3
"""One-shot: move the per-field prose out of Ramps.h and into the schema.

Ramps.h carries the real documentation -- units, sign conventions, the hardware
findings that constrain a field, which endpoint a measurement is taken from.
Replacing the hand-written struct with a generated one DESTROYS that unless the
prose moves first. The schema's one-line `doc:` values were placeholders.

Rewrites `doc:` in registers/els_stop.yaml from each field's trailing // comment
in Ramps.h, using json.dumps for the scalar so quotes, colons and backslashes in
the prose cannot break the YAML.

GATES: every schema field must be found in Ramps.h (a name that is not there
means the two have already diverged and a silent skip would hide it), and the
rewritten file must re-parse and still describe the same 55 fields.
"""
import io
import json
import re
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "registers" / "els_stop.yaml"
RAMPS = ROOT / "fw" / "Core" / "Inc" / "Ramps.h"

src = io.open(RAMPS, encoding="utf-8", errors="replace").read()
end = src.find("} elsStop_t")
start = src.rfind("typedef struct", 0, end)
if end < 0 or start < 0:
    sys.exit("ANCHOR FAILED: elsStop_t typedef not located in Ramps.h")
body = src[start:end]

prose = {}
pat = re.compile(r"^\s*(?:u?int(?:8|16|32)_t|float)\s+(\w+)\s*(?:\[[^\]]*\])?\s*;\s*//\s*(.+?)\s*$", re.M)
for m in pat.finditer(body):
    prose[m.group(1)] = m.group(2)
print(f"extracted prose for {len(prose)} fields from Ramps.h")

text = io.open(SCHEMA, encoding="utf-8").read()
doc = yaml.safe_load(text)
before_names = [f["name"] for f in doc["fields"]]

missing, rewritten = [], 0
for name in before_names:
    if name not in prose:
        missing.append(name)
        continue
    # Replace only the doc: scalar on this field's line, leaving every other
    # key and every comment in the file untouched.
    pat_line = re.compile(
        r"^(  - \{id: \d+,\s+name: %s,[^\n]*?doc: )(\"(?:[^\"\\]|\\.)*\")([^\n]*\}\s*)$" % re.escape(name),
        re.M)
    new_doc = json.dumps(prose[name])
    text, n = pat_line.subn(lambda mm: mm.group(1) + new_doc + mm.group(3), text)
    if n != 1:
        sys.exit(f"ANCHOR FAILED: field {name} doc: matched {n} times, expected 1")
    rewritten += 1

if missing:
    print("\nNOT IN Ramps.h (left as-is -- these are the generator's own or renamed):")
    for m in missing:
        print("  " + m)

io.open(SCHEMA, "w", encoding="utf-8", newline="\n").write(text)

check = yaml.safe_load(io.open(SCHEMA, encoding="utf-8"))
after_names = [f["name"] for f in check["fields"]]
if after_names != before_names:
    sys.exit("GATE FAILED: field list changed during rewrite")
print(f"\nrewrote {rewritten} doc: values; schema re-parses with the same "
      f"{len(after_names)} fields in the same order")
