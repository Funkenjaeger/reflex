"""The transient-notice policy: which message is on screen, and for how long.

This is the whole reason NoticeCenter is a plain Python object with an injected
clock -- the rules it enforces are all about TIME, and the only way to test them
against the real Kivy Clock is to sleep. Here time is a variable.

WHAT IS PINNED, and why each one is a way this surface could quietly fail:

  1. EXPIRY. A notice that outstays its welcome is a persistent banner, which is
     the thing els_advbar.kv's two-overlay split exists to keep separate.
  2. NOTHING IS DROPPED SILENTLY. The task the surface was built for named this
     explicitly. Two things to say means both get said -- so a notice's clock
     starts when it is SHOWN, and a pre-empted one comes back.
  3. URGENCY WINS THE SCREEN. A warning must never queue behind an info; being
     second is indistinguishable from being lost when the operator is looking at
     the machine, not the display.
  4. A CHATTY CALLER CANNOT FLOOD IT. The realistic misuse is a poller posting
     the same sentence every tick; that has to be one strip that stays up, not a
     hundred queued copies.
  5. THE API IS HARD TO GET WRONG. A bad severity, an empty message or an
     absurd duration is a programming error, and none of them may take the app
     down at the lathe or turn this into something it is not.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

import pytest

from reflex.utils import notices as N
from reflex.utils.notices import (NoticeCenter, NOTICE_INFO, NOTICE_WARNING,
                                  SEVERITIES)

INFO_S = SEVERITIES[NOTICE_INFO].seconds
WARN_S = SEVERITIES[NOTICE_WARNING].seconds


class FakeClock:
    """Monotonic time under test control. Not a Mock: the comparison against a
    deadline IS the mechanism, so it is modelled for real."""

    def __init__(self):
        self.t = 1000.0        # not 0, so an uninitialised deadline reads wrong

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def center(clock):
    return NoticeCenter(time_fn=clock)


def _showing(center):
    """The text on screen right now, or '' -- the value the status bar binds."""
    n = center.showing
    return n.message if n is not None else ""


# ─── expiry ───────────────────────────────────────────────────────────────────

def test_a_notice_is_on_screen_as_soon_as_it_is_posted(center):
    center.post("carriage retracted", NOTICE_INFO)
    assert _showing(center) == "carriage retracted"


def test_a_notice_expires_after_its_severity_default(center, clock):
    center.post("carriage retracted", NOTICE_INFO)

    # Just short of the deadline it is still up. Asserted separately from the
    # clear below: without it, a center that expired everything instantly would
    # pass the "it went away" half of this test.
    clock.advance(INFO_S - 0.1)
    center.poll()
    assert _showing(center) == "carriage retracted"

    clock.advance(0.2)
    center.poll()
    assert _showing(center) == ""


def test_reading_the_current_notice_does_not_expire_it(center, clock):
    """`showing` is read by a property republication that may run at any time;
    if reading advanced the state, what is on screen would depend on how often
    the renderer asked."""
    center.post("carriage retracted", NOTICE_INFO)
    clock.advance(INFO_S + 5)
    assert _showing(center) != ""          # still there until something polls
    center.poll()
    assert _showing(center) == ""


def test_a_warning_stays_up_longer_than_an_info(center, clock):
    """A warning normally asks the operator to go DO something, so it has to
    survive them looking away from the screen to do it."""
    assert WARN_S > INFO_S
    center.post("no ELS Z axis assigned", NOTICE_WARNING)
    clock.advance(INFO_S + 0.1)
    center.poll()
    assert _showing(center) == "no ELS Z axis assigned"


def test_an_explicit_duration_is_honoured(center, clock):
    center.post("set", NOTICE_INFO, seconds=2.0)
    clock.advance(1.9)
    center.poll()
    assert _showing(center) == "set"
    clock.advance(0.2)
    center.poll()
    assert _showing(center) == ""


def test_a_duration_longer_than_the_cap_is_clamped(center, clock):
    """The cap is what stops this surface being bent into a permanent banner --
    persistent state gets a strip whose placement is argued on its own merits."""
    center.post("forever", NOTICE_WARNING, seconds=10_000)
    clock.advance(N.MAX_SECONDS + 0.1)
    center.poll()
    assert _showing(center) == ""


def test_a_duration_shorter_than_the_floor_is_clamped(center, clock):
    """Zero seconds is a notice nobody can read: a coloured flash and no
    message. It becomes the minimum readable time instead."""
    center.post("blink", NOTICE_INFO, seconds=0)
    clock.advance(N.MIN_SECONDS - 0.1)
    center.poll()
    assert _showing(center) == "blink"


# ─── severity ─────────────────────────────────────────────────────────────────

def test_severity_is_carried_through_to_the_renderer(center):
    center.post("hello", NOTICE_INFO)
    assert center.showing.severity == NOTICE_INFO
    center.clear()
    center.post("careful", NOTICE_WARNING)
    assert center.showing.severity == NOTICE_WARNING


def test_an_unknown_severity_is_coerced_up_not_dropped(center):
    """A typo'd severity is a programming error, but raising it out of a kv
    handler would take the app down at the machine, and dropping the message
    would hide whatever the caller was trying to say. Coerce UP: over-warning is
    noise, under-warning trains the operator to ignore the strip."""
    center.post("mystery", "URGENT!!")
    assert center.showing is not None
    assert center.showing.message == "mystery"
    assert center.showing.severity == N.FALLBACK_SEVERITY == NOTICE_WARNING


def test_seconds_cannot_be_passed_positionally(center):
    """The plausible typo is post("...", 5) meaning five seconds, which with a
    positional third parameter would set the SEVERITY to 5 -- coerced to a
    warning by the rule above and never noticed. Keyword-only turns that into a
    TypeError at the call site."""
    with pytest.raises(TypeError):
        center.post("oops", NOTICE_INFO, 5)


# ─── nothing is said with nothing ─────────────────────────────────────────────

def test_an_empty_message_is_refused(center):
    assert center.post("", NOTICE_WARNING) is None
    assert center.post("   ", NOTICE_WARNING) is None
    assert center.post(None, NOTICE_WARNING) is None
    assert _showing(center) == ""


def test_a_refused_message_does_not_disturb_the_one_on_screen(center):
    center.post("real message", NOTICE_INFO)
    center.post("", NOTICE_WARNING)
    assert _showing(center) == "real message"


# ─── collisions ───────────────────────────────────────────────────────────────

def test_a_warning_pre_empts_an_info_immediately(center):
    center.post("carriage retracted", NOTICE_INFO)
    center.post("no ELS Z axis assigned", NOTICE_WARNING)
    assert _showing(center) == "no ELS Z axis assigned"


def test_the_pre_empted_info_comes_back_rather_than_being_lost(center, clock):
    """The requirement in one test: two things to say means both get said."""
    center.post("carriage retracted", NOTICE_INFO)
    center.post("no ELS Z axis assigned", NOTICE_WARNING)

    clock.advance(WARN_S + 0.1)
    center.poll()
    assert _showing(center) == "carriage retracted"


def test_a_pre_empted_notice_gets_its_full_time_when_it_resumes(center, clock):
    """Its clock is stopped while it waits. Otherwise a notice interrupted at
    one second would return with three seconds already spent and blink."""
    center.post("carriage retracted", NOTICE_INFO)
    clock.advance(INFO_S - 0.5)                 # nearly expired...
    center.post("no ELS Z axis assigned", NOTICE_WARNING)   # ...then interrupted
    clock.advance(WARN_S + 0.1)
    center.poll()
    assert _showing(center) == "carriage retracted"

    clock.advance(INFO_S - 0.1)
    center.poll()
    assert _showing(center) == "carriage retracted"


def test_an_info_never_interrupts_a_warning(center, clock):
    center.post("no ELS Z axis assigned", NOTICE_WARNING)
    center.post("carriage retracted", NOTICE_INFO)
    assert _showing(center) == "no ELS Z axis assigned"

    clock.advance(WARN_S + 0.1)
    center.poll()
    assert _showing(center) == "carriage retracted"


def test_a_warning_does_not_interrupt_another_warning(center, clock):
    """Ties do not pre-empt: equal urgency arriving in a burst would otherwise
    strobe the strip and nothing would be readable."""
    center.post("first", NOTICE_WARNING)
    center.post("second", NOTICE_WARNING)
    assert _showing(center) == "first"

    clock.advance(WARN_S + 0.1)
    center.poll()
    assert _showing(center) == "second"


def test_a_queued_warning_jumps_ahead_of_a_queued_info(center, clock):
    """Both are waiting behind the same notice; urgency decides who is next."""
    center.post("holding the screen", NOTICE_WARNING)
    center.post("just so you know", NOTICE_INFO)
    center.post("something is wrong", NOTICE_WARNING)

    clock.advance(WARN_S + 0.1)
    center.poll()
    assert _showing(center) == "something is wrong"

    clock.advance(WARN_S + 0.1)
    center.poll()
    assert _showing(center) == "just so you know"


def test_same_severity_is_shown_oldest_first(center, clock):
    """FIFO within a severity: the operator sees the messages in the order the
    machine produced them."""
    for text in ("one", "two", "three"):
        center.post(text, NOTICE_INFO)
    seen = [_showing(center)]
    for _ in range(2):
        clock.advance(INFO_S + 0.1)
        center.poll()
        seen.append(_showing(center))
    assert seen == ["one", "two", "three"]


def test_a_resumed_notice_keeps_its_place_in_the_original_order(center, clock):
    """It carries its original sequence number back into the queue, so being
    interrupted does not send it to the back of a queue of newer messages."""
    center.post("first info", NOTICE_INFO)
    center.post("interrupt", NOTICE_WARNING)     # pre-empts "first info"
    center.post("second info", NOTICE_INFO)      # posted while it waits

    clock.advance(WARN_S + 0.1)
    center.poll()
    assert _showing(center) == "first info"

    clock.advance(INFO_S + 0.1)
    center.poll()
    assert _showing(center) == "second info"


# ─── a chatty caller must not flood it ────────────────────────────────────────

def test_reposting_the_same_message_refreshes_it_instead_of_queueing(center, clock):
    """The realistic misuse: a poller posting the same sentence every tick while
    a condition lasts. That must be ONE strip that stays up."""
    center.post("take-up not confirmed", NOTICE_WARNING)
    for _ in range(50):
        clock.advance(0.1)
        center.post("take-up not confirmed", NOTICE_WARNING)

    assert _showing(center) == "take-up not confirmed"
    assert center.queued == ()

    # And it goes away on its own once the caller stops, rather than replaying
    # fifty times.
    clock.advance(WARN_S + 0.1)
    center.poll()
    assert _showing(center) == ""


def test_a_duplicate_of_a_waiting_notice_is_not_queued_twice(center):
    center.post("holding", NOTICE_WARNING)
    center.post("same thing", NOTICE_INFO)
    center.post("same thing", NOTICE_INFO)
    assert len(center.queued) == 1


def test_the_same_words_at_a_different_severity_are_a_different_notice(center):
    """Identity is (message, severity): the words look the same but they are
    being said with different weight, and collapsing them would silently
    downgrade the more urgent one."""
    center.post("check the tool", NOTICE_INFO)
    center.post("check the tool", NOTICE_WARNING)
    assert center.showing.severity == NOTICE_WARNING
    assert len(center.queued) == 1


def test_the_queue_is_bounded_and_drops_the_least_urgent_oldest(center, clock):
    """An overflowing queue means more is happening than can be narrated. The
    bound is what stops the operator being shown a parade of stale messages long
    after the event; severity decides who survives it."""
    center.post("on screen", NOTICE_WARNING)
    for i in range(N.MAX_QUEUED + 3):
        center.post(f"info {i}", NOTICE_INFO)
    center.post("late warning", NOTICE_WARNING)

    assert len(center.queued) == N.MAX_QUEUED
    queued = [n.message for n in center.queued]
    assert "late warning" in queued, "a warning was dropped in favour of infos"
    assert "info 0" not in queued, "the stalest info survived the trim"


# ─── clear ────────────────────────────────────────────────────────────────────

def test_clear_takes_down_the_screen_and_the_queue(center):
    center.post("one", NOTICE_WARNING)
    center.post("two", NOTICE_INFO)
    assert center.clear() is True
    assert _showing(center) == ""
    assert center.queued == ()


def test_clear_reports_that_there_was_nothing_to_take_down(center):
    assert center.clear() is False
