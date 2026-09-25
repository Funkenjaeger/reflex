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
from unittest.mock import ANY, patch

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


# ── ADV refusal: the advanced bar cannot be hidden while armed ───────
#
# Consumer 1 of the operator-notice surface, wired 2026-08-31. This bar is the
# only place armed-ness is visible: the plain bar has a Sync Enable LED and no
# armed indicator, and that is a decision (2026-08-20), not an oversight. So
# the ADV button hiding the advanced bar mid-job made an armed machine and an
# idle one look identical.

@pytest.fixture
def adv(elsbar):
    bar, _spindle = elsbar
    return bar


def _toggle(bar, engaged):
    from reflex.components.home.elsbar import ElsBar
    with patch.object(ElsBar, "_els_engaged", staticmethod(lambda: engaged)), \
            patch("reflex.components.home.elsbar.notify_operator") as notify:
        changed = bar.toggle_advanced()
    return changed, notify


def test_hiding_is_refused_while_a_stop_job_is_engaged(adv):
    adv.enable_advanced = True
    changed, notify = _toggle(adv, engaged=True)
    assert changed is False
    assert adv.enable_advanced is True, "the bar was hidden anyway"
    notify.assert_called_once_with(adv.HIDE_REFUSED_NOTICE, ANY)


def test_the_refusal_is_never_silent(adv):
    """Refusing silently is worse than allowing it -- the operator presses ADV,
    nothing happens, and they learn the button is broken."""
    adv.enable_advanced = True
    _changed, notify = _toggle(adv, engaged=True)
    assert notify.call_count == 1
    assert "disengage" in adv.HIDE_REFUSED_NOTICE.lower()


def test_hiding_is_allowed_when_no_job_is_engaged(adv):
    """The negative control, and the one Evan corrected the spec on: sync
    armed with the stop disengaged is ORDINARY vanilla ELS feed. servoMode is
    deliberately not part of the condition -- it has its own LED on this very
    bar, so hiding the advanced one conceals nothing about it."""
    adv.enable_advanced = True
    changed, notify = _toggle(adv, engaged=False)
    assert changed is True
    assert adv.enable_advanced is False
    notify.assert_not_called()


def test_showing_is_never_refused(adv):
    """One-way guard by construction: more information on screen is not the
    unsafe direction."""
    adv.enable_advanced = False
    changed, notify = _toggle(adv, engaged=True)
    assert changed is True
    assert adv.enable_advanced is True
    notify.assert_not_called()


def test_engaged_fails_open_when_it_cannot_be_answered(adv):
    """Opposite of the usual rule, on purpose. A wrong True yields a bar that
    CANNOT BE HIDDEN AT ALL on a machine whose app is already in trouble; a
    wrong False yields the defect being fixed. The dismissible failure wins."""
    from reflex.components.home.elsbar import ElsBar
    with patch("reflex.app.MainApp.get_running_app",
               side_effect=RuntimeError("no app")):
        assert ElsBar._els_engaged() is False
