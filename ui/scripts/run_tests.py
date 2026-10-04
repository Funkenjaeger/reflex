#!/usr/bin/env python3
"""Run the test suite with the timing-sensitive cases de-contended, and retry
a failure in isolation before calling it one.

WHY THERE ARE TWO PASSES. A handful of tests assert on WALL-CLOCK behaviour --
how many stopOffset writes the rate limiter allows per second, how far the
carriage travels in a two-second window. Run under `-n auto` alongside sixty
emulator processes they are measuring a starved box, not the code, and they
fail for reasons that have nothing to do with the change under test. They are
marked `timing` and run on their own, afterwards, with no parallelism.

WHY A RETRY IS A SAFETY NET AND NOT A CURE. Retrying until green hides exactly
the defect this project can least afford: the Modbus I/O now runs on its own
thread, and a race that fails one run in twenty looks precisely like a flake.
So a test that only passes on the retry is NOT reported as a pass. It is called
out by name, on stderr and as a CI annotation, because a test that needed
isolating is a finding to chase, not a clean run. The run still goes green --
that is what makes it a net rather than a gate -- but it never goes quiet.

The retry is deliberately ISOLATED: serial, no xdist, only the tests that
failed. If a failure survives that, it is real.

Usage:
    python scripts/run_tests.py                 # the default suite
    python scripts/run_tests.py --system        # the emulator-backed suite
    python scripts/run_tests.py --system -- -x  # extra args go to pytest
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

CACHE = Path(".pytest_cache/v/cache/lastfailed")


def pytest(*args: str) -> int:
    cmd = [sys.executable, "-m", "pytest", *args]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    return subprocess.call(cmd)


def last_failed() -> list[str]:
    """The test ids pytest recorded as failing in the run that just finished."""
    try:
        with CACHE.open() as handle:
            return sorted(json.load(handle))
    except (OSError, ValueError):
        return []


def pass_with_retry(label: str, select: list[str], parallel: bool,
                    extra: list[str]) -> tuple[bool, list[str]]:
    """Run one pass; on failure retry ONLY its failures, serially.

    Returns (ok, retried) where `retried` is the tests that failed first and
    passed alone -- the ones worth looking at even though the run is green.
    """
    args = [*select, *extra]
    if parallel:
        args = ["-n", "auto", *args]
    if pytest(*args) == 0:
        return True, []

    failures = last_failed()
    if not failures:
        # Failed without recording a failing test: a collection error, an
        # internal error, a worker crash. Not something to retry blind.
        print(f"\n[{label}] failed without naming a test; not retrying.",
              file=sys.stderr)
        return False, []

    print(f"\n[{label}] {len(failures)} failed; retrying them ALONE "
          f"(serial, no xdist) before calling it a failure.", flush=True)
    if pytest("--last-failed", "--last-failed-no-failures", "none",
              *select, *extra) != 0:
        return False, []
    return True, failures


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--system", action="store_true",
                        help="run the emulator-backed system suite")
    parser.add_argument("--no-parallel", action="store_true",
                        help="run the first pass serially too")
    parser.add_argument("pytest_args", nargs="*",
                        help="extra arguments passed straight to pytest")
    opts = parser.parse_args()
    extra = list(opts.pytest_args)

    base = "system" if opts.system else "not system"
    target = ["tests/system"] if opts.system else []

    ok_bulk, retried_bulk = pass_with_retry(
        "bulk", ["-m", f"{base} and not timing", *target],
        parallel=not opts.no_parallel, extra=extra)

    # ALWAYS run, even if the bulk pass failed: a timing regression and an
    # ordinary one are different findings and the second must not hide behind
    # the first. Serial on purpose -- see the module docstring.
    ok_timing, retried_timing = pass_with_retry(
        "timing", ["-m", f"{base} and timing", *target],
        parallel=False, extra=extra)

    retried = retried_bulk + retried_timing
    if retried:
        banner = "=" * 72
        print(f"\n{banner}\nPASSED ONLY ON RETRY -- {len(retried)} test(s). "
              f"The run is green; these are not.\nA test that fails in the "
              f"suite and passes alone is contention or a race. With the\n"
              f"Modbus I/O on its own thread, assume a race until shown "
              f"otherwise.\n{banner}", file=sys.stderr)
        for test in retried:
            print(f"  passed on retry: {test}", file=sys.stderr)
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::warning title=Passed only on retry::{test}")
        print(banner, file=sys.stderr)

    return 0 if (ok_bulk and ok_timing) else 1


if __name__ == "__main__":
    raise SystemExit(main())
