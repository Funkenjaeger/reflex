"""The coupling-ratio check: is the carriage moving the way the servo is told to?

ONE MEASUREMENT, TWO FAULTS. While a pass cuts, the servo is told to emit steps
and the Z glass scale reports where the carriage actually went. Their ratio,

        r = (Z travel in counts x steps per count) / commanded steps,

is 1.00 when the drivetrain is coupled as the controller believes. Two things
move it, and they sit far apart:

- the half nut opened mid-cut: the carriage stops, r falls to exactly 0;
- the gearbox lever in another position, on a lathe with a gearbox table
  (reflex/fsms/els_gear.py): r reads the ratio of the two positions' factors
  -- for a table A=1, B=2, C=1/2, 2 for a lever moved A -> B, 0.5 for A -> C,
  4 for C -> B.

With no gearbox (the default) there is one expected ratio, the plain leadscrew
ratio: a mismatch can only be DECOUPLED or RATIO_WRONG, never GEAR_CHANGE, and
nothing is ever adopted.

Decided responses (2026-09-03 and 2026-10-03):

- DECOUPLED: drop to stopped.
- GEAR_CHANGE: confident the lever moved to another position in the table.
  Abort the pass with a yellow notice naming it; the next Cut adopts it.
- RATIO_WRONG: moved off the expected ratio but not confidently onto another
  position. Something is wrong: abort red, adopt nothing, fail again on every
  retry.

THIS MODULE ONLY DECIDES. It is pure -- no Kivy, no registers -- and is fed one
poll at a time. ElsUiController owns acting on a verdict, and in shadow mode it
only logs and records it.

HOW IT DECIDES (the two-stage rule, decided 2026-10-03). A legitimate gear change is
far more likely than a genuinely invalid ratio, so:

1. COARSE, once the pass has run COARSE_MIN_MS and COARSE_MIN_MM, the trailing
   window is full, and the two estimates agree (a reading still changing is
   given until the deadline): the estimate since the pass started must be
   within COARSE_TOL of SOME position, or the verdict is RATIO_WRONG.
2. TIGHT: it must then settle within TIGHT_TOL of that position before the
   later of SETTLE_DEADLINE_MS and SETTLE_DEADLINE_MM, or RATIO_WRONG. Settled
   on the expected position the pass carries on (and keeps being checked);
   settled on another, GEAR_CHANGE.

Settling also needs the TRAILING window to agree with that position. An opening
half nut drags the since-start estimate from 1 down to 0 and through C's band
on the way; the trailing window is near 0 by then, so the half nut can only ever
read as DECOUPLED, never as a confident move to C.

THE TWO ESTIMATES are both least-squares slopes of Z on commanded steps, not a
difference of two endpoints, which the 2026-09-20 analysis found 2-5x tighter.
The recorder's commanded-step reading can be a poll stale against Z, which is
what spreads a short window by ~1/W (RATIO.md section 3, in the analysis folder).

WHEN IT LISTENS. Only while cutting, from the moment the take-up has
finished (takeupPending clear). The take-up is not listened to: its steps
walk the nut through the lash, so the carriage moves 39 to 196 counts in
gear A depending on where in the lash it starts (10-07), which says nothing
about the gearing.

THE PHASE CORRECTION IS LISTENED TO (decided 2026-10-11). Until then the
check also waited for it to drain (stepsToGo back to 0), about 0.4 s into a
thread pass, and a pass in the wrong gear cut that long at the wrong pitch
before the check could start. Once the lash is taken, correction steps move
the carriage like any other steps; the firmware folds the correction into
half a pitch and then biases it into the cutting direction
(fw/Core/Inc/els_phase.h), so it is never a reverse move. What remains is
that the drive lags the burst (09-07 threading set, first windows as low as
0.82). The stage-1 rule already waits while the trailing window disagrees
with the since-start estimate, and 0.82 is far above the decoupled floor,
so a lagging burst delays a verdict rather than producing a wrong one. That
is shown in the emulator (tests/system/test_els_ratio_thread_window.py),
which has no drive lag; on the lathe it is still to be confirmed.

It never runs on a retract. It stops at the end of the pass, so the
deceleration into the stop -- a genuine look-alike, r ~0.74 (RATIO.md section
4d) -- is never seen.

THE NUMBERS come from replaying 205 recorded passes (2026-09-07 threading,
09-13 turning and half-nut events, 10-03 turning in all three positions); each
constant names its evidence. All passes were in air, with no cutting load.
"""
from __future__ import annotations

