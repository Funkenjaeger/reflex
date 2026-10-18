"""The rung-3 adopt-on-connect wiring, live since 2026-10-17.

Until then the path was dark behind a gate that required the retired
mode-watch probe (diag schema 4), which no firmware can be built with, so it
could never run. The mode now comes from the permanent machineMode register.

Three properties are pinned here:

- WHEN it runs: only for a session with no job ('disabled', every session
  after a UI restart). An engaged or alarmed session keeps the state-keyed
  reconcile byte-for-byte, because a reconnect there is a link blip and the
  session still holds the job. A raising mode read, or the flag off, also
  gives byte-for-byte today's state-keyed behavior, compared as whole HAL
  call lists against a freshly recorded baseline rig.
- WHAT each policy cell of els_adopt.adopt_plan does: sync-first where the
  teardown runs (the ordering idiom from test_els_reconnect_teardown), and
  NOTHING beyond the unconditional reads where it does not.
- That no diagnostic schema is consulted: the recorder's learned schema used
  to be the gate, and the reconnect path resets it to None just before
  reconcile runs.

The state-keyed branches themselves are pinned by test_els_reconnect_teardown
and test_els_fsm.
"""
from unittest.mock import MagicMock

import pytest

from reflex.fsms import els_adopt
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
from reflex.fsms.fsm_event_bus import fsm_event_bus as bus

from tests.fsms.test_els_fsm import _build_fsm
from tests.fsms.test_els_reconnect_teardown import _fsm_in_stopped, _reconnect


# The only HAL traffic a hands-off cell may produce: the unconditional
# cal-limits push, the pre-teardown snapshot, and the mode read itself.
READS_ONLY = {'set_cal_limits', 'read_motion_in_flight', 'read_current_mode'}


def _adopt_fsm(*, mode=MODE_IDLE):
    """A fresh session ('disabled') reading a given published mode."""
    fsm = _build_fsm(hal=MagicMock())
    fsm.hal.read_current_mode.return_value = mode
    return fsm


def _baseline_calls(*, moving, active=False, enable=True, state=None):
    """Today's state-keyed reconcile, recorded fresh with the adopt path off.
    Whole mock_calls (names AND arguments), so a comparison against it is the
    byte-for-byte pin."""
    saved = els_adopt.ELS_ADOPT_ON_CONNECT
    els_adopt.ELS_ADOPT_ON_CONNECT = False
    try:
        fsm = _fsm_in(state)
        fsm.hal.reset_mock()
        _reconnect(fsm, moving=moving, active=active, enable=enable)
        return fsm.hal.mock_calls
    finally:
        els_adopt.ELS_ADOPT_ON_CONNECT = saved


def _fsm_in(state):
    """An FSM in `state`. 'stopped' is reached by a real engage; the others are
    set directly, which is all reconcile reads of them."""
    if state == 'stopped':
        return _fsm_in_stopped()
    fsm = _build_fsm(hal=MagicMock())
    if state not in (None, 'disabled'):
        fsm.fsm.set_state(state)
    return fsm


# ─── when the adopt path runs ───────────────────────────────────────────────

def test_flag_is_on():
    """Rung 3 is live. Turning it back off is a decision, not a refactor."""
    assert els_adopt.ELS_ADOPT_ON_CONNECT is True


@pytest.mark.parametrize("moving", [False, True])
def test_flag_off_is_byte_for_byte_the_state_keyed_reconcile(monkeypatch, moving):
    """The flag is the revert switch: off, the mode register is never read and
    every call matches the state-keyed reconcile."""
    monkeypatch.setattr(els_adopt, 'ELS_ADOPT_ON_CONNECT', False)
    fsm = _adopt_fsm(mode=MODE_FEEDING)
    _reconnect(fsm, moving=moving)
    assert fsm.hal.mock_calls == _baseline_calls(moving=moving)
    fsm.hal.read_current_mode.assert_not_called()


def test_no_diagnostic_schema_is_consulted():
    """The gate that kept rung 3 dark: adoption required the diag recorder to
    have learned schema 4, a retired probe. The FSM no longer holds a recorder
    at all, and a fresh session reads the mode straight away."""
    fsm = _adopt_fsm(mode=MODE_FEEDING)
    assert not hasattr(fsm, 'diag_recorder')
    _reconnect(fsm, moving=True)
    fsm.hal.read_current_mode.assert_called_once_with()
    assert fsm.interrupted_pass is not None and fsm.interrupted_pass['mode'] == MODE_FEEDING


def test_mode_read_raising_degrades_to_the_state_keyed_reconcile():
    """The adopt path must never break connect. A raising read falls back to
    the state-keyed reconcile -- identical behavior modulo the one read that
    raised, including the interrupted-pass notice for a moving machine."""
    fsm = _adopt_fsm()
    fsm.hal.read_current_mode.side_effect = RuntimeError("boom")
    _reconnect(fsm, moving=True)
    survivors = [c for c in fsm.hal.mock_calls if c[0] != 'read_current_mode']
    assert survivors == _baseline_calls(moving=True)
    assert fsm.interrupted_pass is not None
    assert fsm.interrupted_pass['teardown'] is True


ENGAGED_OR_ALARMED = ['stopped', 'arming', 'cutting', 'retracting', 'alarm']


