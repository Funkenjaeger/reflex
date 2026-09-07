#!/usr/bin/env python3
"""Generate the ELS register map from registers/els_stop.yaml.

    python tools/genregs.py            write the generated files
    python tools/genregs.py --check    regenerate into memory and diff against
                                       what is checked in; exit 1 on any drift

WHY --check EXISTS. This generator emits BOTH sides of the RS-485 link from one
source, which makes them self-consistent by construction -- and therefore able to
be confidently wrong together. Two things stop that:

  1. The emitted C header carries _Static_assert(offsetof(...) == N) for every
     field. The COMPILER, not this script, has the final vote on padding. If the
     layout pass here disagrees with what the compiler actually lays out, the
     firmware build fails. That is the only check in this pipeline whose outcome
     this script does not control.

  2. --check in CI. A hand-edit to a generated file fails the build instead of
     quietly becoming a third source of truth, which is the failure mode that
     kills codegen projects.

The layout pass additionally REFUSES to emit on: a group over its request
budget, a seq field sitting above anything it acknowledges (the 2026-08-22
torn-read bug shape), an unresolved array-length constant, or a constant whose
value in the C header no longer matches the schema.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "registers" / "els_stop.yaml"

C_TYPE = {"uint16": "uint16_t", "int16": "int16_t",
          "uint32": "uint32_t", "int32": "int32_t", "float": "float"}
PY_FMT = {"uint16": "H", "int16": "h", "uint32": "I", "int32": "i", "float": "f"}


class GenError(SystemExit):
    def __init__(self, msg):
        super().__init__("genregs: REFUSING TO EMIT -- " + msg)


def load_schema():
    return yaml.safe_load(io.open(SCHEMA, encoding="utf-8"))


def resolve_constants(doc):
    """Read each constant out of its C header and FAIL on disagreement.

    Never substitutes a default: a constant that moved in the header while this
    file still says 50 is exactly the silent desync the schema exists to stop.
    """
    out = {}
    for name, spec in (doc.get("constants") or {}).items():
        src = ROOT / spec["source"]
        if not src.exists():
            raise GenError(f"constant {name}: source {spec['source']} not found")
        text = io.open(src, encoding="utf-8", errors="replace").read()
        m = re.search(r"^#define\s+%s\s+(\d+)\s*$" % re.escape(name), text, re.M)
        if not m:
            raise GenError(f"constant {name} not #defined in {spec['source']}")
        actual = int(m.group(1))
        if actual != spec["value"] and spec.get("on_mismatch", "fail") == "fail":
            raise GenError(
                f"constant {name} is {actual} in {spec['source']} but {spec['value']} "
                f"in the schema -- resolve the disagreement, do not guess")
        out[name] = actual
    return out


def field_count(f, consts):
    cnt = f.get("count", 1)
    if isinstance(cnt, str):
        if cnt not in consts:
            raise GenError(f"unresolved array length {cnt!r} on {f['name']}")
        return consts[cnt], cnt
    return cnt, None


def layout(doc, consts):
    """Assign byte offsets group by group. Returns (items, groups, total_regs).

    items: list of dicts in physical order, each either a field or a pad.
    """
    sizes = {k: v["bytes"] for k, v in doc["types"].items()}
    order = [g["group"] for g in doc["layout"]]
    known = {f.get("group") for f in doc["fields"]}
    if known - set(order):
        raise GenError(f"fields reference undeclared groups: {sorted(known - set(order))}")

    items, groups, off = [], {}, 0
    for grp in order:
        start = off
        for f in doc["fields"]:
            if f.get("group") != grp:
                continue
            typ = f["type"]
            if typ not in sizes:
                raise GenError(f"{f['name']}: unknown type {typ!r}")
            cnt, cnt_macro = field_count(f, consts)
            al = sizes[typ]
            if off % al:
                pad = al - (off % al)
                items.append({"pad": True, "bytes": pad, "byte_off": off,
                              "before": f["name"], "group": grp})
                off += pad
            # **f FIRST: the computed keys below must win over the raw schema
            # ones, or the unresolved macro name overwrites the resolved count.
            items.append({**f, "pad": False, "byte_off": off,
                          "bytes": sizes[typ] * cnt,
                          "count": cnt, "count_macro": cnt_macro, "group": grp})
            off += sizes[typ] * cnt
        groups[grp] = {"start_byte": start, "bytes": off - start}
    return items, groups, off // 2


def validate(doc, items, groups, total_regs):
    budgets = {g["group"]: g["budget_registers"] for g in doc["layout"]}
    hard = {g["group"]: g.get("fail_over_budget", False) for g in doc["layout"]}
    placed = {i["name"]: i["byte_off"] // 2 for i in items if not i["pad"]}
    errs = []

    for grp, span in groups.items():
        regs = span["bytes"] // 2
        if regs > budgets[grp] and hard.get(grp):
            errs.append(f"group {grp} is {regs} registers, over its {budgets[grp]} budget")

    if doc.get("invariants", {}).get("seq_precedes_payload"):
        for i in items:
            if i["pad"] or i.get("kind") != "seq":
                continue
            at = placed[i["name"]]
            for target in i.get("acks") or []:
                if target not in placed:
                    errs.append(f"{i['name']} acknowledges unknown field {target}")
                elif placed[target] < at:
                    errs.append(
                        f"TORN-READ INVARIANT: {target} (reg {placed[target]}) sits BELOW "
                        f"its seq {i['name']} (reg {at})")

    for i in items:
        if i["pad"] or i.get("kind") != "command":
            continue
        if i.get("ack") not in placed:
            errs.append(f"command {i['name']} names ack {i.get('ack')!r}, which is not a field")

    seen = set()
    for i in items:
        if i["pad"]:
            continue
        if i["id"] in seen:
            errs.append(f"duplicate field id {i['id']} ({i['name']}) -- ids are never reused")
        seen.add(i["id"])

    if errs:
        raise GenError("\n  - " + "\n  - ".join(errs))
    return total_regs


BANNER = """/* GENERATED by tools/genregs.py from registers/els_stop.yaml -- DO NOT EDIT.
 *
 * Edit the schema and regenerate. CI runs `genregs.py --check` and fails the
 * build if this file has drifted from the schema, so a hand-edit here does not
 * survive; it just breaks the build later and further from the cause.
 *
 * The _Static_asserts below are the point of this file. The generator computes
 * offsets from the schema; these make the COMPILER check that arithmetic against
 * what it actually lays out. Emitting both sides of the link from one source is
 * only trustworthy because this disagreement is a build failure.
 */
