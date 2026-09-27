#!/usr/bin/env python3
"""Vendor and render the per-board pinout header from kicad-reflex's export.

    python tools/genpins.py vendor --board provvedo \\
        --kicad-reflex /path/to/kicad-reflex --rev <git-rev>
                                       pull pinout/<board>.json from kicad-reflex
                                       at <rev>, vendor it byte-identical into
                                       fw/boards/<board>/pins.json, write
                                       fw/boards/<board>/pins.lock, and render
                                       fw/boards/<board>/pins.h

    python tools/genpins.py render     re-render fw/boards/*/pins.h from what is
                                       already vendored (the default mode)

    python tools/genpins.py --check    for every fw/boards/<board>/, re-render in
                                       memory and diff against pins.h, and verify
                                       pins.json's sha256 matches pins.lock's;
                                       exit 1 on any drift. Needs no hardware repo
                                       -- this is what public CI runs.

    python tools/genpins.py --check-handwritten
                                       drift-check the hand-copied pins in
                                       fw/Core/Inc/Ramps.h, fw/Core/Inc/main.h,
                                       fw/Core/Src/tim.c and fw/Core/Src/usart.c
                                       against fw/boards/provvedo/pins.json.
                                       provvedo only. RETIRES once Ramps.h starts
                                       consuming pins.h directly -- the per-board
                                       build -- because at
                                       that point a hand-copy drift is impossible
                                       by construction and this becomes a check
                                       for a thing that can no longer happen.

    python tools/genpins.py --check-upstream [--kicad-reflex PATH]
                                       [--require-upstream] [--head main]
                                       verify the vendored pins.json still equals
                                       kicad-reflex at the commit pins.lock names
                                       (red if not -- someone hand-edited the
                                       vendored copy, or re-vendored without
                                       updating the lock), then WARN (not fail)
                                       if the design at --head has moved since.
                                       Never run in CI: kicad-reflex is private,
                                       reflex is public. The hardware repo is
                                       --kicad-repo PATH if given, else
                                       $KICAD_REFLEX_REPO, else a sibling
                                       checkout at ../kicad/reflex; set
                                       KICAD_REFLEX_REPO when that sibling
                                       checkout is not found. SKIP (exit 0) if
                                       none is a git repo.

WHY A VENDORED COPY, NOT A LIVE READ. fw/boards/<board>/pins.json is a byte-exact
copy of kicad-reflex's export, not a reference to it, so this repo's tree is
self-contained: --check needs no access to a private repo, and the firmware
build depends on nothing outside itself. pins.lock is what ties the copy back to
the design commit it came from, and --check-upstream is the (deliberately
optional, deliberately non-CI) check that the copy has not drifted from it.

WHY THIS EXISTS. Firmware pins were hand-copied into
Ramps.h, main.h and the CubeMX MSP init in tim.c/usart.c -- the same shape of
problem genregs.py solved for the register map. The board design is now the one
source of truth; this generator carries it into firmware; --check-handwritten is
the contract test that goes red on drift until the per-board build
makes Ramps.h consume fw/boards/<board>/pins.h directly, at which point
--check-handwritten retires -- there will be nothing left to hand-copy.

NAMING. The generated macros are BOARD_<NET>_PORT / _PIN / _PIN_NUM, deliberately
never colliding with the CubeMX/Ramps.h spellings (STEP_PIN, USR_LED_Pin, ...)
that still exist today -- this header is not included by anything yet, and must
not be confused for the thing it will eventually replace.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = "reflex-pinout/1"

# MCU part prefix -> CMSIS device macro. Small and explicit on purpose: an
# unmapped part gets a comment in the generated header, never a guessed guard
# (see device_guard_block). Extend this when a new board's MCU ships.
PART_TO_CMSIS_MACRO = [
    ("STM32F411CE", "STM32F411xE"),
    ("STM32F413", "STM32F413xx"),
]

# Hand-written pin macro name -> net name, for --check-handwritten. Explicit:
# an X found in Ramps.h/main.h that is not a key here is a failure ("unmapped
# hand-written pin"), not a silent skip.
HANDWRITTEN_NET_MAP = {
    "STEP": "MTR_STEP",
    "DIR": "MTR_DIR",
    "ENA": "MTR_ENA",
    "USR_LED": "USR_LED",
    "SPARE_1": "SPARE_1",
    "SPARE_2": "SPARE_2",
    "SPARE_3": "SPARE_3",
    "SPARE_4": "SPARE_4",
}

# CubeMX MSP GPIO Configuration signal -> net, for provvedo only. Explicit for
# the same reason: an MSP line naming a signal not in this table is a failure.
PROVVEDO_MSP_SIGNAL_NET = {
    "TIM1_CH1": "ENC1A", "TIM1_CH2": "ENC1B",
    "TIM2_CH1": "ENC2A", "TIM2_CH2": "ENC2B",
    "TIM3_CH1": "ENC3A", "TIM3_CH2": "ENC3B",
    "TIM4_CH1": "ENC4A", "TIM4_CH2": "ENC4B",
    "USART1_RX": "RXD", "USART1_TX": "TXD",
}


class GenError(Exception):
    """A refusal to emit -- caught at the top level and turned into exit 2."""


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------

def net_to_ident(net):
    """Net name -> C identifier: TMS/SWDIO -> TMS_SWDIO."""
    ident = re.sub(r"[^A-Za-z0-9_]", "_", net.upper())
    ident = re.sub(r"_+", "_", ident)
    return ident.strip("_")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def part_to_macro(part):
    for prefix, macro in PART_TO_CMSIS_MACRO:
        if part.startswith(prefix):
            return macro
    return None


# ---------------------------------------------------------------------------
# git helpers -- all `git -C <path>`, which works against a normal checkout
# and a bare repo alike.
# ---------------------------------------------------------------------------

def git_is_repo(path) -> bool:
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--git-dir"],
                        capture_output=True, text=True)
    return r.returncode == 0


def git_resolve_sha(path, rev) -> str:
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--verify", f"{rev}^{{commit}}"],
                        capture_output=True, text=True)
    if r.returncode != 0:
        raise GenError(f"git -C {path} rev-parse {rev}: {r.stderr.strip()}")
    return r.stdout.strip()


def git_show_bytes(path, sha, rel):
    r = subprocess.run(["git", "-C", str(path), "show", f"{sha}:{rel}"], capture_output=True)
    if r.returncode != 0:
        raise GenError(f"git -C {path} show {sha}:{rel}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def git_commit_date(path, sha) -> str:
    r = subprocess.run(["git", "-C", str(path), "show", "-s", "--format=%cs", sha],
                        capture_output=True, text=True)
    if r.returncode != 0:
        raise GenError(f"git -C {path} show -s --format=%cs {sha}: {r.stderr.strip()}")
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate_named_gpio(named_gpio):
    """Refuse if a net sits on two GPIO pins, or two nets collide on one identifier."""
    by_net = {}
    for p in named_gpio:
        by_net.setdefault(p["net"], []).append(p)
    errs = []
    for net, plist in by_net.items():
        if len(plist) > 1:
            nums = ", ".join(p["number"] for p in plist)
            errs.append(f"net {net!r} is named on {len(plist)} GPIO pins (numbers {nums})")
    by_ident = {}
    for net in by_net:
        by_ident.setdefault(net_to_ident(net), []).append(net)
    for ident, nets in by_ident.items():
        if len(nets) > 1:
            errs.append(f"nets {nets} all normalize to identifier {ident}")
    if errs:
        raise GenError("; ".join(errs))


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def device_guard_block(part, board):
    macro = part_to_macro(part)
    if macro is None:
        return [
            f"/* Device check: MCU part {part!r} is not in genpins.py's",
            " * PART_TO_CMSIS_MACRO dict, so no cross-build device guard is emitted",
            " * here. Add it there if this board ships. */",
        ]
    L = [
        f"/* Device check: board {board!r} has MCU part {part}, CMSIS device",
        f" * macro {macro}. If a cross build defines a DIFFERENT device macro this",
        " * generator knows about, it is compiling THIS board's pins for the wrong",
        " * chip -- fail loudly rather than silently miswiring GPIOs. The emulator",
        " * build always defines STM32F411xE for every board",
        " * (fw/emulator/CMakeLists.txt) -- the same macro provvedo expects -- so",
        " * it can never trip this guard. An MCU part this generator has not been",
        " * taught (see PART_TO_CMSIS_MACRO) gets a comment instead of a guard, so",
        " * it cannot misfire on a device it does not recognise. */",
        f"#if defined({macro})",
        f"/* OK: matches board part {part} */",
    ]
    for _, other in PART_TO_CMSIS_MACRO:
        if other == macro:
            continue
        L.append(f"#elif defined({other})")
        L.append(f'#error "pins.h: built for {other}, but board {board!r} is {part} '
                  f'(expects {macro}) -- wrong pins.h for this cross build"')
    L.append("#endif")
    return L


def render_pins_h(obj, lock) -> str:
    board = obj["board"]
    mcu = obj["mcu"]
    source = obj["source"]
    pins = obj["pins"]

    named_gpio = [p for p in pins if p.get("status") == "named" and "gpio" in p]
    unconnected_gpio = [p for p in pins if p.get("status") == "unconnected" and "gpio" in p]
    unnamed_gpio = [p for p in pins if p.get("status") == "unnamed" and "gpio" in p]

    validate_named_gpio(named_gpio)

    named_gpio = sorted(named_gpio, key=lambda p: int(p["number"]))
    unconnected_gpio = sorted(unconnected_gpio, key=lambda p: int(p["number"]))
    unnamed_gpio = sorted(unnamed_gpio, key=lambda p: int(p["number"]))

    entries = [(net_to_ident(p["net"]), p) for p in named_gpio]
    guard = f"REFLEX_BOARD_{board.upper()}_PINS_H"

    L = [
        "/* GENERATED by tools/genpins.py from the board's design export -- DO NOT EDIT.",
        " *",
        " * Regenerate:      python3 tools/genpins.py render",
        " * Re-vendor from a new kicad-reflex commit:",
        f" *   python3 tools/genpins.py vendor --board {board} "
        "--kicad-reflex <path-to-kicad-reflex> --rev <rev>",
        " *",
        f" * from kicad-reflex {lock['commit'][:12]}, {lock['date']} ({lock['path']})",
        " *",
        f" * Design source: {source['reader']} reader, {source['path']}, "
        f"sha256 {source['sha256'][:16]}...",
        f" * MCU: {mcu['ref']}, {mcu['part']}",
        " *",
        " * Nothing includes this header yet. The per-board firmware build",
        " * is what wires Ramps.h to consume it; until then this file",
        " * exists so tools/genpins.py --check-handwritten can hold the hand-copied",
        " * pins in fw/Core/Inc/Ramps.h, fw/Core/Inc/main.h, fw/Core/Src/tim.c and",
        " * fw/Core/Src/usart.c to what the design actually says.",
        " */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        f'#define BOARD_NAME "{board}"',
        f'#define BOARD_MCU_PART "{mcu["part"]}"',
        "",
    ]
    L.extend(device_guard_block(mcu["part"], board))
    L.append("")
    L.append("/* ---- named GPIO nets, in pin-number order ---- */")
    L.append("")

    if entries:
        width = max(len(f"BOARD_{ident}_PIN_NUM") for ident, _ in entries)
        for ident, p in entries:
            gpio = p["gpio"]
            port, bit = gpio["port"], gpio["bit"]
            L.append(f"/* {p['net']} -- pin {p['number']}, P{port}{bit} */")
            for name, value in (
                (f"BOARD_{ident}_PORT", f"GPIO{port}"),
                (f"BOARD_{ident}_PIN", f"GPIO_PIN_{bit}"),
                (f"BOARD_{ident}_PIN_NUM", f"{bit}U"),
            ):
                L.append(f"#define {name:<{width}} {value}")
            L.append("")
    else:
        L.append("/* (no named GPIO nets) */")
        L.append("")

    def names(plist):
        return ", ".join(f"P{p['gpio']['port']}{p['gpio']['bit']}" for p in plist) or "(none)"

    L.append(f"/* Unconnected GPIOs (present, not brought out to a net): {names(unconnected_gpio)} */")
    L.append(f"/* Unnamed GPIOs (present, no net name assigned yet):     {names(unnamed_gpio)} */")
    L.append("")
    L.append(f"#endif /* {guard} */")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# vendor / render
# ---------------------------------------------------------------------------

def cmd_vendor(root: Path, board: str, kicad_reflex: str, rev: str):
    sha = git_resolve_sha(kicad_reflex, rev)
    rel = f"pinout/{board}.json"
    raw = git_show_bytes(kicad_reflex, sha, rel)
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise GenError(f"{rel}@{sha}: not valid JSON ({e})")
    if obj.get("schema") != SCHEMA:
        raise GenError(f"{rel}@{sha}: schema is {obj.get('schema')!r}, expected {SCHEMA!r}")
    if obj.get("board") != board:
        raise GenError(f"{rel}@{sha}: board is {obj.get('board')!r}, expected {board!r}")

    date = git_commit_date(kicad_reflex, sha)
    lock = {
        "repo": "kicad-reflex",
        "commit": sha,
        "date": date,
        "path": rel,
        "sha256": sha256_hex(raw),
    }

    board_dir = root / "fw" / "boards" / board
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "pins.json").write_bytes(raw)
    io.open(board_dir / "pins.lock", "w", encoding="utf-8", newline="\n").write(
        json.dumps(lock, indent=2) + "\n")

    text = render_pins_h(obj, lock)
    io.open(board_dir / "pins.h", "w", encoding="utf-8", newline="\n").write(text)

    print(f"genpins vendor: {board} <- kicad-reflex {sha[:12]} ({date}) -- "
          f"wrote fw/boards/{board}/{{pins.json,pins.lock,pins.h}}")


def _load_board(board_dir: Path):
    raw = (board_dir / "pins.json").read_bytes()
    obj = json.loads(raw.decode("utf-8"))
    lock = json.loads((board_dir / "pins.lock").read_text(encoding="utf-8"))
    return raw, obj, lock


def cmd_render(root: Path):
    boards_dir = root / "fw" / "boards"
    if not boards_dir.is_dir() or not any(boards_dir.iterdir()):
        print("genpins render: no boards vendored under fw/boards/ -- nothing to render")
        return
    for board_dir in sorted(p for p in boards_dir.iterdir() if p.is_dir()):
        board = board_dir.name
        if not (board_dir / "pins.json").exists() or not (board_dir / "pins.lock").exists():
            raise GenError(f"fw/boards/{board}: pins.json and pins.lock must both exist "
                           f"before rendering -- vendor it first")
        _, obj, lock = _load_board(board_dir)
        if obj.get("board") != board:
            raise GenError(f"fw/boards/{board}/pins.json says board {obj.get('board')!r}")
        text = render_pins_h(obj, lock)
        io.open(board_dir / "pins.h", "w", encoding="utf-8", newline="\n").write(text)
        print(f"genpins render: wrote fw/boards/{board}/pins.h")


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------

def cmd_check(root: Path) -> int:
    boards_dir = root / "fw" / "boards"
    if not boards_dir.is_dir() or not any(boards_dir.iterdir()):
        print("genpins --check: no boards vendored under fw/boards/ -- nothing to check")
        return 0

    problems = []
    for board_dir in sorted(p for p in boards_dir.iterdir() if p.is_dir()):
        board = board_dir.name
        json_path, lock_path, hdr_path = (board_dir / n for n in ("pins.json", "pins.lock", "pins.h"))
        if not json_path.exists():
            problems.append(f"fw/boards/{board}/pins.json: missing")
            continue
        if not lock_path.exists():
            problems.append(f"fw/boards/{board}/pins.lock: missing")
            continue
        raw = json_path.read_bytes()
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            problems.append(f"fw/boards/{board}/pins.json: invalid JSON ({e})")
            continue
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"fw/boards/{board}/pins.lock: invalid JSON ({e})")
            continue

        have_sha = sha256_hex(raw)
        want_sha = lock.get("sha256")
        if have_sha != want_sha:
            problems.append(
                f"fw/boards/{board}/pins.json: sha256 {have_sha[:16]}... does not match "
                f"pins.lock ({(want_sha or '?')[:16]}...) -- re-vendor")

        if not hdr_path.exists():
            problems.append(f"fw/boards/{board}/pins.h: missing")
            continue
        try:
            want_text = render_pins_h(obj, lock)
        except GenError as e:
            problems.append(f"fw/boards/{board}: REFUSING TO EMIT -- {e}")
            continue
        have_text = io.open(hdr_path, encoding="utf-8").read()
        if have_text != want_text:
            problems.append(f"fw/boards/{board}/pins.h: differs from pins.json + pins.lock "
                            f"-- regenerate with `python tools/genpins.py render`")

    if problems:
        print("genpins --check FAILED:")
        for p in problems:
            print("  - " + p)
        return 1
    print("genpins --check: every fw/boards/*/pins.h matches its pins.json + pins.lock")
    return 0


# ---------------------------------------------------------------------------
# --check-handwritten (provvedo only; retires with the per-board build)
# ---------------------------------------------------------------------------

PIN_DEFINE_RE = re.compile(r"^#define\s+(\w+)_(?:PIN|Pin)\s+GPIO_PIN_(\d+)\s*$", re.M)
PORT_DEFINE_RE = re.compile(r"^#define\s+(\w+)_(?:GPIO_PORT|GPIO_Port)\s+GPIO([A-Z])\s*$", re.M)
MSP_BLOCK_RE = re.compile(r"/\*\*(?:TIM\d|USART\d) GPIO Configuration\n((?:.*\n)+?)\s*\*/")
MSP_LINE_RE = re.compile(r"P([A-Z])(\d+)\s*-+>\s*(\w+)")


def _line_no(text, pos):
    return text.count("\n", 0, pos) + 1


def parse_handwritten_pins(path: Path):
    """{name: {"pin": (line, bit), "port": (line, port_letter)}} for one file."""
    text = io.open(path, encoding="utf-8").read()
    out = {}
    for m in PIN_DEFINE_RE.finditer(text):
        out.setdefault(m.group(1), {})["pin"] = (_line_no(text, m.start()), int(m.group(2)))
    for m in PORT_DEFINE_RE.finditer(text):
        out.setdefault(m.group(1), {})["port"] = (_line_no(text, m.start()), m.group(2))
    return out


def parse_msp_signals(path: Path):
    """[(line, port_letter, bit, signal)] for every MSP GPIO Configuration line."""
    text = io.open(path, encoding="utf-8").read()
    out = []
    for bm in MSP_BLOCK_RE.finditer(text):
        block, base = bm.group(1), bm.start(1)
        for lm in MSP_LINE_RE.finditer(block):
            out.append((_line_no(text, base + lm.start()), lm.group(1), int(lm.group(2)), lm.group(3)))
    return out


def cmd_check_handwritten(root: Path) -> int:
    board = "provvedo"
    board_dir = root / "fw" / "boards" / board
    json_path = board_dir / "pins.json"
    if not json_path.exists():
        print(f"genpins --check-handwritten: fw/boards/{board}/pins.json is missing "
              f"-- vendor {board} first")
        return 1
    obj = json.loads(json_path.read_bytes().decode("utf-8"))
    by_net = {p["net"]: p["gpio"] for p in obj["pins"]
              if p.get("status") == "named" and "gpio" in p}

    problems = []

    ramps_h = root / "fw" / "Core" / "Inc" / "Ramps.h"
    main_h = root / "fw" / "Core" / "Inc" / "main.h"
    for path in (ramps_h, main_h):
        if not path.exists():
            problems.append(f"{path}: missing")
            continue
        for name, info in parse_handwritten_pins(path).items():
            if name not in HANDWRITTEN_NET_MAP:
                ln = (info.get("pin") or info.get("port"))[0]
                problems.append(f"{path.name}:{ln}: unmapped hand-written pin {name} "
                                f"(add it to HANDWRITTEN_NET_MAP in tools/genpins.py, or remove it)")
                continue
            if "pin" not in info or "port" not in info:
                ln = (info.get("pin") or info.get("port"))[0]
                problems.append(f"{path.name}:{ln}: {name} has only half a pin definition "
                                f"(need both _PIN/_Pin and _GPIO_PORT/_GPIO_Port)")
                continue
            net = HANDWRITTEN_NET_MAP[name]
            exp = by_net.get(net)
            if exp is None:
                problems.append(f"{path.name}: {name} maps to net {net!r}, which is not a "
                                f"named GPIO in fw/boards/{board}/pins.json")
                continue
            pin_line, bit = info["pin"]
            _, port = info["port"]
            if bit != exp["bit"] or port != exp["port"]:
                problems.append(
                    f"{path.name}:{pin_line}: {name} is P{port}{bit}, export says "
                    f"P{exp['port']}{exp['bit']} for net {net}")

    tim_c = root / "fw" / "Core" / "Src" / "tim.c"
    usart_c = root / "fw" / "Core" / "Src" / "usart.c"
    seen_signals = set()
    for path in (tim_c, usart_c):
        if not path.exists():
            problems.append(f"{path}: missing")
            continue
        for ln, port, bit, signal in parse_msp_signals(path):
            seen_signals.add(signal)
            net = PROVVEDO_MSP_SIGNAL_NET.get(signal)
            if net is None:
                problems.append(f"{path.name}:{ln}: MSP signal {signal} is not in "
                                f"PROVVEDO_MSP_SIGNAL_NET (tools/genpins.py)")
                continue
            exp = by_net.get(net)
            if exp is None:
                problems.append(f"{path.name}:{ln}: MSP signal {signal} maps to net {net!r}, "
                                f"which is not a named GPIO in fw/boards/{board}/pins.json")
                continue
            if port != exp["port"] or bit != exp["bit"]:
                problems.append(f"{path.name}:{ln}: {signal} is on P{port}{bit}, export says "
                                f"P{exp['port']}{exp['bit']} for net {net}")

    for signal, net in PROVVEDO_MSP_SIGNAL_NET.items():
        if signal not in seen_signals:
            problems.append(f"tim.c/usart.c: MSP signal {signal} (net {net}) never appears in "
                            f"a GPIO Configuration comment block")

    if problems:
        print("genpins --check-handwritten FAILED:")
        for p in problems:
            print("  DRIFT: " + p)
        return 1
    print(f"genpins --check-handwritten: OK -- hand-written pins in Ramps.h, main.h, tim.c "
          f"and usart.c all match fw/boards/{board}/pins.json")
    return 0


# ---------------------------------------------------------------------------
# --check-upstream (never run in CI -- kicad-reflex is private)
# ---------------------------------------------------------------------------

def find_hw_repo(root: Path, explicit):
    """Return (path, tried) -- path is None if nothing in the search order is a git repo."""
    if explicit:
        candidates = [str(explicit)]
    else:
        candidates = []
        env = os.environ.get("KICAD_REFLEX_REPO")
        if env:
            candidates.append(env)
        candidates.append(str(root / ".." / "kicad" / "reflex"))
    tried = []
    for c in candidates:
        tried.append(c)
        if git_is_repo(c):
            return c, tried
    return None, tried


def cmd_check_upstream(root: Path, kicad_reflex, require_upstream: bool, head: str) -> int:
    hw_repo, tried = find_hw_repo(root, kicad_reflex)
    if hw_repo is None:
        print("SKIP upstream: no hardware repo reachable (tried " + ", ".join(tried) + "); "
              "set KICAD_REFLEX_REPO, or pass --kicad-repo, to a kicad-reflex checkout "
              "or bare repo")
        return 2 if require_upstream else 0

    boards_dir = root / "fw" / "boards"
    if not boards_dir.is_dir() or not any(boards_dir.iterdir()):
        print(f"genpins --check-upstream: no boards vendored -- nothing to check (using {hw_repo})")
        return 0

    failed = False
    for board_dir in sorted(p for p in boards_dir.iterdir() if p.is_dir()):
        board = board_dir.name
        lock_path, json_path = board_dir / "pins.lock", board_dir / "pins.json"
        if not lock_path.exists() or not json_path.exists():
            print(f"FAIL upstream {board}: pins.json/pins.lock missing")
            failed = True
            continue
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        vendored = json_path.read_bytes()

        r = subprocess.run(["git", "-C", hw_repo, "show", f"{lock['commit']}:{lock['path']}"],
                           capture_output=True)
        if r.returncode != 0:
            print(f"FAIL upstream {board}: {lock['path']} at {lock['commit'][:12]} not found "
                 f"in {hw_repo}")
            failed = True
            continue
        if r.stdout != vendored:
            print(f"FAIL upstream {board}: fw/boards/{board}/pins.json does not byte-for-byte "
                 f"match kicad-reflex {lock['commit'][:12]}:{lock['path']}")
            failed = True
            continue
        print(f"OK upstream {board}: matches kicad-reflex {lock['commit'][:12]}:{lock['path']}")

        rh = subprocess.run(["git", "-C", hw_repo, "rev-parse", "--verify", f"{head}^{{commit}}"],
                            capture_output=True, text=True)
        if rh.returncode != 0:
            print(f"NOTE upstream {board}: ref {head!r} does not exist in {hw_repo} -- "
                 f"skipping the moved-since check")
            continue
        head_sha = rh.stdout.strip()
        rp = subprocess.run(["git", "-C", hw_repo, "show", f"{head_sha}:{lock['path']}"],
                            capture_output=True)
        if rp.returncode != 0:
            print(f"NOTE upstream {board}: {lock['path']} does not exist at {head} "
                 f"({head_sha[:12]}) in {hw_repo} -- skipping the moved-since check")
            continue
        try:
            head_obj = json.loads(rp.stdout.decode("utf-8"))
            vendored_obj = json.loads(vendored.decode("utf-8"))
        except Exception as e:
            print(f"NOTE upstream {board}: could not parse JSON to compare ({e}) -- "
                 f"skipping the moved-since check")
            continue
        by_number = {p["number"]: p for p in head_obj.get("pins", [])}
        diffs = []
        for p in vendored_obj.get("pins", []):
            hp = by_number.get(p["number"])
            if hp is None:
                continue
            if hp.get("net") != p.get("net") or hp.get("status") != p.get("status"):
                diffs.append(f"pin {p['number']} ({p.get('name')}): vendored net={p.get('net')!r} "
                             f"status={p.get('status')!r}, {head} has net={hp.get('net')!r} "
                             f"status={hp.get('status')!r}")
        if diffs:
            print(f"WARNING upstream {board}: hardware has moved since this pin was vendored -- "
                 f"{len(diffs)} pin(s) differ at {head} ({head_sha[:12]}):")
            for d in diffs:
                print("    " + d)

    return 1 if failed else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", choices=["vendor", "render"], default="render",
                    help="vendor: pull + vendor + render one board. render (default): "
                         "re-render fw/boards/*/pins.h from what is already vendored.")
    ap.add_argument("--root", default=str(ROOT), help="repo root (default: this checkout)")
    ap.add_argument("--board", help="board name, e.g. provvedo (vendor)")
    ap.add_argument("--kicad-repo", "--kicad-reflex", dest="kicad_reflex",
                    help="path to the kicad-reflex checkout or bare repo (default: "
                         "$KICAD_REFLEX_REPO, then ../kicad/reflex beside this checkout)")
    ap.add_argument("--rev", help="git rev in kicad-reflex to vendor from (vendor)")
    ap.add_argument("--check", action="store_true",
                    help="diff fw/boards/*/pins.h against pins.json+pins.lock; exit 1 on drift")
    ap.add_argument("--check-handwritten", action="store_true", dest="check_handwritten",
                    help="drift-check hand-copied pins in Ramps.h/main.h/tim.c/usart.c "
                         "against fw/boards/provvedo/pins.json (provvedo only)")
    ap.add_argument("--check-upstream", action="store_true", dest="check_upstream",
                    help="verify the vendored copy against kicad-reflex; never run in CI")
    ap.add_argument("--require-upstream", action="store_true", dest="require_upstream",
                    help="with --check-upstream, exit 2 instead of 0 when no hardware repo "
                         "is reachable")
    ap.add_argument("--head", default="main",
                    help="ref to compare against for the moved-since warning (default: main)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()

    if args.check_handwritten:
        return cmd_check_handwritten(root)
    if args.check_upstream:
        return cmd_check_upstream(root, args.kicad_reflex, args.require_upstream, args.head)
    if args.check:
        return cmd_check(root)

    try:
        if args.mode == "vendor":
            if not (args.board and args.kicad_reflex and args.rev):
                ap.error("vendor needs --board, --kicad-reflex and --rev")
            cmd_vendor(root, args.board, args.kicad_reflex, args.rev)
        else:
            cmd_render(root)
    except GenError as e:
        print(f"genpins: REFUSING TO EMIT -- {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
