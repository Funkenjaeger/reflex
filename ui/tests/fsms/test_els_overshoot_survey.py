"""The guided overshoot measurements (els_overshoot_survey): detection and sweep.

Pins the detection verdicts (YES / NO / UNKNOWN, a borderline first coast
asking for a second pass, NO never claiming zero outright) and the sweep
(four feeds of three stops, worst coast per feed as a non-decreasing
envelope, refusal on a spread or on two feeds at one rate, stops that are not
counted) and that no operator line is over 65 characters. Stops are built
directly; mm_per_count is 0.005 so counts/s * 0.005 = mm/s.
"""
import pytest

from reflex.fsms import els_overshoot_survey as sv
from reflex.fsms.els_overshoot_survey import (
    DETECT,
    DETECT_MIN_RATE_MM_S,
    FITTED,
    NO,
    REFUSED,
    RUNNING,
    SWEEP,
    SWEEP_MAX_SPREAD_COUNTS,
    UNKNOWN,
    YES,
    Survey,
)
from reflex.fsms.els_stop_outcome import StopOutcome

K = 0.005


def stop(coast, rate=1200.0, steps=0, seq=1):
    return StopOutcome(seq=seq, offset=0, rate=rate, stop_position=1000,
                       trigger_z=1000, settled_z=1000 + coast, direction=1,
                       steps_to_go=steps)


def detect(*stops):
    s = Survey(DETECT, K)
    msgs = [s.add(o) for o in stops]
    return s, msgs


# ─── detection ───────────────────────────────────────────────────────────

def test_steps_queued_is_unknown():
    s, _ = detect(stop(9, steps=4))
    assert s.state == UNKNOWN and s.stops == []


def test_no_rate_is_unknown():
    s, _ = detect(stop(9, rate=None))
    assert s.state == UNKNOWN


def test_too_slow_is_unknown():
    s, _ = detect(stop(9, rate=(DETECT_MIN_RATE_MM_S - 0.1) / K))
    assert s.state == UNKNOWN
    assert "slow" in s.message


def test_exactly_the_minimum_rate_is_enough():
    s, _ = detect(stop(9, rate=DETECT_MIN_RATE_MM_S / K))
    assert s.state == YES


def test_big_coast_is_yes_after_one_stop():
    s, _ = detect(stop(9))
    assert s.state == YES and s.done
    assert "9" in s.message


def test_no_coast_is_no_after_one_stop_and_never_claims_zero():
    s, _ = detect(stop(0))
    assert s.state == NO and s.done
    text = (s.message + " " + s.detail).lower()
    assert "coast" in s.message.lower() or "at most" in s.detail.lower()
    assert "no overshoot" not in text and "zero" not in text


@pytest.mark.parametrize("first", [1, 2, 3])
def test_borderline_first_coast_asks_for_another_pass(first):
    s, msgs = detect(stop(first))
    assert s.state == RUNNING and not s.done
    assert "again" in msgs[0]


@pytest.mark.parametrize("first,second,state", [
    (3, 4, YES), (3, 9, YES),
    (1, 2, NO), (2, 0, NO), (2, 2, NO),
    (3, 2, UNKNOWN), (3, 0, UNKNOWN), (1, 3, UNKNOWN), (2, 9, UNKNOWN),
])
def test_two_borderline_passes_must_agree(first, second, state):
    s, _ = detect(stop(first), stop(second))
    assert s.state == state


def test_second_pass_with_steps_queued_is_unknown():
    s, _ = detect(stop(3), stop(3, steps=1))
    assert s.state == UNKNOWN


# ─── sweep ───────────────────────────────────────────────────────────────

RATES = (600.0, 1200.0, 2400.0, 3500.0)


def group(rate, coasts):
    return [stop(c, rate=rate + d) for c, d in zip(coasts, (-2.0, 0.0, 2.0))]


def sweep(*stops):
    s = Survey(SWEEP, K)
    for o in stops:
        s.add(o)
    return s


def full_sweep(coasts_by_group, rates=RATES):
    stops = []
    for r, c in zip(rates, coasts_by_group):
        stops += group(r, c)
    return sweep(*stops)


def test_good_sweep_fits_the_envelope():
    s = full_sweep([(3, 3, 4), (9, 8, 9), (28, 29, 29), (42, 43, 41)])
    assert s.state == FITTED and s.done
    assert s.table[0] == (0.0, 0.0)
    assert len(s.table) == 5
    for (r, c), (er, ec) in zip(s.table[1:], [(600, 4), (1200, 9), (2400, 29), (3500, 43)]):
        assert r == pytest.approx(er, abs=0.05)
        assert c == ec


def test_rates_are_group_means_rounded_to_a_tenth():
    stops = [stop(3, rate=r) for r in (598.0, 600.0, 603.1)]    # mean 600.3667
    stops += group(1200.0, (9, 9, 9)) + group(2400.0, (29, 29, 29)) + group(3500.0, (43, 43, 43))
    s = sweep(*stops)
    assert s.state == FITTED
    assert s.table[1][0] == 600.4


