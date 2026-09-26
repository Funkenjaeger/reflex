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

STEADINESS (Evan, 2026-09-19). The first live version sized every tick from the
Z scale's single-tick `speed` register. On the bench (three air passes, .040
in/rev at ~342 rpm) every stop landed 1-2 counts short -- but it wrote stopOffset
15-21 times per pass, dithering 9 -> 10 -> 11 -> 10 -> 9 every ~270 ms, because
the register wanders ~1040-1140 counts/s and every wobble crosses a count
boundary; the offset in effect at the trigger was whichever write came last.
Two changes, both below: the live rate is now the Z POSITION delta over a
~0.5 s window (ZRateWindow) -- the same method the table is keyed on -- and
the offset may RISE at once but only FALLS after the rate has stayed low for
~1 s (OffsetHold). A bigger offset lands shorter, which is the safe side; a
smaller one is only worth a write once it is clearly the new steady state.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional, Sequence, Tuple

# ── The shipped table ────────────────────────────────────────────────────
# (|Z rate| in counts/s, worst overshoot in counts). Z rate is the STREAM rate:
# the Z scale POSITION delta over a short window divided by the time it took.
# The live corrector measures its input the same way (ZRateWindow), so the
# table's x-axis and the live input are one quantity measured by one method.
#
# WHY THE STREAM AND NOT THE REGISTER (2026-09-19). Until then the table was
# keyed on the firmware's trigger snapshot rate (elsStop.stopTriggerZSpeed, a
# copy of the Z scale's `speed` register) and the live input was the same
# register via fastData.scaleSpeed. Same register, but NOT the same quantity in
# practice: the snapshot reads HIGH against truth (median +1.8%, up to +7%
# against the stream over the 2026-09-12..14 passes; 1200 on the 2026-09-19
# bench passes whose true rate from rpm x feed is ~1158), while the live
# single-tick reading wandered ~1040-1140 on those same passes -- so the UI
# sized from a lower rate than the table was built on, up to ~1 count of
# under-correction, and dithered on the wander. Keying both sides on the
# position stream removes the bias rather than calibrating it out.
#
# PROVENANCE: re-derived 2026-09-18 from the protocol-10 flight-recorder
# sessions of 2026-09-12, 09-13 and 09-14 (overshoot = settled Z -
# stopTriggerZ); the rate key is the stream rate over the 300 ms before Z
# reached stopPosition (report + passes.csv of that analysis). The same
# same-speed sets keyed on the snapshot rate read 300, 596, 1187, 1640, 1692,
# 2500, 2507, 3567 -- the table this one replaces; the overshoot column is
# unchanged. It is the ENVELOPE: for each rate, the MAX over every same-speed
# set, so the curve is monotonic and never under-predicts a pass that was
# actually observed. (0, 0) is measured, not assumed: a hand-cranked approach
# overshoots by exactly zero.
#
# BOUND TO THIS MACHINE: elspi's Z scale at 5 um/count (200 counts/mm) -- the
# table is in counts -- its CL86T drive and the drive's own settings, and the
# gearing between motor and carriage (the lathe's A/B/C gearbox position and
# the leadscrew). The overshoot is the drive carrying the carriage on after the
# trigger: ~v*Td (a delay; the same carriage distance at any gearing if Td is a
# time) plus a ramp ~v^2/2a whose deceleration is in MOTOR terms, so gearing
# changes it (about half the total at the top of the table). Change any of
# those and this table is wrong -- RE-MEASURE before relying on it again.
#
# NOT a binding, although an earlier version of this comment (and the docs)
# said so: the ServoBar maxSpeed / acceleration settings. They shape only steps
# still queued, and the firmware has none queued at the trigger
# (stopTriggerStepsToGo was 0 on every recorded stop), so nothing it sends
# after the stop fires exists for them to shape. Binding the table to the
# MEASURED gearing, and characterising it in all three gearbox positions, is
# Open Loops 6ab7c598, which gates 6a92353d item 8 (2026-09-26).
OVERSHOOT_TABLE: Tuple[Tuple[int, int], ...] = (
    (0, 0),
    (280, 1),       # snapshot 300
    (583, 3),       # snapshot 596
    (1185, 9),      # snapshot 1187
    (1600, 12),     # snapshot 1640
    (1679, 17),     # snapshot 1692
    (2430, 29),     # snapshot 2500
    (2469, 32),     # snapshot 2507
    (3529, 43),     # snapshot 3567
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

# ── The live rate and the asymmetric hold (2026-09-19) ───────────────────
#: The live Z rate is the position delta over the newest RATE_WINDOW_S of
#: samples. 0.5 s is ~12 board ticks: on the 2026-09-19 passes the
#: position-derived rate's standard deviation is ~110 counts/s over one tick
#: and ~12 over this window, which still follows a feed change inside half a
#: second. The table's key used a
#: 300 ms window; the method is the same, and at a steady rate the length is
#: immaterial.
RATE_WINDOW_S = 0.5
#: No rate at all until the samples span this long: a two-tick delta is the
#: very noise the window exists to remove.
RATE_MIN_SPAN_S = 0.25
#: A gap this long between samples (a missed tick run, a stalled link) starts
#: the window over rather than averaging across it.
RATE_MAX_GAP_S = 0.3
#: The offset RISES immediately (bigger = lands shorter = safe) but FALLS only
#: after the wanted offset has stayed below the held one this long,
#: continuously -- the target is the largest offset wanted in the last
#: FALL_HOLD_S. Long enough to swallow the rate's wander at a steady feed
#: (the bench wobble crossed a count boundary every ~270 ms), short enough
#: that a real slow-down is followed within a second or two.
FALL_HOLD_S = 1.0

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

    def now(self) -> float:
        """This limiter's clock -- the corrector times its rate window by it."""
        return self._clock()

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


class ZRateWindow:
    """Live Z rate from successive Z POSITIONS: the position delta across the
    newest ~RATE_WINDOW_S of samples over the time it spans (counts/s, signed).

    The same method as the table's key (see OVERSHOOT_TABLE), and it replaces
    the single-tick `speed` register, whose wander made the offset dither.
    rate() is None until the samples span RATE_MIN_SPAN_S. clear() forgets
    everything; add() clears by itself across a gap of RATE_MAX_GAP_S.
    """

    def __init__(self, window_s: float = RATE_WINDOW_S,
                 min_span_s: float = RATE_MIN_SPAN_S,
                 max_gap_s: float = RATE_MAX_GAP_S):
        self.window_s = float(window_s)
        self.min_span_s = float(min_span_s)
        self.max_gap_s = float(max_gap_s)
        self._samples: Deque[Tuple[float, int]] = deque()

    def clear(self) -> None:
        self._samples.clear()

    def add(self, t: float, z: int) -> None:
        s = self._samples
        if s:
            dt = t - s[-1][0]
            if dt <= 0:
                return                       # same instant (or a clock step back): ignore
            if dt > self.max_gap_s:
                s.clear()
        s.append((float(t), int(z)))
        # Keep the shortest tail that still spans the window: drop the oldest
        # while the next-oldest alone would reach back far enough.
        while len(s) > 2 and t - s[1][0] >= self.window_s:
            s.popleft()

    def rate(self) -> Optional[float]:
        s = self._samples
        if len(s) < 2:
            return None
        span = s[-1][0] - s[0][0]
        if span < self.min_span_s:
            return None
        return (s[-1][1] - s[0][1]) / span


class OffsetHold:
    """Asymmetric hold on the offset TARGET (before the write limiter).

    The target is the LARGEST offset wanted over the last fall_after_s. So a
    wanted offset above the held one is taken at once -- a bigger offset fires
    earlier and lands shorter, the safe side, and there is no reason to wait --
    while the target only comes DOWN once every value wanted for a full
    fall_after_s has been lower, and then only as far as the largest of them,
    never to a momentary dip. A single wanted value back up restarts the
    second.

    interrupt() (held at the shoulder) keeps the target and restarts its
    second from the next update: time spent held is not time spent low.
    """

    def __init__(self, fall_after_s: float = FALL_HOLD_S):
        self.fall_after_s = float(fall_after_s)
        self.target: Optional[int] = None
        # (time, wanted), wanted strictly decreasing left to right: a
        # monotonic queue, so the front is always the max of the last second.
        self._recent: Deque[Tuple[float, int]] = deque()
        self._restamp = False

    def reset(self) -> None:
        """Forget the held target (correction off, disarm, new link)."""
        self.target = None
        self._recent.clear()
        self._restamp = False

    def interrupt(self) -> None:
        self._restamp = self.target is not None

    def update(self, wanted: int, now: float) -> int:
        wanted = int(wanted)
        q = self._recent
        if self._restamp:
            q.clear()
            q.append((now, int(self.target)))
            self._restamp = False
        while q and q[-1][1] <= wanted:
            q.pop()
        q.append((now, wanted))
        while len(q) > 1 and now - q[0][0] >= self.fall_after_s:
            q.popleft()
        self.target = q[0][1]
        return self.target


class StopOffsetCorrector:
    """Per-tick glue: Z positions -> rate window -> offset -> hold -> limiter
    -> ElsStopHal.

    Duck-typed so it stays Kivy-free and testable: `hal` needs
    set_stop_offset(int) and `.tick` (TickReads: enable(), active(),
    scale_index()); `board` needs `connected`, `els_stop_values` and
    `fast_data_values` (its `scaleCurrent`, the per-tick scale positions);
    `enabled()` and `margin()` read the operator settings; `notify(message)`
    posts an operator notice. Time is the limiter's clock.

    NEVER writes stopPosition. It is the exact target and this class has no
    business with it; the correction lives entirely in stopOffset.
    """

    def __init__(self, hal, board, enabled: Callable[[], bool],
                 margin: Callable[[], int], notify: Callable[[str], object],
                 limiter: Optional[OffsetWriteLimiter] = None,
                 table: Sequence[Tuple[int, int]] = OVERSHOOT_TABLE,
                 window: Optional[ZRateWindow] = None,
                 hold: Optional[OffsetHold] = None):
        self._hal = hal
        self._board = board
        self._enabled = enabled
        self._margin = margin
        self._notify = notify
        self._table = table
        self.limiter = limiter or OffsetWriteLimiter()
        self.window = window or ZRateWindow()
        self.hold = hold or OffsetHold()
        self._notified_this_pass = False
        self._was_active: Optional[bool] = None
        self._scale_index: Optional[int] = None
        self.last_rate: Optional[float] = None
        self.last_correction: Optional[Correction] = None

    @property
    def offset_in_effect(self) -> Optional[int]:
        """The last value written to stopOffset on this link (None = unknown)."""
        return self.limiter.last_written

    def reset(self) -> None:
        """New link: the firmware's register is unknown again."""
        self.limiter.reset()
        self.hold.reset()
        self.window.clear()
        self._was_active = None
        self._notified_this_pass = False

    def _sample(self, now: float, active: bool) -> None:
        """File this tick's Z position into the rate window.

        The window starts over on the active 1 -> 0 edge (Cut): the retract
        that happened while held at the shoulder runs at rapid speed, the
        wrong way, and must not be averaged into the approach. It also starts
        over if the compared scale changes, since a position delta across two
        scales is meaningless.
        """
        idx = int(self._hal.tick.scale_index())
        if idx != self._scale_index or (self._was_active and not active):
            self.window.clear()
        self._scale_index = idx
        self._was_active = active
        scales = (self._board.fast_data_values or {}).get("scaleCurrent")
        if not scales or not 0 <= idx < len(scales):
            return
        self.window.add(now, int(scales[idx]))

    def poll(self) -> Optional[int]:
        """Run one tick. Returns the value written, or None if nothing was."""
        if not self._board.connected or not self._board.els_stop_values:
            # No snapshot this tick: the link is down or the read failed. A
            # write would be a guess on a failing link; wait for a real tick.
            # (The rate window bridges or restarts on its own gap rule.)
            return None
        now = self.limiter.now()
        active = bool(self._hal.tick.active())
        self._sample(now, active)
        armed = self._hal.tick.enable()
        if not self._enabled() or not armed:
            self._notified_this_pass = False
            self.hold.reset()
            return self._write(self.limiter.force_zero(now))
        if active:
            # HELD AT THE SHOULDER (or armed-idle before the first Cut): the
            # firmware cannot fire the stop while active == 1, so an offset
            # written now does nothing but spend an exchange. Worse, the
            # carriage RETRACTS in this state -- at rapid speed, far above the
            # table -- and following it would post an above-range notice on
            # every retract. So the offset is HELD: the next pass starts with
            # the one its predecessor approached at, and the live rate takes
            # over once Cut clears active -- through the hold, so a slower
            # pass is written only after ~1 s of it, and the acceleration
            # ramp of a pass at the same feed costs no write at all (on the
            # 2026-09-19 bench every Cut wrote a 1, then climbed back through
            # 3, 10, 11 in the first second). Two exceptions: nothing written yet
            # on this link (a previous session may have left any value, so it
            # is overwritten once), and the zero on off/disarm above. Any
            # low stretch in the hold ends here: nothing measured while held
            # says the next pass is slower.
            self._notified_this_pass = False
            self.hold.interrupt()
            if self.limiter.last_written is not None:
                return None
        rate = self.window.rate()
        self.last_rate = rate
        if rate is None:
            return None
        c = correction(rate, self._margin(), self._table)
        self.last_correction = c
        if c.above_range and not self._notified_this_pass:
            self._notified_this_pass = True
            self._notify(ABOVE_RANGE_NOTICE.format(top=self._table[-1][0]))
        target = self.hold.update(c.offset, now)
        return self._write(self.limiter.update(target, now))

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
