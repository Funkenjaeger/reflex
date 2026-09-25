#!/usr/bin/env python3
"""Generate the reflex register map from the schemas in registers/*.yaml.

    python tools/genregs.py            write the generated files
    python tools/genregs.py --check    regenerate into memory and diff against
                                       what is checked in; exit 1 on any drift

ONE SCHEMA PER STRUCT. Every registers/*.yaml describes one struct of the
shared Modbus block (rampsSharedData_t) and says where it sits in it
(`meta.parent_member`, `meta.parent_offset_registers`, `meta.parent_count` for
an array member such as `input_t scales[SCALES_COUNT]`). Until 2026-09-18 this
generator knew only elsStop_t; servo_t, input_t and fastData_t were hand-written
in Ramps.h and hand-mirrored in reflex-ui with hand-placed _pad fields. They
were moved onto it byte-identically (Open Loops 6a9f3106).

WHY --check EXISTS. This generator emits BOTH sides of the RS-485 link from one
source, which makes them self-consistent by construction -- and therefore able to
be confidently wrong together. Two things stop that:

  1. The emitted C header carries static_assert(offsetof(...) == N) for every
     field and static_assert(sizeof(...)) for every struct, plus a macro Ramps.h
     expands after rampsSharedData_t that asserts where each struct sits in it.
     The COMPILER, not this script, has the final vote on padding. If the
     layout pass here disagrees with what the compiler actually lays out, the
     firmware build fails. That is the only check in this pipeline whose outcome
     this script does not control.

  2. --check in CI. A hand-edit to a generated file fails the build instead of
     quietly becoming a third source of truth, which is the failure mode that
     kills codegen projects.

The layout pass additionally REFUSES to emit on: a group over its request
budget, a seq field sitting above anything it acknowledges (the 2026-08-22
torn-read bug shape), an unresolved array-length constant, a constant whose
value in the C header no longer matches the schema, two structs overlapping in
the parent, modbus-flash.py hardcoding a register the schema disagrees with, or
a `mirror_of:` field (a publishing copy of another struct's field, e.g.
fastData.stepsToGo mirroring servo.stepsToGo) whose type or length disagrees
with the field it mirrors -- see check_mirrors().

    python tools/genregs.py --pin      pin the current layout's fingerprint for
                                       the current protocol_version

VERSION BINDING (2026-09-18). registers/layout-fingerprints.json maps each
protocolVersion to a fingerprint of the layout it means, and both a regenerate
and --check fail when the current layout does not match the pin for the current
protocol_version -- so a layout edit cannot ship under an old version number.
See check_fingerprint().
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTERS = ROOT / "registers"
C_HEADER_OUT = "fw/Core/Inc/Ramps_generated.h"
JSON_OUT = "registers/offsets.json"
MD_OUT = "docs/reference/register-map.md"

C_TYPE = {"uint16": "uint16_t", "int16": "int16_t",
          "uint32": "uint32_t", "int32": "int32_t", "float": "float"}
PY_FMT = {"uint16": "H", "int16": "h", "uint32": "I", "int32": "i", "float": "f"}


class GenError(SystemExit):
    def __init__(self, msg):
        super().__init__("genregs: REFUSING TO EMIT -- " + msg)


class Schema:
    """One registers/*.yaml, laid out."""

    def __init__(self, path: Path):
        self.path = path
        self.rel = path.relative_to(ROOT).as_posix()
        self.doc = yaml.safe_load(io.open(path, encoding="utf-8"))
        meta = self.doc.get("meta") or {}
        for key in ("struct", "short_name", "macro_prefix", "parent",
                    "parent_member", "parent_offset_registers", "py_out"):
            if key not in meta:
                raise GenError(f"{self.rel}: meta.{key} is required")
        self.meta = meta
        self.struct = meta["struct"]
        self.prefix = meta["macro_prefix"]
        self.base = meta["parent_offset_registers"]
        self.consts = resolve_constants(self.doc, self.rel)
        cnt = meta.get("parent_count", 1)
        if isinstance(cnt, str):
            if cnt not in self.consts:
                raise GenError(f"{self.rel}: parent_count {cnt!r} is not a declared constant")
            self.parent_count, self.parent_count_macro = self.consts[cnt], cnt
        else:
            self.parent_count, self.parent_count_macro = cnt, None
        self.items, self.groups, self.total, self.align = layout(self.doc, self.consts, self.rel)
        validate(self.doc, self.items, self.groups, self.rel)

    @property
    def group_names(self):
        return [g["group"] for g in self.doc["layout"]]

    @property
    def end(self):
        """First parent register past this member (all elements of an array)."""
        return self.base + self.total * self.parent_count


def load_schemas():
    paths = sorted(REGISTERS.glob("*.yaml"))
    if not paths:
        raise GenError("no schemas in registers/")
    schemas = sorted((Schema(p) for p in paths), key=lambda s: s.base)
    validate_parent(schemas)
    check_mirrors(schemas)
    return schemas


def resolve_constants(doc, rel):
    """Read each constant out of its C header and FAIL on disagreement.

    Never substitutes a default: a constant that moved in the header while this
    file still says 50 is exactly the silent desync the schema exists to stop.
    """
    out = {}
    for name, spec in (doc.get("constants") or {}).items():
        src = ROOT / spec["source"]
        if not src.exists():
            raise GenError(f"{rel}: constant {name}: source {spec['source']} not found")
        text = io.open(src, encoding="utf-8", errors="replace").read()
        m = re.search(r"^#define\s+%s\s+(\d+)\s*$" % re.escape(name), text, re.M)
        if not m:
            raise GenError(f"{rel}: constant {name} not #defined in {spec['source']}")
        actual = int(m.group(1))
        if actual != spec["value"] and spec.get("on_mismatch", "fail") == "fail":
            raise GenError(
                f"constant {name} is {actual} in {spec['source']} but {spec['value']} "
                f"in {rel} -- resolve the disagreement, do not guess")
        out[name] = actual
    return out


def field_count(f, consts):
    cnt = f.get("count", 1)
    if isinstance(cnt, str):
        if cnt not in consts:
            raise GenError(f"unresolved array length {cnt!r} on {f['name']}")
        return consts[cnt], cnt
    return cnt, None


def layout(doc, consts, rel):
    """Assign byte offsets group by group. Returns (items, groups, total_regs, align).

    items: list of dicts in physical order, each either a field or a pad. A
    pad is either an INTERIOR alignment pad (emitted as a named member on both
    sides) or the struct's TAIL pad, marked `tail: True` -- see emit_c for why
    the two are treated differently in C.
    """
    sizes = {k: v["bytes"] for k, v in doc["types"].items()}
    order = [g["group"] for g in doc["layout"]]
    known = {f.get("group") for f in doc["fields"]}
    if known - set(order):
        raise GenError(f"{rel}: fields reference undeclared groups: {sorted(known - set(order))}")

    items, groups, off, align = [], {}, 0, 1
    for grp in order:
        start = off
        for f in doc["fields"]:
            if f.get("group") != grp:
                continue
            typ = f["type"]
            if typ not in sizes:
                raise GenError(f"{rel}: {f['name']}: unknown type {typ!r}")
            cnt, cnt_macro = field_count(f, consts)
            al = sizes[typ]
            align = max(align, al)
            if off % al:
                pad = al - (off % al)
                items.append({"pad": True, "tail": False, "bytes": pad, "byte_off": off,
                              "before": f["name"], "group": grp})
                off += pad
            # **f FIRST: the computed keys below must win over the raw schema
            # ones, or the unresolved macro name overwrites the resolved count.
            items.append({**f, "pad": False, "byte_off": off,
                          "bytes": sizes[typ] * cnt,
                          "count": cnt, "count_macro": cnt_macro, "group": grp})
            off += sizes[typ] * cnt
        groups[grp] = {"start_byte": start, "bytes": off - start}
    # TRAILING PADDING. A struct is as aligned as its most-aligned member, so
    # sizeof rounds up to it -- servo_t ends in a lone int16 and is 36 bytes for
    # 34 of fields. That tail is part of the wire map (the parent is cast
    # wholesale onto holding registers), so it is modelled here rather than left
    # for a reader to discover. It belongs to the last group's span.
    if off % align:
        pad = align - (off % align)
        last = order[-1]
        items.append({"pad": True, "tail": True, "bytes": pad, "byte_off": off,
                      "before": None, "group": last})
        off += pad
        groups[last]["bytes"] += pad
    return items, groups, off // 2, align


def validate(doc, items, groups, rel):
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

    seen, names = set(), set()
    for i in items:
        if i["pad"]:
            continue
        if i["id"] in seen:
            errs.append(f"duplicate field id {i['id']} ({i['name']}) -- ids are never reused")
        seen.add(i["id"])
        if i["name"] in names:
            errs.append(f"duplicate field name {i['name']}")
        names.add(i["name"])

    if errs:
        raise GenError(f"{rel}:\n  - " + "\n  - ".join(errs))


def validate_parent(schemas):
    """The structs must agree on their parent and must not overlap in it.

    Gaps are allowed -- the parent's leading four uint32s are hand-written and
    not described by any schema -- and the compiler checks every placement via
    RAMPS_GENERATED_ASSERT_PARENT_LAYOUT anyway. What this catches earlier and
    in plain words is two schemas claiming the same registers.
    """
    parents = {s.meta["parent"] for s in schemas}
    if len(parents) != 1:
        raise GenError(f"schemas disagree about their parent struct: {sorted(parents)}")
    versions = [s for s in schemas if "protocol_version" in s.doc]
    if len(versions) != 1:
        raise GenError("exactly one schema must carry protocol_version (it versions the "
                       f"whole shared block); found {[s.rel for s in versions]}")
    errs = []
    for a, b in zip(schemas, schemas[1:]):
        if b.base < a.end:
            errs.append(f"{b.struct} starts at register {b.base}, inside {a.struct} "
                        f"({a.base}..{a.end - 1})")
    for s in schemas:
        if (s.base * 2) % s.align:
            errs.append(f"{s.struct} at register {s.base} is not {s.align}-byte aligned")
    if errs:
        raise GenError("parent layout:\n  - " + "\n  - ".join(errs))


MIRROR_RE = re.compile(r"^(\w+)(\[\])?\.(\w+)$")


def check_mirrors(schemas):
    """Resolve every `mirror_of:` against the schema that owns that parent
    member, and REFUSE TO EMIT on a type or length disagreement.

    THE BUG THIS CLOSES (2026-09-12, build/2026-09-12-5 d7de6de). fastData_t is
    a publishing copy of fields that live in servo_t and input_t; before this,
    that fact was carried only in `doc:` prose ("mirror of servo.stepsToGo").
    The prose was true and the TYPE was not -- fastData.stepsToGo was uint32
    over servo.stepsToGo's int32 -- so a 1287-step reverse indexing move
    published as 4294966009, and nothing caught it: the register-map contract
    test compares the two SIDES of the RS-485 link, and both sides mirrored the
    wrong type faithfully.

    `mirror_of: member.field` (or `member[].field` when the member is an array
    in the parent, e.g. `scales[].position`) turns that prose into a checked
    declaration. Unlike the original gate -- which had to re-parse the
    hand-maintained Ramps.h text, because servo_t and input_t were still
    hand-written there -- servo_t and input_t are schemas too now (Open Loops
    6a9f3106), so the source of truth is the sibling Schema this generator
    already loaded: no header text, no re-parsing, one fewer thing that can
    disagree with itself. A mirror_of naming a parent_member or field that does
    not exist refuses exactly like a type or length mismatch does; nothing
    here warns.
    """
    by_member = {s.meta["parent_member"]: s for s in schemas}
    for s in schemas:
        for item in s.items:
            spec = item.get("mirror_of")
            if item["pad"] or not spec:
                continue
            m = MIRROR_RE.match(str(spec))
            if not m:
                raise GenError(f"{s.struct}.{item['name']}: mirror_of {spec!r} is not of the "
                               f"form member.field or member[].field")
            member, is_array, srcfield = m.group(1), bool(m.group(2)), m.group(3)
            if member not in by_member:
                raise GenError(f"{s.struct}.{item['name']} mirrors {spec}, but no loaded schema "
                               f"declares parent_member {member!r}")
            src = by_member[member]
            src_item = next((i for i in src.items if not i["pad"] and i["name"] == srcfield), None)
            if src_item is None:
                raise GenError(f"{s.struct}.{item['name']} mirrors {spec}, but {src.struct} "
                               f"({src.rel}) has no field {srcfield!r}")
            if is_array and src.parent_count <= 1:
                raise GenError(f"{s.struct}.{item['name']} mirrors {spec} as an array, but "
                               f"{member} ({src.rel}) is not an array member of the parent -- "
                               f"write {member}.{srcfield}")
            if not is_array and src.parent_count > 1:
                raise GenError(f"{s.struct}.{item['name']} mirrors {spec}, but {member} "
                               f"({src.rel}) is an array of {src.parent_count} -- write "
                               f"{member}[].{srcfield}")
            if item["type"] != src_item["type"]:
                raise GenError(
                    f"{s.struct.removesuffix('_t')}.{item['name']} is {item['type']} but mirrors "
                    f"{spec}, {src_item['type']} in {src.rel} (id {src_item['id']})")
            # An array mirror has to be as long as the number of elements it is
            # replicated over, or the tail of the source silently stops being
            # published -- that reads as "that scale is dead", not as drift.
            want_cnt = src.parent_count if is_array else 1
            if item["count"] != want_cnt:
                raise GenError(
                    f"{s.struct.removesuffix('_t')}.{item['name']} is [{item['count']}] but "
                    f"mirrors {spec}, {want_cnt} of them in {src.rel}")


def protocol_version(schemas):
    return next(s.doc["protocol_version"] for s in schemas if "protocol_version" in s.doc)


BANNER = """/* GENERATED by tools/genregs.py from the schemas in registers/ -- DO NOT EDIT.
 *
 * Edit the schema and regenerate. CI runs `genregs.py --check` and fails the
 * build if this file has drifted from the schema, so a hand-edit here does not
 * survive; it just breaks the build later and further from the cause.
 *
 * The static_asserts below are the point of this file. The generator computes
 * offsets from the schema; these make the COMPILER check that arithmetic against
 * what it actually lays out. Emitting both sides of the link from one source is
 * only trustworthy because this disagreement is a build failure.
 */
