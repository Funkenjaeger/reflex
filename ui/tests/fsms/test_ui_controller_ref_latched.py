"""The thread-reference latch lamp: when the operator is told "latched".

elsStop.referenceLatched said it all along; nothing on screen repeated it, so
at the bench (2026-08-24) there was no way to verify "nothing latched" short
of trusting the machine. The controller now republishes it as
`thread_ref_latched`, and what is worth pinning is the POLICY, not the copy:

  1. THE ENABLE GATE. The firmware clears referenceLatched on the enable
     RISING edge, not at disengage -- so between jobs the register still holds
     the OLD job's latch. Shown bare, that claims a reference the next Cut
     will not use.
  2. FAIL DARK, NOT STALE. This is the OPPOSITE of the phase-offset strip's
     failure policy, on purpose. There, a failed read clearing the strip hides
     a live offset the operator must not forget, so a failed poll holds.
     Here, a stale "latched" invites re-entering a thread the machine cannot
     pick up; a dark lamp only ever costs a re-latch. So a failed or absent
     snapshot HIDES the lamp -- a freeze must be impossible.

Driven through a REAL snapshot dict on the board (the fixture's
board.els_stop_values is a real dict -- see _make_collaborators), so the
actual TickReads accessors run, including _no_link bumping read_failures on an
empty snapshot. Stubbing the accessors instead would cut the fallback
accounting -- half the mechanism under test -- out of the loop.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

import pytest

from reflex.fsms.ui_controller import ElsUiController
from tests.fsms.test_ui_controller import (_make_collaborators, _make_x_axis,
                                           _make_z_axis, _pump)


@pytest.fixture
def ctrl():
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    _pump()
    return c


def _snapshot_poll(ctrl, **regs):
    """One tick: put a snapshot on the board, run the poller."""
    ctrl._board.els_stop_values = dict(regs)
    ctrl._poll_thread_ref_latched()


def _latched_and_enabled(ctrl):
    """Fixture step for the tests that need the lamp lit first."""
    _snapshot_poll(ctrl, referenceLatched=1, enable=1)
    assert ctrl.thread_ref_latched is True, \
        "fixture precondition: the lamp is lit"


# ─── the visibility rule ──────────────────────────────────────────────────

def test_shown_when_latched_and_enabled(ctrl):
    _snapshot_poll(ctrl, referenceLatched=1, enable=1)
    assert ctrl.thread_ref_latched is True


def test_hidden_when_nothing_is_latched(ctrl):
    _snapshot_poll(ctrl, referenceLatched=0, enable=1)
    assert ctrl.thread_ref_latched is False


def test_a_previous_jobs_latch_is_hidden_while_disabled(ctrl):
    """referenceLatched survives disengage (it is cleared on the enable
    RISING edge), so latched-but-disabled is the normal between-jobs state --
    and showing it would claim a reference the next Cut will not use."""
    _snapshot_poll(ctrl, referenceLatched=1, enable=0)
    assert ctrl.thread_ref_latched is False


def test_disengaging_mid_job_takes_the_lamp_down(ctrl):
    _latched_and_enabled(ctrl)
    _snapshot_poll(ctrl, referenceLatched=1, enable=0)
    assert ctrl.thread_ref_latched is False


# ─── a freeze must be impossible ──────────────────────────────────────────

def test_a_lost_snapshot_hides_the_lamp(ctrl):
    """No snapshot this tick (refresh failed / link down): the lamp goes DARK.
    Holding the last state here is exactly the stale-latched lie the enable
    gate exists to prevent, arriving by a different road."""
    _latched_and_enabled(ctrl)
    cm = ctrl._board.connection_manager
    baseline = cm.read_failures

    _snapshot_poll(ctrl)   # empty dict: board.py clears it when a refresh fails

    assert ctrl.thread_ref_latched is False
    # Sanity that the fabrication path really ran: the empty snapshot must
    # have been counted, or this test lit no part of the mechanism it names.
    assert cm.read_failures > baseline


def test_the_lamp_relights_on_the_first_good_tick(ctrl):
    """Hide-on-failure is a per-tick answer, not a latch of its own."""
    _latched_and_enabled(ctrl)
    _snapshot_poll(ctrl)   # outage
    assert ctrl.thread_ref_latched is False

    _snapshot_poll(ctrl, referenceLatched=1, enable=1)
    assert ctrl.thread_ref_latched is True


def test_a_fabricated_true_never_lights_the_lamp(ctrl):
    """The empty-snapshot fallback happens to be False today. If it ever
    becomes anything truthy, the reads_failed_since guard is what still keeps
    a fabricated value off the screen -- so it is pinned separately from the
    fallback's current value."""
    cm = ctrl._board.connection_manager

    def _fabricated_true(*_a):
        cm.fail_read()
        return True

    ctrl._hal.tick.reference_latched = _fabricated_true
    ctrl._hal.tick.enable = _fabricated_true
    ctrl._poll_thread_ref_latched()

    assert ctrl.thread_ref_latched is False


def test_a_register_map_bug_hides_and_never_raises_into_dispatch(ctrl):
    """A snapshot MISSING the key is a bug, not a runtime condition, and
    TickReads deliberately lets it raise (see _get). The poller is bound to
    update_tick, so it must swallow that into a dark lamp -- an exception
    escaping a tick handler takes down the only interface the lathe has."""
    _latched_and_enabled(ctrl)

    _snapshot_poll(ctrl, enable=1)   # referenceLatched absent -> KeyError inside

    assert ctrl.thread_ref_latched is False
