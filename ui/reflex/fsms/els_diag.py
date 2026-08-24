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
disagreed with itself about the ISR rate by 10x when this was written -- AGENTS.md
and ARCHITECTURE.md said ~100 us, todo.md and els_slip.h said 100 kHz, and
reflex.ioc described a third rate it had not matched since 2024. Those were
reconciled to 100 kHz on 2026-08-16, so the contradiction is gone.

THE RULE STANDS ANYWAY, and not merely out of caution. A rate compiled into this
file is an assumption about a number the firmware is free to change, and the
capture would keep parsing cleanly and silently mean something else -- the same
class of failure diagSchema exists to prevent one level up. That is why the
firmware PUBLISHES diagBucketTicks rather than expecting anyone to know it.
Record what was measured; convert when analysing.
"""

import json
import logging
from datetime import datetime, timezone

from reflex.utils.devices import (
    ELS_DIAG_SCHEMA_NONE,
    ELS_DIAG_SCHEMA_TAKEUP_SETTLE,
    ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2,
    ELS_DIAG_SCHEMA_DISENGAGE_LATCH,
    ELS_DIAG_SCHEMA_MODE_WATCH,
    ELS_DIAG_SCHEMA_MODE_WATCH_V2,
    ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V3,
)
from reflex.utils.paths import diag_dir

log = logging.getLogger(__name__)

# Schemas this UI knows how to record. A schema id outside this set means the
# firmware carries a probe written after this UI: refuse it rather than storing
# its numbers under a shape they do not have.
#
# v1 is still accepted even though the firmware has moved on, because recording
# a v1 capture off older firmware is strictly better than recording nothing --
# every line carries its own schema, so the analysis can tell them apart. What
# must never happen is v1 data stored as though it were v2.
KNOWN_SCHEMAS = frozenset({
    ELS_DIAG_SCHEMA_TAKEUP_SETTLE,
    ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2,
    # Schema 3 is NOT a trace. It counts events, and its fields mean different
    # things -- diagSeq is "times the firmware caught servoEnableTask trying to
    # re-assert the feed after a disengage", not "captures completed". A
    # non-zero value is the finding. See reflex-fw DIAG.md.
    ELS_DIAG_SCHEMA_DISENGAGE_LATCH,
    # Schemas 4 and 5: diagSeq counts MODE TRANSITIONS, so each recorded line
    # is one transition -- capture_ticks holds the mode entered, settle_ticks
    # the mode left, net_counts the cumulative latch-suppression count. The
    # continuous current-mode register is read elsewhere (ElsModeWatch); the
    # recorder's job here is just the durable transition log. Schema 4 is
    # retired firmware-side but stays accepted (the takeup v1 reasoning
    # above): the lathe runs a schema-4 build until its next flash + power
    # cycle, and every line carries its own schema, so v1 net_counts ("every
    # refusal") is never confused with v2's ("effective refusals only").
    ELS_DIAG_SCHEMA_MODE_WATCH,
    ELS_DIAG_SCHEMA_MODE_WATCH_V2,
    # v3 retires v2 in firmware but both stay readable here: a .jsonl line
    # carries its own schema, and the 148 captures taken under v2 on
    # 2026-08-21 are still evidence -- of what the probe could not see.
    ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V3,
})

# Schemas that publish diagEndReason. Only these can be checked for "did a
# capture actually complete" -- v1 has no such field, and its register reads as
# 0, so applying the check there would reject every v1 capture as empty.
#
# Schemas 4 and 5 publish the field but MUST NOT be in this set: there it
# means "a latch suppression has been seen", and 0 is the healthy steady state
# -- gating on it would silently drop every mode-transition record on a
# machine where nothing is wrong, which is all of them if the fixes hold.
SCHEMAS_WITH_END_REASON = frozenset({
    ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2,
    ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V3,
})

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

    @property
    def schema(self):
        """The recognised probe's schema id, or None before interrogation /
        when dormant. Lets other consumers (the mode watch) key on which
        probe is present without issuing their own diagSchema read."""
        return self._schema if self._enabled is True else None

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

            # The cheap tick, from the board's once-per-tick elsStop snapshot
            # rather than its own Modbus exchange -- this runs on every board
            # update, and property 2 above ("cheap to watch") is now literally
            # free. _interrogate/_choose_baseline below stay LIVE: they run
            # once per connection, before any snapshot is guaranteed to exist,
            # and one exchange at connect time is not the problem.
            seq = self._hal.tick.diag_seq()
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
        self._baseline_seq = self._choose_baseline()
        log.info(
            f"ELS diagnostic recorder active: schema={schema}, "
            f"baseline diagSeq={self._baseline_seq}, "
            f"writing to {self._capture_path()}"
        )

    def _last_recorded_seq(self):
        """Highest diagSeq already written to the capture file, or None."""
        path = self._capture_path()
        if not path.exists():
            return None
        best = None
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    seq = int(json.loads(line)["seq"])
                except (ValueError, KeyError, TypeError):
                    continue          # a torn last line must not lose the file
                best = seq if best is None else max(best, seq)
        return best

    def _choose_baseline(self) -> int:
        """What counts as 'already seen' for this connection.

        Three cases, and getting any one of them wrong loses or invents data.

        NO FILE: baseline from the firmware, so a capture sitting in the block
        from before this UI ever ran is not date-stamped as though it just
        happened.

        FILE, AND THE FIRMWARE IS AT OR BEYOND IT: baseline from the FILE.
        Baselining from the firmware here is right for a fresh start and wrong
        for a reconnect -- it treats anything completed while disconnected as
        history and drops it. That cost a real capture on 2026-08-16, when the
        board reconnected mid-test and capture 12 went from the firmware to
        nowhere, leaving the file jumping 11 to 13.

        FILE, BUT THE FIRMWARE IS BEHIND IT: the firmware's counter has RESET --
        it is zeroed by a power cycle, which this board requires after every
        flash. The two numbers are no longer on the same scale, so the file
        cannot say what is new. Baseline from the firmware. Fixing the reconnect
        case without this one is how, on 2026-08-16, a power cycle left the file
        holding seq 13 while the firmware said 0, and an empty block got recorded
        twice as though it were a capture.
        """
        recorded = self._last_recorded_seq()
        if recorded is None:
            return self._hal.read_diag_seq()

        live = self._hal.read_diag_seq()
        if live < recorded:
            log.info(
                f"firmware diagSeq ({live}) is behind the capture file ({recorded}); "
                f"treating as a firmware restart and re-baselining")
            return live
        return recorded

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

        # A completed capture says WHY it ended. end_reason == 0 means no capture
        # has finished and the block is empty -- there is nothing to record, and
        # writing it produces a row of zeros that looks like a real measurement
        # of a carriage that never moved. This is the semantic backstop behind
        # the counter-reset handling in _choose_baseline: that reasons about
        # sequence numbers, this asks the block whether it holds anything.
        if capture.get("schema") in SCHEMAS_WITH_END_REASON and not capture.get("end_reason"):
            log.info(f"diagSeq advanced to {seq} but the block reports no completed "
                     f"capture (end_reason=0); nothing recorded")
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
        if record.get("schema") in (ELS_DIAG_SCHEMA_MODE_WATCH,
                                    ELS_DIAG_SCHEMA_MODE_WATCH_V2):
            # Same registers, different meanings (see KNOWN_SCHEMAS): this line
            # is one mode transition, and net_counts is the suppression count.
            log.info(
                f"ELS mode transition #{seq}: "
                f"{record['settle_ticks']} -> {record['capture_ticks']}, "
                f"suppressions={record['net_counts']} -> {path}"
            )
        else:
            log.info(
                f"ELS settle capture #{seq}: settle_ticks={record['settle_ticks']}, "
                f"net_counts={record['net_counts']} -> {path}"
            )
