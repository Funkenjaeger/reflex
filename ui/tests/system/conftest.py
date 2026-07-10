"""Fixtures for the emulator-backed system test suite.

Locates/builds the reflex-fw emulator (real firmware compiled against a
host-side physics model + PTY-based Modbus transport) and launches it as a
subprocess per test, in EMU_SCENARIO=serve mode.

See .hermes/plans/2026-07-09_emulator-backed-system-tests.md for the full
suite plan. This file implements Task 3: locate/build/launch fixtures.

WSL/Linux only -- the emulator's Modbus link is a PTY pair (/dev/pts/N),
which native Windows cannot open. `emulator_binary` skips cleanly when the
reflex-fw tree/binary is unavailable so the suite degrades gracefully
anywhere else.
"""

import os

# Kivy mock GL/window backends: the harness builds real dispatchers (which pull
# in Kivy) but has no UI. On WSLg a real SDL2/OpenGL context takes ~135 s to
# init and needs a display; mock backends make it ~instant. MUST be set before
# any kivy import selects a backend, so it lives at the very top of conftest.
os.environ.setdefault("KIVY_GL_BACKEND", "mock")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_NO_ARGS", "1")

import queue
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest

REFLEX_FW_DIR = Path(os.environ.get("REFLEX_FW_DIR", "/mnt/c/projects/embedded/reflex-fw"))
EMULATOR_DIR = REFLEX_FW_DIR / "emulator"
EMULATOR_BIN = EMULATOR_DIR / "build" / "lathe-emulator"

# Confirmed format: "Modbus serial: /dev/pts/N" (emulator/src/transport.cpp:57)
PTY_PATH_RE = re.compile(r"Modbus serial: (/dev/pts/\d+)")

# Firmware C sources the emulator links against (in addition to everything
# under emulator/src and emulator/CMakeLists.txt) -- checked for staleness
# so the fixture rebuilds when firmware code changes underneath the tests,
# not just when the binary is missing outright.
_FW_SRC_NAMES = ("Ramps.c", "Modbus.c", "Scales.c", "UARTCallback.c")