"""


def emit_c(doc, items, groups, total_regs, consts):
    L = [BANNER, "#ifndef RAMPS_GENERATED_H", "#define RAMPS_GENERATED_H", "",
         "#include <stddef.h>", "#include <stdint.h>", ""]
    meta = doc["meta"]
    # Constants this layout depends on, guarded so the real definition in the
    # firmware header wins when both are in scope. Emitted so the generated
    # header is STANDALONE and can be compile-checked on its own -- a check that
    # needed another header to supply an array length would not be checking this
    # file's arithmetic. resolve_constants() has already failed the run if any of
    # these disagrees with the C header it came from.
    for name, spec in (doc.get("constants") or {}).items():
        L.append(f"#ifndef {name}")
        L.append(f"#define {name} {consts[name]}  /* verified against {spec['source']} */")
        L.append("#endif")
    L.append("")
    L.append(f"#define ELS_STOP_PROTOCOL_VERSION {doc['protocol_version']}u")
    L.append(f"#define ELS_STOP_TOTAL_REGS {total_regs}u")
    for grp in [g["group"] for g in doc["layout"]]:
        s = groups[grp]
        L.append(f"#define ELS_STOP_{grp.upper()}_REG_BASE {s['start_byte'] // 2}u")
        L.append(f"#define ELS_STOP_{grp.upper()}_REG_COUNT {s['bytes'] // 2}u")
    L += ["",
          f"/* elsStop_t occupies registers {meta['parent_offset_registers']}.."
          f"{meta['parent_offset_registers'] + total_regs - 1} of {meta['parent']}. */",
          "typedef struct {"]

    cur = None
    pad_n = 0
    for i in items:
        if i["group"] != cur:
            cur = i["group"]
            gdoc = next(g for g in doc["layout"] if g["group"] == cur).get("doc", "").strip()
            L.append("")
            L.append(f"  /* ---- {cur.upper()} ----")
            line = "   *"
            for word in gdoc.split():
                if len(line) + len(word) + 1 > 78:
                    L.append(line)
                    line = "   *"
                line += " " + word
            L.append(line)
            L.append("   */")
        if i["pad"]:
            pad_n += 1
            L.append(f"  uint16_t _pad{pad_n};" + " " * 8 +
                     f"/* generator-emitted alignment, before {i['before']} */")
            continue
        arr = ""
        if i["count"] > 1:
            arr = "[%s]" % (i["count_macro"] or i["count"])
        decl = f"  {C_TYPE[i['type']]} {i['name']}{arr};"
        acc = {"sw_write": "SW write", "ro_firmware": "READ-ONLY (firmware-owned)",
               "bidirectional": "bidirectional"}.get(i.get("access"), i.get("access", ""))
        note = ""
        if i.get("kind") == "command":
            note = f" CLEARED BY FIRMWARE ON CONSUME -- poll {i['ack']}, never this."
        elif i.get("kind") == "seq":
            note = " Monotonic ack; edge-detect it. Sits below everything it counts."
        L.append(f"{decl:<52}/* reg {i['byte_off'] // 2:3d}  {acc}: {i.get('doc','')}{note} */")
    L += ["} elsStop_t;", "",
          "/* ---- the compiler's vote ---- */"]
    for i in items:
        if i["pad"]:
            continue
        L.append(f"_Static_assert(offsetof(elsStop_t, {i['name']}) == {i['byte_off']}, "
                 f"\"{i['name']} moved: schema says register {i['byte_off'] // 2}\");")
    L.append(f"_Static_assert(sizeof(elsStop_t) == {total_regs * 2}, "
             f"\"elsStop_t is not {total_regs} registers\");")
    for grp in [g["group"] for g in doc["layout"]]:
        s = groups[grp]
        budget = next(g for g in doc["layout"] if g["group"] == grp)["budget_registers"]
        L.append(f"_Static_assert(ELS_STOP_{grp.upper()}_REG_COUNT <= {budget}, "
                 f"\"{grp} group exceeds its FC3 request budget\");")
    L += ["", "#endif /* RAMPS_GENERATED_H */", ""]
    return "\n".join(L)


def emit_py(doc, items, groups, total_regs):
    L = ['"""GENERATED by tools/genregs.py from registers/els_stop.yaml -- DO NOT EDIT.',
         "",
         "Offsets are elsStop-relative registers. Add ELS_STOP_BASE for an absolute",
         "address in rampsSharedData_t. No hand-placed padding: alignment pads are",
         'emitted into the format strings below, which is the whole point.',
         '"""',
         "",
         f"PROTOCOL_VERSION = {doc['protocol_version']}",
         f"ELS_STOP_BASE = {doc['meta']['parent_offset_registers']}",
         f"TOTAL_REGISTERS = {total_regs}", ""]
    for grp in [g["group"] for g in doc["layout"]]:
        s = groups[grp]
        fmt, names = "", []
        for i in items:
            if i["group"] != grp:
                continue
            if i["pad"]:
                fmt += "%dx" % i["bytes"]
                continue
            fmt += (str(i["count"]) if i["count"] > 1 else "") + PY_FMT[i["type"]]
            names.append(i["name"])
        L.append(f"{grp.upper()}_BASE = {s['start_byte'] // 2}")
        L.append(f"{grp.upper()}_COUNT = {s['bytes'] // 2}")
        L.append(f'{grp.upper()}_FORMAT = "<{fmt}"')
        L.append(f"{grp.upper()}_FIELDS = [")
        for n in names:
            L.append(f'    "{n}",')
        L.append("]")
        L.append("")
    L.append("OFFSETS = {")
    for i in items:
        if not i["pad"]:
            L.append(f'    "{i["name"]}": {i["byte_off"] // 2},')
    L.append("}")
    L.append("")
    return "\n".join(L)


