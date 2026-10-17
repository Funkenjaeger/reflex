"""Where each ELS stop landed (els_stop_outcome), driven poll by poll.

Pins: the first seq read is a baseline; a trigger is accepted only when seen
on two consecutive polls (a torn read is ignored); the outcome arrives on the
poll where Z has read the same SETTLE_POLLS times and not before; the rate is
the APPROACH rate from before the trigger; past/overshoot signs in both travel
directions; premise and corrected flags; a stop that never settles gives
nothing; reset() makes the next seq a baseline; and the operator texts fit 65
characters at the widest realistic case.
"""
from types import SimpleNamespace

import pytest

from reflex.fsms import els_stop_outcome as so
from reflex.fsms.els_stop_outcome import (
    SETTLE_POLLS,
    SETTLE_TIMEOUT_S,
    StopOutcome,
    StopOutcomeWatch,
    distance_text,
    landed_text,
    past_off_text,
)

DT = 0.04


class Rig:
    """A fake board, a controllable clock, and a watch over them."""

    def __init__(self, z=10000, seq=0, direction=1):
        self.t = 100.0
        self.z = z
        self.board = SimpleNamespace(
            connected=True,
            els_stop_values={"scaleIndex": 1, "stopTriggerSeq": seq,
                             "stopTriggerZ": 0, "stopTriggerOffset": 0,
                             "stopTriggerStepsToGo": 0, "stopPosition": 0,
                             "stopDirection": direction},
            fast_data_values={"scaleCurrent": [0, z, 0, 0]})
        self.watch = StopOutcomeWatch(self.board, lambda: self.t)

    def poll(self, dz=0, **snap):
        """Advance one tick: Z moves by dz, snapshot fields are updated."""
        self.t += DT
        self.z += dz
        self.board.fast_data_values = {"scaleCurrent": [0, self.z, 0, 0]}
        self.board.els_stop_values.update(snap)
        return self.watch.poll()

    def approach(self, n=15, rate=1200.0, sign=1):
        step = round(rate * DT) * sign
        for _ in range(n):
            assert self.poll(step) is None


def trigger_fields(rig, seq=1, offset=5, steps_to_go=0, stop_position=None,
                   direction=1):
    return dict(stopTriggerSeq=seq, stopTriggerZ=rig.z, stopTriggerOffset=offset,
                stopTriggerStepsToGo=steps_to_go,
                stopPosition=rig.z if stop_position is None else stop_position,
                stopDirection=direction)


def run_stop(rig, coast=(20, 10), settle_extra=0, **kw):
    """Trigger, a short coast, then Z constant. Returns (outcome, polls)."""
    fields = trigger_fields(rig, **kw)
    results = [rig.poll(coast[0], **fields)]          # first sighting
    results.append(rig.poll(coast[1]))                # second: accepted
    for _ in range(SETTLE_POLLS + settle_extra + 2):
        results.append(rig.poll(0))
    return results


# ─── baseline and tearing ────────────────────────────────────────────────

def test_first_seq_is_a_baseline_never_an_outcome():
    rig = Rig(seq=7)
    for _ in range(20):
        assert rig.poll(0) is None


def test_seq_seen_once_then_reverting_gives_nothing():
    rig = Rig()
    rig.approach()
    assert rig.poll(48, **trigger_fields(rig)) is None
    assert rig.poll(0, stopTriggerSeq=0) is None
    for _ in range(10):
        assert rig.poll(0) is None


def test_seq_seen_on_two_consecutive_polls_is_accepted():
    rig = Rig()
    rig.approach()
    results = run_stop(rig)
    assert [r for r in results if r is not None]


# ─── when the outcome arrives ────────────────────────────────────────────

def test_outcome_arrives_on_the_poll_z_has_read_the_same_settle_polls_times():
    rig = Rig()
    rig.approach()
    fields = trigger_fields(rig)
    assert rig.poll(20, **fields) is None             # first sighting
    assert rig.poll(10) is None                       # accepted; Z read 1x
    for _ in range(SETTLE_POLLS - 2):                 # reads 2 .. N-1
        assert rig.poll(0) is None
    out = rig.poll(0)                                 # Nth read
    assert isinstance(out, StopOutcome)
    assert rig.poll(0) is None                        # and only once


def test_still_moving_z_is_not_settled():
    rig = Rig()
    rig.approach()
    rig.poll(20, **trigger_fields(rig))
    for _ in range(10):
        assert rig.poll(2) is None


def test_no_outcome_when_z_never_settles_in_time():
    rig = Rig()
    rig.approach()
    rig.poll(20, **trigger_fields(rig))
    for _ in range(int(SETTLE_TIMEOUT_S / DT) + 10):
        assert rig.poll(1) is None
    for _ in range(10):                               # settling now is too late
        assert rig.poll(0) is None


