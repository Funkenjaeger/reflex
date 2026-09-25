"""Scale resolution <-> ratio conversion for the input setup screen.

The feature: let a linear scale be entered as the number printed on it
("1um", "5um") instead of as ratioNum/ratioDen, which is millimetres per count
and was labelled with no units at all.

THE PRECISION TRAP THE TASK WAS FILED AGAINST DOES NOT EXIST, and these tests
record the measurement rather than the assumption. The filed reason for keeping
ratio entry was that "non-2/5 denominators have no terminating decimal" -- 1/3
mm per count being 333.333... um. Measured 2026-09-01: limit_denominator
recovers 1/3, 1/7, 22/7, 127/64000 and 1/999983 EXACTLY from the float, because
a best-rational-approximation search does not care whether the decimal
terminated. The first draft of these tests asserted the trap and went red; the
premise was wrong, not the code.

What the round-trip guard actually catches is a denominator past MAX_DEN, which
limits to ZERO -- a dead scale. That case is real, silent, and was found by
testing the wrong premise.

Ratio entry survives for the reason Evan gave in the first place: a drive ratio
like the servo's 127/64000 is NAMED as a ratio by whoever specified it.
"""
from fractions import Fraction

import pytest

from reflex.utils.scale_resolution import (
    default_entry_mode, ratio_from_resolution_um, resolution_um,
    round_trips_exactly,
)


# ── the real scales on the machine ──────────────────────────────────────────
#
# Every one of these is a value that has actually been on elspi. The 2.5 um row
# is the misprovisioning the 2026-08-31 dial check caught; 2 um is the
# deliberate diameter doubling Evan set the same day; 1 um is the true head.

@pytest.mark.parametrize("num,den,um", [
    (1, 1000, 1.0),     # a 1 um head, provisioned faithfully
    (1, 500,  2.0),     # X today: 1 um head doubled for a diameter DRO
    (1, 400,  2.5),     # X before the dial check -- the misprovisioning
    (1, 200,  5.0),     # Z
])
def test_known_machine_values_convert_both_ways(num, den, um):
    assert resolution_um(num, den) == pytest.approx(um)
    assert ratio_from_resolution_um(um) == (num, den)
    assert round_trips_exactly(num, den)
    assert default_entry_mode(num, den) == "Resolution"


# ── the precision trap ──────────────────────────────────────────────────────

@pytest.mark.parametrize("num,den", [(1, 3), (1, 7), (22, 7), (127, 64000),
                                     (1, 999983), (123457, 1000000)])
def test_non_terminating_decimals_round_trip_FINE(num, den):
    """The measurement that refuted the filed premise, kept as a test so nobody
    re-derives the trap from the arithmetic and adds a guard for it.

    127/64000 is the servo's real drive ratio; 1/3 and 22/7 are deliberately
    nasty. All exact.
    """
    assert round_trips_exactly(num, den)
    assert default_entry_mode(num, den) == "Resolution"


def test_a_denominator_past_the_ceiling_is_the_case_that_really_breaks():
    """1/2000003 limits to ZERO -- a dead scale, where the axis would read a
    constant. Found by testing the wrong premise, which is the only reason it
    was found at all."""
    assert not round_trips_exactly(1, 2000003)
    assert default_entry_mode(1, 2000003) == "Ratio"

    num, den = ratio_from_resolution_um(resolution_um(1, 2000003))
    assert num > 0, "a zero numerator would write a dead scale to the machine"


def test_a_typed_decimal_is_read_as_the_decimal_it_is():
    """Fraction(0.1) carries binary representation error and would produce a
    denominator in the trillions; the conversion goes through str() so a typed
    0.1 means one tenth."""
    num, den = ratio_from_resolution_um(0.1)
    assert Fraction(num, den) == Fraction(1, 10000)
    assert den < 100000


@pytest.mark.parametrize("um", [1.0, 2.0, 2.5, 5.0, 0.5, 10.0, 0.1])
def test_resolution_round_trips_through_the_ratio(um):
    num, den = ratio_from_resolution_um(um)
    assert resolution_um(num, den) == pytest.approx(um)


# ── degenerate input must not take the setup screen down ────────────────────

@pytest.mark.parametrize("num,den", [(1, 0), (0, 0), ("x", 1), (None, 1), (1, None)])
def test_a_degenerate_ratio_reads_as_zero_rather_than_raising(num, den):
    """This renders while the setup screen is drawn. A half-entered ratio must
    show something harmless, not crash the screen at the lathe."""
    assert resolution_um(num, den) == 0.0
    assert round_trips_exactly(num, den) is False
    assert default_entry_mode(num, den) == "Ratio"


@pytest.mark.parametrize("bad", [0, -1, "", None, "abc"])
def test_a_nonsense_resolution_falls_back_to_a_safe_ratio(bad):
    """Never 1/0, and never a negative scale: direction is the `reverse` flag's
    job, and folding a sign in here would give two ways to invert an axis."""
    num, den = ratio_from_resolution_um(bad)
    assert den > 0
    assert num > 0


def test_zero_and_negative_resolutions_are_not_stored():
    assert ratio_from_resolution_um(0) == (1, 1)
    assert ratio_from_resolution_um(-5) == (1, 1)
