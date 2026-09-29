#!/usr/bin/env python3
"""Seen-red check for tools/genpins.py's contract tests.

Every mutation below runs against a TEMP COPY of the files genpins.py needs
(via its --root option), never the working tree, and each must flip its check
from green to red -- a check that cannot go red has not been proven to work.

  (a) clean tree: --check, --check-handwritten and --check-upstream (against a
      throwaway git repo built for this run) are all green.
  (b) hand-edit a BOARD_* define in fw/boards/provvedo/pins.h without touching
      pins.json -> --check red (the rendered header no longer matches).
  (c) hand-edit fw/boards/provvedo/pins.json without re-vendoring (pins.lock's
      sha256 now stale) -> --check red.
  (d) --check-upstream: green against a throwaway hardware repo whose HEAD is
      what pins.lock names; red once the vendored pins.json is edited out from
      under it; SKIP (exit 0) when no hardware repo is reachable, exit 2 with
      --require-upstream.
  (e) --check-handwritten: Ramps.h's STEP_PIN edited to the wrong GPIO_PIN_n;
      tim.c's TIM2_CH1 MSP line edited to the wrong port; an extra hand-written
      pin macro with no HANDWRITTEN_NET_MAP entry. Each must go red naming the
      field/signal that moved (or, for the last one, naming it unmapped).
  (f) the device-check guard in pins.h: compiling with the WRONG CMSIS device
      macro must fail naming the mismatch; the correct one must compile clean.
      Also proves the guard cannot break the emulator, which always defines
      the same macro provvedo expects (fw/emulator/CMakeLists.txt).

(f) and the positive control it depends on need a C compiler. Preference order,
same as tools/test_generated_layout_seen_red.py: $ARM_GCC or arm-none-eabi-gcc
on PATH, else host gcc/cc against the real HAL (host gcc agrees with the cross
ABI for these scalar/pointer macros even though it is not what ships), else gcc
with stand-in GPIOx/GPIO_PIN_n defines instead of the real HAL. If literally no
C compiler exists, those cases SKIP LOUDLY -- printed, not silently green -- and
do not count as pass; every other case still runs, since it needs no compiler.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
GENPINS = ROOT / "tools" / "genpins.py"
BOARD = "provvedo"

sys.path.insert(0, str(ROOT / "tools"))
import genpins  # noqa: E402  -- reuse net_to_ident, the single source of truth

NEEDED = [
    f"fw/boards/{BOARD}/pins.json",
    f"fw/boards/{BOARD}/pins.lock",
    f"fw/boards/{BOARD}/pins.h",
    "fw/Core/Inc/Ramps.h",
    "fw/Core/Inc/main.h",
    "fw/Core/Src/tim.c",
    "fw/Core/Src/usart.c",
]

results = []  # list of "pass" | "fail" | "skip"


def record(status, label, detail=""):
    tag = {"pass": "[PASS]", "fail": "[FAIL]", "skip": "[SKIP]"}[status]
    print(f"{tag} {label}")
    if detail and status != "pass":
        for line in detail.strip().splitlines()[:12]:
            print("       " + line)
    results.append(status)


# ---------------------------------------------------------------------------
# temp-tree plumbing
# ---------------------------------------------------------------------------

def fresh_root():
    tmp = Path(tempfile.mkdtemp(prefix="genpins_red_"))
    for rel in NEEDED:
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dst)
    return tmp


def run_genpins(root, *args):
    return subprocess.run([sys.executable, str(GENPINS), *args, "--root", str(root)],
                          capture_output=True, text=True)


def make_hw_repo(files: dict) -> tuple[Path, str]:
    """A throwaway git repo (files: {relpath: bytes}), committed once. Returns (path, HEAD sha)."""
    d = Path(tempfile.mkdtemp(prefix="genpins_hw_"))
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    for rel, data in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
    # A CI runner has no git identity, and a developer's may sign commits: the
    # fixture carries its own, and touches no real repo's config.
    fixture_env = dict(os.environ, GIT_AUTHOR_NAME="genpins seen-red", GIT_AUTHOR_EMAIL="seen-red@invalid",
                       GIT_COMMITTER_NAME="genpins seen-red", GIT_COMMITTER_EMAIL="seen-red@invalid")
    subprocess.run(["git", "-C", str(d), "-c", "commit.gpgsign=false", "commit", "-q", "-m",
                    "genpins seen-red fixture"], check=True, env=fixture_env)
    sha = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return d, sha


def make_root_pointed_at(hw_repo: Path, hw_sha: str) -> Path:
    """A fresh temp root whose pins.lock names hw_repo/hw_sha, re-rendered so
    pins.h stays consistent with the edited lock (the banner names the commit)."""
    root = fresh_root()
    lock_path = root / f"fw/boards/{BOARD}/pins.lock"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["commit"] = hw_sha
    lock["path"] = f"pinout/{BOARD}.json"
    io.open(lock_path, "w", encoding="utf-8", newline="\n").write(json.dumps(lock, indent=2) + "\n")
    r = run_genpins(root, "render")
    if r.returncode != 0:
        sys.exit("SETUP FAILED: could not re-render after pointing pins.lock at the "
                 "throwaway hw repo:\n" + r.stdout + r.stderr)
    return root


# ---------------------------------------------------------------------------
# (a)-(e): file-based mutations against genpins.py --check / --check-handwritten
#          / --check-upstream. Need no compiler.
# ---------------------------------------------------------------------------

pins_json_bytes = (ROOT / f"fw/boards/{BOARD}/pins.json").read_bytes()
hw_repo, hw_sha = make_hw_repo({f"pinout/{BOARD}.json": pins_json_bytes})

# (a) clean tree
root = make_root_pointed_at(hw_repo, hw_sha)
r = run_genpins(root, "--check")
record("pass" if r.returncode == 0 else "fail", "(a) clean tree: --check green", r.stdout + r.stderr)
r = run_genpins(root, "--check-handwritten")
record("pass" if r.returncode == 0 else "fail", "(a) clean tree: --check-handwritten green",
      r.stdout + r.stderr)
r = run_genpins(root, "--check-upstream", "--kicad-reflex", str(hw_repo))
record("pass" if r.returncode == 0 else "fail", "(a) clean tree: --check-upstream green",
      r.stdout + r.stderr)

# (b) hand-edit a BOARD_* define in pins.h -> --check red
root = make_root_pointed_at(hw_repo, hw_sha)
hdr = root / f"fw/boards/{BOARD}/pins.h"
text = hdr.read_text(encoding="utf-8")
mut, n = re.subn(r"(#define BOARD_MTR_STEP_PIN\s+)GPIO_PIN_0\b", r"\g<1>GPIO_PIN_3", text, count=1)
if n != 1:
    sys.exit("(b) ANCHOR FAILED: BOARD_MTR_STEP_PIN GPIO_PIN_0 not found in pins.h")
hdr.write_text(mut, encoding="utf-8")
r = run_genpins(root, "--check")
record("pass" if (r.returncode == 1 and "pins.h" in r.stdout) else "fail",
      "(b) hand-edit BOARD_MTR_STEP_PIN in pins.h -> --check red", r.stdout + r.stderr)

# (c) edit pins.json without re-vendoring -> --check red (sha256)
root = make_root_pointed_at(hw_repo, hw_sha)
json_path = root / f"fw/boards/{BOARD}/pins.json"
raw = json_path.read_bytes()
mut, n = re.subn(rb'"MTR_STEP"', b'"MTR_STEPX"', raw, count=1)
if n != 1:
    sys.exit("(c) ANCHOR FAILED: \"MTR_STEP\" not found in pins.json")
json_path.write_bytes(mut)
r = run_genpins(root, "--check")
record("pass" if (r.returncode == 1 and "sha256" in r.stdout) else "fail",
      "(c) edit pins.json without re-vendoring -> --check red (sha256)", r.stdout + r.stderr)

# (d) --check-upstream: green against the throwaway repo (already shown in (a)); now
# red once the vendored copy is edited out from under it; then the two "no repo" paths.
root = make_root_pointed_at(hw_repo, hw_sha)
json_path = root / f"fw/boards/{BOARD}/pins.json"
raw = json_path.read_bytes()
mut, n = re.subn(rb'"MTR_STEP"', b'"MTR_STEPX"', raw, count=1)
if n != 1:
    sys.exit("(d) ANCHOR FAILED: \"MTR_STEP\" not found in pins.json")
json_path.write_bytes(mut)
r = run_genpins(root, "--check-upstream", "--kicad-reflex", str(hw_repo))
record("pass" if (r.returncode == 1 and "FAIL upstream" in r.stdout) else "fail",
      "(d) edit vendored pins.json -> --check-upstream red", r.stdout + r.stderr)

root = make_root_pointed_at(hw_repo, hw_sha)
no_such = root / "no-such-hw-repo"
r = run_genpins(root, "--check-upstream", "--kicad-reflex", str(no_such))
record("pass" if (r.returncode == 0 and r.stdout.strip().startswith("SKIP upstream:")) else "fail",
      "(d) missing hardware repo -> SKIP line, exit 0", r.stdout + r.stderr)
r = run_genpins(root, "--check-upstream", "--kicad-reflex", str(no_such), "--require-upstream")
record("pass" if r.returncode == 2 else "fail",
      "(d) missing hardware repo + --require-upstream -> exit 2", r.stdout + r.stderr)

# (e) --check-handwritten drift
root = make_root_pointed_at(hw_repo, hw_sha)
ramps_h = root / "fw/Core/Inc/Ramps.h"
text = ramps_h.read_text(encoding="utf-8")
mut, n = re.subn(r"#define\s+STEP_PIN\s+GPIO_PIN_0\b", "#define STEP_PIN GPIO_PIN_3", text, count=1)
if n != 1:
    sys.exit("(e1) ANCHOR FAILED: 'STEP_PIN GPIO_PIN_0' not found in Ramps.h")
ramps_h.write_text(mut, encoding="utf-8")
r = run_genpins(root, "--check-handwritten")
record("pass" if (r.returncode == 1 and "STEP" in r.stdout) else "fail",
      "(e1) Ramps.h STEP_PIN GPIO_PIN_0 -> _3: --check-handwritten red naming STEP",
      r.stdout + r.stderr)

root = make_root_pointed_at(hw_repo, hw_sha)
tim_c = root / "fw/Core/Src/tim.c"
text = tim_c.read_text(encoding="utf-8")
mut, n = re.subn(r"PA5(\s*-+>\s*)TIM2_CH1", r"PA4\g<1>TIM2_CH1", text, count=1)
if n != 1:
    sys.exit("(e2) ANCHOR FAILED: 'PA5 ------> TIM2_CH1' not found in tim.c")
tim_c.write_text(mut, encoding="utf-8")
r = run_genpins(root, "--check-handwritten")
record("pass" if (r.returncode == 1 and "TIM2_CH1" in r.stdout) else "fail",
      "(e2) tim.c TIM2_CH1 PA5 -> PA4: --check-handwritten red naming TIM2_CH1",
      r.stdout + r.stderr)

root = make_root_pointed_at(hw_repo, hw_sha)
main_h = root / "fw/Core/Inc/main.h"
with io.open(main_h, "a", encoding="utf-8", newline="\n") as f:
    f.write("\n#define FOO_PIN GPIO_PIN_2\n#define FOO_GPIO_PORT GPIOC\n")
r = run_genpins(root, "--check-handwritten")
record("pass" if (r.returncode == 1 and "unmapped" in r.stdout and "FOO" in r.stdout) else "fail",
      "(e3) extra hand-written FOO_PIN with no dict entry: --check-handwritten red as unmapped",
      r.stdout + r.stderr)


# ---------------------------------------------------------------------------
# (control) + (f): compile checks. Need a C compiler; SKIP loudly if none.
# ---------------------------------------------------------------------------

def find_compiler():
    gcc = os.environ.get("ARM_GCC") or shutil.which("arm-none-eabi-gcc")
    if gcc:
        return gcc, "arm-none-eabi-gcc (cross)", True
    host = shutil.which("gcc") or shutil.which("cc")
    if host:
        return host, "host gcc (no cross compiler on PATH)", False
    return None, None, False


GCC, COMPILER_TIER, IS_CROSS = find_compiler()

HAL_INCLUDES = [
    "fw/Core/Inc",
    "fw/Drivers/STM32F4xx_HAL_Driver/Inc",
    "fw/Drivers/STM32F4xx_HAL_Driver/Inc/Legacy",
    "fw/Drivers/CMSIS/Device/ST/STM32F4xx/Include",
    "fw/Drivers/CMSIS/Include",
]


def compile_tu(hal_root, board_dir, source_text, defines, use_hal):
    tmp = tempfile.mkdtemp(prefix="genpins_compile_")
    src = os.path.join(tmp, "tu.c")
    io.open(src, "w", encoding="utf-8", newline="\n").write(source_text)
    cmd = [GCC, "-std=c11"]
    if IS_CROSS:
        cmd += ["-mcpu=cortex-m4", "-mthumb"]
    cmd += ["-fsyntax-only", *defines]
    if use_hal:
        cmd += [f"-I{hal_root / p}" for p in HAL_INCLUDES]
    cmd += [f"-I{board_dir}", src]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stderr


def stub_prelude():
    lines = [f"#define GPIO{p} ((void*)0)" for p in "ABCDEFGH"]
    lines += [f"#define GPIO_PIN_{n} ({n}u)" for n in range(16)]
    return "\n".join(lines) + "\n"


def compile_control(hal_root, board_dir, main_body, defines):
    """arm-none-eabi-gcc / host gcc against real HAL; host gcc + stub defines
    only if the real-HAL compile fails under host gcc (tier 3, spec item 3)."""
    real_hal_src = '#include "stm32f4xx_hal.h"\n#include "pins.h"\n' + main_body
    ok, err = compile_tu(hal_root, board_dir, real_hal_src, defines, use_hal=True)
    if ok or IS_CROSS:
        return ok, err, "real HAL"
    stub_src = stub_prelude() + '#include "pins.h"\n' + main_body
    ok2, err2 = compile_tu(hal_root, board_dir, stub_src, [], use_hal=False)
    return ok2, err2, "stub defines -- real HAL failed under host gcc"


if GCC is None:
    record("skip", "(control) fw/boards/provvedo/pins.h compiles against real HAL",
          "no C compiler found on PATH (arm-none-eabi-gcc or gcc/cc) -- asserts NOT exercised")
    record("skip", "(f) device-check guard: wrong device macro -> compile error",
          "no C compiler found on PATH -- asserts NOT exercised")
else:
    real_board_dir = ROOT / "fw" / "boards" / BOARD
    pins_obj = json.loads((real_board_dir / "pins.json").read_bytes().decode("utf-8"))
    named = [p for p in pins_obj["pins"] if p.get("status") == "named" and "gpio" in p]
    idents = [genpins.net_to_ident(p["net"]) for p in named]
    body_lines = ["int main(void) {", "  unsigned long x = 0;"]
    for ident in idents:
        body_lines.append(f"  x += (unsigned long)BOARD_{ident}_PORT + BOARD_{ident}_PIN "
                          f"+ BOARD_{ident}_PIN_NUM;")
    body_lines += ["  return (int)x;", "}"]
    main_body = "\n".join(body_lines) + "\n"

    ok, err, hal_tier = compile_control(ROOT, real_board_dir, main_body,
                                        ["-DSTM32F411xE", "-DUSE_HAL_DRIVER"])
    record("pass" if ok else "fail",
          f"(control) fw/boards/{BOARD}/pins.h compiles against real HAL and uses every "
          f"BOARD_ macro [{COMPILER_TIER}, {hal_tier}]", err)

    # The emulator's own -D flags (fw/emulator/CMakeLists.txt): STM32F411xE always,
    # for every board. provvedo expects exactly that macro, so the guard cannot trip.
    ok, err, hal_tier = compile_control(ROOT, real_board_dir, main_body,
                                        ["-DSTM32F411xE", "-DUSE_HAL_DRIVER", "-DEMULATOR_BUILD"])
    record("pass" if ok else "fail",
          "(control) same pins.h compiles with the emulator's own -D flags "
          f"(cannot break the emulator) [{COMPILER_TIER}, {hal_tier}]", err)

    # (f) the device guard is pure preprocessor, so it needs no HAL at all -- compiled
    # standalone so a MISSING stm32f413xx.h device header (this repo ships only F411
    # device files) can never be mistaken for the guard itself firing.
    standalone_src = '#include "pins.h"\nint main(void) { return 0; }\n'
    ok_right, err_right = compile_tu(ROOT, real_board_dir, standalone_src,
                                     ["-DSTM32F411xE"], use_hal=False)
    record("pass" if ok_right else "fail",
          "(f) device guard standalone: correct macro STM32F411xE compiles clean", err_right)

    ok_wrong, err_wrong = compile_tu(ROOT, real_board_dir, standalone_src,
                                     ["-DSTM32F413xx"], use_hal=False)
    named_mismatch = "STM32F413xx" in err_wrong and "provvedo" in err_wrong
    if not ok_wrong and named_mismatch:
        record("pass", "(f) device guard standalone: wrong macro STM32F413xx -> "
              "compile error naming the mismatch")
    elif ok_wrong:
        record("fail", "(f) device guard standalone: wrong macro STM32F413xx -> "
              "compile error naming the mismatch",
              "compiled with NO error -- the device guard did not fire")
    else:
        record("fail", "(f) device guard standalone: wrong macro STM32F413xx -> "
              "compile error naming the mismatch",
              "compile failed but did not name the mismatch:\n" + err_wrong)


# ---------------------------------------------------------------------------
print()
fails = [s for s in results if s == "fail"]
skips = [s for s in results if s == "skip"]
if fails:
    print(f"AT LEAST ONE CHECK MISBEHAVED -- {len(fails)} of {len(results)} case(s) FAILED")
    sys.exit(1)
tail = f" ({len(skips)} skipped -- no C compiler)" if skips else ""
print(f"SEEN RED: all {len(results) - len(skips)} exercised case(s) behaved as expected{tail}")
sys.exit(0)