"""


def c_comment_safe(s):
    """Neutralise a comment terminator inside migrated prose.

    Ramps.h prose legitimately contains things like `ELS_CAL_*/ELS_TAKEUP_*`,
    and a bare `*/` closes the block comment early -- which the target compile
    catches, but as a confusing cascade of type errors a hundred lines later
    rather than at the cause. C has no escape for this, so the only fix is to
    break the digraph.
    """
    return s.replace("*/", "* /")


def _wrap_block(text, first="  /*", cont="   *"):
    out, line = [], first
    for word in text.split():
        if len(line) + len(word) + 1 > 78:
            out.append(line)
            line = cont
        line += " " + word
    out.append(line)
    return out


def emit_c_struct(s):
    doc, items, groups, total = s.doc, s.items, s.groups, s.total
    P = s.prefix
    L = []
    if "protocol_version" in doc:
        L.append(f"#define {P}_PROTOCOL_VERSION {doc['protocol_version']}u")
    L.append(f"#define {P}_TOTAL_REGS {total}u")
    for grp in s.group_names:
        g = groups[grp]
        L.append(f"#define {P}_{grp.upper()}_REG_BASE {g['start_byte'] // 2}u")
        L.append(f"#define {P}_{grp.upper()}_REG_COUNT {g['bytes'] // 2}u")
    L.append("")
    if s.parent_count > 1:
        L.append(f"/* {s.struct} is {s.meta['parent_member']}[{s.parent_count_macro or s.parent_count}] "
                 f"of {s.meta['parent']}: element i occupies")
        L.append(f" * registers {s.base} + {total}*i, {s.parent_count} elements, "
                 f"{s.base}..{s.end - 1} in all. */")
    else:
        L.append(f"/* {s.struct} occupies registers {s.base}..{s.end - 1} of {s.meta['parent']}. */")
    L.append("typedef struct {")

    cur = None
    pad_n = 0
    for i in items:
        if i["group"] != cur:
            cur = i["group"]
            gdoc = next(g for g in doc["layout"] if g["group"] == cur).get("doc", "").strip()
            L.append("")
            L.append(f"  /* ---- {cur.upper()} ----")
            L.extend(_wrap_block(c_comment_safe(gdoc), first="   *")[0:])
            L.append("   */")
        if i["pad"] and i["tail"]:
            # IMPLICIT, deliberately. The tail pad is the compiler's own
            # rounding of sizeof, and the hand-written structs this replaced
            # left it implicit; naming it would add a member, which is a change
            # to the struct and not merely to its documentation. It is still
            # pinned: the sizeof assert below includes it, and reflex-ui's
            # DEFINITION carries it as an explicit _pad (its parser cannot align).
            L.append(f"  /* {i['bytes']} bytes of IMPLICIT tail padding (reg {i['byte_off'] // 2}): "
                     f"sizeof rounds up to")
            L.append(f"   * the struct's {s.align}-byte alignment. Pinned by the sizeof assert below. */")
            continue
        if i["pad"]:
            pad_n += 1
            arr = "" if i["bytes"] == 2 else "[%d]" % (i["bytes"] // 2)
            L.append(f"  uint16_t _pad{pad_n}{arr};" + " " * 8 +
                     f"/* generator-emitted alignment, before {i['before']} */")
            continue
        arr = ""
        if i["count"] > 1:
            arr = "[%s]" % (i["count_macro"] or i["count"])
        decl = f"  {C_TYPE[i['type']]} {i['name']}{arr};"
        note = ""
        if i.get("kind") == "command":
            note = f" CLEARED BY FIRMWARE ON CONSUME -- poll {i['ack']}, never this."
        elif i.get("kind") == "seq":
            note = " Monotonic ack; edge-detect it. Sits below everything it counts."
        # The prose is the field documentation migrated out of Ramps.h -- units,
        # sign conventions, the hardware findings that constrain the field. It is
        # long on purpose and must not be truncated, so anything that will not
        # fit on the declaration line becomes a wrapped block comment above it.
        text = c_comment_safe(f"reg {i['byte_off'] // 2}  {i.get('doc', '')}{note}".strip())
        if len(decl) + len(text) + 8 <= 100:
            L.append(f"{decl:<52}/* {text} */")
        else:
            L.append("")
            L.extend(_wrap_block(text))
            L.append("   */")
            L.append(decl)
    L += [f"}} {s.struct};", "",
          "/* ---- the compiler's vote ---- */"]
    for i in items:
        if i["pad"]:
            continue
        L.append(f"static_assert(offsetof({s.struct}, {i['name']}) == {i['byte_off']}, "
                 f"\"{i['name']} moved: schema says register {i['byte_off'] // 2}\");")
    L.append(f"static_assert(sizeof({s.struct}) == {total * 2}, "
             f"\"{s.struct} is not {total} registers\");")
    for grp in s.group_names:
        budget = next(g for g in doc["layout"] if g["group"] == grp)["budget_registers"]
        L.append(f"static_assert({P}_{grp.upper()}_REG_COUNT <= {budget}, "
                 f"\"{grp} group exceeds its FC3 request budget\");")
    return L


def emit_c(schemas):
    L = [BANNER, "#ifndef RAMPS_GENERATED_H", "#define RAMPS_GENERATED_H", "",
         "#include <assert.h>", "#include <stddef.h>", "#include <stdint.h>", "",
         "/* static_assert, not _Static_assert: parts of the emulator suite compile",
         " * this header as C++, where _Static_assert does not exist and the errors",
         " * are unhelpful (\"expected constructor, destructor, or type conversion\").",
         " * <assert.h> defines static_assert for C11 and it is a keyword in C++11,",
         " * so this one spelling works for the ARM build, the host C build and the",
         " * C++ translation units alike. */", ""]
    # Constants the layouts depend on, guarded so the real definition in the
    # firmware header wins when both are in scope. Emitted so the generated
    # header is STANDALONE and can be compile-checked on its own -- a check that
    # needed another header to supply an array length would not be checking this
    # file's arithmetic. resolve_constants() has already failed the run if any of
    # these disagrees with the C header it came from.
    consts = {}
    for s in schemas:
        for name, spec in (s.doc.get("constants") or {}).items():
            if name in consts and consts[name][0] != s.consts[name]:
                raise GenError(f"constant {name} resolves differently across schemas")
            consts.setdefault(name, (s.consts[name], spec["source"]))
    for name in sorted(consts):
        value, source = consts[name]
        L.append(f"#ifndef {name}")
        L.append(f"#define {name} {value}  /* verified against {source} */")
        L.append("#endif")
    for s in schemas:
        L += ["", "",
              f"/* ======== {s.struct} -- from {s.rel} ======== */", ""]
        L += emit_c_struct(s)

    parent = schemas[0].meta["parent"]
    end = max(s.end for s in schemas)
    L += ["", "",
          f"/* ======== placement in {parent} ======== */", "",
          f"/* {parent} itself is hand-written in Ramps.h -- its leading members are not",
          " * described by any schema -- so where each generated struct sits in it can",
          " * only be asserted once it exists. Ramps.h expands this immediately after",
          f" * the typedef. The final assert is what makes offsets.json's struct_registers",
          " * (\"where the shared block ends\") a compiler-checked number. */",
          "#define RAMPS_GENERATED_ASSERT_PARENT_LAYOUT() \\"]
    asserts = []
    for s in schemas:
        m = s.meta["parent_member"]
        asserts.append(f"  static_assert(offsetof({parent}, {m}) == {s.base * 2}, "
                       f"\"{m} is not at register {s.base} of {parent}\")")
        if s.parent_count > 1:
            asserts.append(f"  static_assert(sizeof((({parent} *)0)->{m}) == "
                           f"{s.parent_count} * sizeof({s.struct}), "
                           f"\"{m} is not {s.parent_count} x {s.struct}\")")
    asserts.append(f"  static_assert(sizeof({parent}) == {end * 2}, "
                   f"\"{parent} is not {end} registers\")")
    L += [a + "; \\" for a in asserts[:-1]] + [asserts[-1]]
    L += ["", "#endif /* RAMPS_GENERATED_H */", ""]
    return "\n".join(L)


def emit_py(s, schemas):
    doc, items, groups, total = s.doc, s.items, s.groups, s.total
    P = s.prefix
    L = [f'"""GENERATED by tools/genregs.py from {s.rel} -- DO NOT EDIT.',
         "",
         f"Offsets are {s.meta['short_name']}-relative registers. Add {P}_BASE for an absolute",
         f"address in {s.meta['parent']}. No hand-placed padding: alignment pads are",
         'emitted into the format strings below, which is the whole point.']
    if s.parent_count > 1:
        L += ["",
              f"{s.struct} is the element type of {s.meta['parent_member']}[{s.parent_count}]: element i",
              f"starts at {P}_BASE + i * TOTAL_REGISTERS."]
    L += ['"""',
          "",
          "import struct",
          ""]
    if "protocol_version" in doc:
        L.append(f"PROTOCOL_VERSION = {doc['protocol_version']}")
    L.append(f"{P}_BASE = {s.base}")
    if s.parent_count > 1:
        L.append(f"{P}_COUNT = {s.parent_count}")
    L += [f"TOTAL_REGISTERS = {total}", ""]
    for grp in s.group_names:
        g = groups[grp]
        fmt, names = "", []
        for i in items:
            if i["group"] != grp:
                continue
            if i["pad"]:
                fmt += "%dx" % i["bytes"]
                continue
            fmt += (str(i["count"]) if i["count"] > 1 else "") + PY_FMT[i["type"]]
            names.append(i["name"])
        L.append(f"{grp.upper()}_BASE = {g['start_byte'] // 2}")
        L.append(f"{grp.upper()}_COUNT = {g['bytes'] // 2}")
        L.append(f'{grp.upper()}_FORMAT = "<{fmt}"')
        L.append(f"{grp.upper()}_FIELDS = [")
        for n in names:
            L.append(f'    "{n}",')
        L.append("]")
        # (name, count) so an array field regroups into a list instead of
        # silently consuming N slots and shifting every field after it -- the
        # column-shift failure the register-map contract test exists to prevent.
        L.append(f"{grp.upper()}_LAYOUT = [")
        for i in items:
            if i["group"] != grp or i["pad"]:
                continue
            L.append(f'    ("{i["name"]}", {i["count"]}),')
        L.append("]")
        L.append("")
    L.append("OFFSETS = {")
    for i in items:
        if not i["pad"]:
            L.append(f'    "{i["name"]}": {i["byte_off"] // 2},')
    L.append("}")
    L.append("")
    # The C-ish string BaseDevice.parse_addresses_from_definition consumes. That
    # parser packs sequentially with NO alignment awareness, which is why the
    # hand-maintained mirror carried hand-placed _pad fields -- a human doing a
    # compiler's job. They are emitted here instead, at offsets the target
    # compiler has already agreed to via static_assert -- the tail pad
    # included, which C leaves implicit and this parser cannot infer.
    #
    # Array lengths are emitted as RESOLVED LITERALS because that parser does
    # int(count) and cannot read a macro. That limitation is why someone had to
    # hand-copy diagTrace[ELS_DIAG_TRACE_BUCKETS] as [50]; now the generator
    # resolves it from the header and the copy cannot drift.
    L += [
        "",
        "def _decode(raw, fmt, layout):",
        '    """Turn one FC3 span into {field: value}, arrays regrouped.',
        "",
        "    A group is decoded from its OWN format string, so a field added to",
        "    the schema cannot shift the columns of a reader that was not",
        "    regenerated -- the format and the layout move together or not at all.",
        '    """',
        "    if len(raw) * 2 != struct.calcsize(fmt):",
        "        raise ValueError(",
        '            "span is %d registers, format wants %d -- refusing to decode"',
        "            % (len(raw), struct.calcsize(fmt) // 2))",
        '    values = list(struct.unpack(fmt, struct.pack("<%dH" % len(raw), *raw)))',
        "    out = {}",
        "    for name, count in layout:",
        "        if count > 1:",
        "            out[name] = [values.pop(0) for _ in range(count)]",
        "        else:",
        "            out[name] = values.pop(0)",
        "    if values:",
        '        raise ValueError("%d values left over after decode" % len(values))',
        "    return out",
    ]
    for grp in s.group_names:
        G = grp.upper()
        L += ["", "",
              f"def decode_{grp}(raw):",
              f"    return _decode(raw, {G}_FORMAT, {G}_LAYOUT)"]
    L.append("")
    L.append('DEFINITION = """')
    L.append("typedef struct {")
    pad_n = 0
    for i in items:
        if i["pad"]:
            pad_n += 1
            arr = "" if i["bytes"] == 2 else "[%d]" % (i["bytes"] // 2)
            L.append(f"  uint16_t _pad{pad_n}{arr};")
            continue
        arr = f"[{i['count']}]" if i["count"] > 1 else ""
        L.append(f"  {C_TYPE[i['type']]} {i['name']}{arr};")
    L.append(f"}} {s.struct};")
    L.append('"""')
    L.append("")
    return "\n".join(L)


def exported_offsets(schemas):
    """{field: absolute register} for every `export_offset: true` field."""
    out = {}
    for s in schemas:
        for i in s.items:
            if i["pad"] or not i.get("export_offset"):
                continue
            if s.parent_count > 1:
                raise GenError(f"{s.rel}: export_offset on {i['name']} is ambiguous -- "
                               f"{s.struct} is an array member of the parent")
            if i["name"] in out:
                raise GenError(f"export_offset name {i['name']} exported twice")
            out[i["name"]] = s.base + i["byte_off"] // 2
    return out


def check_external_consumers(schemas):
    """Fail if a tool that hardcodes an absolute register has not been updated.

    modbus-flash.py reaches into the app's register space by ABSOLUTE address to
    write bootCommand, and it keeps a per-protocolVersion whitelist because a
    guess there lands a flash on the wrong register. That whitelist is a
    hand-copied number, which is the exact class of thing this generator exists
    to stop being hand-copied -- so the copy is checked rather than trusted.

    Deliberately a HARD failure and not a warning: the remap moved bootCommand
    from 232 to 168, and 232 under the new layout is stopTriggerZSpeed. A stale
    entry does not fail safe.
    """
    version = protocol_version(schemas)
    exported = exported_offsets(schemas)
    if "bootCommand" not in exported:
        return
    path = ROOT / "fw" / "scripts" / "modbus-flash.py"
    if not path.exists():
        return
    text = io.open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"^APP_BOOT_COMMAND_REG\s*=\s*\{([^}]*)\}", text, re.M)
    if not m:
        raise GenError("modbus-flash.py has no APP_BOOT_COMMAND_REG table to check")
    table = {int(k): int(v) for k, v in re.findall(r"(\d+)\s*:\s*(\d+)", m.group(1))}
    want = exported["bootCommand"]
    if version not in table:
        raise GenError(
            f"modbus-flash.py APP_BOOT_COMMAND_REG has no entry for "
            f"protocolVersion {version}; add {version}: {want}")
    if table[version] != want:
        raise GenError(
            f"modbus-flash.py maps protocolVersion {version} to register "
            f"{table[version]}, the schema puts bootCommand at {want}")

    # bootSeq, the ack the flasher reads to recognise a REFUSED reboot
    # (2026-09-25). Same hand-copied whitelist, same hard failure: a stale
    # entry would compare against some other register and could report a
    # refusal that did not happen.
    if "bootSeq" not in exported:
        return
    m = re.search(r"^APP_BOOT_SEQ_REG\s*=\s*\{([^}]*)\}", text, re.M)
    if not m:
        raise GenError("modbus-flash.py has no APP_BOOT_SEQ_REG table to check")
    seq_table = {int(k): int(v) for k, v in re.findall(r"(\d+)\s*:\s*(\d+)", m.group(1))}
    want = exported["bootSeq"]
    if seq_table.get(version) != want:
        raise GenError(
            f"modbus-flash.py APP_BOOT_SEQ_REG maps protocolVersion {version} to "
            f"{seq_table.get(version)}, the schema puts bootSeq at {want}")


