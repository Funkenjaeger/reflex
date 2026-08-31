"""Connect-time adoption policy: what to do about the mode the firmware is in.

Rung 3 of the 2026-08-16 architecture direction, built DARK. The firmware
keeps running across a UI restart, and with the mode-watch probe (diag
schema 4) it continuously publishes what it is actually doing as a machine
mode (els_mode_watch.py mirrors the values). That turns connect from a guess
into a read: instead of keying the reconcile on which state THIS session
happens to be in -- which after a restart is always 'disabled', making every
other branch unreachable (review §4.4) -- the session can adopt a plan keyed
on the machine's own published mode.

This module is that plan, and nothing else: a pure mode -> AdoptPlan table.
No Kivy, no board, no clock. The wiring in els_fsm consults it only behind
ELS_ADOPT_ON_CONNECT (below), so against today's builds it decides nothing.

Why each row is what it is:

- OFF / IDLE / JOG -> adopt 'disabled', RUN the standard teardown. The
  machine is at rest (or hand-jogging, which lives outside the elsStop
  block), so the teardown is idempotent against what it is doing -- and it is
  exactly today's always-clear policy: a fresh session must not inherit the
  previous session's armed stop, because feed_without_armed_stop() trusts
  the firmware enable bit against whatever shoulder that stale stop guarded.

- HELD -> adopt 'stopped', NO teardown. The machine is legitimately holding
  the carriage at a shoulder, and active == 1 IS that hold (Ramps.c:815
  accumulates sync steps only while active == 0; clearing it 1->0 is the
  resume trigger, Ramps.c:826). The standard teardown would therefore
  RELEASE the hold and disarm the stop protecting it -- the one thing worse
  than touching nothing. This is the cell the state-keyed reconcile can
  never reach after a restart (a fresh session starts 'disabled' and tears
  the hold down); adopting 'stopped' keeps the machine's own protection
  armed and makes the UI truthful about what it connected to.

- FEEDING / MOVING / TAKEUP / CAL -> hands off ENTIRELY, announce. Motion or
  a live measurement is in flight, driven by a session that no longer
  exists. Today's reconcile stops it -- an accepted cost, because without a
  published mode the UI could not tell a live pass from a stale register.
  With the mode published, the pass keeps its OWN armed stop if it is left
  alone, so the safer plan is to write nothing and tell the operator what
  was found; the session adopts 'disabled' because it has no committed
  targets and no context for the work in flight -- 'disabled' is the only
  honest session state, and it is a statement about the SESSION, not a
  command to the machine.

- anything unrecognized -> hands off, announce. A mode this table does not
  know means firmware newer than this UI. Guessing what a future mode means
  is exactly the failure diagSchema exists to prevent one level up, so the
  unknown row fails toward the row that touches nothing and says so.

The table is exhaustive by construction: every published mode is in exactly
one of the three sets below (pinned by test), and everything else falls to
the unknown row.
"""

from typing import NamedTuple

from reflex.fsms.els_mode_watch import (
    MODE_OFF,
    MODE_IDLE,
    MODE_FEEDING,
    MODE_MOVING,
    MODE_JOG,
    MODE_HELD,
    MODE_TAKEUP,
    MODE_CAL,
)


# THE RUNG-3 MASTER SWITCH — ships False, and flips only after the rung-2
# census (els_mode_watch) validates the mode table on hardware: weeks of real
# (fsm_state, mode) pairings with the divergence alarm quiet are what earn the
# published mode the authority to drive connect. Until then the adopt path is
# dark scaffolding: fully wired, fully tested, deciding nothing.
#
# Read as a module attribute (els_adopt.ELS_ADOPT_ON_CONNECT), never imported
# by value, so the eventual flip -- and tests -- reach the live setting.
ELS_ADOPT_ON_CONNECT = False


class AdoptPlan(NamedTuple):
    """One connect-time decision, whole. `state` is the domain-FSM state the
    session adopts; `teardown` runs the standard sync-first safety teardown;
    `announce` surfaces the interrupted-work notice to the operator."""
    state: str
    teardown: bool
    announce: bool


# The three rows of the table, named so the exhaustiveness test can walk them.
AT_REST_MODES = frozenset({MODE_OFF, MODE_IDLE, MODE_JOG})
HOLDING_MODES = frozenset({MODE_HELD})
LIVE_WORK_MODES = frozenset({MODE_FEEDING, MODE_MOVING, MODE_TAKEUP, MODE_CAL})


def adopt_plan(observed_mode: int) -> AdoptPlan:
    """Map a firmware-published machine mode to a connect-time plan.

    Pure and total: every int maps to a plan, and unrecognized values fail
    toward the hands-off row. The reasoning per row is the module docstring's
    job; this function is just the table.
    """
    if observed_mode in AT_REST_MODES:
        return AdoptPlan(state='disabled', teardown=True, announce=False)
    if observed_mode in HOLDING_MODES:
        return AdoptPlan(state='stopped', teardown=False, announce=False)
    # LIVE_WORK_MODES -- and every value this table does not recognize, which
    # deliberately shares the row that touches nothing.
    return AdoptPlan(state='disabled', teardown=False, announce=True)
