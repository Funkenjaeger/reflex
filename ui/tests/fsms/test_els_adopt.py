"""Tests for the rung-3 connect-time adoption policy (els_adopt.py).

The module is a pure mode -> plan table, so these tests ARE the table,
one cell at a time, plus the structural properties that keep it honest:
exhaustive over the published modes, total over the ints, and failing
toward not-touching on anything it does not recognize.
"""
import pytest

from reflex.fsms.els_adopt import (
    AT_REST_MODES,
    HOLDING_MODES,
    LIVE_WORK_MODES,
    AdoptPlan,
    adopt_plan,
)
from reflex.fsms.els_mode_watch import (
    MODE_NAMES,
    MODE_OFF,
    MODE_IDLE,
    MODE_FEEDING,
    MODE_MOVING,
    MODE_JOG,
    MODE_HELD,
    MODE_TAKEUP,
    MODE_CAL,
)


# ─── the cells ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", [MODE_OFF, MODE_IDLE, MODE_JOG])
def test_at_rest_modes_adopt_disabled_and_tear_down(mode):
    """OFF / IDLE / JOG: the machine is doing nothing the teardown could hurt,
    and a fresh session must not inherit the previous session's armed stop --
    today's always-clear policy, kept."""
    assert adopt_plan(mode) == AdoptPlan(
        state='disabled', teardown=True, announce=False)


def test_held_adopts_stopped_and_does_not_tear_down():
    """HELD: active == 1 IS the hold, so the standard teardown would release
    the carriage and disarm the stop protecting it. Hands off the registers;
    the session adopts 'stopped' so the UI is truthful about the hold. This is
    the cell the state-keyed reconcile can never reach after a restart."""
    assert adopt_plan(MODE_HELD) == AdoptPlan(
        state='stopped', teardown=False, announce=False)


@pytest.mark.parametrize("mode", [MODE_FEEDING, MODE_MOVING, MODE_TAKEUP, MODE_CAL])
def test_live_work_modes_are_hands_off_and_announced(mode):
    """FEEDING / MOVING / TAKEUP / CAL: motion or a live measurement is in
    flight with its own protection still armed. Touch nothing, tell the
    operator; 'disabled' describes the SESSION (no targets, no context),
    not a command to the machine."""
    assert adopt_plan(mode) == AdoptPlan(
        state='disabled', teardown=False, announce=True)


@pytest.mark.parametrize("mode", [8, 42, 255, -1])
def test_unknown_modes_fail_toward_not_touching(mode):
    """A mode this UI does not know means firmware newer than this UI.
    Guessing is the failure diagSchema exists to prevent one level up, so
    unknown shares the hands-off-and-announce row."""
    assert adopt_plan(mode) == AdoptPlan(
        state='disabled', teardown=False, announce=True)


# ─── structural properties ──────────────────────────────────────────────────

def test_table_is_exhaustive_over_published_modes():
    """Every published mode sits in exactly ONE row. A mode added to
    els_mode_watch without a deliberate row here would silently fall to the
    unknown row -- safe, but it must be a decision, not an accident, so this
    fails until the new mode is placed."""
    rows = (AT_REST_MODES, HOLDING_MODES, LIVE_WORK_MODES)
    for mode in MODE_NAMES:
        assert sum(mode in row for row in rows) == 1, (
            f"mode {MODE_NAMES[mode]} is in {sum(mode in row for row in rows)} "
            f"rows; every published mode belongs in exactly one"
        )


def test_plans_only_name_real_resting_states():
    """A plan's state is handed to the domain FSM verbatim. Only the two
    resting states are adoptable -- adopting a transient state (cutting,
    retracting) would claim work this session never started. Mirrors
    ElsFsm.STATES without importing the Kivy-laden module here."""
    for mode in list(MODE_NAMES) + [8, 99]:
        assert adopt_plan(mode).state in ('disabled', 'stopped')


def test_teardown_never_runs_toward_stopped():
    """The teardown clears the very registers a 'stopped' adoption exists to
    preserve; a plan carrying both would contradict itself."""
    for mode in list(MODE_NAMES) + [8, 99]:
        plan = adopt_plan(mode)
        assert not (plan.teardown and plan.state == 'stopped')