FINGERPRINTS = "registers/layout-fingerprints.json"


def layout_fingerprint(schemas):
    """sha256 over the WIRE LAYOUT of every generated struct -- nothing else.

    Covers, per struct in parent order: its name, where it sits in the parent
    (member, base register, element count), its size, and every field's
    (name, type, byte offset, element count). Prose, ids, access, kind and
    group names are deliberately NOT in it: they can change without a single
    register moving, and a fingerprint that moved on a doc edit would train
    people to re-pin it without reading why it moved.
    """
    canon = []
    for s in schemas:
        canon.append({
            "struct": s.struct,
            "member": s.meta["parent_member"],
            "base": s.base,
            "count": s.parent_count,
            "registers": s.total,
            "fields": [[i["name"], i["type"], i["byte_off"], i["count"]]
                       for i in s.items if not i["pad"]],
        })
    canon.append({"parent_registers": max(s.end for s in schemas)})
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_fingerprints():
    p = ROOT / FINGERPRINTS
    if not p.exists():
        return {}
    doc = json.loads(io.open(p, encoding="utf-8").read())
    return {str(k): v for k, v in (doc.get("protocol_versions") or {}).items()}


def check_fingerprint(schemas):
    """Return an error string if the layout no longer matches the version's pin.

    THE GAP THIS CLOSES (found by the 2026-09-18 register-map sweep). --check
    compares generated files against the schema, so a DELIBERATE schema edit
    that is regenerated passes every gate -- including the static_asserts, which
    the same regeneration rewrote -- while protocol_version still says 10. A
    UI built against the old map and firmware built against the new one would
    then both report protocolVersion 10 and decode each other's registers as
    garbage, which is exactly what that number exists to prevent.

    So the version is bound to a layout here, in a file the generator never
    writes on its own: layout-fingerprints.json maps protocolVersion -> the
    fingerprint of the one layout that version means. A layout edit without a
    bump now fails, and the only way to make it pass is to bump the version
    and pin the new fingerprint -- `genregs.py --pin`, which refuses to
    overwrite an existing pin.
    """
    version = str(protocol_version(schemas))
    have = layout_fingerprint(schemas)
    pins = load_fingerprints()
    if version not in pins:
        return (f"protocolVersion {version} has no pinned layout fingerprint in "
                f"{FINGERPRINTS}. If this is a new version, pin it: "
                f"python tools/genregs.py --pin  (fingerprint {have})")
    if pins[version] != have:
        return (f"LAYOUT CHANGED WITHOUT A protocol_version BUMP: the register layout "
                f"no longer matches the fingerprint pinned for protocolVersion {version} "
                f"in {FINGERPRINTS} (pinned {pins[version][:16]}..., now {have[:16]}...). "
                f"Bump protocol_version in the schema that carries it, add the new "
                f"version to modbus-flash.py APP_BOOT_COMMAND_REG and APP_BOOT_SEQ_REG, "
                f"regenerate, and pin "
                f"the new fingerprint with `python tools/genregs.py --pin`. Never re-pin "
                f"an existing version: firmware that says {version} is already in the field.")
    return None


