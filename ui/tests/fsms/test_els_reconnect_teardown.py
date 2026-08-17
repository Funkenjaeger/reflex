"""Startup teardown: what reconcile_firmware_on_connect does to a machine that
is still moving.

The firmware keeps running across a UI restart (observed on the real machine
2026-08-01), so on connect the app can find the carriage mid-pass with the
previous session gone. Decided 2026-08-16: ALWAYS disable sync on connect, and
TELL the operator when doing so interrupted a live pass.

Each test here pins one half of that decision. The ORDER test is the one that
matters most and the one least likely to survive a refactor by accident.
"""
from unittest.mock import MagicMock, call

import pytest

from tests.fsms.test_els_fsm import _build_fsm


def _reconnect(fsm, *, moving, enable=True, active=False):
    """Drive reconcile with the firmware reporting a given motion state."""
    fsm.hal.read_motion_in_flight.return_value = {
        'moving': moving, 'sync': moving, 'servoMode': 1 if moving else 0,
        'active': active, 'enable': enable,
    }
    fsm.reconcile_firmware_on_connect()


def test_sync_is_cleared_before_enable_and_active():
    """THE ORDERING TEST. Clearing enable also clears elsStop.active in firmware,
    and active == 0 is what lets sync steps accumulate (Ramps.c:815) AND what the
    firmware treats as the resume trigger (Ramps.c:826). So clearing enable while
    sync is still live is a resume command issued with the stop being torn down.

    Asserting the calls happened is not enough -- they always did. Only the
    SEQUENCE distinguishes safe from unsafe, so assert on the sequence."""
    fsm = _build_fsm(hal=MagicMock())
    _reconnect(fsm, moving=True)

    names = [c[0] for c in fsm.hal.method_calls]
    assert 'stop_sync' in names, "sync was never cleared on connect"
    assert 'set_enable' in names
    assert names.index('stop_sync') < names.index('set_enable'), (
        f"stop_sync must precede set_enable, got order: {names}"
    )
    assert names.index('stop_sync') < names.index('set_active'), (
        f"stop_sync must precede set_active, got order: {names}"
    )


def test_snapshot_is_read_before_anything_is_torn_down():
    """The registers are the only surviving evidence of what the machine was
    doing, and the teardown destroys them. Read first or do not read at all."""
    fsm = _build_fsm(hal=MagicMock())
    _reconnect(fsm, moving=True)

    names = [c[0] for c in fsm.hal.method_calls]
    assert names.index('read_motion_in_flight') < names.index('stop_sync'), (
        f"snapshot must be taken before teardown, got order: {names}"
    )


def test_interrupted_pass_is_reported_when_the_machine_was_moving():
    """The accepted cost of always-disable-sync is stopping a live pass. The
    operator has to be told, or a thread that stops partway is a mystery."""
    fsm = _build_fsm(hal=MagicMock())
    _reconnect(fsm, moving=True)
    assert fsm.interrupted_pass is not None
    assert fsm.interrupted_pass['moving'] is True


def test_no_notice_when_the_machine_was_idle():
    """The common case by far. A startup warning that fires on every boot is a
    warning nobody reads, which would defeat the one time it matters."""
    fsm = _build_fsm(hal=MagicMock())
    _reconnect(fsm, moving=False)
    assert fsm.interrupted_pass is None


def test_sync_is_cleared_even_when_nothing_was_moving():
    """Unconditional on purpose. The dangerous case is NOT only 'mid-pass': a
    carriage parked at a shoulder is held there by active == 1, and clearing
    active with sync still live releases it. That state reports moving=False --
    it genuinely is not moving -- so gating the teardown on `moving` would skip
    exactly the case with the widest window."""
    fsm = _build_fsm(hal=MagicMock())
    _reconnect(fsm, moving=False, enable=True, active=True)
    assert 'stop_sync' in [c[0] for c in fsm.hal.method_calls]


def test_teardown_survives_an_unreadable_snapshot():
    """A diagnostic read must never block a safety teardown."""
    fsm = _build_fsm(hal=MagicMock())
    fsm.hal.read_motion_in_flight.return_value = {
        'moving': False, 'reason': 'read failed: boom',
    }
    fsm.reconcile_firmware_on_connect()
    assert 'stop_sync' in [c[0] for c in fsm.hal.method_calls]
    assert fsm.interrupted_pass is None