def test_reset_makes_the_next_seq_a_baseline_again():
    rig = Rig()
    rig.approach()
    rig.watch.reset()
    rig.poll(48, **trigger_fields(rig, seq=9))
    for _ in range(10):
        assert rig.poll(0) is None


# ─── what the outcome holds ──────────────────────────────────────────────

def test_rate_is_the_approach_rate_not_the_coast():
    rig = Rig()
    rig.approach(rate=1200.0)
    results = run_stop(rig, coast=(300, 200))         # a violent coast
    out = next(r for r in results if r is not None)
    assert out.rate == pytest.approx(1200.0, rel=0.03)


def test_outcome_carries_the_snapshot_fields():
    rig = Rig()
    rig.approach()
    z0 = rig.z
    out = next(r for r in run_stop(rig, offset=4, stop_position=z0 + 4, seq=3) if r)
    assert (out.seq, out.offset, out.trigger_z, out.stop_position) == (3, 4, z0, z0 + 4)
    assert out.settled_z == z0 + 30
    assert out.direction == 1
    assert out.steps_to_go == 0


def test_forward_signs_past_and_short():
    rig = Rig()
    rig.approach()
    z0 = rig.z
    past = next(r for r in run_stop(rig, stop_position=z0 - 5) if r)      # settled z0+30
    assert past.past == 35 and past.overshoot == 30

    rig = Rig()
    rig.approach()
    z0 = rig.z
    short = next(r for r in run_stop(rig, stop_position=z0 + 100) if r)
    assert short.past == -70 and short.overshoot == 30


def test_reverse_signs_travelling_negative():
    rig = Rig(direction=-1)
    rig.approach(sign=-1)
    z0 = rig.z
    # Coasts 30 counts further negative. Target 5 counts beyond the trigger
    # in the travel direction, so the carriage is 25 counts past it.
    out = next(r for r in run_stop(rig, coast=(-20, -10), direction=-1,
                                   stop_position=z0 - 5) if r)
    assert out.settled_z == z0 - 30
    assert out.overshoot == 30
    assert out.past == 25
    # A target 100 counts further on: short.
    rig = Rig(direction=-1)
    rig.approach(sign=-1)
    z0 = rig.z
    out = next(r for r in run_stop(rig, coast=(-20, -10), direction=-1,
                                   stop_position=z0 - 100) if r)
    assert out.past == -70 and out.overshoot == 30


def test_premise_and_corrected_flags():
    rig = Rig()
    rig.approach()
    out = next(r for r in run_stop(rig, steps_to_go=12, offset=0) if r)
    assert out.premise_ok is False
    assert out.corrected is False
    rig = Rig()
    rig.approach()
    out = next(r for r in run_stop(rig, offset=1) if r)
    assert out.premise_ok is True
    assert out.corrected is True


def test_not_connected_or_no_snapshot_gives_nothing():
    rig = Rig()
    rig.board.connected = False
    assert rig.poll(48) is None
    rig.board.connected = True
    rig.board.els_stop_values = {}
    assert rig.watch.poll() is None


# ─── the texts ───────────────────────────────────────────────────────────

def outcome(past, direction=1):
    return StopOutcome(seq=1, offset=1, rate=1000.0, stop_position=1000,
                       trigger_z=990, settled_z=1000 + past * direction,
                       direction=direction, steps_to_go=0)


MM, INCH = 1.0, 10 / 254


def test_landed_on_target():
    assert landed_text(outcome(0), 0.005, MM) == "Stop landed on target."


def test_landed_short_and_past_in_mm():
    assert landed_text(outcome(-1), 0.005, MM) == "Stop landed 1 count (0.005 mm) short."
    assert landed_text(outcome(2), 0.005, MM) == "Stop landed 2 counts (0.010 mm) past."


def test_inch_shows_four_decimals_and_unit():
    s = landed_text(outcome(-2), 0.005, INCH)
    assert s == "Stop landed 2 counts (0.0004 in) short."


def test_past_off_text_ends_with_the_action():
    s = past_off_text(outcome(2), 0.005, MM)
    assert s == "Stop landed 2 counts (0.010 mm) past. Correction is off."
    assert s.endswith("past. Correction is off.")


@pytest.mark.parametrize("factor", [MM, INCH])
@pytest.mark.parametrize("sign", [1, -1])
def test_every_text_fits_65_characters_at_the_widest_case(factor, sign):
    o = outcome(999 * sign)
    for text in (landed_text(o, 0.005, factor), past_off_text(o, 0.005, factor),
                 distance_text(999, 0.005, factor)):
        assert len(text) <= 65, text