def pin_fingerprint(schemas):
    version = str(protocol_version(schemas))
    have = layout_fingerprint(schemas)
    pins = load_fingerprints()
    if version in pins and pins[version] != have:
        raise GenError(
            f"protocolVersion {version} is already pinned to a DIFFERENT layout in "
            f"{FINGERPRINTS}; bump protocol_version instead of re-pinning. (If {version} "
            f"genuinely never left this branch, delete its entry by hand and say so "
            f"in the commit message.)")
    pins[version] = have
    p = ROOT / FINGERPRINTS
    doc = {
        "note": "protocolVersion -> sha256 of the register layout that version means "
                "(tools/genregs.py layout_fingerprint). PINNED BY HAND with "
                "`genregs.py --pin`, never rewritten by a plain regenerate; "
                "`genregs.py --check` fails if the current layout does not match the "
                "pin for the current protocol_version.",
        "protocol_versions": {k: pins[k] for k in sorted(pins, key=int)},
    }
    io.open(p, "w", encoding="utf-8", newline="\n").write(json.dumps(doc, indent=2) + "\n")
    return version, have


def emit_json(schemas):
    return json.dumps({
        "note": "Absolute rampsSharedData_t register addresses for external tools. "
                "GENERATED by tools/genregs.py -- do not hand-edit.",
        # Total register count of the PARENT struct, for consumers that need to
        # know where the shared block ends -- a read past it must be refused.
        # It is the end of the last generated member, which is only the end of
        # the parent because elsStop is the parent's LAST member -- and that is
        # no longer an assumption: RAMPS_GENERATED_ASSERT_PARENT_LAYOUT asserts
        # sizeof(parent) equals this, so the firmware build fails if it is not.
        #
        # Emitted because this was a hand-maintained literal that had already
        # been re-derived by hand twice (468 -> 488 -> 492 bytes). Twice is one
        # more than a number a generator knows exactly should ever be typed.
        "struct_registers": max(s.end for s in schemas),
        "protocol_versions": {str(protocol_version(schemas)): exported_offsets(schemas)},
    }, indent=2) + "\n"


