from fractions import Fraction

import pytest

from reflex.feeds import (
    FeedConfiguration, THREAD_MM, THREAD_IN, FEED_IN, FEED_MM, table,
    THREADING_MODES, is_threading_table,
)


class TestFeedConfiguration:
    def test_has_required_fields(self):
        fc = FeedConfiguration(name="1.00", ratio=Fraction(1), mode=1)
        assert fc.name == "1.00"
        assert fc.ratio == Fraction(1)
        assert fc.mode == 1

    def test_optional_fields_default_none(self):
        fc = FeedConfiguration()
        assert fc.name is None
        assert fc.ratio is None
        assert fc.mode is None


class TestThreadMM:
    def test_not_empty(self):
        assert len(THREAD_MM) > 0

    def test_all_have_ratios(self):
        for fc in THREAD_MM:
            assert fc.ratio is not None
            assert isinstance(fc.ratio, Fraction)

    def test_all_mode_1(self):
        for fc in THREAD_MM:
            assert fc.mode == 1

    def test_ratios_are_positive(self):
        for fc in THREAD_MM:
            assert fc.ratio > 0

    def test_sorted_ascending(self):
        ratios = [fc.ratio for fc in THREAD_MM]
        assert ratios == sorted(ratios)

    def test_known_values(self):
        names = [fc.name for fc in THREAD_MM]
        assert "1.00" in names
        assert "0.50" in names
        assert "2.00" in names


class TestThreadIN:
    def test_not_empty(self):
        assert len(THREAD_IN) > 0

    def test_all_have_ratios(self):
        for fc in THREAD_IN:
            assert fc.ratio is not None
            assert isinstance(fc.ratio, Fraction)

    def test_all_mode_2(self):
        for fc in THREAD_IN:
            assert fc.mode == 2

    def test_ratios_are_positive(self):
        for fc in THREAD_IN:
            assert fc.ratio > 0

    def test_ratio_formula_correct(self):
        """Imperial threads use 254/(TPI*10) formula."""
        # 20 TPI: ratio = 254/200 = 127/100
        fc_20 = [fc for fc in THREAD_IN if fc.name == "20"][0]
        assert fc_20.ratio == Fraction(254, 200)

    def test_higher_tpi_has_smaller_ratio(self):
        """Higher TPI = finer thread = smaller pitch ratio."""
        fc_20 = [fc for fc in THREAD_IN if fc.name == "20"][0]
        fc_10 = [fc for fc in THREAD_IN if fc.name == "10"][0]
        assert fc_20.ratio < fc_10.ratio


class TestFeedIN:
    def test_not_empty(self):
        assert len(FEED_IN) > 0

    def test_all_mode_3(self):
        for fc in FEED_IN:
            assert fc.mode == 3

    def test_ratios_are_positive(self):
        for fc in FEED_IN:
            assert fc.ratio > 0

    def test_sorted_ascending(self):
        ratios = [fc.ratio for fc in FEED_IN]
        assert ratios == sorted(ratios)


class TestFeedMM:
    def test_not_empty(self):
        assert len(FEED_MM) > 0

    def test_all_mode_4(self):
        for fc in FEED_MM:
            assert fc.mode == 4

    def test_ratios_are_positive(self):
        for fc in FEED_MM:
            assert fc.ratio > 0

    def test_sorted_ascending(self):
        ratios = [fc.ratio for fc in FEED_MM]
        assert ratios == sorted(ratios)

    def test_ratio_matches_name(self):
        """MM feed ratios should equal the name as a fraction."""
        for fc in FEED_MM:
            assert fc.ratio == Fraction(fc.name)


class TestIsThreadingTable:
    """Threading classification comes from FeedConfiguration.mode (the
    structured field), NOT the table's display name. Regression guard for the
    old `"Thread" in name` check, under which renaming a table silently
    flipped ELS into feed mode — no thread geometry, no X-clear retract gate."""

    def test_real_threading_tables_classify_true(self):
        assert is_threading_table("Thread MM") is True
        assert is_threading_table("Thread IN") is True

    def test_real_feed_tables_classify_false(self):
        assert is_threading_table("Feed IN") is False
        assert is_threading_table("Feed MM") is False

    def test_unknown_table_classifies_false(self):
        assert is_threading_table("No Such Table") is False
        assert is_threading_table("") is False

    def test_renamed_threading_table_still_classifies_true(self, monkeypatch):
        """The point of the change: a rename must not alter classification.
        Under the old name-based check this exact case returned False."""
        renamed = dict(table)
        renamed["Imperial Pitches"] = renamed.pop("Thread IN")
        monkeypatch.setattr("reflex.feeds.table", renamed)
        assert is_threading_table("Imperial Pitches") is True
        # And a feed table renamed to CONTAIN "Thread" must not classify true
        # (the old check's other failure direction).
        renamed["Thread Cutting Feeds"] = renamed.pop("Feed MM")
        assert is_threading_table("Thread Cutting Feeds") is False

    def test_mixed_table_classifies_false(self, monkeypatch):
        """A table mixing threading and feed modes is a configuration error —
        classify as NOT threading rather than half-applying thread behavior."""
        mixed = dict(table)
        mixed["Broken"] = list(THREAD_MM[:2]) + list(FEED_MM[:2])
        monkeypatch.setattr("reflex.feeds.table", mixed)
        assert is_threading_table("Broken") is False

    def test_threading_modes_matches_data(self):
        # If a new mode value is ever added to the thread tables, this forces
        # the THREADING_MODES set to be updated in the same change.
        assert {c.mode for c in THREAD_MM} | {c.mode for c in THREAD_IN} == THREADING_MODES
        assert not ({c.mode for c in FEED_IN} | {c.mode for c in FEED_MM}) & THREADING_MODES


class TestTable:
    def test_has_four_entries(self):
        assert len(table) == 4

    def test_expected_keys(self):
        assert set(table.keys()) == {"Thread MM", "Thread IN", "Feed IN", "Feed MM"}

    def test_values_are_lists(self):
        for key, value in table.items():
            assert isinstance(value, list)
            assert len(value) > 0

    def test_all_entries_are_feed_configurations(self):
        for key, entries in table.items():
            for fc in entries:
                assert isinstance(fc, FeedConfiguration)
