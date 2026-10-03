"""The lathe's gearbox between the servo and the leadscrew: which position is
engaged, and what that does to every number measured in motor steps.

THE MACHINE. elspi has a three-position lever (A / B / C) between the servo and
the leadscrew. Moving it changes how far the carriage travels per motor step,
and nothing in the controller can see the lever: the servo is open loop, and the
Z scale only ever reports where the carriage went. Measured 2026-10-03, steps
per Z count: A 2.50-2.62, B 1.22-1.35, C 4.95-5.08. B is the FAST position
(leadscrew twice as fast as in A), C the SLOW one.

THE CONVENTION. Every step-valued setting -- Lead Screw Pitch Steps, the backlash
take-up, the calibration legs -- is stored as measured in the REFERENCE gear,
A. The engaged gear enters in exactly two places:

- ServoDispatcher.configure_lead_screw_ratio multiplies the leadscrew's
  mm-per-step by the gear's factor, so the sync ratio, thread geometry,
  retract moves and the cut-start margin all see the real carriage travel per
  step without each of them knowing about gears;
- steps_in_gear() converts a stored step count to the engaged gear at the
  point it is written to the firmware. The play being taken up is on the
  leadscrew side of the lever, so a fixed carriage distance needs half the
  motor steps in B and twice as many in C. On 2026-10-03 the A-sized take-up
  was refused three times in C for exactly this reason.

HARD-CODED FOR NOW. The positions and their factors are this machine's; making
them configurable is deliberately left for later. The factors are nominal: the
measured whole-pass ratios agree with 2:1 and 1:2 to within 0.7 %.
"""
from __future__ import annotations

from fractions import Fraction

#: Carriage travel per motor step, relative to the reference gear.
GEAR_FACTORS: dict[str, Fraction] = {
    "A": Fraction(1),
    "B": Fraction(2),       # fast
    "C": Fraction(1, 2),    # slow
}

#: The gear every step-valued setting is stored in.
REFERENCE_GEAR = "A"

#: The largest carriage travel per motor step any position can produce.
FASTEST_FACTOR: Fraction = max(GEAR_FACTORS.values())


def gear_factor(gear: str) -> Fraction:
    """Factor for a gear position; an unknown name reads as the reference.

    A persisted value from a later build (or a hand-edited file) must not take
    the machine down at load, and the reference gear is what every stored
    setting assumes anyway.
    """
    return GEAR_FACTORS.get(gear, GEAR_FACTORS[REFERENCE_GEAR])


def steps_in_gear(reference_steps: int, gear: str) -> int:
    """A step count stored in the reference gear, converted to `gear`.

    Rounded to nearest, never to zero from a nonzero input: a take-up of one
    step must not silently vanish in the fast gear.
    """
    reference_steps = int(reference_steps)
    if reference_steps == 0:
        return 0
    raw = Fraction(reference_steps) / gear_factor(gear)
    out = int(round(raw))
    if out == 0:
        return 1 if reference_steps > 0 else -1
    return out


def worst_case_travel_factor(gear: str) -> Fraction:
    """How much further a motor step can move the carriage than `gear` expects.

    The lever can be in any position while the controller still believes in the
    last one it knew. Motion the firmware commits before the coupling check can
    read anything (the take-up, the thread phase correction) is therefore sized
    for the FASTEST position: in A that is 2x, in C 4x, in B 1x.
    """
    return FASTEST_FACTOR / gear_factor(gear)