import time

from dataclasses import dataclass, field
from typing import Optional

from reflex.fsms.els_gear import GearTable
from reflex.utils.ctype_calc import uint32_subtract_to_int32

# ── Constants ───────────────────────────────────────────────────────────

#: Trailing window, in polls (~40 ms each). 10 separates the three positions
#: cleanly on 10-03 (C 0.44-0.54, A 0.90-1.06, B 1.85-2.16 by regression); 5
#: does not (C reached 0.69 against the 0.71 boundary).
WINDOW_POLLS = 10

#: Fewer commanded steps than this across a window and its ratio is not
#: computed: the quotient of two tiny numbers is noise (RATIO.md --min-steps).
MIN_WINDOW_STEPS = 40

#: DECOUPLED below this absolute coupling (reference-gear terms). An open half
#: nut reads exactly 0.0000; the slowest legitimate reading on the recorded
#: lathe is its x1/2 position at ~0.5, whose 10-poll windows dipped to 0.43 on
#: 10-03. The 2026-09-20 value, 0.5, would abort every good pass there.
#: This is the CEILING; a table with a slower position lowers it (below).
DECOUPLED_BELOW = 0.25

#: With a position slower than x1/2 in the table, the floor sits at this
#: fraction of the slowest factor instead, keeping the same proportion to the
#: slowest reading as 0.25 has to 0.5 (els_gear.MIN_FACTOR bounds it).
DECOUPLED_FRACTION_OF_SLOWEST = 0.5


def decoupled_below(table: GearTable) -> float:
    """The DECOUPLED floor for `table`: DECOUPLED_BELOW, or half its slowest
    factor if that is lower."""
    return min(DECOUPLED_BELOW,
               DECOUPLED_FRACTION_OF_SLOWEST * float(table.slowest))

#: Consecutive trailing windows below DECOUPLED_BELOW before it is called.
#: RATIO.md section 6: 250-290 ms from onset at W=10, M=2.
DECOUPLED_POLLS = 2

#: Stage 1 tolerance. Every turning pass on 10-03 was inside +/-15 % by 311 ms.
COARSE_TOL = 0.15

#: Stage 1 runs at the LATER of these. Time alone would judge a slow finishing
#: pass on too few scale counts; distance alone would rush a fast one.
COARSE_MIN_MS = 350
COARSE_MIN_MM = 0.75            # 0.030 in

#: Stage 2 tolerance. Whole passes sit within 0.7 % of nominal in turning and
#: 1.0 % threading, so this is ~5x the real scatter.
TIGHT_TOL = 0.05

#: Consecutive polls inside TIGHT_TOL (with the trailing window agreeing) that
#: count as settled.
SETTLE_POLLS = 3

#: Stage 2 deadline, the LATER of these. Turning on 10-03: worst 686 ms /
#: 0.062 in to settle at +/-5 %; at slow feeds time grows while travel stays
#: small, so the distance term covers feeds slower than any recorded.
SETTLE_DEADLINE_MS = 1000
SETTLE_DEADLINE_MM = 2.0        # 0.079 in

#: After settling, a trailing window off the expected gear (but above the
#: decoupled floor) for this many polls counts as a steady partial slip even
#: while the since-start estimate still lags it. Two windows (~0.8 s): an
#: opening half nut crosses that band in a few polls and must reach
#: DECOUPLED first.
PARTIAL_SLIP_POLLS = 2 * WINDOW_POLLS

#: The trailing window must sit within this of the candidate position for the
#: since-start estimate to count as settled on it (the steadiness test).
TRAILING_AGREE_TOL = 0.15


# ── Verdicts ────────────────────────────────────────────────────────────

