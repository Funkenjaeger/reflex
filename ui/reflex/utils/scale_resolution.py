"""Scale resolution <-> ratio, for the input setup screen.

WHY. An input's scale is stored as ratioNum/ratioDen, which is millimetres per
count. Entering a linear scale that way means doing arithmetic to express a
number the scale's own sticker already states -- and nothing on screen said the
ratio was in millimetres at all, which on a machine otherwise run in inches is
a trap that produces a wrong number the operator has no reason to doubt. Evan,
2026-08-31, after re-provisioning X.

MICRONS, NOT THE DISPLAY UNIT. Linear scale stickers are marked in microns
("1um", "5um") regardless of what the operator otherwise works in, so this is
the number being copied off the hardware. Expressing it in inches would be
0.00003937 in/count -- technically the same fact and useless to type.

RATIO ENTRY SURVIVES BECAUSE DRIVE RATIOS ARE RATIOS, not because of a
precision trap. The trap was the stated reason when this was filed -- "non-2/5
denominators have no terminating decimal", e.g. 1/3 mm per count is 333.333...
um -- and MEASURED 2026-09-01 it does not happen: limit_denominator recovers
1/3, 1/7, 22/7, 127/64000 and even 1/999983 exactly from the float. The reason
that argument is wrong is that a best-rational-approximation search does not
care whether the decimal terminated.

What is actually true is simpler and was Evan's own instinct: a real drive
ratio (the servo's 127/64000) is NAMED as a ratio by whoever specified the
hardware, and asking for its micron equivalent is a conversion the operator
should not have to do or check. That is why both forms exist.

The one case resolution genuinely cannot carry is a denominator past MAX_DEN,
which round_trips_exactly still catches. It is exotic; it is not the reason.
"""
from fractions import Fraction

UM_PER_MM = 1000

# Ceiling on the denominator when converting a typed resolution back to a
# ratio. Generous enough that any real scale lands exactly (a 1 um head is
# 1/1000, a 5 um head 1/200, a 2.5 um head 1/400) without letting a float
# artifact become a 6-digit denominator.
MAX_DEN = 1_000_000


def resolution_um(ratio_num, ratio_den) -> float:
    """Microns per count for a stored ratio. 0.0 for a degenerate ratio."""
    try:
        if int(ratio_den) == 0:
            return 0.0
        return float(Fraction(int(ratio_num), int(ratio_den)) * UM_PER_MM)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def ratio_from_resolution_um(microns) -> tuple:
    """(ratioNum, ratioDen) for a resolution in microns per count.

    The value is taken through str() so a typed decimal is read as the decimal
    it is: Fraction(2.5) is exact, but Fraction(0.1) carries the binary
    representation error and would produce a denominator in the trillions.
    """
    try:
        exact = Fraction(str(microns)) / UM_PER_MM
    except (TypeError, ValueError, ZeroDivisionError):
        return (1, 1)
    if exact <= 0:
        return (1, 1)
    exact = exact.limit_denominator(MAX_DEN)
    # CHECKED AGAIN AFTER LIMITING, and this is not belt-and-braces. A value
    # smaller than 1/MAX_DEN limits to ZERO -- 1/2000003 mm per count did
    # exactly that in testing -- and a zero numerator is a DEAD SCALE: the axis
    # would read a constant. Falling back is the only safe answer, since there
    # is no representable ratio to return.
    if exact <= 0:
        return (1, 1)
    return (exact.numerator, exact.denominator)


def round_trips_exactly(ratio_num, ratio_den) -> bool:
    """Can this ratio be shown as a resolution without losing anything?

    True iff converting to microns and back returns the same ratio.

    MEASURED SCOPE, 2026-09-01: this is true of every realistic value tried,
    including the ones the filed task expected to fail (1/3, 1/7, 22/7,
    127/64000, 1/999983). What it actually catches is a denominator past
    MAX_DEN, where the value limits to zero. Kept because that case is real
    and silent, not because non-terminating decimals are a problem -- they
    are not.
    """
    try:
        original = Fraction(int(ratio_num), int(ratio_den))
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    if original <= 0:
        return False
    num, den = ratio_from_resolution_um(resolution_um(ratio_num, ratio_den))
    return Fraction(num, den) == original


def default_entry_mode(ratio_num, ratio_den) -> str:
    """Which form to OPEN on for an already-configured input.

    Resolution when it is exact, ratio otherwise -- so an input the resolution
    field cannot carry says so by showing the form that can, rather than
    displaying a number that would quietly change the setting if accepted.

    In practice this returns "Resolution" for every real scale on the machine.
    The ratio branch is for the unrepresentable tail (see round_trips_exactly)
    and for a degenerate stored value, where opening on a resolution of 0.0
    would be worse than useless.
    """
    return "Resolution" if round_trips_exactly(ratio_num, ratio_den) else "Ratio"
