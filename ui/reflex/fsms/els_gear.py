"""The lathe's gearbox between the servo and the leadscrew, if it has one:
which positions it has, which one is engaged, and what that does to every
number measured in motor steps.

NOT EVERY LATHE HAS ONE. The default is NO_GEARBOX: the servo drives the
leadscrew directly, every factor is 1, nothing on screen names a position, and
the coupling check expects the plain leadscrew ratio.

A GEARBOX is a table of 2 to 4 positions, each a name the owner chooses (1 to
3 characters, anything but spaces, commas and "=") and a factor: carriage
travel per motor step relative to the FIRST position, which is the reference
and has factor 1. Moving the lever changes how far the carriage travels per
motor step, and nothing in the controller can see the lever: the servo is open
loop, and the Z scale only ever reports where the carriage went.

THE TEXT FORM is what the owner types and what is persisted (the servo's
``gearbox_table`` setting): ``name=factor`` entries separated by commas or
spaces, the reference first, e.g. ``A=1, B=2, C=1/2`` or ``Lo=1, Hi=3/2``.
A factor is a decimal or a fraction. The reference's ``=1`` may be left out.
Empty text is no gearbox.

THE LIMITS, and why:

- factors between MIN_FACTOR and MAX_FACTOR (1/8 and 8). Beyond that the
  cut-start margin, which is sized for the fastest position, grows past any
  useful distance, and the decoupled floor of the coupling check (half the
  slowest factor) gets close to the noise of a stalled carriage;
- every two factors at least MIN_SEPARATION (1.2x) apart. The coupling check
  names a position by settling within 5 % of its factor; 1.2x keeps those
  bands apart with room for the ~1 % scatter of a whole pass, so two positions
  can never both claim a reading.

THE CONVENTION. Every step-valued setting -- Lead Screw Pitch Steps, the
backlash take-up, the calibration legs -- is stored as measured in the
REFERENCE position. The engaged position enters in exactly two places:

- ServoDispatcher.configure_lead_screw_ratio multiplies the leadscrew's
  mm-per-step by the position's factor, so the sync ratio, thread geometry,
  retract moves and the cut-start margin all see the real carriage travel per
  step without each of them knowing about gears;
- GearTable.steps_in_gear() converts a stored step count to the engaged
  position at the point it is written to the firmware. The play being taken
  up is on the leadscrew side of the lever, so a fixed carriage distance needs
  half the motor steps in a x2 position and twice as many in a x1/2 one. A
  reference-sized take-up was refused three times in a x1/2 position for
  exactly this reason before this conversion existed.

A position name that is not in the table (a hand-edited file, a table changed
since) reads as the reference. With no gearbox, a stored position is ignored.
"""
from __future__ import annotations

import math
import re

from dataclasses import dataclass
from fractions import Fraction

#: How many positions a gearbox table may have.
MIN_POSITIONS = 2
MAX_POSITIONS = 4

#: Position names are 1 to this many characters (the main-screen label and
#: the gear-change notice are sized for it).
MAX_NAME_LEN = 3

#: The factor range, relative to the reference position.
MIN_FACTOR = Fraction(1, 8)
MAX_FACTOR = Fraction(8)

#: The smallest ratio allowed between any two factors.
MIN_SEPARATION = Fraction(6, 5)

_ENTRY_SPLIT = re.compile(r"[,\s]+")


class GearTableError(ValueError):
    """The text is not a valid gearbox table; the message says why, in words
    an operator can act on."""


