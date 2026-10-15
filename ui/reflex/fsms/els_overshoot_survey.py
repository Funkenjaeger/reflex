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

THE SWEEP ("measure the table"), decided 2026-10-11. SWEEP_RATES feeds,
SWEEP_REPEATS stops at each, about five minutes in air. The operator picks the
feeds, slow to the fastest they use; the fastest becomes the table's top, above
which the correction holds its top value and says so. It REFUSES to fit when:

- the repeats at one feed disagree by more than SWEEP_MAX_SPREAD_COUNTS (the
  coast is not repeatable, and feedforward needs it to repeat);
- two feeds came out at the same rate (within SAME_RATE_FRACTION).

A stop with steps still queued, or whose rate is off its group's by more than
SAME_RATE_FRACTION (the feed changed mid-group), is not counted and is asked for
again. The fit is the ENVELOPE: per feed the worst coast seen, made
non-decreasing, plus (0, 0). Between (0, 0) and the slowest feed it is a straight
line, which over-predicts a coast that grows faster than linearly with speed:
the safe side. The margin is added at use, as for any table.

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
# the next Cut, and "Press Cut again." skipped that. After the last feed
# the spindle speed has to rise instead, so the cue says "setting".
NEXT_PASS = "Proceed with the next pass."
NEXT_SETTING = "Proceed to the next faster setting."


def _counts(n: int) -> str:
    return f"{n} count" if n == 1 else f"{n} counts"


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
    #: FITTED only: (rate counts/s, overshoot counts), from (0, 0).
    table: Optional[Tuple[Tuple[float, float], ...]] = None

    def __post_init__(self):
        if self.kind == DETECT:
            self.message = "Detection: feed to the stop in air."
        else:
            self.message = (f"Sweep: {SWEEP_RATES} feeds, {SWEEP_REPEATS} stops "
                            f"each. Start with the slowest.")

    @property
    def done(self) -> bool:
        return self.state in FINAL

    @property
    def total(self) -> int:
        return 2 if self.kind == DETECT else SWEEP_RATES * SWEEP_REPEATS

    def _mm_s(self, rate: Optional[float]) -> float:
        return (rate or 0.0) * self.mm_per_count

    def add(self, o: StopOutcome) -> str:
        """File one stop. Returns the operator line."""
        if self.done:
            return self.message
        if self.kind == DETECT:
            self._detect(o)
        else:
            self._sweep(o)
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

    def _sweep(self, o: StopOutcome) -> None:
        n = len(self.stops)
        if not o.premise_ok or not o.rate:
            # No rate (the window had not filled) or a zero one: nothing to
            # key the coast on, and a zero would divide below.
            self.message = f"Pass {n + 1} not counted. {NEXT_PASS}"
            return
        group, k = divmod(n, SWEEP_REPEATS)
        if k:
            first = self.stops[group * SWEEP_REPEATS].rate
            if abs(o.rate / first - 1.0) > SAME_RATE_FRACTION:
                self.message = (f"Speed changed. Pass {n + 1} not counted. "
                                f"Repeat it at the same setting.")
                return
        self.stops.append(o)
        n += 1
        if n % SWEEP_REPEATS:
            self.message = (f"Pass {n}/{self.total}: coast {_counts(o.overshoot)}. "
                            f"{NEXT_PASS}")
            return
        g = self.stops[n - SWEEP_REPEATS:n]
        coasts = [s.overshoot for s in g]
        spread = max(coasts) - min(coasts)
        v = self._mm_s(sum(s.rate for s in g) / len(g))
        if spread > SWEEP_MAX_SPREAD_COUNTS:
            self._finish(REFUSED, f"Sweep refused: coasts spread {spread} counts.",
                         f"At {v:.1f} mm/s the coasts were "
                         f"{', '.join(map(str, coasts))} counts, a spread of "
                         f"{spread} (at most {SWEEP_MAX_SPREAD_COUNTS} allowed). "
                         f"The coast is not repeatable, so no table was made.")
            return
        if n < self.total:
            self.message = (f"Pass {n}/{self.total}: coast {_counts(o.overshoot)}. "
                            f"{NEXT_SETTING}")
            return
        self._fit()

    def _fit(self) -> None:
        groups = [self.stops[i:i + SWEEP_REPEATS]
                  for i in range(0, len(self.stops), SWEEP_REPEATS)]
        pts = sorted((sum(s.rate for s in g) / len(g), max(s.overshoot for s in g))
                     for g in groups)
        for (r0, _), (r1, _) in zip(pts, pts[1:]):
            if r1 / r0 - 1.0 < SAME_RATE_FRACTION:
                self._finish(REFUSED, "Sweep refused: two feeds gave the same rate.",
                             f"Rates of {self._mm_s(r0):.1f} and "
                             f"{self._mm_s(r1):.1f} mm/s are within "
                             f"{SAME_RATE_FRACTION:.0%}. Use {SWEEP_RATES} "
                             f"different feeds.")
                return
        table = [(0.0, 0.0)]
        top = 0
        for r, c in pts:
            top = max(top, c)
            table.append((round(r, 1), float(top)))
        self.table = tuple(table)
        rows = ", ".join(f"{self._mm_s(r):.1f} mm/s: {int(c)}" for r, c in table[1:])
        self._finish(FITTED, "Sweep done. Save or discard the new table.",
                     f"Worst coast per feed, in counts: {rows}.")