def emit_json(doc, items, total_regs):
    base = doc["meta"]["parent_offset_registers"]
    exported = {i["name"]: base + i["byte_off"] // 2
                for i in items if not i["pad"] and i.get("export_offset")}
    return json.dumps({
        "note": "Absolute rampsSharedData_t register addresses for external tools. "
                "GENERATED by tools/genregs.py -- do not hand-edit.",
        "protocol_versions": {str(doc["protocol_version"]): exported},
    }, indent=2) + "\n"


def emit_md(doc, items, groups, total_regs):
    base = doc["meta"]["parent_offset_registers"]
    L = ["<!-- GENERATED by tools/genregs.py from registers/els_stop.yaml. DO NOT EDIT. -->",
         "", f"# ELS register map (protocolVersion {doc['protocol_version']})", "",
         f"`elsStop_t` occupies {total_regs} registers at "
         f"{base}..{base + total_regs - 1} of `rampsSharedData_t`.", ""]
    for g in doc["layout"]:
        grp = g["group"]
        s = groups[grp]
        L += [f"## `{grp}` — {s['bytes'] // 2} registers "
              f"(elsStop {s['start_byte'] // 2}..{s['start_byte'] // 2 + s['bytes'] // 2 - 1}, "
              f"absolute {base + s['start_byte'] // 2}..{base + s['start_byte'] // 2 + s['bytes'] // 2 - 1})",
              "", " ".join(g.get("doc", "").split()), "",
              "| reg | abs | type | field | access | notes |",
              "|----:|----:|------|-------|--------|-------|"]
        for i in items:
            if i["group"] != grp:
                continue
            r = i["byte_off"] // 2
            if i["pad"]:
                L.append(f"| {r} | {base + r} | — | *(alignment pad)* | — | before `{i['before']}` |")
                continue
            arr = f"[{i['count']}]" if i["count"] > 1 else ""
            flag = ""
            if i.get("kind") == "command":
                flag = f"**command** — cleared on consume, poll `{i['ack']}` "
            elif i.get("kind") == "seq":
                flag = "**seq** — monotonic ack "
            elif i.get("kind") == "reserved":
                flag = "**reserved** "
            if i.get("audit") == "unverified":
                flag += "⚠ group assignment UNVERIFIED "
            L.append(f"| {r} | {base + r} | `{i['type']}{arr}` | `{i['name']}` | "
                     f"{i.get('access','')} | {flag}{i.get('doc','')} |")
        L.append("")
    return "\n".join(L)


