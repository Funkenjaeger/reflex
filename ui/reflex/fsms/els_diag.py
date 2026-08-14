"""Recorder for the firmware diagnostic scratchpad.

Watches ``elsStop.diagSeq`` for a completed capture and appends the whole block
to a JSONL file, so the data lands somewhere analysable instead of somewhere you
have to transcribe off a 1024x600 touchscreen at the lathe.

THREE PROPERTIES THIS MUST HAVE, and each one is load-bearing:

1. **Inert against release firmware.** The scratchpad is reserved in every build
   but written only when reflex-fw is compiled with ``ELS_DIAG_SCRATCH``. This
   reads ``diagSchema`` once per connection; if it is 0 (or a number this UI does
   not know) the recorder disables itself for that connection and issues no
   further reads at all. Against production firmware the steady-state cost is
   exactly zero, not merely small.

2. **Cheap to watch, expensive only on demand.** ``diagSeq`` is one register, so
   polling it is free. The 64-register block read -- roughly 12 ms of serial time
   at 115200 baud -- happens only when the firmware has said, via its own ack,
   that a capture is complete. That split is the entire reason ``diagSeq``
   exists, and there is deliberately no capture-in-progress register: polling one
   would race the ISR and could read a half-written trace.

3. **Incapable of taking the UI down.** A diagnostic that can break the machine
   control surface is worse than no diagnostic. Every operation is guarded, and
   repeated failures disable the recorder rather than retrying forever against a
   fault that is not going to clear.

NO UNIT CONVERSION HAPPENS HERE. Bucket width is recorded in ISR ticks, and the
ISR's measured interval in CPU cycles is recorded alongside it, so ticks can be
converted to seconds from the capture itself. reflex-fw's own documentation
disagrees with itself about the ISR rate by 10x (AGENTS.md and ARCHITECTURE.md
say ~100 us, todo.md and els_slip.h say 100 kHz, and reflex.ioc describes a third
rate it has not matched since 2024), so a conversion baked in here would be a
confident wrong answer. Record what was measured; convert when analysing.
"""

import json
import logging
from datetime import datetime, timezone

from reflex.utils.devices import (
    ELS_DIAG_SCHEMA_NONE,
    ELS_DIAG_SCHEMA_TAKEUP_SETTLE,
)
from reflex.utils.paths import diag_dir

log = logging.getLogger(__name__)

# Schemas this UI knows how to record. A schema id outside this set means the
# firmware carries a probe written after this UI: refuse it rather than storing
# its numbers under a shape they do not have.
KNOWN_SCHEMAS = frozenset({ELS_DIAG_SCHEMA_TAKEUP_SETTLE})

# Consecutive failures tolerated before the recorder gives up for this
# connection. Small on purpose -- if reads are failing, the useful behaviour is
# to say so once and stop, not to keep hammering a broken link.
MAX_FAILURES = 3


class ElsDiagRecorder:
    """Edge-detects diagSeq and appends completed captures to a JSONL file."""

    def __init__(self, hal, board, capture_dir=None):
        self._hal = hal
        self._board = board
        self._dir = capture_dir
        self.reset()

    def reset(self):
        """Forget everything learned about the connected firmware.

        Called on (re)connect: the board on the other end may have been reflashed
        between connections, so a schema learned last time says nothing about
        this one.
        """
        self._schema = None          # None = not yet interrogated
        self._enabled = None         # None = undecided, False = dormant
        self._baseline_seq = None
        self._failures = 0
        self.captures_written = 0

    @property
    def enabled(self) -> bool:
        """True only once a recognised probe has actually been found."""
        return self._enabled is True

    def _disable(self, reason: str):
        if self._enabled is not False:
            log.info(f"ELS diagnostic recorder dormant: {reason}")
        self._enabled = False

    def _capture_path(self):
        d = self._dir if self._dir is not None else diag_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d / "takeup_settle.jsonl"

    def poll(self):
        """Cheap tick. Safe to call on every UI update; does nothing when dormant.

        Never raises. A diagnostic that can propagate an exception into the board
        update loop would take the control surface down with it, which is a worse
        outcome than losing a measurement.
        """
        try:
            if self._enabled is False:
                return
            if not self._board.connected:
                return

            if self._enabled is None:
                self._interrogate()
                return

            seq = self._hal.read_diag_seq()
            if seq == self._baseline_seq:
                return

            self._record(seq)
            self._baseline_seq = seq
            self._failures = 0

        except Exception as e:
            self._failures += 1
            log.warning(
                f"ELS diagnostic read failed ({self._failures}/{MAX_FAILURES}): {e}"
            )
            if self._failures >= MAX_FAILURES:
                self._disable("too many consecutive read failures")

    def _interrogate(self):
        """One-time probe identification for this connection."""
        schema = self._hal.read_diag_schema()
        if schema == ELS_DIAG_SCHEMA_NONE:
            self._disable("firmware carries no diagnostic probe")
            return
        if schema not in KNOWN_SCHEMAS:
            self._disable(
                f"firmware reports diagSchema={schema}, which this UI does not "
                f"know how to interpret; refusing to guess"
            )
            return

        self._schema = schema
        self._enabled = True
        # Baseline against whatever the firmware has already counted, so a
        # capture completed before the UI connected is not replayed as new.
        self._baseline_seq = self._hal.read_diag_seq()
        log.info(
            f"ELS diagnostic recorder active: schema={schema}, "
            f"baseline diagSeq={self._baseline_seq}, "
            f"writing to {self._capture_path()}"
        )

    def _record(self, seq: int):
        capture = self._hal.read_diag_capture()
        if not capture:
            raise RuntimeError("empty capture read")

        # The firmware may have been reflashed under a live connection. Trust the
        # schema in the payload over the one learned at connect.
        if capture.get("schema") not in KNOWN_SCHEMAS:
            self._disable(
                f"capture reports diagSchema={capture.get('schema')}; refusing it"
            )
            return

        # ISR interval in CPU cycles, from the data the UI already polls. This is
        # what makes ticks convertible to seconds without assuming a rate.
        fast = getattr(self._board, "fast_data_values", None) or {}
        try:
            execution_interval_cycles = int(fast.get("executionInterval", 0))
        except (TypeError, ValueError):
            execution_interval_cycles = 0

        record = dict(capture)
        record["seq"] = seq
        # UTC deliberately: the lathe's clock runs an hour off the rest of the
        # estate, so a local timestamp here would not line up with anything.
        record["utc"] = datetime.now(timezone.utc).isoformat()
        record["execution_interval_cycles"] = execution_interval_cycles

        path = self._capture_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

        self.captures_written += 1
        log.info(
            f"ELS settle capture #{seq}: settle_ticks={record['settle_ticks']}, "
            f"net_counts={record['net_counts']} -> {path}"
        )
