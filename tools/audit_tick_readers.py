#!/usr/bin/env python3
"""Which elsStop fields are actually read on the board tick path?

The hot/cold split in registers/els_stop.yaml is only as good as this answer. A
field grouped `cold` that a tick-driven reader touches either raises KeyError or
-- worse -- silently vanishes from a recording, depending on whether its reader
indexes or `.get`s. So this enumerates the readers rather than trusting the
field names to describe themselves.

FOUR SOURCES, all of which read Board.els_stop_values (the once-per-tick
snapshot):

  1. TickReads in fsms/els_stop_hal.py  -- the deliberate, visible tick API
  2. dispatchers/board.py               -- protocolVersion at connect
  3. fsms/els_flight_recorder.py        -- 30 Hz rows + context on change
  4. fsms/els_phase_recorder.py         -- identity + context on change

GATES: every name extracted must exist in the schema (otherwise the extraction
is scraping something that is not a register, and the audit is measuring the
wrong thing), and each source must yield a non-empty set (an extraction that
silently matches nothing would report a clean audit).
"""
import io
import re
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "reflex"


def read(rel):
    return io.open(ROOT / rel, encoding="utf-8", errors="replace").read()


def tuple_literal(text, name):
    """Extract the string entries of a module-level NAME = ( ... ) tuple."""
    m = re.search(r"^%s\s*=\s*\((.*?)^\)" % re.escape(name), text, re.M | re.S)
    if not m:
        raise SystemExit(f"GATE FAILED: tuple {name} not found -- extraction is blind")
    return set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', m.group(1)))


sources = {}

hal = read("ui/reflex/fsms/els_stop_hal.py")
tick_cls = re.search(r"^class TickReads.*?^class ", hal, re.M | re.S)
if not tick_cls:
    raise SystemExit("GATE FAILED: TickReads class not found")
sources["TickReads (the visible tick API)"] = set(
    re.findall(r"_get\('([A-Za-z_]\w*)'", tick_cls.group(0)))

board = read("ui/reflex/dispatchers/board.py")
sources["board.py"] = set(re.findall(r"els_stop_values\['([A-Za-z_]\w*)'\]", board))

fr = read("ui/reflex/fsms/els_flight_recorder.py")
# FAST_FIELDS mixes fastData and elsStop columns; only the ones actually taken
# from `snap` in _sample() are elsStop reads.
fr_fast = set(re.findall(r'snap\.get\("([A-Za-z_]\w*)"', fr)) | \
    set(re.findall(r'snap\["([A-Za-z_]\w*)"\]', fr))
sources["flight recorder (30 Hz row)"] = fr_fast
sources["flight recorder (context on change)"] = tuple_literal(fr, "CONTEXT_FIELDS")

pr = read("ui/reflex/fsms/els_phase_recorder.py")
sources["phase recorder (direct)"] = set(re.findall(r'snapshot\["([A-Za-z_]\w*)"\]', pr))
sources["phase recorder (identity)"] = tuple_literal(pr, "IDENTITY_FIELDS")
sources["phase recorder (context)"] = tuple_literal(pr, "CONTEXT_FIELDS")

doc = yaml.safe_load(io.open(ROOT / "registers" / "els_stop.yaml", encoding="utf-8"))
schema = {f["name"]: f.get("group") for f in doc["fields"]}
hot = {n for n, g in schema.items() if g == "hot"}
cold = {n for n, g in schema.items() if g == "cold"}

problems = []
tick = set()
for label, names in sources.items():
    if not names:
        problems.append(f"source {label!r} yielded NO names -- extraction is blind")
    unknown = names - set(schema)
    # fastData columns legitimately appear in FAST_FIELDS; they are not registers
    # of elsStop and are not the subject of this audit.
    if label.startswith("flight recorder (30 Hz"):
        unknown = set()
    if unknown:
        problems.append(f"{label}: names not in the schema: {sorted(unknown)}")
    tick |= (names & set(schema))
    print(f"  {label}: {len(names & set(schema))} elsStop fields")

if problems:
    print("\nGATE FAILED:")
    for p in problems:
        print("  - " + p)
    sys.exit(1)

misgrouped = sorted(tick & cold)
never_read = sorted(hot - tick)

print(f"\ntick-path elsStop fields: {len(tick)} of {len(schema)}")
print(f"schema says hot: {len(hot)}   cold: {len(cold)}")

print("\nCOLD BUT READ ON THE TICK PATH (schema is wrong about these):")
for n in misgrouped:
    who = [l for l, s in sources.items() if n in s]
    print(f"  {n:<22} read by: {', '.join(who)}")
if not misgrouped:
    print("  (none)")

print("\nHOT BUT NEVER READ ON THE TICK PATH (demotion candidates):")
for n in never_read:
    print(f"  {n}")
if not never_read:
    print("  (none)")

sys.exit(1 if misgrouped else 0)