def _reflex_fw_sha() -> str:
    """Best-effort short git SHA of the reflex-fw checkout, for run provenance.

    Never raises/fails the session -- if reflex-fw isn't a git repo (or git
    itself is unavailable), falls back to "unknown".
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(REFLEX_FW_DIR), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                return sha
    except Exception:
        pass
    return "unknown"


def pytest_report_header(config):
    """Surface the reflex-fw SHA a system-test run exercised, in the header."""
    return f"reflex-fw: {_reflex_fw_sha()}"


def _emulator_sources_newer_than(mtime: float) -> bool:
    """True if any emulator/firmware source file is newer than `mtime`.

    Cheap mtime-only comparison (no hashing); globs are guarded so a missing
    optional path (e.g. no Core/Inc checked out) doesn't crash the fixture.
    """
    candidates = []

    for sub in ("src", "shim"):
        d = EMULATOR_DIR / sub
        if d.is_dir():
            candidates.extend(d.rglob("*"))

    cmakelists = EMULATOR_DIR / "CMakeLists.txt"
    if cmakelists.is_file():
        candidates.append(cmakelists)

    fw_src_dir = REFLEX_FW_DIR / "Core" / "Src"
    for name in _FW_SRC_NAMES:
        path = fw_src_dir / name
        if path.is_file():
            candidates.append(path)

    fw_inc_dir = REFLEX_FW_DIR / "Core" / "Inc"
    if fw_inc_dir.is_dir():
        candidates.extend(fw_inc_dir.rglob("*"))

    for path in candidates:
        try:
            if path.is_file() and path.stat().st_mtime > mtime:
                return True
        except OSError:
            continue
    return False


@pytest.fixture(scope="session")
def emulator_binary():
    """Locate (building if necessary) the reflex-fw lathe-emulator binary.

    Skips the whole system-test session cleanly if reflex-fw isn't checked
    out alongside reflex-ui (or REFLEX_FW_DIR isn't pointed at it). Rebuilds
    when the binary is missing OR stale relative to emulator/firmware
    sources; cmake itself will no-op when nothing actually changed.
    """
    if not EMULATOR_DIR.is_dir():
        pytest.skip(
            f"reflex-fw not found at {REFLEX_FW_DIR}; set REFLEX_FW_DIR to run system tests"
        )

    # (The reflex-fw SHA is surfaced via pytest_report_header for run provenance.)
    needs_build = not EMULATOR_BIN.exists()
    if not needs_build:
        try:
            needs_build = _emulator_sources_newer_than(EMULATOR_BIN.stat().st_mtime)
        except OSError:
            needs_build = True

    if needs_build:
        subprocess.run(["cmake", "-B", "build"], cwd=EMULATOR_DIR, check=True)
        subprocess.run(["cmake", "--build", "build"], cwd=EMULATOR_DIR, check=True)
    return EMULATOR_BIN


def _start_emulator(emulator_binary, config_path, extra_env: dict):
    """Launch the emulator in EMU_SCENARIO=serve mode, parse the PTY path from
    stdout, and start a background drain so a full stdout pipe never blocks it.
    Returns (proc, pty_path); pytest.fail if no PTY path appears within 5 s.

    EMU_SCENARIO=serve is REQUIRED: it forces the half-nut engaged and runs
    runPhysicsServer (spindle running, reflex-ui drives the ELS over Modbus).
    """
    proc = subprocess.Popen(
        [str(emulator_binary), str(config_path)],
        cwd=EMULATOR_DIR,
        env={**os.environ, "EMU_SCENARIO": "serve", **extra_env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # Read stdout on a background thread into a Queue. NOTE: don't combine
    # select() with proc.stdout (a buffered TextIOWrapper) for the startup
    # timeout -- the first readline() slurps ALL available bytes into Python's
    # userspace buffer, so a later select() on the raw fd reports "not ready"
    # even with fully-parsed lines waiting. queue.Queue.get(timeout=...) is safe.
    line_queue: "queue.Queue[str]" = queue.Queue()

    def _reader():
        try:
            for line in proc.stdout:
                line_queue.put(line)
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()

    pty_path = None
    startup_lines = []
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            line = line_queue.get(timeout=max(remaining, 0))
        except queue.Empty:
            break
        startup_lines.append(line)
        m = PTY_PATH_RE.search(line)
        if m:
            pty_path = m.group(1)
            break

    if pty_path is None:
        _stop_emulator(proc)
        pytest.fail(
            "emulator did not report a PTY path on startup; stdout so far:\n"
            + "".join(startup_lines)
        )

    # Keep draining for the process lifetime -- otherwise once the OS pipe
    # buffer fills (the emulator prints a line per ELS-stop/retract event) the
    # emulator blocks on write() and the test hangs.
    def _drain():
        while True:
            try:
                line_queue.get(timeout=1.0)
            except queue.Empty:
                if proc.poll() is not None:
                    return
            except Exception:
                return

    threading.Thread(target=_drain, daemon=True).start()
    return proc, pty_path


def _stop_emulator(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _resolve_emulator_param(param):
    """Normalize an emulator_process/request param into (config_path, extra_env).
    param may be None, a config path (str/Path), or {"config": path, "env": {...}}."""
    config_path = EMULATOR_DIR / "config" / "lathe.toml"
    extra_env: dict = {}
    if isinstance(param, dict):
        config_path = param.get("config", config_path)
        extra_env = param.get("env", {})
    elif param is not None:
        config_path = param
    return config_path, extra_env


@pytest.fixture
def emulator_process(emulator_binary, request):
    """Launch/tear down one emulator per test; yields (proc, pty_path).

    `request.param` (indirect parametrization): a config path, or a dict
    {"config": <path>, "env": {<extra emulator env, e.g. EMU_RPM>}}.
    """
    config_path, extra_env = _resolve_emulator_param(getattr(request, "param", None))
    proc, pty_path = _start_emulator(emulator_binary, config_path, extra_env)
    yield proc, pty_path
    _stop_emulator(proc)


@pytest.fixture
def harness(emulator_process, tmp_path, monkeypatch):
    """A connected SystemHarness (real dispatcher + FSM stack on the emulator).

    Redirects HOME to a tmp dir so SavingDispatcher settings I/O is hermetic
    and never touches the developer's real ~/.config/reflex.
    """
    from tests.system.harness import SystemHarness

    monkeypatch.setenv("HOME", str(tmp_path))

    _, pty_path = emulator_process
    h = SystemHarness(pty_path)
    h.connect()
    yield h
    h.disconnect()


@pytest.fixture  # parametrized indirectly by pytest_generate_tests (16 wiring perms)
def wiring_cut_harness(request, emulator_binary, tmp_path, monkeypatch):
    """Parametrized over all 16 physical-wiring permutations: launches an
    emulator on the permutation's generated config, connects a harness, sets the
    stop-only turning modes, and applies the CANCELING UI toggles (production
    write path). Yields (harness, permutation).

    EMU_RPM=-30 so the spindle turns the direction consistent with the UI's
    els_forward=True "forward" feed convention (see Task 7 finding), for every
    permutation — the canceling toggles neutralize the physical wiring, so all 16
    valid configs should behave like the all-normal baseline.
    """
    from tests.system.harness import SystemHarness
    from tests.system.wiring import make_config

    perm = request.param
    monkeypatch.setenv("HOME", str(tmp_path))
    base_toml = EMULATOR_DIR / "config" / "lathe.toml"
    config = make_config(base_toml, perm.overrides, tmp_path / f"wiring_{perm.id}.toml")

    # EMU_NO_AUTO_RETRACT: after the ELS stop the carriage stays put (no
    # simulated hand-retract), so the test reads the resting stop position with
    # no race against the emulator's 400 ms retract.
    proc, pty_path = _start_emulator(
        emulator_binary, config, {"EMU_RPM": "-30", "EMU_NO_AUTO_RETRACT": "1"})
    h = None
    try:
        h = SystemHarness(pty_path)
        h.connect()
        h.configure(is_threading=False, retract_enabled=False,
                    wizard_enabled=False, els_forward=True)
        h.apply_wiring_toggles(perm.toggles)
        yield h, perm
    finally:
        if h is not None:
            try:
                h.disconnect()
            except Exception:
                pass
        _stop_emulator(proc)


def _wiring_permutation_params():
    from tests.system.wiring import WIRING_PERMUTATIONS
    return WIRING_PERMUTATIONS


def pytest_generate_tests(metafunc):
    # Parametrize wiring_cut_harness over the 16 permutations with readable ids.
    if "wiring_cut_harness" in metafunc.fixturenames:
        perms = _wiring_permutation_params()
        metafunc.parametrize(
            "wiring_cut_harness", perms,
            ids=[p.id for p in perms], indirect=True,
        )
