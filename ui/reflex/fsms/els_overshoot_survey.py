"""Measuring a machine's stop overshoot: the detection pass and the sweep.

Both are GUIDED AIR PASSES the operator runs with the ELS bar as usual, in
FEED, with the stop on and the tool clear of the work. While one is running the
correction is held off (stopOffset 0), so each stop shows the machine's whole
coast. Each step waits for the operator: nothing happens until the next Cut.
The stops are measured by els_stop_outcome.StopOutcomeWatch.

DETECTION ("does this machine overshoot?"), decided 2026-10-11. One pass at a
medium feed. YES when it coasts more than DETECT_THRESHOLD_COUNTS. Within one
count of the threshold one more pass is asked for, and the two must agree.
UNKNOWN, never a confident zero, when:

- the approach was slower than DETECT_MIN_RATE_MM_S, where even the
  development lathe coasts only a few counts;
- steps were still queued at the trigger (the premise fails);
- the two passes of a borderline result disagree.

NO is worded as what was measured, at that feed and below, not as "none".

THE SWEEP ("measure the table"), decided 2026-10-11. SWEEP_RATES speeds,
SWEEP_REPEATS stops at each, about five minutes in air. The operator picks the
settings, from the slowest they turn to above the fastest; the fastest becomes
the table's top, above which the correction holds its top value and says so.

GROUPED BY MEASURED SPEED, not by position (decided 2026-10-19). A counted stop
joins the group whose FIRST stop's rate it matches within SAME_RATE_FRACTION,
the nearer one if two match. The first stop is the one compared against
because it is the one the group's label describes. A stop that matches no
group starts a new one while fewer than SWEEP_RATES exist, and is not counted
once they do. A stop at a speed whose group is full is not counted either. So
the speeds may come in any order, and a group may be left and finished later.
Filing by position (stops 1-3 were group 1) trapped the operator on the lathe
on 2026-10-17: two passes at one feed, then the cued change of feed, and the
third pass was refused until the sweep was canceled.

EACH GROUP IS LABELED with how it was made: the feed selected on the ELS bar
and the spindle rpm the UI measured, both at its first stop (Setting). That is
how the operator finds the setting again to finish a group.

It REFUSES to fit when:

- the repeats at one speed disagree by more than SWEEP_MAX_SPREAD_COUNTS (the
  coast is not repeatable, and feedforward needs it to repeat), checked when
  that group fills;
- two groups came out at the same mean rate (within SAME_RATE_FRACTION). Since
  stops at one speed now join one group this is rare, but it can still happen:
  two groups whose first stops were just over SAME_RATE_FRACTION apart, and
  whose later stops landed between them. Kept because the fit needs distinct
  rates, and two points at one rate say nothing about the slope.

A stop with steps still queued, or with no rate, is not counted. The fit is
the ENVELOPE: per speed the worst coast seen, made non-decreasing, plus (0, 0).
Between (0, 0) and the slowest speed it is a straight line, which over-predicts
a coast that grows faster than linearly with speed: the safe side. The margin
is added at use, as for any table.

PURE: no Kivy, no registers. The popup and the controller drive it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from reflex.fsms.els_stop_outcome import StopOutcome

DETECT = "detect"
SWEEP = "sweep"

#: Detection: a coast above this is YES.
DETECT_THRESHOLD_COUNTS = 2
#: Detection: within this of the threshold, one more pass.
DETECT_BORDER_COUNTS = 1
#: Detection: slower than this cannot tell. On the development lathe ~3 mm/s
#: coasts 3 counts of 5 um.
DETECT_MIN_RATE_MM_S = 3.0

SWEEP_RATES = 4
SWEEP_REPEATS = 3
SWEEP_MAX_SPREAD_COUNTS = 3
#: Rates within this fraction are the same feed.
SAME_RATE_FRACTION = 0.10

# States
RUNNING = "running"
YES = "yes"
NO = "no"
UNKNOWN = "unknown"
FITTED = "fitted"
REFUSED = "refused"

FINAL = frozenset({YES, NO, UNKNOWN, FITTED, REFUSED})

# ── The cues (first run on the development lathe, 2026-10-11) ──────────────────────────
# Shown on the ELS bar strip until the next pass starts. They name the
# goal, not the button: after a stop the carriage needs a Retract before
# the next Cut, and "Press Cut again." skipped that. NEXT_SETTING is the
# gearbox measurement's (els_gear_survey.py) since 2026-10-19: the sweep's
# speeds come in any order, so it no longer cues an order.
NEXT_PASS = "Proceed with the next pass."
NEXT_SETTING = "Proceed to the next faster setting."

# ── The sweep's strip (2026-10-19) ──────────────────────────────────────
# The strip has room for ONE line: the notice strip is centered across the
# bar and translucent over the status gutter, so a line wider than
# CUE_BUDGET_PX draws over the phase-offset chip's text. Measured at
# 1024x600 with both chips up (previews/preview_phase_offset.py); every
# sweep line is checked against it at its widest values in
# test_els_overshoot_survey.py. After a pass the strip says only what that
# pass did and a count. The full tally is in the Measure window.
CUE_BUDGET_PX = 435
SWEEP_START = (f"Sweep: {SWEEP_REPEATS} passes at each of {SWEEP_RATES} "
               f"speeds, in any order.")
NOT_COUNTED = f"Pass not counted. {NEXT_PASS}"
TOO_MANY_SPEEDS = f"Pass not counted: {SWEEP_RATES} speeds already open"


def _counts(n: int) -> str:
    return f"{n} count" if n == 1 else f"{n} counts"


def round_rpm(rpm: float) -> int:
    """To the nearest 10 from 100 rpm, else to the nearest 1: the label
    finds the setting again, it does not record the speed."""
    return int(round(rpm, -1)) if rpm >= 100 else int(round(rpm))


@dataclass(frozen=True)
class Setting:
    """How one stop was made, as the operator set it: the feed selected on
    the ELS bar (its name as the bar shows it, "0.020"), that feed table's
    unit ("in/rev"), and the spindle speed the UI measured, in rpm. Any of
    them may be missing; the label then falls back to the measured speed."""

    feed: str = ""
    unit: str = ""
    rpm: Optional[float] = None

    def _rpm(self) -> Optional[int]:
        if self.rpm is None or abs(self.rpm) < 0.5:
            return None
        return round_rpm(abs(self.rpm))

    def short(self, rpm_unit: bool = True) -> str:
        """For the strip: "0.020 @ 340 rpm", or "" with nothing to say."""
        rpm = self._rpm()
        r = "" if rpm is None else (f"{rpm} rpm" if rpm_unit else f"{rpm}")
        if self.feed and r:
            return f"{self.feed} @ {r}"
        return self.feed or r

    def long(self) -> str:
        """For the Measure window: "0.020 in/rev, ~340 rpm"."""
        rpm = self._rpm()
        feed = f"{self.feed} {self.unit}".strip()
        r = "" if rpm is None else f"~{rpm} rpm"
        return ", ".join(p for p in (feed, r) if p)


@dataclass
class SweepGroup:
    """The counted stops at one speed, and how its first stop was made."""

    setting: Setting
    stops: List[StopOutcome] = field(default_factory=list)

    @property
    def first_rate(self) -> float:
        return self.stops[0].rate

    @property
    def rate(self) -> float:
        return sum(s.rate for s in self.stops) / len(self.stops)

    @property
    def full(self) -> bool:
        return len(self.stops) >= SWEEP_REPEATS

    @property
    def coasts(self) -> List[int]:
        return [s.overshoot for s in self.stops]


@dataclass
class Survey:
    """One detection pass or one sweep. Feed it each StopOutcome."""

    kind: str
    mm_per_count: float
    state: str = RUNNING
    #: The latest line for the operator (the status strip).
    message: str = ""
    #: The longer result, for the popup.
    detail: str = ""
    stops: List[StopOutcome] = field(default_factory=list)
    #: SWEEP only: the counted stops by speed, in the order each was started.
    groups: List[SweepGroup] = field(default_factory=list)
    #: FITTED only: (rate counts/s, overshoot counts), from (0, 0).
    table: Optional[Tuple[Tuple[float, float], ...]] = None
    #: FITTED only: the label of the group behind each row of `table` after
    #: (0, 0), in the same order.
    table_labels: Tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind == DETECT:
            self.message = "Detection: feed to the stop in air."
        else:
            self.message = SWEEP_START

    @property
    def done(self) -> bool:
        return self.state in FINAL

    @property
    def total(self) -> int:
        return 2 if self.kind == DETECT else SWEEP_RATES * SWEEP_REPEATS

    def _mm_s(self, rate: Optional[float]) -> float:
        return (rate or 0.0) * self.mm_per_count

    def add(self, o: StopOutcome, setting: Optional[Setting] = None) -> str:
        """File one stop, made at `setting` (the sweep labels a new group
        with it). Returns the operator line."""
        if self.done:
            return self.message
        if self.kind == DETECT:
            self._detect(o)
        else:
            self._sweep(o, setting or Setting())
        return self.message

    # ── detection ───────────────────────────────────────────────────────

    def _finish(self, state: str, message: str, detail: str = "") -> None:
        self.state = state
        self.message = message
        detail = detail or message
        if self.kind == DETECT:
            # The popup shows the detail alone: it must say the verdict.
            word = {YES: "Yes", NO: "No", UNKNOWN: "Unknown"}[state]
            if not detail.startswith(f"Detection: {word.lower()}"):
                detail = f"{word}. {detail}"
        self.detail = detail

    def _detect(self, o: StopOutcome) -> None:
        if not o.premise_ok:
            self._finish(UNKNOWN, "Detection: unknown. Steps were queued at the stop.")
            return
        v = self._mm_s(o.rate)
        if o.rate is None or v < DETECT_MIN_RATE_MM_S:
            self._finish(UNKNOWN,
                         f"Detection: unknown. Feed is too slow to tell.",
                         f"The approach was {v:.1f} mm/s; use a feed of at "
                         f"least {DETECT_MIN_RATE_MM_S:.0f} mm/s.")
            return
        self.stops.append(o)
        c = o.overshoot
        border = abs(c - DETECT_THRESHOLD_COUNTS) <= DETECT_BORDER_COUNTS
        if len(self.stops) == 1 and border:
            self.message = f"Coast {_counts(c)}, near the limit. {NEXT_PASS}"
            return
        verdicts = {s.overshoot > DETECT_THRESHOLD_COUNTS for s in self.stops}
        counts = ", ".join(str(s.overshoot) for s in self.stops)
        if len(verdicts) > 1:
            self._finish(UNKNOWN, "Detection: unknown. The two passes disagree.",
                         f"Coasts of {counts} counts at {v:.1f} mm/s, on both "
                         f"sides of {DETECT_THRESHOLD_COUNTS}.")
        elif verdicts == {True}:
            self._finish(YES, f"Detection: yes, coast {counts} counts. Run the sweep.",
                         f"The carriage coasted {counts} counts past the "
                         f"trigger at {v:.1f} mm/s. Run the sweep to measure "
                         f"this machine's table.")
        else:
            self._finish(NO, f"Detection: coast {counts} counts at {v:.1f} mm/s.",
                         f"The carriage coasted at most "
                         f"{DETECT_THRESHOLD_COUNTS} counts at {v:.1f} mm/s. "
                         f"At this feed and slower, no correction is needed.")

    # ── sweep ───────────────────────────────────────────────────────────

    def label(self, g: SweepGroup, rpm_unit: bool = True) -> str:
        """The group's strip label: its setting, or its speed without one."""
        return g.setting.short(rpm_unit) or f"{self._mm_s(g.first_rate):.1f} mm/s"

    def long_label(self, g: SweepGroup) -> str:
        """The group's label in the Measure window, with its speed."""
        v = f"{self._mm_s(g.rate):.1f} mm/s"
        lab = g.setting.long()
        return f"{lab}, {v}" if lab else v

    def _match(self, rate: float) -> Optional[SweepGroup]:
        best, off = None, SAME_RATE_FRACTION
        for g in self.groups:
            d = abs(rate / g.first_rate - 1.0)
            if d <= off:
                best, off = g, d
        return best

    def _left(self, g: SweepGroup) -> str:
        """The other speeds not finished yet, started or not."""
        left = SWEEP_RATES - 1 - sum(1 for o in self.groups if o is not g and o.full)
        if left <= 0:
            return "last speed"
        return "1 speed left" if left == 1 else f"{left} speeds left"

    def _unfinished(self, g: SweepGroup) -> Optional[SweepGroup]:
        """The first-started group other than `g` that is not full."""
        return next((o for o in self.groups if o is not g and not o.full), None)

    def _sweep(self, o: StopOutcome, setting: Setting) -> None:
        if not o.premise_ok or not o.rate:
            # No rate (the window had not filled) or a zero one: nothing to
            # key the coast on, and a zero would divide below.
            self.message = NOT_COUNTED
            return
        g = self._match(o.rate)
        if g is not None and g.full:
            self.message = (f"Pass not counted: {self.label(g)} already has "
                            f"{SWEEP_REPEATS}/{SWEEP_REPEATS}")
            return
        new = g is None
        if new:
            if len(self.groups) >= SWEEP_RATES:
                self.message = TOO_MANY_SPEEDS
                return
            g = SweepGroup(setting)
            self.groups.append(g)
        g.stops.append(o)
        self.stops.append(o)
        k = len(g.stops)
        if g.full:
            coasts = g.coasts
            spread = max(coasts) - min(coasts)
            if spread > SWEEP_MAX_SPREAD_COUNTS:
                self._finish(REFUSED,
                             f"Sweep refused: coasts spread {spread} counts at "
                             f"{self.label(g)}.",
                             f"At {self.long_label(g)} the coasts were "
                             f"{', '.join(map(str, coasts))} counts, a spread of "
                             f"{spread} (at most {SWEEP_MAX_SPREAD_COUNTS} allowed). "
                             f"The coast is not repeatable, so no table was made.")
                return
            if len(self.groups) == SWEEP_RATES and all(x.full for x in self.groups):
                self._fit()
                return
        other = self._unfinished(g)
        need = (f"{self.label(other, rpm_unit=False)} needs "
                f"{SWEEP_REPEATS - len(other.stops)} more") if other else ""
        if new and other is not None and k < SWEEP_REPEATS:
            self.message = (f"New speed: {self.label(g)}, {k}/{SWEEP_REPEATS} "
                            f"\u00b7 {need}")
        elif g.full and other is not None:
            self.message = f"{self.label(g)}: {k}/{SWEEP_REPEATS} \u00b7 {need}"
        else:
            self.message = (f"{self.label(g)}: {k}/{SWEEP_REPEATS} "
                            f"\u00b7 {self._left(g)}")

    def tally(self) -> List[str]:
        """The Measure window's rows while a sweep runs: one per group, in
        the order each was started, then the speeds not started yet."""
        rows = [f"{self.long_label(g)}: {len(g.stops)}/{SWEEP_REPEATS}, coasts "
                f"{', '.join(map(str, g.coasts))}" for g in self.groups]
        n = SWEEP_RATES - len(self.groups)
        if n:
            rows.append("1 more speed to start." if n == 1
                        else f"{n} more speeds to start.")
        return rows

    def _fit(self) -> None:
        groups = sorted(self.groups, key=lambda g: g.rate)
        pts = [(g.rate, max(g.coasts)) for g in groups]
        for (r0, _), (r1, _) in zip(pts, pts[1:]):
            if r1 / r0 - 1.0 < SAME_RATE_FRACTION:
                self._finish(REFUSED, "Sweep refused: two speeds gave the same rate.",
                             f"Rates of {self._mm_s(r0):.1f} and "
                             f"{self._mm_s(r1):.1f} mm/s are within "
                             f"{SAME_RATE_FRACTION:.0%}. Use {SWEEP_RATES} "
                             f"different speeds.")
                return
        table = [(0.0, 0.0)]
        top = 0
        for r, c in pts:
            top = max(top, c)
            table.append((round(r, 1), float(top)))
        self.table = tuple(table)
        self.table_labels = tuple(self.long_label(g) for g in groups)
        rows = "\n".join(f"{lab}: {int(c)}"
                         for lab, (_, c) in zip(self.table_labels, table[1:]))
        self._finish(FITTED, "Sweep done. Save or discard the new table.",
                     f"The new table, the worst coast at each speed in counts:\n"
                     f"{rows}")
