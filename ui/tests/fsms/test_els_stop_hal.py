"""Tests for ElsStopHal — the firmware HAL boundary used by the ELS FSM.

The HAL is a thin shim over `board.device[...]`; we mock the board and
assert that each HAL method touches the right register key with the
right encoded value, and that the read-side methods correctly interpret
firmware state.
"""
from unittest.mock import MagicMock

import pytest

from rcp.fsms.els_stop_hal import ElsStopHal


def _make_board(connected=True, **registers):
    """Mock board exposing `device['...'][...]` register access. Pass any
    initial values via kwargs grouped per-block (e.g. servo={...})."""
    board = MagicMock()
    board.connected = connected
    # Map block-name → mutable dict of register values.
    blocks = {
        'servo': {'stepsToGo': 0, 'currentSteps': 0,
                  'desiredSteps': 0, 'maxSpeed': 720},
        'elsStop': {'enable': 0, 'active': 0, 'stopPosition': 0,
                    'stopDirection': 0, 'hysteresis': 0,
                    'scaleIndex': 0, 'threadPitchSteps': 0.0,
                    'zCountsPerPitch': 0.0, 'backlashSteps': 0,
                    'referenceLatched': 0, 'takeupPending': 0,
                    'latchedZ': 0, 'latchedSpindle': 0,
                    'lastIdealAdvance': 0.0, 'lastActualAdvance': 0.0,
                    'lastPhaseError': 0.0, 'lastCorrection': 0.0},
    }
    for block, overrides in registers.items():
        blocks[block].update(overrides)
    board.device.__getitem__.side_effect = lambda key: blocks[key]
    board._blocks = blocks   # expose for assertions
    board.servo.reverse = False
    return board


# ─── is_move_done: the regression we just fixed ────────────────────────────

def test_is_move_done_false_when_steps_to_go_nonzero():
    board = _make_board(servo={'stepsToGo': 100,
                               'desiredSteps': 0, 'currentSteps': 0})
    hal = ElsStopHal(board)
    assert hal.is_move_done() is False


def test_is_move_done_false_when_pulses_still_in_flight():
    """Regression: with stepsToGo==0 but the rate-limited step pulse
    generator still catching up (currentSteps < desiredSteps), the move
    is NOT done. Triggering retract_done here would let the ELS FSM
    re-issue a retract on top of pending pulses → physical overshoot."""
    board = _make_board(servo={'stepsToGo': 0,
                               'desiredSteps': 1512, 'currentSteps': 1094})
    hal = ElsStopHal(board)
    assert hal.is_move_done() is False


def test_is_move_done_true_when_planner_done_and_pulses_flushed():
    board = _make_board(servo={'stepsToGo': 0,
                               'desiredSteps': 1512, 'currentSteps': 1512})
    hal = ElsStopHal(board)
    assert hal.is_move_done() is True


def test_is_move_done_false_when_board_disconnected():
    board = _make_board(connected=False,
                        servo={'stepsToGo': 0,
                               'desiredSteps': 100, 'currentSteps': 100})
    hal = ElsStopHal(board)
    assert hal.is_move_done() is False


def test_is_move_done_true_at_rest():
    board = _make_board(servo={'stepsToGo': 0,
                               'desiredSteps': 0, 'currentSteps': 0})
    hal = ElsStopHal(board)
    assert hal.is_move_done() is True


# ─── basic writer pass-through sanity ──────────────────────────────────────

def test_set_enable_writes_register():
    board = _make_board()
    hal = ElsStopHal(board)
    hal.set_enable(True)
    assert board._blocks['elsStop']['enable'] == 1
    hal.set_enable(False)
    assert board._blocks['elsStop']['enable'] == 0


def test_set_steps_to_go_writes_register():
    board = _make_board()
    hal = ElsStopHal(board)
    hal.set_steps_to_go(-1512)
    assert board._blocks['servo']['stepsToGo'] == -1512


def test_set_active_skipped_when_disconnected():
    board = _make_board(connected=False)
    hal = ElsStopHal(board)
    hal.set_active(True)
    # Mock will still allow assignment to a returned dict, but we wired
    # the board path through .connected; verify the dict was untouched.
    assert board._blocks['elsStop']['active'] == 0


def test_read_active_false_when_disconnected():
    board = _make_board(connected=False, elsStop={'active': 1})
    hal = ElsStopHal(board)
    assert hal.read_active() is False


def test_read_active_reflects_register():
    board = _make_board(elsStop={'active': 1})
    hal = ElsStopHal(board)
    assert hal.read_active() is True
    board._blocks['elsStop']['active'] = 0
    assert hal.read_active() is False


def test_set_hysteresis_tight_vs_loose():
    board = _make_board()
    hal = ElsStopHal(board)
    hal.set_hysteresis_tight()
    assert board._blocks['elsStop']['hysteresis'] == 0
    hal.set_hysteresis_loose()
    assert board._blocks['elsStop']['hysteresis'] == 800