@pytest.mark.parametrize("state", ENGAGED_OR_ALARMED)
@pytest.mark.parametrize("mode", [MODE_FEEDING, MODE_HELD, MODE_OFF])
def test_a_session_holding_a_job_keeps_the_state_keyed_reconcile(state, mode):
    """A RECONNECT WHILE ENGAGED. board.connected drops on a single failed scan
    and comes back on the next good one, so reconcile runs mid-session after
    any link blip. The session still holds the job -- committed stop,
    direction, the pass in flight -- so it must not be re-keyed on the
    published mode: a mid-cut blip reading FEEDING would otherwise drop the
    session to 'disabled' and tell the operator their own pass was found
    running. The mode register is not even read."""
    fsm = _fsm_in(state)
    fsm.hal.reset_mock()
    fsm.hal.read_current_mode.return_value = mode
    moving = state in ('cutting', 'retracting')
    _reconnect(fsm, moving=moving, active=(state == 'stopped'))

    fsm.hal.read_current_mode.assert_not_called()
    assert fsm.hal.mock_calls == _baseline_calls(
        moving=moving, active=(state == 'stopped'), state=state)
    assert fsm.state == state


def test_mid_cut_reconnect_stays_cutting_with_no_notice():
    """The case the gate above exists for, end to end: no writes beyond the
    unconditional pushes, still 'cutting', no operator notice."""
    events = []
    unsub = bus.subscribe("els_pass_interrupted", lambda **kw: events.append(kw))
    try:
        fsm = _fsm_in('cutting')
        fsm.hal.reset_mock()
        fsm.hal.read_current_mode.return_value = MODE_FEEDING
        _reconnect(fsm, moving=True)
    finally:
        unsub()
    names = {c[0] for c in fsm.hal.method_calls}
    assert not names & {'stop_sync', 'set_enable', 'set_active'}, names
    assert fsm.state == 'cutting'
    assert fsm.interrupted_pass is None
    assert events == []


# ─── the policy cells, for a fresh session ──────────────────────────────────

@pytest.mark.parametrize("mode", [MODE_OFF, MODE_IDLE, MODE_JOG])
def test_at_rest_mode_tears_down_sync_first_and_adopts_disabled(mode):
    """OFF / IDLE / JOG: the standard teardown runs, and the ordering
    contract carries over from the state-keyed branch verbatim -- snapshot
    before anything is torn down, sync cleared before enable and active."""
    fsm = _adopt_fsm(mode=mode)
    _reconnect(fsm, moving=False, active=True)

    names = [c[0] for c in fsm.hal.method_calls]
    assert 'stop_sync' in names, "adopt teardown never cleared sync"
    assert names.index('read_motion_in_flight') < names.index('stop_sync'), (
        f"snapshot must precede teardown, got order: {names}"
    )
    assert names.index('stop_sync') < names.index('set_enable'), (
        f"stop_sync must precede set_enable, got order: {names}"
    )
    assert names.index('stop_sync') < names.index('set_active'), (
        f"stop_sync must precede set_active, got order: {names}"
    )
    fsm.hal.set_enable.assert_called_once_with(False)
    fsm.hal.set_active.assert_called_once_with(False)
    assert fsm.state == 'disabled'
    assert fsm.interrupted_pass is None


def test_held_on_a_fresh_session_tears_down_sync_first():
    """HELD: a fresh session must not inherit the previous session's armed
    stop (the 2026-08-01 rule, tests/system/test_warm_firmware.py), so the
    hold is torn down as it always was: sync first, so releasing active moves
    nothing. No notice: nothing was moving."""
    fsm = _adopt_fsm(mode=MODE_HELD)
    _reconnect(fsm, moving=False, active=True)

    names = [c[0] for c in fsm.hal.method_calls]
    assert 'stop_sync' in names, "HELD teardown never cleared sync"
    assert names.index('stop_sync') < names.index('set_enable') < names.index('set_active'), (
        f"sync, then enable, then active; got: {names}"
    )
    fsm.hal.set_enable.assert_called_once_with(False)
    fsm.hal.set_active.assert_called_once_with(False)
    assert fsm.state == 'disabled'
    assert fsm.interrupted_pass is None


def _assert_hands_off_and_announced(fsm, events, mode):
    assert {c[0] for c in fsm.hal.method_calls} == READS_ONLY, (
        f"hands-off cell wrote to the HAL: "
        f"{[c[0] for c in fsm.hal.method_calls]}"
    )
    assert fsm.state == 'disabled'
    assert fsm.interrupted_pass is not None, "operator notice payload missing"
    assert fsm.interrupted_pass['mode'] == mode
    assert fsm.interrupted_pass['teardown'] is False, (
        "the notice must know nothing was stopped")
    assert fsm.interrupted_pass['moving'] is True   # snapshot rides along
    assert events, "els_pass_interrupted was never published"


@pytest.mark.parametrize("mode", [MODE_FEEDING, MODE_MOVING, MODE_TAKEUP, MODE_CAL])
def test_live_work_mode_is_hands_off_and_announced(mode):
    """FEEDING / MOVING / TAKEUP / CAL: work is in flight with its own
    protection armed. No writes at all -- unlike the state-keyed
    accepted-cost interrupt -- and the operator is told what was found."""
    events = []
    unsub = bus.subscribe("els_pass_interrupted", lambda **kw: events.append(kw))
    try:
        fsm = _adopt_fsm(mode=mode)
        _reconnect(fsm, moving=True)
    finally:
        unsub()
    _assert_hands_off_and_announced(fsm, events, mode)


@pytest.mark.parametrize("mode", [8, 99])
def test_unknown_mode_is_hands_off_and_announced(mode):
    """A mode value newer than this UI fails toward not-touching: same
    behavior as the live-work row, asserted separately because 'unknown'
    is its own policy cell, not an accident of the others."""
    events = []
    unsub = bus.subscribe("els_pass_interrupted", lambda **kw: events.append(kw))
    try:
        fsm = _adopt_fsm(mode=mode)
        _reconnect(fsm, moving=True)
    finally:
        unsub()
    _assert_hands_off_and_announced(fsm, events, mode)