UNKNOWN = "unknown"          # not enough data yet, or not listening
OK = "ok"                    # settled on the expected position
DECOUPLED = "decoupled"
GEAR_CHANGE = "gear_change"
RATIO_WRONG = "ratio_wrong"

#: Verdicts that end the pass. Once one is reached it is latched.
TERMINAL = frozenset({DECOUPLED, GEAR_CHANGE, RATIO_WRONG})


@dataclass(frozen=True)
class Verdict:
    kind: str
    #: For GEAR_CHANGE: the position the lever appears to be in.
    gear: Optional[str] = None
    #: Absolute coupling (reference-gear terms) since the pass started.
    r_since_start: Optional[float] = None
    #: Absolute coupling over the trailing window.
    r_trailing: Optional[float] = None
    elapsed_ms: float = 0.0
    travel_mm: float = 0.0
    reason: str = ""


def nearest_gear(r_abs: float, table: GearTable) -> tuple[str, float]:
    """The position in `table` whose factor is nearest r_abs on a ratio
    scale, and the relative deviation r_abs / factor - 1. With no gearbox the
    only position is "" (the plain leadscrew ratio)."""
    return table.nearest(r_abs)


def _where(gear: Optional[str]) -> str:
    """A position for a log reason; "" is the plain leadscrew ratio."""
    return gear if gear else "the leadscrew ratio"


class _Fit:
    """Running least-squares slope of y on x."""

    __slots__ = ("n", "sx", "sy", "sxx", "sxy")

    def __init__(self):
        self.n = 0
        self.sx = self.sy = self.sxx = self.sxy = 0.0

    def add(self, x: float, y: float) -> None:
        self.n += 1
        self.sx += x
        self.sy += y
        self.sxx += x * x
        self.sxy += x * y

    def slope(self) -> Optional[float]:
        d = self.n * self.sxx - self.sx * self.sx
        if self.n < 3 or d <= 0:
            return None
        return (self.n * self.sxy - self.sx * self.sy) / d


def _window_slope(xs, ys) -> Optional[float]:
    f = _Fit()
    for x, y in zip(xs, ys):
        f.add(x, y)
    return f.slope()


