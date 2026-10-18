"""Measuring one gearbox position's factor: three guided air passes.

The gearbox table (els_gear.py) holds a factor per lever position: carriage
travel per motor step relative to the FIRST position, the reference. Typing a
factor from the gear plate gives the plate's number, not the machine's; this
measures it. The operator puts the lever in ONE position, then cuts
GEAR_PASSES passes in air with the ELS bar as usual. Each pass's coupling,
carriage travel per commanded step in reference-gear terms, is the lever
position's factor; WholePassRatio measures it and each pass's reading is fed
to this class.

The coupling-ratio check (els_ratio.py) runs during these passes too, held so
it cannot act, and its estimate since the pass started is not the
measurement: the check latches its verdict as soon as the reading has settled
within 5 % of a position, from 350 ms into the pass and at the latest by the
later of 1 s and 2 mm, because its job is to tell positions apart.
WholePassRatio fits its own slope, from SETTLE_MS after the take-up to the
stop, and the check's verdict only says whether the pass counts at all.

The passes must agree: the readings may spread by at most GEAR_MAX_SPREAD of
their mean. The gate comes from the scatter of whole passes on the recorded
lathe, 1.002 +/- 0.003 (0.3 %); three passes may sit anywhere in that band, so
the limit is its full width, 0.6 %. It is relative, not absolute: scatter
scales with the reading, and a fixed 0.006 would refuse a good x2 position.
A tighter gate would cost the operator more passes on a good position, and a
loose one is still caught by the rounding to a simple fraction and by the
separation from the other positions.

The result is a PROPOSAL, not a change: Save writes it, Discard drops it. It is
rounded to a form GearTable.parse accepts, and refused when it could not be
saved: outside MIN_FACTOR..MAX_FACTOR, or closer than MIN_SEPARATION to
another position. The REFERENCE position is never measured; its factor is 1 by
definition.

States: RUNNING, PROPOSED, REFUSED. A pass with no reading, a decoupled one
(half nut open), or one shorter than MIN_COUNTS, is not counted and is asked
for again.

PURE: no Kivy, no registers. RatioWatch feeds WholePassRatio while the
measurement holds it; the guard and the popup drive the rest.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import List, Optional

from reflex.fsms.els_gear import (
    MAX_FACTOR,
    MIN_FACTOR,
    MIN_SEPARATION,
    GearTable,
    GearTableError,
    _fmt,
)
from reflex.fsms.els_overshoot_survey import NEXT_PASS, NEXT_SETTING  # noqa: F401
from reflex.fsms.els_ratio import DECOUPLED, Verdict, _Fit

#: Air passes per position.
GEAR_PASSES = 3

#: The most the readings may spread, as a fraction of their mean: the full
#: width of the +/- 0.3 % whole-pass scatter (see above).
GEAR_MAX_SPREAD = Fraction(6, 1000)

#: A proposal within this fraction of a simple fraction becomes that fraction
#: (a x2 or x3/2 position reads 1.998 and is 2): the scatter's half width.
SNAP_TOLERANCE = Fraction(3, 1000)
#: "Simple" means a denominator up to this.
SNAP_MAX_DENOMINATOR = 12
#: Otherwise a decimal of this many places.
DECIMAL_PLACES = 3

#: The start of a pass that is not measured, after the take-up has ended:
#: the drive lags a step burst, so the first windows read low (as low as 0.82
#: of the true ratio).
SETTLE_MS = 300

#: The least Z travel, in scale counts after the settle, that counts as a
#: pass. A count is the reading's quantum; over 1000 of them it is at most
#: 0.1 % at the two ends and far less across a fitted slope, a sixth of the
#: gate.
MIN_COUNTS = 1000

#: The least number of polls in the fit, so a pass of a few fast polls is
#: not a slope through three points.
MIN_POLLS = 10

# States
RUNNING = "running"
PROPOSED = "proposed"
REFUSED = "refused"

FINAL = frozenset({PROPOSED, REFUSED})


@dataclass(frozen=True)
class PassReading:
    """One pass, measured. `r` is None when the pass does not count, and
    `why` then says why, for the log."""

    r: Optional[float]
    counts: int = 0
    polls: int = 0
    why: str = ""


class WholePassRatio:
    """The coupling over one whole pass: the least-squares slope of Z counts
    on commanded steps, from SETTLE_MS after the take-up ends to the stop, in
    reference-gear terms. Fed once per poll; a fresh one per pass.

    steps_per_ref_count: commanded steps per Z count with the lever in the
    reference position, so a pass in a position of factor f reads f.
    """

    def __init__(self, steps_per_ref_count: float):
        self._k = float(steps_per_ref_count)
        self._t_start: Optional[float] = None
        self._s0 = 0
        self._z0 = 0
        self._z_last = 0
        self._fit = _Fit()

    def feed(self, *, t_ms: float, servo_steps: int, z_counts: int,
             takeup_pending: bool) -> None:
        """One poll. servo_steps is the commanded-step counter unwrapped to a
        signed running count; z_counts the Z scale position."""
        if takeup_pending:
            # Before the take-up has ended; a pass that re-enters it starts
            # the settle again.
            self._t_start = None
            self._fit = _Fit()
            return
        if self._t_start is None:
            self._t_start = t_ms
        if t_ms - self._t_start < SETTLE_MS:
            return
        if self._fit.n == 0:
            self._s0, self._z0 = servo_steps, z_counts
        self._fit.add(float(servo_steps - self._s0), float(z_counts - self._z0))
        self._z_last = z_counts

    def reading(self) -> PassReading:
        polls = self._fit.n
        counts = abs(self._z_last - self._z0) if polls else 0
        if polls == 0:
            # A refused take-up ends the pass before it ever settles.
            return PassReading(None, 0, 0, "nothing measured after the take-up")
        if polls < MIN_POLLS or counts < MIN_COUNTS:
            return PassReading(None, counts, polls,
                               f"too short: {counts} counts over {polls} polls "
                               f"(at least {MIN_COUNTS} and {MIN_POLLS})")
        slope = self._fit.slope()
        if slope is None:
            return PassReading(None, counts, polls, "no slope")
        return PassReading(abs(slope) * self._k, counts, polls)


def refusal_for(table: GearTable, position: str) -> Optional[str]:
    """Why `position` cannot be measured in `table`, or None. At most 65
    characters."""
    if not table.has_gearbox:
        return "No gearbox table. Enter the positions first."
    if position not in table.names:
        return "Choose a position from the gearbox table."
    if position == table.reference:
        return f"{position} is the reference. Its factor is 1."
    return None


def round_factor(r: float) -> Fraction:
    """A measured coupling as a factor GearTable.parse accepts: the nearest
    simple fraction when the reading is within SNAP_TOLERANCE of one, else a
    decimal of DECIMAL_PLACES places."""
    exact = Fraction(f"{r:.{DECIMAL_PLACES + 3}f}")
    near = exact.limit_denominator(SNAP_MAX_DENOMINATOR)
    if near > 0 and abs(near / exact - 1) <= SNAP_TOLERANCE:
        return near
    return Fraction(f"{r:.{DECIMAL_PLACES}f}")


@dataclass
class GearSurvey:
    """One position's measurement. Feed it each pass's Verdict and reading."""

    position: str
    table: GearTable
    state: str = RUNNING
    #: The latest line for the operator (the status strip).
    message: str = ""
    #: The longer result, for the popup.
    detail: str = ""
    #: Each counted pass's whole-pass coupling.
    readings: List[float] = field(default_factory=list)
    #: PROPOSED only: the position's measured factor.
    factor: Optional[Fraction] = None

    def __post_init__(self):
        self.message = (f"Gear {self.position}: {GEAR_PASSES} passes in air. "
                        f"Lever in {self.position}.")

    @property
    def done(self) -> bool:
        return self.state in FINAL

    def add(self, v: Verdict, reading: Optional[PassReading]) -> str:
        """File one pass: the coupling check's final verdict, which only says
        whether the pass counts (a decoupled one does not), and the pass's
        whole-pass reading. Returns the operator line."""
        if self.done:
            return self.message
        r = reading.r if reading is not None else None
        if r is None or r <= 0 or v.kind == DECOUPLED:
            short = reading is not None and reading.why.startswith("too short")
            self.message = (f"Pass too short to measure. {NEXT_PASS}" if short
                            and v.kind != DECOUPLED
                            else f"Pass not counted. {NEXT_PASS}")
            return self.message
        self.readings.append(float(r))
        n = len(self.readings)
        if n < GEAR_PASSES:
            self.message = f"Pass {n}/{GEAR_PASSES}: r {r:.3f}. {NEXT_PASS}"
            return self.message
        self._decide()
        return self.message

    def _refuse(self, message: str, detail: str) -> None:
        self.state = REFUSED
        self.message = message
        self.detail = detail

    def _decide(self) -> None:
        pos = self.position
        got = [Fraction(f"{r:.6f}") for r in self.readings]
        mean = sum(got) / len(got)
        spread = (max(got) - min(got)) / mean
        listed = ", ".join(f"{r:.3f}" for r in self.readings)
        if spread > GEAR_MAX_SPREAD:
            self._refuse(
                f"Gear {pos} refused: readings spread {float(spread):.2%}.",
                f"The passes read {listed}, a spread of {float(spread):.2%} "
                f"(at most {float(GEAR_MAX_SPREAD):.2%} allowed). The "
                f"coupling is not repeatable, so no factor was proposed.")
            return
        factor = round_factor(float(mean))
        if not MIN_FACTOR <= factor <= MAX_FACTOR:
            self._refuse(
                f"Gear {pos} refused: factor {_fmt(factor)} is out of range.",
                f"The passes read {listed}. A factor must be between "
                f"{_fmt(MIN_FACTOR)} and {_fmt(MAX_FACTOR)}.")
            return
        for name, other in self.table.positions:
            if name == pos:
                continue
            if max(factor, other) / min(factor, other) < MIN_SEPARATION:
                self._refuse(
                    f"Gear {pos} refused: too close to {name}.",
                    f"The passes read {listed}, a factor of {_fmt(factor)}. "
                    f"{pos} and {name} (factor {_fmt(other)}) must be at "
                    f"least {float(MIN_SEPARATION):g}x apart. Check the "
                    f"lever is in {pos}.")
                return
        self.factor = factor
        self.state = PROPOSED
        self.message = f"Gear {pos}: factor {_fmt(factor)}. Save or discard."
        self.detail = (f"The passes read {listed}, a spread of "
                       f"{float(spread):.2%}. Position {pos} has factor "
                       f"{_fmt(factor)}. Save writes it to the gearbox table; "
                       f"the other positions are unchanged.")

    def updated_table(self) -> GearTable:
        """The table with this position's measured factor. Built from the
        parsed table and checked by parsing its own text, so it is exactly
        what GearTable accepts. Raises GearTableError (or ValueError with no
        proposal) when it is not."""
        if self.state != PROPOSED or self.factor is None:
            raise ValueError("no proposed factor")
        positions = tuple((n, self.factor if n == self.position else f)
                          for n, f in self.table.positions)
        return GearTable.parse(replace(self.table, positions=positions).text())


__all__ = [
    "GEAR_PASSES", "GEAR_MAX_SPREAD", "RUNNING", "PROPOSED", "REFUSED", "FINAL",
    "NEXT_PASS", "NEXT_SETTING", "GearSurvey", "GearTableError", "MIN_COUNTS",
    "MIN_POLLS", "PassReading", "SETTLE_MS", "WholePassRatio", "refusal_for",
    "round_factor",
]
