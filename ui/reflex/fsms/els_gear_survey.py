"""Measuring one gearbox position's factor: three guided air passes.

The gearbox table (els_gear.py) holds a factor per lever position: carriage
travel per motor step relative to the FIRST position, the reference. Typing a
factor from the gear plate gives the plate's number, not the machine's; this
measures it. The operator puts the lever in ONE position, then cuts
GEAR_PASSES passes in air with the ELS bar as usual. The coupling-ratio check
(els_ratio.py) already reads, for every pass, the absolute coupling since the
pass started -- `Verdict.r_since_start`, in reference-gear terms -- and in the
lever's position that IS the position's factor. Each pass's reading is fed to
this class.

The passes must agree: the readings may spread by at most GEAR_MAX_SPREAD of
their mean. The gate comes from the scatter of whole passes on the recorded
lathe, 1.002 +/- 0.003 (0.3 %); three passes may sit anywhere in that band, so
the limit is its full width, 0.6 %. It is relative, not absolute: scatter
scales with the reading, and a fixed 0.006 would refuse a good x2 position.

The result is a PROPOSAL, not a change: Save writes it, Discard drops it. It is
rounded to a form GearTable.parse accepts, and refused when it could not be
saved: outside MIN_FACTOR..MAX_FACTOR, or closer than MIN_SEPARATION to
another position. The REFERENCE position is never measured; its factor is 1 by
definition.

States: RUNNING, PROPOSED, REFUSED. A pass with no reading, or a decoupled one
(half nut open), is not counted and is asked for again.

PURE: no Kivy, no registers. The guard and the popup drive it.
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
from reflex.fsms.els_ratio import DECOUPLED, Verdict

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

# States
RUNNING = "running"
PROPOSED = "proposed"
REFUSED = "refused"

FINAL = frozenset({PROPOSED, REFUSED})


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
    """One position's measurement. Feed it each pass's Verdict."""

    position: str
    table: GearTable
    state: str = RUNNING
    #: The latest line for the operator (the status strip).
    message: str = ""
    #: The longer result, for the popup.
    detail: str = ""
    #: Each counted pass's r_since_start.
    readings: List[float] = field(default_factory=list)
    #: PROPOSED only: the position's measured factor.
    factor: Optional[Fraction] = None

    def __post_init__(self):
        self.message = (f"Gear {self.position}: {GEAR_PASSES} passes in air. "
                        f"Lever in {self.position}.")

    @property
    def done(self) -> bool:
        return self.state in FINAL

    def add(self, v: Verdict) -> str:
        """File one pass. Returns the operator line."""
        if self.done:
            return self.message
        r = v.r_since_start
        if r is None or r <= 0 or v.kind == DECOUPLED:
            self.message = f"Pass not counted. {NEXT_PASS}"
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
    "NEXT_PASS", "NEXT_SETTING", "GearSurvey", "GearTableError", "refusal_for",
    "round_factor",
]