@dataclass
class CouplingRatioCheck:
    """Fed once per poll for one pass; call start_pass() before each pass.

    steps_per_count: commanded steps per Z scale count in the EXPECTED gear
    (scale mm/count over the servo's mm/step, which already carries the gear
    factor), so a coupled pass reads 1.0 relative to it.
    gear: the position the controller expects; resolved against `table`, so
    a name not in it is the reference and, with no gearbox, "".
    mm_per_count: Z scale resolution, for the distance terms.
    table: the gearbox table (els_gear.NO_GEARBOX for none).
    """

    steps_per_count: float
    gear: str
    mm_per_count: float
    table: GearTable

    _started: bool = field(default=False, init=False)
    _t0: float = field(default=0.0, init=False)
    _s0: int = field(default=0, init=False)
    _z0: int = field(default=0, init=False)
    _fit: _Fit = field(default_factory=_Fit, init=False)
    _xs: list = field(default_factory=list, init=False)
    _ys: list = field(default_factory=list, init=False)
    _low_polls: int = field(default=0, init=False)
    _coarse_done: bool = field(default=False, init=False)
    _candidate: Optional[str] = field(default=None, init=False)
    _settle_polls: int = field(default=0, init=False)
    _settled: bool = field(default=False, init=False)
    _drift_polls: int = field(default=0, init=False)
    _trail_off_polls: int = field(default=0, init=False)
    _latched: Optional[Verdict] = field(default=None, init=False)
    _last: Verdict = field(default_factory=lambda: Verdict(UNKNOWN), init=False)

    def __post_init__(self):
        self.gear = self.table.resolve(self.gear)

    def start_pass(self, *, steps_per_count: Optional[float] = None,
                   gear: Optional[str] = None) -> None:
        if steps_per_count is not None:
            self.steps_per_count = steps_per_count
        if gear is not None:
            self.gear = self.table.resolve(gear)
        self._started = False
        self._fit = _Fit()
        self._xs, self._ys = [], []
        self._low_polls = 0
        self._coarse_done = False
        self._candidate = None
        self._settle_polls = 0
        self._settled = False
        self._drift_polls = 0
        self._trail_off_polls = 0
        self._latched = None
        self._last = Verdict(UNKNOWN)

    @property
    def verdict(self) -> Verdict:
        return self._latched or self._last

    def _abs(self, slope: Optional[float]) -> Optional[float]:
        """|slope of counts on steps| -> absolute coupling, reference terms."""
        if slope is None:
            return None
        return abs(slope) * self.steps_per_count * float(self.table.factor(self.gear))

    def feed(self, *, t_ms: float, servo_steps: int, z_counts: int,
             cutting: bool, takeup_pending: bool, steps_to_go: int) -> Verdict:
        """One poll. servo_steps is the commanded-step counter already unwrapped
        to a signed running count; z_counts the Z scale position."""
        if self._latched is not None:
            return self._latched
        if not cutting:
            return self._last
        if not self._started:
            # The take-up only. A phase correction still draining
            # (steps_to_go != 0) is listened to: see WHEN IT LISTENS.
            if takeup_pending:
                return self._last
            self._started = True
            self._t0, self._s0, self._z0 = t_ms, servo_steps, z_counts

        x = float(servo_steps - self._s0)
        y = float(z_counts - self._z0)
        self._fit.add(x, y)
        self._xs.append(x)
        self._ys.append(y)
        if len(self._xs) > WINDOW_POLLS + 1:
            del self._xs[0], self._ys[0]

        elapsed = t_ms - self._t0
        travel = abs(y) * self.mm_per_count

        r_trail = None
        if (len(self._xs) == WINDOW_POLLS + 1
                and abs(self._xs[-1] - self._xs[0]) >= MIN_WINDOW_STEPS):
            r_trail = self._abs(_window_slope(self._xs, self._ys))
        r_all = None
        if abs(x) >= MIN_WINDOW_STEPS:
            r_all = self._abs(self._fit.slope())

        def verdict(kind, gear=None, reason=""):
            return Verdict(kind, gear, r_all, r_trail, elapsed, travel, reason)

        # DECOUPLED first: it outranks everything, at any stage of the pass.
        floor = decoupled_below(self.table)
        if r_trail is not None and r_trail < floor:
            self._low_polls += 1
            if self._low_polls >= DECOUPLED_POLLS:
                return self._latch(verdict(
                    DECOUPLED, reason=f"trailing r {r_trail:.3f} < {floor:g}"))
        else:
            self._low_polls = 0

        if r_all is None:
            self._last = verdict(UNKNOWN)
            return self._last

        gear, dev = self.table.nearest(r_all)

        past_deadline = (elapsed >= SETTLE_DEADLINE_MS
                         and travel >= SETTLE_DEADLINE_MM)

        if not self._coarse_done:
            if (elapsed < COARSE_MIN_MS or travel < COARSE_MIN_MM
                    or r_trail is None):
                self._last = verdict(UNKNOWN)
                return self._last
            # STILL CHANGING: the trailing window disagrees with the estimate
            # since the start. A constant ratio has the two agree; a half nut
            # opening early in the pass does not, and judging it here would
            # call it RATIO_WRONG before the trailing window can show it
            # DECOUPLED. Wait, but not past the deadline.
            if (abs(r_trail / r_all - 1.0) > TRAILING_AGREE_TOL
                    and not past_deadline):
                self._last = verdict(UNKNOWN)
                return self._last
            self._coarse_done = True
            if abs(dev) > COARSE_TOL:
                return self._latch(verdict(
                    RATIO_WRONG,
                    reason=f"r {r_all:.3f} is {dev:+.1%} from {_where(gear)}, "
                           f"outside {COARSE_TOL:.0%} of every position"))
            self._candidate = gear

        if not self._settled:
            agrees = (gear == self._candidate and abs(dev) <= TIGHT_TOL
                      and r_trail is not None
                      and abs(r_trail / float(self.table.factor(gear)) - 1.0)
                      <= TRAILING_AGREE_TOL)
            self._settle_polls = self._settle_polls + 1 if agrees else 0
            if self._settle_polls >= SETTLE_POLLS:
                self._settled = True
                if gear != self.gear:
                    return self._latch(verdict(
                        GEAR_CHANGE, gear,
                        reason=f"r {r_all:.3f} settled on {_where(gear)} ({dev:+.1%})"))
                self._last = verdict(OK, gear)
                return self._last
            if past_deadline:
                return self._latch(verdict(
                    RATIO_WRONG,
                    reason=f"r {r_all:.3f} did not settle within "
                           f"{TIGHT_TOL:.0%} of {_where(self._candidate)}"))
            self._last = verdict(UNKNOWN, self._candidate)
            return self._last

        # Settled on the expected position: keep watching for a drift off it,
        # but only once past the settle deadline. Before that a since-start
        # estimate can settle for three polls and still wander a little: one
        # C pass on 10-03, replayed as if C were expected, settled at ~500 ms
        # and read -5.8 % at 644 ms before converging.
        if not past_deadline:
            self._last = verdict(OK, self.gear)
            return self._last
        # A DRIFT IS ONLY A DRIFT IF IT IS NOT STILL FALLING (2026-10-03,
        # shadow bench). A half nut opened 5 s into a pass dragged the
        # since-start estimate 6.8 % low within ~0.3 s, while the trailing
        # window was still on its way down to 0: the drift rule called
        # RATIO_WRONG one poll before DECOUPLED could. So a poll off the gear
        # counts only when the trailing window AGREES with the since-start
        # estimate (a steady wrong ratio), or has itself sat off the gear --
        # above the decoupled floor -- for PARTIAL_SLIP_POLLS (a steady
        # partial slip, which the since-start estimate converges on only
        # slowly). An opening half nut passes through that band far faster.
        gf = float(self.table.factor(self.gear))
        trail_off = (r_trail is not None and r_trail >= floor
                     and abs(r_trail / gf - 1.0) > TIGHT_TOL)
        self._trail_off_polls = self._trail_off_polls + 1 if trail_off else 0
        steady = (r_trail is not None
                  and abs(r_trail / r_all - 1.0) <= TRAILING_AGREE_TOL)
        if gear == self.gear and abs(dev) <= TIGHT_TOL:
            self._drift_polls = 0
        elif not (steady or self._trail_off_polls >= PARTIAL_SLIP_POLLS):
            self._drift_polls = 0
        else:
            self._drift_polls += 1
            if self._drift_polls >= SETTLE_POLLS:
                return self._latch(verdict(
                    RATIO_WRONG,
                    reason=f"r {r_all:.3f} drifted to {dev:+.1%} from {_where(gear)}"))
        self._last = verdict(OK, self.gear)
        return self._last

    def end_pass(self) -> Verdict:
        """The pass reached its stop. A steady reading on another position
        that ran out of pass before settling still counts as a gear change
        (decided 2026-10-03): positions are at least els_gear.MIN_SEPARATION
        apart, and failing every retry on a short pass would be worse."""
        if self._latched is not None:
            return self._latched
        v = self._last
        if (self._coarse_done and not self._settled
                and self._candidate is not None and self._candidate != self.gear
                and v.r_trailing is not None
                and abs(v.r_trailing / float(self.table.factor(self._candidate)) - 1.0)
                <= TRAILING_AGREE_TOL):
            return self._latch(Verdict(
                GEAR_CHANGE, self._candidate, v.r_since_start, v.r_trailing,
                v.elapsed_ms, v.travel_mm,
                reason=f"pass ended before settling; steady on {self._candidate}"))
        return v

    def _latch(self, v: Verdict) -> Verdict:
        self._latched = v
        self._last = v
        return v


