"""The rung-3 adopt-on-connect wiring, which ships DARK.

Two properties, and the first one matters more: with any gate failed --
flag off (the shipped state), no recorder, wrong schema, or a raising mode
read -- reconcile_firmware_on_connect must be BYTE-FOR-BYTE today's
state-keyed behavior, pinned here by comparing whole HAL call lists against
a freshly recorded baseline rig. And with every gate passed, each policy
cell of els_adopt.adopt_plan must land as the right HAL call sequence:
sync-first where the teardown runs (the ordering idiom from
test_els_reconnect_teardown), and NOTHING beyond the unconditional reads
where it does not.

The pre-existing reconcile tests (test_els_reconnect_teardown.py) stay
untouched and green -- they pin the state-keyed branches themselves; this
file pins the gates around them and the adopt path beside them.
"""
from types import SimpleNamespace
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
from reflex.utils.devices import ELS_DIAG_SCHEMA_MODE_WATCH

from tests.fsms.test_els_fsm import _build_fsm
from tests.fsms.test_els_reconnect_teardown import _fsm_in_stopped, _reconnect


# The only HAL traffic a hands-off cell may produce: the unconditional
# cal-limits push, the pre-teardown snapshot, and the mode read itself.
READS_ONLY = {'set_cal_limits', 'read_motion_in_flight', 'read_current_mode'}


def _adopt_fsm(*, schema=ELS_DIAG_SCHEMA_MODE_WATCH, mode=MODE_IDLE):
    """FSM rigged with a recorder stub and a readable published mode."""
    fsm = _build_fsm(hal=MagicMock())
    fsm.diag_recorder = SimpleNamespace(schema=schema)
    fsm.hal.read_current_mode.return_value = mode
    return fsm


def _baseline_calls(*, moving, active=False, enable=True):
    """Today's state-keyed reconcile, recorded fresh off an unrigged FSM.
    Whole mock_calls (names AND arguments), so a comparison against it is
    the byte-for-byte pin the dark wiring promises."""
    fsm = _build_fsm(hal=MagicMock())
    _reconnect(fsm, moving=moving, active=active, enable=enable)
    return fsm.hal.mock_calls


# ─── darkness pins: any failed gate is exactly today ────────────────────────

def test_flag_ships_off():
    """The whole point of 'dark'. Flipping it is a decision the rung-2
    census earns on hardware, not something a refactor may do in passing."""
    assert els_adopt.ELS_ADOPT_ON_CONNECT is False


@pytest.mark.parametrize("moving", [False, True])
def test_flag_off_with_schema_4_present_is_byte_for_byte_today(moving):
    """THE DARKNESS PIN. Schema 4 recognized, mode readable, machine moving
    or not -- with the shipped flag False none of it may matter: same calls,
    same arguments, same order as a rig with no adopt wiring engaged, and
    the mode register is never even read."""
    fsm = _adopt_fsm()
    _reconnect(fsm, moving=moving)
    assert fsm.hal.mock_calls == _baseline_calls(moving=moving)
    fsm.hal.read_current_mode.assert_not_called()


@pytest.mark.parametrize("schema", [None, 2])
def test_flag_on_without_mode_watch_schema_is_today(monkeypatch, schema):
    """Flag on but the recorder learned no probe (None) or a different one:
    read_current_mode means something else under any other schema, so the
    gate must refuse and the state-keyed reconcile run unchanged."""
    monkeypatch.setattr(els_adopt, 'ELS_ADOPT_ON_CONNECT', True)
    fsm = _adopt_fsm(schema=schema)
    _reconnect(fsm, moving=True)
    assert fsm.hal.mock_calls == _baseline_calls(moving=True)
    fsm.hal.read_current_mode.assert_not_called()


def test_flag_on_with_no_recorder_wired_is_today(monkeypatch):
    """An owner that never handed the FSM a recorder (diag_recorder=None)
    keeps the gates failed rather than crashing or guessing."""
    monkeypatch.setattr(els_adopt, 'ELS_ADOPT_ON_CONNECT', True)
    fsm = _build_fsm(hal=MagicMock())
    assert fsm.diag_recorder is None
    _reconnect(fsm, moving=True)
    assert fsm.hal.mock_calls == _baseline_calls(moving=True)


def test_mode_read_raising_degrades_to_today(monkeypatch):
    """The new path must never break connect. A raising read falls back to
    the state-keyed reconcile -- identical behavior modulo the one read that
    raised, including the interrupted-pass notice for a moving machine."""
    monkeypatch.setattr(els_adopt, 'ELS_ADOPT_ON_CONNECT', True)
    fsm = _adopt_fsm()
    fsm.hal.read_current_mode.side_effect = RuntimeError("boom")
    _reconnect(fsm, moving=True)
    survivors = [c for c in fsm.hal.mock_calls if c[0] != 'read_current_mode']
    assert survivors == _baseline_calls(moving=True)
    assert fsm.interrupted_pass is not None