def test_envelope_is_made_non_decreasing():
    s = full_sweep([(3, 3, 4), (9, 8, 9), (5, 6, 6), (42, 43, 41)])
    assert s.state == FITTED
    assert [c for _, c in s.table] == [0.0, 4.0, 9.0, 9.0, 43.0]


def test_progress_lines_while_running():
    s = Survey(SWEEP, K)
    first = s.add(stop(3, rate=600.0))
    assert s.state == RUNNING and "1 of 12" in first
    s.add(stop(3, rate=600.0))
    last_of_group = s.add(stop(3, rate=600.0))
    assert "faster" in last_of_group


def test_spread_beyond_the_limit_is_refused_and_named():
    bad = SWEEP_MAX_SPREAD_COUNTS + 1
    s = sweep(*group(600.0, (3, 3, 3 + bad)))
    assert s.state == REFUSED
    assert f"spread of {bad}" in s.detail
    assert str(bad) in s.message


def test_spread_at_the_limit_is_accepted():
    s = sweep(*group(600.0, (3, 3, 3 + SWEEP_MAX_SPREAD_COUNTS)))
    assert s.state == RUNNING


def test_stop_at_a_different_rate_is_not_counted():
    s = sweep(stop(3, rate=600.0))
    msg = s.add(stop(3, rate=700.0))
    assert len(s.stops) == 1
    assert "not counted" in msg


def test_stop_within_ten_percent_is_counted():
    s = sweep(stop(3, rate=600.0))
    s.add(stop(3, rate=655.0))
    assert len(s.stops) == 2


def test_stop_with_steps_queued_is_not_counted():
    s = sweep(stop(3, rate=600.0))
    msg = s.add(stop(3, rate=600.0, steps=2))
    assert len(s.stops) == 1 and "not counted" in msg


def test_stop_without_a_rate_is_not_counted():
    s = Survey(SWEEP, K)
    msg = s.add(stop(3, rate=None))
    assert s.stops == [] and "not counted" in msg


def test_first_stop_of_a_group_may_be_any_rate():
    s = sweep(*group(600.0, (3, 3, 3)))
    s.add(stop(9, rate=1200.0))
    assert len(s.stops) == 4


def test_two_feeds_at_the_same_rate_are_refused():
    s = full_sweep([(3, 3, 3), (4, 4, 4), (29, 29, 29), (43, 43, 43)],
                   rates=(600.0, 640.0, 2400.0, 3500.0))
    assert s.state == REFUSED
    assert "same rate" in s.message
    assert s.table is None


def test_feeding_after_a_final_state_changes_nothing():
    s = full_sweep([(3, 3, 4), (9, 8, 9), (28, 29, 29), (42, 43, 41)])
    before = (s.state, s.message, s.detail, list(s.stops), s.table)
    s.add(stop(50, rate=100.0))
    assert (s.state, s.message, s.detail, list(s.stops), s.table) == before
    d, _ = detect(stop(9))
    d.add(stop(0))
    assert d.state == YES and len(d.stops) == 1


def test_zero_rate_first_stop_does_not_crash_the_next_stop():
    """A zero rate is not counted, like no rate: it would key the coast on
    nothing, and the next stop's same-feed check divides by it. Seen red
    before the fix: ZeroDivisionError on the second add."""
    s = Survey(SWEEP, K)
    s.add(stop(0, rate=0.0))
    assert s.stops == []
    s.add(stop(3, rate=600.0))
    assert len(s.stops) == 1


# ─── operator lines ──────────────────────────────────────────────────────

def test_every_message_fits_65_characters():
    seen = []
    # Detection, every ending.
    for stops in ([stop(9)], [stop(0)], [stop(3)], [stop(3), stop(4)],
                  [stop(3), stop(2)], [stop(9, steps=1)], [stop(9, rate=300.0)]):
        s = Survey(DETECT, K)
        seen.append(s.message)
        seen += [s.add(o) for o in stops]
        seen.append(s.message)
    # Sweep: progress, not counted, feed changed, refused both ways, fitted.
    s = Survey(SWEEP, K)
    seen.append(s.message)
    for o in [stop(3, rate=600.0, steps=1), stop(3, rate=600.0), stop(3, rate=900.0),
              stop(3, rate=600.0), stop(3, rate=600.0)]:
        seen.append(s.add(o))
    seen.append(full_sweep([(3, 3, 9), (9, 9, 9), (29, 29, 29), (43, 43, 43)]).message)
    seen.append(full_sweep([(3, 3, 3), (4, 4, 4), (29, 29, 29), (43, 43, 43)],
                           rates=(600.0, 640.0, 2400.0, 3500.0)).message)
    seen.append(full_sweep([(3, 3, 4), (9, 8, 9), (28, 29, 29), (42, 43, 41)]).message)
    assert all(seen)
    for m in seen:
        assert len(m) <= 65, m