OUTPUTS = {
    "fw/Core/Inc/Ramps_generated.h": emit_c,
    "ui/reflex/utils/els_stop_map.py": emit_py,
    "registers/offsets.json": None,
    "docs/reference/register-map.md": emit_md,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="diff against checked-in output; exit 1 on drift")
    args = ap.parse_args()

    doc = load_schema()
    consts = resolve_constants(doc)
    items, groups, total = layout(doc, consts)
    validate(doc, items, groups, total)

    rendered = {
        "fw/Core/Inc/Ramps_generated.h": emit_c(doc, items, groups, total, consts),
        "ui/reflex/utils/els_stop_map.py": emit_py(doc, items, groups, total),
        "registers/offsets.json": emit_json(doc, items, total),
        "docs/reference/register-map.md": emit_md(doc, items, groups, total),
    }

    drift = []
    for rel, text in rendered.items():
        p = ROOT / rel
        if args.check:
            if not p.exists():
                drift.append(f"{rel}: missing")
            elif io.open(p, encoding="utf-8").read() != text:
                drift.append(f"{rel}: differs from schema")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            io.open(p, "w", encoding="utf-8", newline="\n").write(text)

    if args.check:
        if drift:
            print("genregs --check FAILED:")
            for d in drift:
                print("  - " + d)
            return 1
        print("genregs --check: all generated files match the schema")
        return 0

    for grp in [g["group"] for g in doc["layout"]]:
        s = groups[grp]
        print(f"  {grp:<5} regs {s['start_byte'] // 2:3d}.."
              f"{s['start_byte'] // 2 + s['bytes'] // 2 - 1:<3d} "
              f"({s['bytes'] // 2} registers)")
    print(f"  total {total} registers, protocolVersion {doc['protocol_version']}")
    for rel in rendered:
        print("  wrote " + rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