# ─── the policy cells, gates all passed ─────────────────────────────────────

@pytest.mark.parametrize("mode", [MODE_OFF, MODE_IDLE, MODE_JOG])
def test_at_rest_mode_tears_down_sync_first_and_adopts_disabled(monkeypatch, mode):
    """OFF / IDLE / JOG: the standard teardown runs, and the ordering
    contract carries over from the state-keyed branch verbatim -- snapshot
    before anything is torn down, sync cleared before enable and active."""
    monkeypatch.setattr(els_adopt, 'ELS_ADOPT_ON_CONNECT', True)
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


def test_held_adopts_stopped_and_writes_nothing(monkeypatch):
    """HELD: the machine is legitimately holding at a shoulder, and the hold
    IS active == 1 -- any teardown would release it. Reads only, and the
    session adopts 'stopped' (broadcast included, so the controller mirrors
    it like any transition). The cell today's state-keyed code can never
    reach after a restart."""
    monkeypatch.setattr(els_adopt, 'ELS_ADOPT_ON_CONNECT', True)
    states = []
    unsub = bus.subscribe("state_changed", lambda state: states.append(state))
    try:
        fsm = _adopt_fsm(mode=MODE_HELD)
        _reconnect(fsm, moving=False, active=True)
    finally:
        unsub()

    assert {c[0] for c in fsm.hal.method_calls} == READS_ONLY, (
        f"HELD must be hands-off, got: {[c[0] for c in fsm.hal.method_calls]}"
    )
    assert fsm.state == 'stopped'
    assert states == ['stopped'], "adoption must broadcast like a transition"
    assert fsm.interrupted_pass is None


def test_held_on_a_mid_session_reconnect_does_not_rearm(monkeypatch):
    """Same cell from an engaged session: the state-keyed 'stopped' branch
    would rewrite direction/hysteresis and re-arm, and with the adopt path
    gated in it must NOT -- the plan keys on the machine's mode, not on
    which state this session happens to be in."""
    monkeypatch.setattr(els_adopt, 'ELS_ADOPT_ON_CONNECT', True)
    fsm = _fsm_in_stopped()
    fsm.diag_recorder = SimpleNamespace(schema=ELS_DIAG_SCHEMA_MODE_WATCH)
    fsm.hal.read_current_mode.return_value = MODE_HELD
    _reconnect(fsm, moving=False, active=True)

    assert {c[0] for c in fsm.hal.method_calls} == READS_ONLY, (
        f"HELD must be hands-off, got: {[c[0] for c in fsm.hal.method_calls]}"
    )
    assert fsm.state == 'stopped'


def _assert_hands_off_and_announced(fsm, events, mode):
    assert {c[0] for c in fsm.hal.method_calls} == READS_ONLY, (
        f"hands-off cell wrote to the HAL: "
        f"{[c[0] for c in fsm.hal.method_calls]}"
    )
    assert fsm.state == 'disabled'
    assert fsm.interrupted_pass is not None, "operator notice payload missing"
    assert fsm.interrupted_pass['mode'] == mode
    assert fsm.interrupted_pass['moving'] is True   # snapshot rides along
    assert events, "els_pass_interrupted was never published"


@pytest.mark.parametrize("mode", [MODE_FEEDING, MODE_MOVING, MODE_TAKEUP, MODE_CAL])
def test_live_work_mode_is_hands_off_and_announced(monkeypatch, mode):
    """FEEDING / MOVING / TAKEUP / CAL: work is in flight with its own
    protection armed. No writes at all -- unlike today's accepted-cost
    interrupt -- and the operator is told what was found."""
    monkeypatch.setattr(els_adopt, 'ELS_ADOPT_ON_CONNECT', True)
    events = []
    unsub = bus.subscribe("els_pass_interrupted", lambda **kw: events.append(kw))
    try:
        fsm = _adopt_fsm(mode=mode)
        _reconnect(fsm, moving=True)
    finally:
        unsub()
    _assert_hands_off_and_announced(fsm, events, mode)


@pytest.mark.parametrize("mode", [8, 99])
def test_unknown_mode_is_hands_off_and_announced(monkeypatch, mode):
    """A mode value newer than this UI fails toward not-touching: same
    behavior as the live-work row, asserted separately because 'unknown'
    is its own policy cell, not an accident of the others."""
    monkeypatch.setattr(els_adopt, 'ELS_ADOPT_ON_CONNECT', True)
    events = []
    unsub = bus.subscribe("els_pass_interrupted", lambda **kw: events.append(kw))
    try:
        fsm = _adopt_fsm(mode=mode)
        _reconnect(fsm, moving=True)
    finally:
        unsub()
    _assert_hands_off_and_announced(fsm, events, mode)