def emit_md(schemas):
    parent = schemas[0].meta["parent"]
    first = schemas[0].base
    L = ["<!-- GENERATED by tools/genregs.py from registers/*.yaml. DO NOT EDIT. -->",
         "", f"# Register map (protocolVersion {protocol_version(schemas)})", "",
         f"The generated structs of `{parent}`, in the order they sit in it; "
         f"{max(s.end for s in schemas)} registers in all. Registers 0..{first - 1} "
         f"(the ISR timing counters ahead of `servo`) are declared by hand in "
         f"`fw/Core/Inc/Ramps.h` and are not described here.", ""]
    for s in schemas:
        m = s.meta["parent_member"]
        L += [f"## `{s.struct}` — `{parent}.{m}`", "", f"From `{s.rel}`. "]
        if s.parent_count > 1:
            L[-1] += (f"`{m}[{s.parent_count}]`: {s.total} registers per element at "
                      f"{s.base} + {s.total}·i, {s.base}..{s.end - 1} in all. "
                      f"The `abs` column is element 0.")
        else:
            L[-1] += f"{s.total} registers at {s.base}..{s.end - 1}."
        L.append("")
        for g in s.doc["layout"]:
            grp = g["group"]
            span = s.groups[grp]
            r0 = span["start_byte"] // 2
            n = span["bytes"] // 2
            L += [f"### `{grp}` — {n} registers "
                  f"({s.meta['short_name']} {r0}..{r0 + n - 1}, "
                  f"absolute {s.base + r0}..{s.base + r0 + n - 1})",
                  "", " ".join(g.get("doc", "").split()), "",
                  "| reg | abs | type | field | access | notes |",
                  "|----:|----:|------|-------|--------|-------|"]
            for i in s.items:
                if i["group"] != grp:
                    continue
                r = i["byte_off"] // 2
                if i["pad"] and i["tail"]:
                    L.append(f"| {r} | {s.base + r} | — | *(implicit tail pad)* | — | "
                             f"sizeof rounds up to {s.align}-byte alignment |")
                    continue
                if i["pad"]:
                    L.append(f"| {r} | {s.base + r} | — | *(alignment pad)* | — | before `{i['before']}` |")
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
                L.append(f"| {r} | {s.base + r} | `{i['type']}{arr}` | `{i['name']}` | "
                         f"{i.get('access','')} | {flag}{i.get('doc','')} |")
            L.append("")
    return "\n".join(L)


