"""ElsModeWatch: the rung-2 comparator between the UI's model and the
firmware-published machine mode.

Two behaviors are load-bearing and pinned here: the census must record every
pairing exactly once as a first sighting (it is the data the architecture
direction runs on), and the divergence alarm must be debounced and
once-per-episode (weeks of logs on elspi have to stay readable, and a
transition caught mid-flight between two state holders sampled at different
rates must not cry wolf).
"""

from reflex.fsms.els_mode_watch import (
    ElsModeWatch, DIVERGENT, MODE_NAMES,
    MODE_OFF, MODE_IDLE, MODE_FEEDING, MODE_MOVING,
    MODE_JOG, MODE_HELD, MODE_TAKEUP, MODE_CAL,
    mode_name,
)


def test_wire_values_are_pinned():
    """Mirror of reflex-fw els_machine_mode.h ELS_MMODE_* — a wire contract,
    pinned as literals on both sides so renumbering costs a deliberate edit
    here AND there (same discipline as the diag schema ids)."""
    assert (MODE_OFF, MODE_IDLE, MODE_FEEDING, MODE_MOVING,
            MODE_JOG, MODE_HELD, MODE_TAKEUP, MODE_CAL) == (0, 1, 2, 3, 4, 5, 6, 7)
    assert set(MODE_NAMES) == set(range(8))


def test_unknown_mode_names_do_not_raise():
    # A newer firmware may publish a mode this UI does not know yet; the
    # sampler must render it legibly, not crash the log line.
    assert "8" in mode_name(8)


class TestCensus:
    def test_first_sighting_reports_census(self):
        w = ElsModeWatch()
        assert w.feed("disabled", MODE_OFF) == 'census'

    def test_repeat_sighting_is_silent_but_counted(self):
        w = ElsModeWatch()
        w.feed("disabled", MODE_OFF)
        assert w.feed("disabled", MODE_OFF) is None
        assert w.pair_counts[("disabled", MODE_OFF)] == 2

    def test_each_distinct_pair_gets_its_own_first_sighting(self):
        w = ElsModeWatch()
        assert w.feed("disabled", MODE_OFF) == 'census'
        assert w.feed("disabled", MODE_JOG) == 'census'
        assert w.feed("stopped", MODE_HELD) == 'census'


class TestDivergence:
    def test_below_debounce_stays_quiet(self):
        w = ElsModeWatch()
        for _ in range(w.CONSECUTIVE - 1):
            assert w.feed("disabled", MODE_HELD) != 'divergence'

    def test_reports_at_debounce_threshold(self):
        w = ElsModeWatch()
        results = [w.feed("disabled", MODE_HELD) for _ in range(w.CONSECUTIVE)]
        assert results[-1] == 'divergence'

    def test_once_per_episode(self):
        w = ElsModeWatch()
        results = [w.feed("disabled", MODE_HELD) for _ in range(w.CONSECUTIVE * 4)]
        assert results.count('divergence') == 1

    def test_new_episode_reports_again_after_clearing(self):
        w = ElsModeWatch()
        for _ in range(w.CONSECUTIVE):
            w.feed("disabled", MODE_HELD)
        w.feed("disabled", MODE_OFF)          # healthy: episode closes
        results = [w.feed("disabled", MODE_HELD) for _ in range(w.CONSECUTIVE)]
        assert results[-1] == 'divergence'

    def test_switching_divergent_pair_restarts_the_debounce(self):
        # Two different bad pairings in quick succession are two separate
        # claims about the machine; neither should inherit the other's run.
        w = ElsModeWatch()
        for _ in range(w.CONSECUTIVE - 1):
            w.feed("disabled", MODE_HELD)
        assert w.feed("alarm", MODE_FEEDING) != 'divergence'

    def test_transition_skew_never_alarms(self):
        # The realistic near-miss: the FSM disengages a beat before the
        # firmware republishes, so one or two samples pair 'disabled' with a
        # stale HELD. Below the debounce, that must stay census-only.
        w = ElsModeWatch()
        w.feed("stopped", MODE_HELD)
        assert w.feed("disabled", MODE_HELD) != 'divergence'
        assert w.feed("disabled", MODE_OFF) != 'divergence'
        assert w._bad_run == 0


class TestDivergentSet:
    """Membership is a design decision; pin it so growth is deliberate."""

    def test_no_job_but_machine_engaged_is_divergent(self):
        assert ("disabled", MODE_HELD) in DIVERGENT
        assert ("disabled", MODE_TAKEUP) in DIVERGENT

    def test_expected_pairings_are_not(self):
        # 'stopped' + HELD is the armed-idle design intent; 'stopped' + OFF is
        # the arm-refused path; 'cutting' + HELD is the stop firing before the
        # FSM hears about it. All census, none defects.
        assert ("stopped", MODE_HELD) not in DIVERGENT
        assert ("stopped", MODE_OFF) not in DIVERGENT
        assert ("cutting", MODE_HELD) not in DIVERGENT

    def test_alarm_teardown_failures_are_divergent(self):
        for mode in (MODE_FEEDING, MODE_MOVING, MODE_HELD, MODE_TAKEUP):
            assert ("alarm", mode) in DIVERGENT
