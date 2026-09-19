"""ELS stop-overshoot correction: how many counts early to fire the stop.

THE PROBLEM. The ELS stop is a COMMANDED position. The firmware stops emitting
steps when the Z scale crosses the threshold, and the carriage then coasts on
by an amount that is deterministic and grows with the approach rate -- zero when
hand-cranked, ~43 counts (0.2 mm) at 3567 counts/s. Since protocolVersion 11 the
firmware takes a correction in elsStop.stopOffset and fires that many counts
EARLY (clamped to [0, ELS_STOP_OFFSET_MAX]); stopPosition stays the exact,
overshoot-ignorant target. This module decides the number. It is PURE -- no
Kivy, no registers -- so every rule in it is tested directly; the one piece that
touches the machine, StopOffsetCorrector, takes its HAL and board as arguments.

THE SIGN, WHICH IS THE WHOLE POINT. The carriage settles at

        target - offset + overshoot

so it lands SHORT of the target only if offset > overshoot. An offset scaled
BELOW the prediction lands PAST the target, into the shoulder. An earlier draft
of the decision record proposed applying "~90%" of the prediction; that lands
10% of the coast past the target and is superseded (decisions/
els-stop-overshoot-compensation.md, 2026-09-18). Nothing here ever scales the
prediction below 100%, and test_els_overshoot pins offset > predicted at every
table point.

SIZING (Evan, 2026-09-18): the per-rate ENVELOPE (worst case seen at each rate)
plus a margin of 1 count, rounded UP. Between table points the prediction is a
straight line; ABOVE the top point it is HELD at the top value and the caller is
told (`above_range`), because extrapolating a coast curve past the fastest pass
anyone has measured is a guess about metal.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

# ── The shipped table ────────────────────────────────────────────────────
# (|Z rate| in counts/s, worst overshoot in counts). Z rate is the Z scale's
# `speed` register -- the same register elsStop.stopTriggerZSpeed copies at the
# trigger and fastData.scaleSpeed mirrors every tick -- so the table's x-axis and
# the live input are one quantity, not two that merely share units.
#
# PROVENANCE: re-derived 2026-09-18 from the protocol-10 flight-recorder
# sessions of 2026-09-12, 09-13 and 09-14 (trigger-instant snapshot, overshoot
# = settled Z - stopTriggerZ). It is the ENVELOPE: for each rate, the MAX over
# every same-speed set, so the curve is monotonic and never under-predicts a
# pass that was actually observed. (0, 0) is measured, not assumed: a
# hand-cranked approach overshoots by exactly zero.
#
# BOUND TO THIS MACHINE'S CURRENT CONFIGURATION: elspi's Z scale at 5 um/count
# (200 counts/mm), and the ServoBar motion settings maxSpeed 10000 /
# acceleration 20000. The coast is a property of the drive's deceleration and
# the scale's resolution; change either and this table is wrong in a direction
# nobody can predict -- RE-MEASURE before relying on the correction again. It
# lives in code for this release; a per-machine calibration wizard that writes
# it is separate, later work.
OVERSHOOT_TABLE: Tuple[Tuple[int, int], ...] = (
    (0, 0),
    (300, 1),
    (596, 3),
    (1187, 9),
    (1640, 12),
    (1692, 17),
    (2500, 29),
    (2507, 32),
    (3567, 43),
)

#: Counts added on top of the rounded-up envelope. Evan's sizing, 2026-09-18.
DEFAULT_MARGIN_COUNTS = 1

#: Mirror of the firmware's ELS_STOP_OFFSET_MAX (fw/Core/Inc/Ramps.h). The
#: firmware clamps to this anyway; clamping here too keeps "the offset in
#: effect" that the flight recorder files equal to what the ISR used.
#: test_els_overshoot reads the #define out of Ramps.h and fails on drift.
ELS_STOP_OFFSET_MAX = 200

# ── The write limiter ────────────────────────────────────────────────────
# Every register assignment is an IMMEDIATE Modbus exchange, and the board tick
# is held at two exchanges (fastData + the elsStop hot group); a third on every
# tick is what halved it to ~16 Hz before 2026-09-07. So a new offset is written
# only when it moved by at least WRITE_MIN_DELTA_COUNTS AND at least
# WRITE_MIN_INTERVAL_S has passed since the last write: at most one extra
# exchange every 250 ms (one tick in ~8), and none at all at a steady rate.
WRITE_MIN_DELTA_COUNTS = 1
WRITE_MIN_INTERVAL_S = 0.25

#: Operator notice when the live rate is past the table's top point.
ABOVE_RANGE_NOTICE = ("Z rate above the calibrated range ({top} counts/s); "
                      "correction held at its top value")


def _validate(table: Sequence[Tuple[int, int]]) -> None:
    if not table or tuple(table[0]) != (0, 0):
        raise ValueError("overshoot table must start at (0, 0)")
    for (r0, v0), (r1, v1) in zip(table, table[1:]):
        if r1 <= r0:
            raise ValueError(f"table rates must strictly increase ({r0} -> {r1})")
        if v1 < v0:
            raise ValueError(f"table must be an envelope (non-decreasing): {v0} -> {v1}")


_validate(OVERSHOOT_TABLE)


@dataclass(frozen=True)
class Prediction:
    counts: float        # predicted coast, encoder counts
    above_range: bool    # |rate| was past the top table point; counts is HELD


def predicted(rate: float, table: Sequence[Tuple[int, int]] = OVERSHOOT_TABLE) -> Prediction:
    """Predicted coast at a Z rate, by linear interpolation over |rate|.

    Below the lowest nonzero point it interpolates toward (0, 0), which is a
    table point. Above the top point it HOLDS the top value and says so.
    """
    r = abs(float(rate))
    top_rate, top_val = table[-1]
    if r > top_rate:
        return Prediction(float(top_val), True)
    for (r0, v0), (r1, v1) in zip(table, table[1:]):
        if r <= r1:
            return Prediction(v0 + (v1 - v0) * (r - r0) / (r1 - r0), False)
    return Prediction(float(top_val), False)   # r == top_rate, unreachable in practice


@dataclass(frozen=True)
class Correction:
    offset: int          # counts to fire early: ceil(predicted) + margin, clamped
    predicted: float
    above_range: bool


def correction(rate: float, margin: int = DEFAULT_MARGIN_COUNTS,
               table: Sequence[Tuple[int, int]] = OVERSHOOT_TABLE) -> Correction:
    """offset = ceil(predicted(rate)) + margin, clamped to [0, ELS_STOP_OFFSET_MAX].

    The margin is floored at 0: a negative margin would put the offset below
    the prediction and land the carriage PAST the target.
    """
    p = predicted(rate, table)
    m = max(0, int(margin))
    off = int(math.ceil(p.counts)) + m
    off = max(0, min(ELS_STOP_OFFSET_MAX, off))
    return Correction(off, p.counts, p.above_range)


def offset(rate: float, margin: int = DEFAULT_MARGIN_COUNTS) -> int:
    """Just the number: counts early to fire the stop at this Z rate."""
    return correction(rate, margin).offset


class OffsetWriteLimiter:
    """Decides WHEN an offset is worth a Modbus exchange. See the constants.

    `last_written` is None until something has been written on this link --
    which forces the first decision through, so a register left nonzero by a
    previous UI session (the firmware outlives UI restarts) is overwritten
    rather than trusted. Call reset() on reconnect for the same reason.
    """

    def __init__(self, min_interval_s: float = WRITE_MIN_INTERVAL_S,
                 min_delta_counts: int = WRITE_MIN_DELTA_COUNTS,
                 clock: Callable[[], float] = time.monotonic):
        self.min_interval_s = float(min_interval_s)
        self.min_delta_counts = int(min_delta_counts)
        self._clock = clock
        self.last_written: Optional[int] = None
        self._last_time: Optional[float] = None

    def reset(self) -> None:
        self.last_written = None
        self._last_time = None

    def _record(self, value: int, now: float) -> int:
        self.last_written = int(value)
        self._last_time = now
        return int(value)

    def update(self, desired: int, now: Optional[float] = None) -> Optional[int]:
        """The value to write now, or None to write nothing this tick."""
        now = self._clock() if now is None else now
        desired = int(desired)
        if self.last_written is None:
            return self._record(desired, now)
        if abs(desired - self.last_written) < self.min_delta_counts:
            return None
        if now - self._last_time < self.min_interval_s:
            return None
        return self._record(desired, now)

    def force_zero(self, now: Optional[float] = None) -> Optional[int]:
        """0 once when the correction goes off or the stop disarms; then nothing.

        Not rate-limited: turning the correction off must take effect now, and
        it happens once per off/disarm edge, not per tick.
        """
        now = self._clock() if now is None else now
        if self.last_written == 0:
            return None
        return self._record(0, now)


class StopOffsetCorrector:
    """Per-tick glue: live Z rate -> offset -> limiter -> ElsStopHal.

    Duck-typed so it stays Kivy-free and testable: `hal` needs
    set_stop_offset(int) and `.tick` (TickReads: enable(), active(),
    scale_index()); `board` needs `connected`, `els_stop_values` and
    `fast_data_values`; `enabled()` and `margin()` read the operator settings;
    `notify(message)` posts an operator notice.

    NEVER writes stopPosition. It is the exact target and this class has no
    business with it; the correction lives entirely in stopOffset.
    """

    def __init__(self, hal, board, enabled: Callable[[], bool],
                 margin: Callable[[], int], notify: Callable[[str], object],
                 limiter: Optional[OffsetWriteLimiter] = None,
                 table: Sequence[Tuple[int, int]] = OVERSHOOT_TABLE):
        self._hal = hal
        self._board = board
        self._enabled = enabled
        self._margin = margin
        self._notify = notify
        self._table = table
        self.limiter = limiter or OffsetWriteLimiter()
        self._notified_this_pass = False
        self.last_correction: Optional[Correction] = None

    @property
    def offset_in_effect(self) -> Optional[int]:
        """The last value written to stopOffset on this link (None = unknown)."""
        return self.limiter.last_written

    def reset(self) -> None:
        """New link: the firmware's register is unknown again."""
        self.limiter.reset()
        self._notified_this_pass = False

    def _z_rate(self) -> Optional[float]:
        speeds = (self._board.fast_data_values or {}).get("scaleSpeed")
        idx = int(self._hal.tick.scale_index())
        if not speeds or not 0 <= idx < len(speeds):
            return None
        return float(speeds[idx])

    def poll(self) -> Optional[int]:
        """Run one tick. Returns the value written, or None if nothing was."""
        if not self._board.connected or not self._board.els_stop_values:
            # No snapshot this tick: the link is down or the read failed. A
            # write would be a guess on a failing link; wait for a real tick.
            return None
        armed = self._hal.tick.enable()
        if not self._enabled() or not armed:
            self._notified_this_pass = False
            return self._write(self.limiter.force_zero())
        if self._hal.tick.active():
            # HELD AT THE SHOULDER (or armed-idle before the first Cut): the
            # firmware cannot fire the stop while active == 1, so an offset
            # written now does nothing but spend an exchange. Worse, the
            # carriage RETRACTS in this state -- at rapid speed, far above the
            # table -- and following it would post an above-range notice on
            # every retract. So the offset is HELD: the next pass starts with
            # the one its predecessor approached at, and the live rate takes
            # over once Cut clears active. Two exceptions: nothing written yet
            # on this link (a previous session may have left any value, so it
            # is overwritten once), and the zero on off/disarm above.
            self._notified_this_pass = False
            if self.limiter.last_written is not None:
                return None
        rate = self._z_rate()
        if rate is None:
            return None
        c = correction(rate, self._margin(), self._table)
        self.last_correction = c
        if c.above_range and not self._notified_this_pass:
            self._notified_this_pass = True
            self._notify(ABOVE_RANGE_NOTICE.format(top=self._table[-1][0]))
        return self._write(self.limiter.update(c.offset))

    def _write(self, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        self._hal.set_stop_offset(value)
        cm = getattr(self._board, "connection_manager", None)
        if cm is not None and not getattr(cm, "connected", True):
            # The write was not acknowledged (the write helpers drop the link
            # on a failed exchange). Forget it, so the next good tick re-sends
            # instead of the limiter believing a value the firmware never got.
            self.limiter.reset()
            return None
        return value