def _fmt(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def _parse_factor(name: str, raw: str) -> Fraction:
    try:
        f = Fraction(raw)
    except (ValueError, ZeroDivisionError):
        raise GearTableError(
            f"{name}: factor must be a number like 2, 0.5 or 3/2") from None
    if not MIN_FACTOR <= f <= MAX_FACTOR:
        raise GearTableError(
            f"{name}: factor must be between {_fmt(MIN_FACTOR)} and "
            f"{_fmt(MAX_FACTOR)}")
    return f


@dataclass(frozen=True)
class GearTable:
    """An immutable gearbox table. ``positions`` is empty for no gearbox;
    otherwise (name, factor) pairs, reference first, factor 1."""

    positions: tuple[tuple[str, Fraction], ...] = ()

    # ── Construction ────────────────────────────────────────────────────

    @classmethod
    def parse(cls, text: str) -> "GearTable":
        """The table a typed or persisted text describes. Raises
        GearTableError with the reason when it is not a valid table."""
        text = (text or "").strip()
        if not text:
            return NO_GEARBOX
        entries = [e for e in _ENTRY_SPLIT.split(text) if e]
        if not MIN_POSITIONS <= len(entries) <= MAX_POSITIONS:
            raise GearTableError(
                f"a gearbox needs {MIN_POSITIONS} to {MAX_POSITIONS} positions "
                f"(leave it empty for none)")
        positions: list[tuple[str, Fraction]] = []
        seen: dict[str, str] = {}
        for i, entry in enumerate(entries):
            if entry.count("=") > 1:
                raise GearTableError(f"'{entry}': each entry is name=factor")
            if "=" in entry:
                name, raw = entry.split("=", 1)
            elif i == 0:
                name, raw = entry, "1"
            else:
                raise GearTableError(f"'{entry}': each entry is name=factor")
            if not 1 <= len(name) <= MAX_NAME_LEN:
                raise GearTableError(
                    f"'{name}': position names are 1 to {MAX_NAME_LEN} "
                    f"characters")
            key = name.casefold()
            if key in seen:
                raise GearTableError(f"'{name}': a position is named twice")
            seen[key] = name
            factor = _parse_factor(name, raw)
            if i == 0 and factor != 1:
                raise GearTableError(
                    f"{name}: the first position is the reference; its "
                    f"factor is 1")
            positions.append((name, factor))
        for i, (a, fa) in enumerate(positions):
            for b, fb in positions[i + 1:]:
                if max(fa, fb) / min(fa, fb) < MIN_SEPARATION:
                    raise GearTableError(
                        f"{a} and {b} are too close (factors must be at least "
                        f"{float(MIN_SEPARATION):g}x apart)")
        return cls(tuple(positions))

    def text(self) -> str:
        """The canonical text form: what is persisted and shown."""
        return ", ".join(f"{n}={_fmt(f)}" for n, f in self.positions)

    # ── Reading it ──────────────────────────────────────────────────────

    @property
    def has_gearbox(self) -> bool:
        return bool(self.positions)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(n for n, _f in self.positions)

    @property
    def reference(self) -> str:
        """The reference position's name; "" with no gearbox."""
        return self.positions[0][0] if self.positions else ""

    @property
    def _effective(self) -> tuple[tuple[str, Fraction], ...]:
        # No gearbox behaves as one unnamed position at factor 1: the plain
        # leadscrew ratio.
        return self.positions or (("", Fraction(1)),)

    @property
    def fastest(self) -> Fraction:
        return max(f for _n, f in self._effective)

    @property
    def slowest(self) -> Fraction:
        return min(f for _n, f in self._effective)

    def resolve(self, gear: str) -> str:
        """The position that `gear` means: itself if it is in the table, the
        reference otherwise ("" with no gearbox).

        A persisted value from another table (or a hand-edited file) must not
        take the machine down at load, and the reference is what every stored
        setting assumes anyway.
        """
        return gear if gear in self.names else self.reference

    def factor(self, gear: str) -> Fraction:
        """Carriage travel per motor step in `gear`, relative to the
        reference; an unknown name reads as the reference."""
        return dict(self._effective)[self.resolve(gear)]

    def describe(self, gear: str) -> str:
        """The name as the operator reads it, with a speed hint taken from the
        factor alone: "B (fast)" above the reference, "C (slow)" below."""
        f = self.factor(gear)
        if f > 1:
            return f"{gear} (fast)"
        if f < 1:
            return f"{gear} (slow)"
        return gear

    def steps_in_gear(self, reference_steps: int, gear: str) -> int:
        """A step count stored in the reference position, converted to `gear`.

        Rounded to nearest, never to zero from a nonzero input: a take-up of
        one step must not silently vanish in a fast position.
        """
        reference_steps = int(reference_steps)
        if reference_steps == 0:
            return 0
        out = int(round(Fraction(reference_steps) / self.factor(gear)))
        if out == 0:
            return 1 if reference_steps > 0 else -1
        return out

    def worst_case_travel_factor(self, gear: str) -> Fraction:
        """How much further a motor step can move the carriage than `gear`
        expects.

        The lever can be in any position while the controller still believes
        in the last one it knew. Motion the firmware commits before the
        coupling check can read anything (the take-up, the thread phase
        correction) is therefore sized for the FASTEST position: for A=1, B=2,
        C=1/2 that is 2x in A, 4x in C, 1x in B. With no gearbox, 1x.
        """
        return self.fastest / self.factor(gear)

    def nearest(self, r_abs: float) -> tuple[str, float]:
        """The position whose factor is nearest r_abs on a ratio scale, and
        the relative deviation r_abs / factor - 1. With no gearbox the only
        position is "" (the plain leadscrew ratio)."""
        eff = self._effective
        if r_abs <= 0.0:
            name = min(eff, key=lambda p: p[1])[0]
            return name, -1.0
        name, f = min(eff, key=lambda p: abs(math.log(r_abs / float(p[1]))))
        return name, r_abs / float(f) - 1.0


#: The default: the servo drives the leadscrew directly.
NO_GEARBOX = GearTable()


def table_of(servo) -> GearTable:
    """The gearbox table a servo carries, or NO_GEARBOX for one that carries
    none (a rotary configuration, or a test double)."""
    table = getattr(servo, "gear_table", None)
    return table if isinstance(table, GearTable) else NO_GEARBOX
