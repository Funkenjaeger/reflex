"""The stop-overshoot table as a MACHINE SETTING: stored, bound, and checked.

WHAT CHANGED (1.2.2). Until then the table was the development lathe's,
hard-coded in els_overshoot.py and applied on any machine that switched the
correction on. Now each machine carries its own, in its ELS settings
(Els-0.yaml), so backup and restore carry it like the gearbox table. With no
table stored the correction cannot turn on.

UNITS: mm/s and mm. The firmware's offset is in Z counts, so the table is
converted to counts at use (StoredTable.in_counts) with the scale resolution
it was MEASURED under, which is part of its binding.

THE BINDING is what the table was measured under, and what makes it stale:

- the Z scale resolution, mm per count;
- the leadscrew's mm per motor step in the gearbox's REFERENCE position
  (Lead Screw Pitch and Lead Screw Pitch Steps);
- the gearbox table text.

Together the first two are the commanded steps per Z count. When any of the
three differs from the live settings the table is STALE: the correction is set
to off with a notice naming the change, and the table is kept, so putting the
setting back makes it usable again. It is checked on every board tick, so a
restart is the same check as any other moment: the table applies after a
restart only if the binding still matches.

NOT IN THE BINDING, and why:

- the engaged gearbox position. The development lathe's overshoot was
  measured in all three positions and one envelope covers them (2026-10-03);
  the correction keys on the measured Z rate, not on the gearing;
- the controller board. Protocol 11 has no board identity register, so a
  board change cannot be detected here. The outcome watch (a corrected stop
  landing past its target turns the correction off) is what catches a
  changed drive or board;
- the drive's own settings, for the same reason.

THIS MODULE IS PURE: no Kivy, no registers. The persisted strings live on
ElsDispatcher; ElsUiController reads them through here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional, Sequence, Tuple

from reflex.fsms.els_overshoot import (
    DEVELOPMENT_LATHE_MM_PER_COUNT,
    DEVELOPMENT_LATHE_MM_PER_STEP,
    DEVELOPMENT_LATHE_TABLE,
    _validate,
)

#: Decimal places a stored value keeps. 6 places of a mm is a nanometre; the
#: finest scale in use is 1 um, so nothing measured is lost.
PLACES = 6

#: `source` values. The date and gear of a measured table follow the word.
SOURCE_MIGRATED = "migrated"
SOURCE_MEASURED = "measured"


class TableTextError(ValueError):
    """The stored text is not a valid table."""


def _decimal(x: Fraction, *, up: bool = False) -> str:
    """x to PLACES decimals, rounded to nearest, or UP when `up` (an
    overshoot rounded up fires earlier and lands shorter: the safe side)."""
    scale = 10 ** PLACES
    scaled = x * scale
    n = math.ceil(scaled) if up else round(scaled)
    sign = "-" if n < 0 else ""
    n = abs(n)
    whole, frac = divmod(n, scale)
    if not frac:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{frac:0{PLACES}d}".rstrip("0")


def format_points(points: Sequence[Tuple[Fraction, Fraction]]) -> str:
    """The persisted text: ``rate:overshoot`` pairs in mm/s and mm,
    comma-separated, e.g. ``0:0, 1.4:0.005, 17.645:0.215``."""
    return ", ".join(f"{_decimal(r)}:{_decimal(o, up=True)}" for r, o in points)


def parse_points(text: str) -> Tuple[Tuple[Fraction, Fraction], ...]:
    """The points a persisted text describes; () for empty text. Raises
    TableTextError for anything else that is not a valid envelope."""
    text = (text or "").strip()
    if not text:
        return ()
    points = []
    for entry in text.split(","):
        entry = entry.strip()
        if entry.count(":") != 1:
            raise TableTextError(f"'{entry}': each point is rate:overshoot")
        r, o = entry.split(":")
        try:
            points.append((Fraction(r.strip()), Fraction(o.strip())))
        except (ValueError, ZeroDivisionError):
            raise TableTextError(f"'{entry}': not a number") from None
    try:
        _validate(points)
    except ValueError as e:
        raise TableTextError(str(e)) from None
    if len(points) < 2:
        raise TableTextError("a table needs at least one point above (0, 0)")
    return tuple(points)


def _fraction(text: str) -> Optional[Fraction]:
    try:
        f = Fraction((text or "").strip())
    except (ValueError, ZeroDivisionError):
        return None
    return f if f > 0 else None


@dataclass(frozen=True)
class Binding:
    """What a table was measured under (see the module docstring)."""

    mm_per_count: Fraction
    mm_per_step: Fraction       # reference gearbox position
    gearbox: str                # GearTable.text() of the gearbox table

    @property
    def steps_per_count(self) -> Fraction:
        return self.mm_per_count / self.mm_per_step


def stale_reason(stored: Binding, live: Binding) -> Optional[str]:
    """Why a table bound to `stored` does not describe a machine at `live`,
    as the first words of an operator notice; None when it still does."""
    if stored.mm_per_count != live.mm_per_count:
        return "Z scale changed"
    if stored.mm_per_step != live.mm_per_step:
        return "Leadscrew settings changed"
    if stored.gearbox != live.gearbox:
        return "Gearbox table changed"
    return None


@dataclass(frozen=True)
class StoredTable:
    """A table as persisted: points in mm/s and mm, its binding, its source."""

    points: Tuple[Tuple[Fraction, Fraction], ...]
    binding: Binding
    source: str

    def in_counts(self) -> Tuple[Tuple[Fraction, Fraction], ...]:
        """The points in Z counts/s and Z counts, at the resolution the table
        was MEASURED under. A binding mismatch makes the table stale before it
        is ever used, so this never converts with a different resolution."""
        k = self.binding.mm_per_count
        return tuple((r / k, o / k) for r, o in self.points)

    @property
    def top_rate_mm_s(self) -> Fraction:
        return self.points[-1][0]


def load(table_text: str, mm_per_count: str, mm_per_step: str,
         gearbox: str, source: str) -> Optional[StoredTable]:
    """The table the persisted strings describe, or None when there is none
    or it cannot be read. An unreadable table is treated as no table: the
    correction then stays off, which is the safe reading."""
    fields = (table_text, mm_per_count, mm_per_step, gearbox, source)
    if not all(isinstance(f, str) for f in fields):
        return None
    try:
        points = parse_points(table_text)
    except TableTextError:
        return None
    k, s = _fraction(mm_per_count), _fraction(mm_per_step)
    if not points or k is None or s is None:
        return None
    return StoredTable(points, Binding(k, s, gearbox or ""), source or "")


def persisted(table: StoredTable) -> dict:
    """The ElsDispatcher properties a table is stored in."""
    return {
        "els_overshoot_table": format_points(table.points),
        "els_overshoot_mm_per_count": str(table.binding.mm_per_count),
        "els_overshoot_mm_per_step": str(table.binding.mm_per_step),
        "els_overshoot_gearbox": table.binding.gearbox,
        "els_overshoot_source": table.source,
    }


def from_counts(points_counts: Sequence[Tuple[float, float]], binding: Binding,
                source: str) -> StoredTable:
    """A table measured in counts (the sweep, the migration), stored in mm.
    Overshoots are rounded UP to the stored precision, rates to nearest."""
    k = binding.mm_per_count
    pts = []
    for r, o in points_counts:
        rm = Fraction(_decimal(Fraction(r) * k))
        om = Fraction(_decimal(Fraction(o) * k, up=True))
        pts.append((rm, om))
    _validate(pts)
    return StoredTable(tuple(pts), binding, source)


def migration(live: Binding) -> Optional[StoredTable]:
    """The development lathe's built-in table as this machine's own, or None
    when this machine is not at the geometry it was measured under.

    Run once, for a machine that had the correction switched on before the
    table could be stored. The table is in counts at 200 counts/mm on an 8 TPI,
    1600-step leadscrew; anywhere else it is a guess, so None, and the caller
    turns the correction off instead.
    """
    if (live.mm_per_count != DEVELOPMENT_LATHE_MM_PER_COUNT
            or live.mm_per_step != DEVELOPMENT_LATHE_MM_PER_STEP):
        return None
    return from_counts(DEVELOPMENT_LATHE_TABLE, live, SOURCE_MIGRATED)


def status_text(table: Optional[StoredTable], reason: Optional[str]) -> str:
    """One line for the Advanced ELS Settings row."""
    if table is None:
        return "None stored"
    if reason:
        return f"Stale: {reason[0].lower()}{reason[1:]}"
    top = float(table.top_rate_mm_s)
    if table.source == SOURCE_MIGRATED:
        # Not "Built-in table": it reads as built into the software, when it
        # is this machine's saved setting, seeded from the development lathe's
        # values (first run on the development lathe, 2026-10-11).
        return f"Saved from the development lathe, up to {top:.1f} mm/s"
    return f"{table.source[:1].upper()}{table.source[1:]}, up to {top:.1f} mm/s"