def render(schemas):
    out = {C_HEADER_OUT: emit_c(schemas)}
    for s in schemas:
        rel = s.meta["py_out"]
        if rel in out:
            raise GenError(f"{s.rel}: py_out {rel} is already written by another schema")
        out[rel] = emit_py(s, schemas)
    out[JSON_OUT] = emit_json(schemas)
    out[MD_OUT] = emit_md(schemas)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="diff against checked-in output; exit 1 on drift")
    ap.add_argument("--pin", action="store_true",
                    help=f"pin the current layout's fingerprint for the current "
                         f"protocol_version in {FINGERPRINTS}; refuses to overwrite a "
                         f"different existing pin")
    args = ap.parse_args()

    schemas = load_schemas()
    check_external_consumers(schemas)
    if args.pin:
        version, fp = pin_fingerprint(schemas)
        print(f"genregs --pin: protocolVersion {version} -> {fp}")
        return 0
    rendered = render(schemas)
    fp_err = check_fingerprint(schemas)

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
        if fp_err:
            drift.append(fp_err)
        if drift:
            print("genregs --check FAILED:")
            for d in drift:
                print("  - " + d)
            return 1
        print("genregs --check: all generated files match the schema, and the layout "
              f"matches the fingerprint pinned for protocolVersion {protocol_version(schemas)}")
        return 0

    for s in schemas:
        print(f"  {s.struct:<11} regs {s.base:3d}..{s.end - 1:<3d} "
              f"({s.total} registers{' x %d' % s.parent_count if s.parent_count > 1 else ''})")
        for grp in s.group_names:
            g = s.groups[grp]
            print(f"    {grp:<5} {g['start_byte'] // 2:3d}.."
                  f"{g['start_byte'] // 2 + g['bytes'] // 2 - 1:<3d} "
                  f"({g['bytes'] // 2} registers)")
    print(f"  parent {max(s.end for s in schemas)} registers, "
          f"protocolVersion {protocol_version(schemas)}")
    for rel in rendered:
        print("  wrote " + rel)
    if fp_err:
        # Written anyway, so a layout can be iterated on; but it is not a green
        # run, and --check (CI) refuses the same state.
        print("genregs: " + fp_err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