# ── The glue: one per controller, polled every board tick ──────────────

class RatioWatch:
    """Feeds CouplingRatioCheck from the board's per-tick snapshots, one pass
    at a time, and reports each pass's terminal verdict exactly once.

    Duck-typed and Kivy-free, like StopOffsetCorrector: everything it needs
    arrives as an argument or a callable, so it is tested without a board.

    fsm_state()         the domain FSM state ('cutting' is a pass)
    steps_per_count()   steps per Z count in the expected gear, or None
    gear()              the gearbox position the controller expects
    gear_table()        the configured gearbox table, read at each pass start
    mm_per_count()      Z scale resolution, or None
    on_verdict(v)       called once per pass with a TERMINAL verdict

    It only DECIDES and reports; on_verdict owns any action, including none
    at all in shadow mode.
    """

    def __init__(self, hal, board, *, fsm_state, steps_per_count, gear,
                 gear_table, mm_per_count, on_verdict, now_ms=None):
        self._hal = hal
        self._board = board
        self._fsm_state = fsm_state
        self._steps_per_count = steps_per_count
        self._gear = gear
        self._gear_table = gear_table
        self._mm_per_count = mm_per_count
        self._on_verdict = on_verdict
        self._now_ms = now_ms or (lambda: time.monotonic() * 1000.0)
        self._prev_state = None
        self._check: Optional[CouplingRatioCheck] = None
        self._servo_raw = None
        self._servo = 0
        self._reported = False

    @property
    def verdict(self) -> Verdict:
        return self._check.verdict if self._check else Verdict(UNKNOWN)

    def _report(self, v: Verdict) -> None:
        if v.kind in TERMINAL and not self._reported:
            self._reported = True
            self._on_verdict(v)

    def _start(self) -> bool:
        k, mm = self._steps_per_count(), self._mm_per_count()
        if not k or not mm:
            self._check = None
            return False
        self._check = CouplingRatioCheck(steps_per_count=float(k),
                                         gear=self._gear(),
                                         mm_per_count=float(mm),
                                         table=self._gear_table())
        self._check.start_pass()
        self._servo_raw = None
        self._servo = 0
        self._reported = False
        return True

    def poll(self) -> None:
        state = self._fsm_state()
        prev, self._prev_state = self._prev_state, state
        if state == "cutting" and prev != "cutting":
            self._start()
        if state != "cutting":
            if prev == "cutting" and self._check is not None:
                self._report(self._check.end_pass())
            return
        if self._check is None:
            return
        fast = self._board.fast_data_values or {}
        raw = fast.get("servoCurrent")
        scales = fast.get("scaleCurrent")
        if raw is None or not scales:
            return
        # servoCurrent is a uint32 counter: unwrap it to a running signed count.
        if self._servo_raw is not None:
            self._servo += uint32_subtract_to_int32(raw, self._servo_raw)
        self._servo_raw = raw
        tick = self._hal.tick
        idx = tick.scale_index()
        if not 0 <= idx < len(scales):
            return
        v = self._check.feed(
            t_ms=self._now_ms(), servo_steps=self._servo,
            z_counts=int(scales[idx]), cutting=True,
            takeup_pending=tick.takeup_pending(),
            steps_to_go=int(fast.get("stepsToGo", 0) or 0))
        self._report(v)


# ── What the operator reads ─────────────────────────────────────────────
# Same contract as ELS_TAKEUP_MESSAGES in reflex/utils/devices.py, because the
# strip sits in the same place: each text stands alone, says what happened and
# then one action, and fits 65 characters, or it lands on the status chips
# under the translucent strip. test_els_ratio and test_cut_refusal_text pin
# the length for the longest names a table allows (els_gear.MAX_NAME_LEN).
#
# The names are the owner's own, so nothing here knows any of them. A name
# is shown as it is: names are unique, so the name alone says which position.
# A wrong ratio reads the same with or without a gearbox: it names the
# drivetrain, never gears, so it is correct on a lathe with none.


def gear_change_text(gear: str) -> str:
    # 53 ch, 353 px of 435 with a 3-character name.
    return f"Detected gearbox position {gear}. Press Cut to continue."


def abort_text(kind: str) -> str:
    if kind == DECOUPLED:
        # 65 ch, 408 px of 435.
        return "Carriage decoupled. Close the half nut or examine the drivetrain."
    # 52 ch, 321 px of 435.
    return "Carriage speed is incorrect. Examine the drivetrain."
