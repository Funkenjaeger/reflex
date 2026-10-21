"""The guided overshoot measurements (els_overshoot_survey): detection and sweep.

Pins the detection verdicts (YES / NO / UNKNOWN, a borderline first coast
asking for a second pass, NO never claiming zero outright) and the sweep
(four speeds of three stops, grouped by measured speed in any order, each
group labeled with its feed and rpm, worst coast per speed as a
non-decreasing envelope, refusal on a spread or on two groups at one rate,
stops that are not counted) and that no strip line is wider than the strip
has room for. Stops are built directly; mm_per_count is 0.005 so
counts/s * 0.005 = mm/s.
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
    assert msgs[0].endswith("Proceed with the next pass.")


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
    """After a pass the strip says what that pass did and a count, and
    names no order (2026-10-19): the speeds may come in any order."""
    s = Survey(SWEEP, K)
    assert s.message == "Sweep: 3 passes at each of 4 speeds, in any order."
    at = sv.Setting("0.020", "in/rev", 342.0)
    assert s.add(stop(3, rate=600.0), at) == "0.020 @ 340 rpm: 1/3 \u00b7 3 speeds left"
    assert s.state == RUNNING
    assert s.add(stop(3, rate=600.0), at) == "0.020 @ 340 rpm: 2/3 \u00b7 3 speeds left"
    assert s.add(stop(3, rate=600.0), at) == "0.020 @ 340 rpm: 3/3 \u00b7 3 speeds left"
    for r in (1200.0, 2400.0):
        for _ in range(3):
            s.add(stop(9, rate=r), sv.Setting("0.040", "in/rev", 700.0))
    last = sv.Setting("0.040", "in/rev", 1400.0)
    assert s.add(stop(40, rate=3500.0), last) == "0.040 @ 1400 rpm: 1/3 \u00b7 last speed"


def test_speeds_left_counts_the_other_unfinished_speeds():
    s = sweep(*group(600.0, (3, 3, 3)))
    assert s.add(stop(9, rate=1200.0)).endswith("\u00b7 2 speeds left")
    s = sweep(*group(600.0, (3, 3, 3)), *group(1200.0, (9, 9, 9)))
    assert s.add(stop(9, rate=2400.0)).endswith("\u00b7 1 speed left")


def test_a_new_speed_names_the_unfinished_one():
    s = Survey(SWEEP, K)
    slow = sv.Setting("0.020", "in/rev", 342.0)
    fast = sv.Setting("0.040", "in/rev", 701.0)
    s.add(stop(3, rate=600.0), slow)
    s.add(stop(3, rate=600.0), slow)
    m = s.add(stop(9, rate=2400.0), fast)
    assert m == "New speed: 0.040 @ 700 rpm, 1/3 \u00b7 0.020 @ 340 needs 1 more"
    s.add(stop(9, rate=2400.0), fast)
    m = s.add(stop(9, rate=2400.0), fast)
    assert m == "0.040 @ 700 rpm: 3/3 \u00b7 0.020 @ 340 needs 1 more"
    # Back at the first setting: the group is finished.
    assert s.add(stop(3, rate=600.0), slow) == "0.020 @ 340 rpm: 3/3 \u00b7 2 speeds left"


def test_without_a_setting_the_label_is_the_speed():
    s = Survey(SWEEP, K)
    assert s.add(stop(3, rate=600.0)) == "3.0 mm/s: 1/3 \u00b7 3 speeds left"


def test_spread_beyond_the_limit_is_refused_and_named():
    bad = SWEEP_MAX_SPREAD_COUNTS + 1
    s = sweep(*group(600.0, (3, 3, 3 + bad)))
    assert s.state == REFUSED
    assert f"spread of {bad}" in s.detail
    assert str(bad) in s.message


def test_spread_at_the_limit_is_accepted():
    s = sweep(*group(600.0, (3, 3, 3 + SWEEP_MAX_SPREAD_COUNTS)))
    assert s.state == RUNNING


def test_stop_at_a_different_rate_starts_a_new_group():
    s = sweep(stop(3, rate=600.0))
    msg = s.add(stop(3, rate=700.0))
    assert len(s.stops) == 2 and len(s.groups) == 2
    assert "not counted" not in msg


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


def test_two_groups_at_the_same_mean_rate_are_refused():
    """Still possible with grouping by speed: two groups whose first stops
    were just over 10 % apart (600, 667), and whose later stops landed
    between them, end with means 620 and 649, under 5 % apart."""
    s = sweep(stop(3, rate=600.0), stop(4, rate=667.0),
              stop(3, rate=630.0), stop(3, rate=630.0),
              stop(4, rate=640.0), stop(4, rate=640.0),
              *group(2400.0, (29, 29, 29)), *group(3500.0, (43, 43, 43)))
    assert [len(g.stops) for g in s.groups] == [3, 3, 3, 3]
    assert s.state == REFUSED
    assert s.message == "Sweep refused: two speeds gave the same rate."
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
    seen.append(full_sweep([(3, 3, 4), (9, 8, 9), (28, 29, 29), (42, 43, 41)]).message)
    assert all(seen)
    for m in seen:
        assert len(m) <= 65, m


# ─── grouped by measured speed (lathe, 2026-10-17) ───────────────────────
# Stops used to be filed by POSITION: stops 1-3 were group 1, 4-6 group 2.
# On the lathe two passes at 0.040 in/rev were followed by the cued switch to
# 0.010, and the third pass was refused ("Speed changed"), so the only way
# out was to cancel the sweep. A stop now joins the group whose speed it
# matches, so any order fits, and a group can be left and finished later.

# The 2026-10-17 rates on the development lathe (200 counts/mm, ~342 rpm):
# 0.040 in/rev is ~1158 counts/s, 0.010 in/rev ~290.
R040, R010, R020, R040_FAST = 1158.0, 290.0, 579.0, 2380.0
COASTS = {R010: (2, 2, 3), R020: (5, 6, 6), R040: (14, 15, 15), R040_FAST: (40, 41, 42)}


def test_the_2026_10_17_sequence_completes_the_sweep():
    """Two stops at 0.040, then the cued switch to 0.010, then the rest.
    Seen red before the change: the third stop was refused."""
    s = Survey(SWEEP, K)
    s.add(stop(14, rate=R040))
    s.add(stop(15, rate=R040))
    m = s.add(stop(2, rate=R010))
    assert "not counted" not in m
    assert len(s.stops) == 3
    for c in (2, 3):
        s.add(stop(c, rate=R010))
    s.add(stop(15, rate=R040))                      # back to finish 0.040
    for r in (R020, R040_FAST):
        for c in COASTS[r]:
            s.add(stop(c, rate=r))
    assert s.state == FITTED, s.message
    assert [c for _, c in s.table] == [0.0, 3.0, 6.0, 15.0, 42.0]


def _sorted_table():
    s = Survey(SWEEP, K)
    for r in sorted(COASTS):
        for c in COASTS[r]:
            s.add(stop(c, rate=r))
    assert s.state == FITTED
    return s.table


@pytest.mark.parametrize("order", [
    (R040_FAST, R040, R020, R010),
    (R020, R040_FAST, R010, R040),
    (R040, R010, R040_FAST, R020),
])
def test_any_order_of_four_speeds_fits_the_sorted_table(order):
    s = Survey(SWEEP, K)
    for r in order:
        for c in COASTS[r]:
            s.add(stop(c, rate=r))
    assert s.state == FITTED, s.message
    assert s.table == _sorted_table()


def test_interleaved_passes_fit_the_sorted_table():
    """One pass at each speed in turn, round robin."""
    s = Survey(SWEEP, K)
    for i in range(3):
        for r in (R040, R010, R040_FAST, R020):
            s.add(stop(COASTS[r][i], rate=r))
    assert s.state == FITTED, s.message
    assert s.table == _sorted_table()


def test_a_fifth_speed_is_not_counted():
    s = Survey(SWEEP, K)
    for r in (R010, R020, R040, R040_FAST):
        s.add(stop(3, rate=r))
    m = s.add(stop(3, rate=4000.0))
    assert len(s.stops) == 4
    assert m == "Pass not counted: 4 speeds already open"
    assert s.state == RUNNING


def test_a_stop_within_ten_percent_of_a_group_joins_it():
    s = Survey(SWEEP, K)
    s.add(stop(3, rate=600.0))
    s.add(stop(3, rate=2400.0))
    s.add(stop(3, rate=655.0))
    assert len(s.groups) == 2
    assert [len(g.stops) for g in s.groups] == [2, 1]


def test_a_stop_joins_the_nearer_of_two_groups():
    s = Survey(SWEEP, K)
    s.add(stop(3, rate=600.0))
    s.add(stop(3, rate=700.0))                      # 16.7 % off: its own group
    s.add(stop(3, rate=655.0))                      # 9.2 % and 6.4 %: the second
    assert [len(g.stops) for g in s.groups] == [1, 2]


def test_a_stop_at_a_finished_speed_is_not_counted():
    s = Survey(SWEEP, K)
    for c in (3, 3, 4):
        s.add(stop(c, rate=600.0))
    m = s.add(stop(3, rate=610.0))
    assert len(s.stops) == 3
    assert m.startswith("Pass not counted:") and "3/3" in m


def test_spread_is_refused_when_a_group_fills_out_of_order():
    bad = SWEEP_MAX_SPREAD_COUNTS + 1
    s = Survey(SWEEP, K)
    s.add(stop(3, rate=600.0))
    s.add(stop(9, rate=1200.0))
    s.add(stop(3, rate=600.0))
    s.add(stop(9, rate=1200.0))
    assert s.state == RUNNING and len(s.stops) == 4
    s.add(stop(3 + bad, rate=600.0))
    assert s.state == REFUSED
    assert f"spread of {bad}" in s.detail


# ─── labels: how each group was made ─────────────────────────────────────

def test_a_group_is_labeled_with_the_feed_and_rpm_of_its_first_stop():
    s = Survey(SWEEP, K)
    s.add(stop(3, rate=600.0), sv.Setting("0.020", "in/rev", 342.4))
    # A later stop in the group at a slightly different spindle speed does
    # not relabel it: the label finds the setting the group was started at.
    s.add(stop(3, rate=610.0), sv.Setting("0.020", "in/rev", 351.0))
    g = s.groups[0]
    assert g.setting.short() == "0.020 @ 340 rpm"
    assert g.setting.long() == "0.020 in/rev, ~340 rpm"
    assert s.long_label(g) == "0.020 in/rev, ~340 rpm, 3.0 mm/s"


@pytest.mark.parametrize("rpm, text", [(342.4, "340"), (1155.0, "1160"),
                                       (72.4, "72"), (-342.0, "340")])
def test_rpm_is_rounded_for_the_label(rpm, text):
    assert sv.Setting("0.10", "mm/rev", rpm).short() == f"0.10 @ {text} rpm"


def test_a_label_with_a_part_missing():
    assert sv.Setting("0.020", "in/rev", None).short() == "0.020"
    assert sv.Setting("0.020", "in/rev", 0.0).long() == "0.020 in/rev"
    assert sv.Setting("", "", 340.0).short() == "340 rpm"
    assert sv.Setting().short() == "" and sv.Setting().long() == ""


def test_the_tally_has_one_row_per_group_then_the_speeds_not_started():
    s = Survey(SWEEP, K)
    s.add(stop(14, rate=R040), sv.Setting("0.040", "in/rev", 342.0))
    s.add(stop(15, rate=R040), sv.Setting("0.040", "in/rev", 342.0))
    s.add(stop(1, rate=R010), sv.Setting("0.010", "in/rev", 342.0))
    assert s.tally() == [
        "0.040 in/rev, ~340 rpm, 5.8 mm/s: 2/3, coasts 14, 15",
        "0.010 in/rev, ~340 rpm, 1.4 mm/s: 1/3, coasts 1",
        "2 more speeds to start.",
    ]


def test_the_finished_table_carries_the_group_labels_in_rate_order():
    s = Survey(SWEEP, K)
    for r, at in ((R040_FAST, sv.Setting("0.040", "in/rev", 700.0)),
                  (R010, sv.Setting("0.010", "in/rev", 342.0)),
                  (R040, sv.Setting("0.040", "in/rev", 342.0)),
                  (R020, sv.Setting("0.020", "in/rev", 342.0))):
        for c in COASTS[r]:
            s.add(stop(c, rate=r), at)
    assert s.state == FITTED
    assert s.table_labels == (
        "0.010 in/rev, ~340 rpm, 1.4 mm/s",
        "0.020 in/rev, ~340 rpm, 2.9 mm/s",
        "0.040 in/rev, ~340 rpm, 5.8 mm/s",
        "0.040 in/rev, ~700 rpm, 11.9 mm/s",
    )
    assert s.detail.splitlines()[1:] == [
        "0.010 in/rev, ~340 rpm, 1.4 mm/s: 3",
        "0.020 in/rev, ~340 rpm, 2.9 mm/s: 6",
        "0.040 in/rev, ~340 rpm, 5.8 mm/s: 15",
        "0.040 in/rev, ~700 rpm, 11.9 mm/s: 42",
    ]


def test_a_spread_refusal_names_the_group():
    s = Survey(SWEEP, K)
    for c in (3, 3, 9):
        s.add(stop(c, rate=600.0), sv.Setting("0.020", "in/rev", 342.0))
    assert s.message == "Sweep refused: coasts spread 6 counts at 0.020 @ 340 rpm."
    assert s.detail.startswith("At 0.020 in/rev, ~340 rpm, 3.0 mm/s the coasts were 3, 3, 9")


# ─── the strip's one line ────────────────────────────────────────────────
# The notice strip is centered across the bar and translucent over the status
# gutter, so a line wider than sv.CUE_BUDGET_PX (435 px) draws over the
# phase-offset chip's text. Measured on the live 1024x600 layout on
# 2026-10-19 (previews/preview_phase_offset.py: chip_reference.right 197,
# chip_phase.x 783, bar center 566), with the face and size the strip uses,
# theme.font_bold at dp(13). PIL measures that face up to about 4 % narrower
# than the SDL texture the strip renders (281.5 against 293 px for
# NOT_COUNTED), so the check adds SDL_MARGIN on top. The widest values: the
# widest feed name in the feed tables and a four-digit rpm.

SDL_MARGIN = 1.05


def _font(ImageFont):
    from pathlib import Path
    import reflex
    from reflex.components.widgets.palettes import PALETTES
    faces = {p["font_bold"] for p in PALETTES.values()}
    assert len(faces) == 1, f"one bold face assumed, found {faces}"
    return ImageFont.truetype(str(Path(reflex.__file__).parent / faces.pop()), 13)


def _widest_feed():
    from PIL import ImageFont
    from reflex import feeds
    f = _font(ImageFont)
    names = [c.name for t, cs in feeds.table.items()
             if not feeds.is_threading_table(t) for c in cs]
    return max(names, key=f.getlength)


def _sweep_lines():
    wide = sv.Setting(_widest_feed(), "in/rev", 8000.0)
    seen = [Survey(SWEEP, K).message, sv.NOT_COUNTED, sv.TOO_MANY_SPEEDS]
    s = Survey(SWEEP, K)
    for r in (600.0, 1200.0, 2400.0):
        seen.append(s.add(stop(3, rate=r), wide))          # New speed: ... needs 1 more
        seen.append(s.add(stop(3, rate=r), wide))
    seen += [s.add(stop(3, rate=600.0), wide),             # 3/3, another needs 1 more
             s.add(stop(3, rate=600.0), wide)]             # full group: not counted
    s = Survey(SWEEP, K)
    seen += [s.add(stop(3, rate=600.0), wide) for _ in range(3)]
    s = Survey(SWEEP, K)
    for c in (3, 3, 999):                                  # a three-digit spread
        s.add(stop(c, rate=600.0), wide)
    seen.append(s.message)
    s = Survey(SWEEP, K)
    seen.append(s.add(stop(3, rate=99999.0)))              # the speed as the label
    seen.append(full_sweep([(3, 3, 4), (9, 8, 9), (28, 29, 29), (42, 43, 41)]).message)
    seen.append(sweep(stop(3, rate=600.0), stop(4, rate=667.0), stop(3, rate=630.0),
                      stop(3, rate=630.0), stop(4, rate=640.0), stop(4, rate=640.0),
                      *group(2400.0, (29, 29, 29)), *group(3500.0, (43, 43, 43))).message)
    return seen


def test_every_sweep_line_fits_the_strip():
    from PIL import ImageFont
    f = _font(ImageFont)
    lines = _sweep_lines()
    assert all(lines)
    assert any(m.startswith("New speed:") and "needs 1 more" in m for m in lines)
    for m in lines:
        w = f.getlength(m) * SDL_MARGIN
        assert w <= sv.CUE_BUDGET_PX, (
            f"{w:.0f} px against the strip's {sv.CUE_BUDGET_PX}: it would draw "
            f"over the phase-offset chip. Shorten it.\n  {m}")


def test_the_old_cues_are_gone():
    lines = " ".join(_sweep_lines())
    assert "Start with the slowest" not in lines
    assert sv.NEXT_SETTING not in lines
