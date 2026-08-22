"""ElsBar feed/thread selection wiring (reflex/components/home/elsbar.py).

Exists because of a real on-machine defect, 2026-08-21: the operator switched
from THREAD "12" tpi to FEED "0.020" in the feeds popup, the display read
"12 in", and the carriage fed at the 12 tpi ratio (2.117 mm/rev) instead of
0.508 mm/rev. Both entries sit at list index 12 of their tables, and
``update_feeds_ratio`` was reached only through a Kivy binding on
``current_feeds_index`` -- which does not dispatch when a property is assigned
the value it already holds. The popup path must apply the ratio explicitly.

Same headless pattern as test_els_advbar.py: apply_class_lang_rules is patched
out so the widget's kv tree is never built under the mock GL backend.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from reflex.feeds import table as FEEDS_TABLE


@pytest.fixture
def elsbar(running_app):
    """A real ElsBar against the component conftest's fake app, with the two
    extra collaborators ElsBar.update_feeds_ratio reads: the spindle axis (via
    app.board) and the direction sign (via app.els)."""
    spindle = SimpleNamespace(syncRatioNum=0, syncRatioDen=1)
    running_app.board = SimpleNamespace(
        connected=False,
        get_spindle_axis=lambda: spindle,
    )
    running_app.els.direction_sign.side_effect = lambda forward: 1 if forward else -1

    from reflex.components.home.elsbar import ElsBar
    with patch.object(ElsBar, "apply_class_lang_rules"):
        bar = ElsBar()
    return bar, spindle


def _applied_ratio(spindle):
    from fractions import Fraction
    return Fraction(abs(spindle.syncRatioNum), spindle.syncRatioDen)


def test_switching_tables_to_the_same_index_applies_the_new_ratio(elsbar):
    bar, spindle = elsbar

    bar.set_feed_ratio("Thread IN", 12)
    assert FEEDS_TABLE["Thread IN"][12].name == "12"
    assert bar.feed_name == "12"
    assert _applied_ratio(spindle) == FEEDS_TABLE["Thread IN"][12].ratio

    # Same list index, different table: the binding on current_feeds_index
    # does NOT fire here. Before the fix the name stayed "12" and the spindle
    # kept the thread ratio.
    bar.set_feed_ratio("Feed IN", 12)
    assert FEEDS_TABLE["Feed IN"][12].name == "0.020"
    assert bar.mode_name == "Feed IN"
    assert bar.feed_name == "0.020"
    assert _applied_ratio(spindle) == FEEDS_TABLE["Feed IN"][12].ratio
    assert _applied_ratio(spindle) != FEEDS_TABLE["Thread IN"][12].ratio


def test_selecting_a_different_index_still_applies_once_per_selection(elsbar):
    bar, spindle = elsbar

    bar.set_feed_ratio("Feed IN", 3)
    assert bar.feed_name == FEEDS_TABLE["Feed IN"][3].name
    assert _applied_ratio(spindle) == FEEDS_TABLE["Feed IN"][3].ratio

    bar.set_feed_ratio("Feed IN", 7)
    assert bar.feed_name == FEEDS_TABLE["Feed IN"][7].name
    assert _applied_ratio(spindle) == FEEDS_TABLE["Feed IN"][7].ratio


def test_arrow_navigation_still_applies_through_the_binding(elsbar):
    bar, spindle = elsbar
    bar.set_feed_ratio("Feed IN", 3)
    bar.next_feed()
    assert bar.feed_name == FEEDS_TABLE["Feed IN"][4].name
    assert _applied_ratio(spindle) == FEEDS_TABLE["Feed IN"][4].ratio
    bar.previous_feed()
    assert bar.feed_name == FEEDS_TABLE["Feed IN"][3].name
    assert _applied_ratio(spindle) == FEEDS_TABLE["Feed IN"][3].ratio
