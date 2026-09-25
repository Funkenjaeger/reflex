"""Measure what the flight recorder actually costs, and what that buys.

WHY THIS EXISTS. els_flight_recorder.py's three disk constants -- SEGMENT_MAX_BYTES,
MAX_TOTAL_BYTES, MIN_FREE_BYTES -- are only defensible against a MEASURED byte
rate. elspi is a Pi with one SD card in a machine shop; "a rotating log, bounded
at 64 MB" means nothing until somebody can say how many hours of cutting that
holds, and an estimate from counting characters in a field list is exactly the
kind of number that turns out to be 2x wrong in the direction that matters.

So this drives the real recorder with a synthetic pass -- real fastData and
elsStop shapes, real counter magnitudes, the real writer -- and reports bytes
per sample, bytes per armed hour, and the retention the budget therefore buys.

IT REPORTS RETENTION IN *ARMED* HOURS, not wall-clock hours, and the difference
is the whole reason the arming gate exists. The recorder writes nothing while
the machine sits engaged-and-idle overnight, so a shop day of a few hours of
actual cutting costs a fraction of what a 30 Hz always-on log would.

Run this after adding or removing a per-sample column. Run (WSL):

    cd ui && ./.venv/bin/python previews/preview_flight_recorder_budget.py
"""
import os
import shutil
import tempfile

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

import sys                                                        # noqa: E402
from pathlib import Path                                          # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reflex.fsms.els_flight_recorder import (                     # noqa: E402
    FAST_FIELDS,
    MAX_TOTAL_BYTES,
    SEGMENT_GLOB,
    SEGMENT_MAX_BYTES,
    FlightRecorder,
)

TICK_HZ = 30
SAMPLES = 30 * 60 * 5          # five minutes of armed machine

# Counter magnitudes matter more than field count: servoCurrent is a uint32 that
# reaches ten digits on a long session, and a measurement taken at zero would
# understate every row. These are the shapes a real pass produces.
FAST = {
    "servoMode": 1,
    "servoCurrent": 3_141_592_653,
    "servoDesired": 3_141_592_700,
    "stepsToGo": -1287,
    "servoSpeed": 4821.37,
    "scaleCurrent": [1_234_567, -894_233, 41_002, 0],
    "scaleSpeed": [1420, -318, 0, 0],
}
SNAP = {
    "enable": 1, "active": 0, "takeupPending": 0,
    "scaleIndex": 1, "stopDirection": 1, "stopPosition": -894_100,
    "hysteresis": 4, "backlashSteps": 385, "takeupSeq": 17, "takeupResult": 0,
    "lastTakeupZDelta": 13, "takeupThreshCounts": 11,
    "referenceLatched": 1, "latchSeq": 3, "latchedZ": -880_000,
    "latchedSpindle": 1_200_000,
    "threadPitchSteps": 631.5748, "zCountsPerPitch": 250.0,
    "phaseOffsetSteps": 0,
    "lastIdealAdvance": 12345.678, "lastActualAdvance": 12345.123,
    "lastPhaseError": 0.555, "lastCorrection": -392.76,
    "machineMode": 2, "protocolVersion": 7,
    "stepPulseMinCycles": 912, "stepPulseRuntCount": 0,
}


class _Board:
    fast_data_values = FAST
    els_stop_values = SNAP
    connected = True


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}"
        n /= 1024.0


def main():
    out = Path(tempfile.mkdtemp(prefix="reflex-flight-budget-"))
    try:
        clock = _Clock()
        rec = FlightRecorder(
            _Board(), fsm_state=lambda: "cutting",
            fsm_states=["disabled", "stopped", "retracting", "cutting", "alarm"],
            directory=out, now=clock,
            free_bytes=lambda _p: 10 ** 12,
            # Budget deliberately not applied here: pruning would delete the
            # very bytes being measured. The bound is exercised by the tests.
            segment_max_bytes=10 ** 9, max_total_bytes=10 ** 12)
        for _ in range(SAMPLES):
            rec.poll()
            clock.t += 1.0 / TICK_HZ
        rec.close("measurement")

        total = sum(p.stat().st_size for p in out.glob(SEGMENT_GLOB))
        per_sample = total / rec.samples_written
        per_hour = per_sample * TICK_HZ * 3600

        print(f"columns per sample      {len(FAST_FIELDS)}")
        print(f"samples written         {rec.samples_written:,}")
        print(f"bytes on disk           {_human(total)}")
        print(f"BYTES PER SAMPLE        {per_sample:.1f}")
        print(f"BYTES PER ARMED HOUR    {_human(per_hour)}  (at {TICK_HZ} Hz)")
        print()
        print(f"SEGMENT_MAX_BYTES       {_human(SEGMENT_MAX_BYTES)}"
              f"  = {SEGMENT_MAX_BYTES / per_hour * 60:.0f} armed minutes/file")
        print(f"MAX_TOTAL_BYTES         {_human(MAX_TOTAL_BYTES)}"
              f"  = {MAX_TOTAL_BYTES / per_hour:.1f} armed hours retained")
        print()
        print("Armed hours, not wall-clock hours -- the gate writes nothing")
        print("while the machine is engaged and idle. See the module docstring.")
    finally:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    main()
