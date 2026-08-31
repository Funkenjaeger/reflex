"""Tests for ElsStopHal — the firmware HAL boundary used by the ELS FSM.

The HAL is a thin shim over `board.device[...]`; we mock the board and
assert that each HAL method touches the right register key with the
right encoded value, and that the read-side methods correctly interpret
firmware state.
"""
from unittest.mock import MagicMock

import pytest

from reflex.fsms.els_stop_hal import ElsStopHal


class _CountingCM:
    """The ConnectionManager surface the HAL uses, with REAL semantics.

    Every stub board gets one. A MagicMock here returns a MagicMock from
    reads_failed_since(), which is truthy, so every fabricated-read guard in
    the HAL reads as "the whole poll was fabricated" regardless of what
    happened -- a guard would look permanently stuck, and a test asserting the
    guard fires would pass with the guard deleted. Neither is a test.
    """

    def __init__(self):
        self.read_failures = 0

    def reads_failed_since(self, baseline):
        return self.read_failures != baseline


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
                    'lastPhaseError': 0.0, 'lastCorrection': 0.0,
                    # Registers added after this stub was first written. The
                    # no-link tests below read them on a LIVE link too, to
                    # prove a healthy read is not counted as fabricated.
                    'takeupSeq': 0, 'takeupResult': 0, 'latchSeq': 0,
                    'calSeq': 0, 'phaseOffsetSteps': 0, 'phaseOffsetSeq': 0},
    }
    for block, overrides in registers.items():
        blocks[block].update(overrides)
    board.device.__getitem__.side_effect = lambda key: blocks[key]
    board._blocks = blocks   # expose for assertions
    board.connection_manager = _CountingCM()
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


# ─── is_move_done: a timed-out read must not read as "done" ────────────────
#
# The disconnect case above has been covered since this file was written. The
# case below is the one that actually bites: the board is CONNECTED and the
# frame simply never comes back. Every read helper returns 0 for that, and
# three zeros compose into "planner idle, pulses flushed" -- a retract reported
# complete while the carriage is still moving, which is the exact overshoot
# test_is_move_done_false_when_pulses_still_in_flight exists to prevent.
#
# Six comms losses in six cuts on 2026-08-23 were all timeouts, not CRC errors.
# Both mutations -- drop the guard, or check the counter before the reads
# instead of after -- survive every other test in this suite.


class _FabricatingBlock(dict):
    """A register block where SOME keys time out.

    Returns the same 0 the real read helpers return on a failed frame and
    counts it the same way, so the HAL sees exactly what a live timeout looks
    like. Keys outside `fabricate` read normally, which is what lets the
    partial-failure case below be built.
    """

    def __init__(self, cm, real, fabricate):
        super().__init__(real)
        self._cm = cm
        self._fabricate = set(fabricate)

    def __getitem__(self, key):
        if key in self._fabricate:
            self._cm.read_failures += 1
            return 0
        return super().__getitem__(key)


def _hal_with_timeouts(fabricate, **servo):
    """A CONNECTED hal whose named servo registers time out."""
    board = _make_board(connected=True, servo=servo)
    cm = _CountingCM()
    board.connection_manager = cm
    board._blocks['servo'] = _FabricatingBlock(
        cm, board._blocks['servo'], fabricate)
    return ElsStopHal(board)


def _healthy_counting_hal(**servo):
    board = _make_board(connected=True, servo=servo)
    board.connection_manager = _CountingCM()
    return ElsStopHal(board)


def test_is_move_done_false_when_every_read_times_out():
    """The whole poll fabricates. Without the guard this returns True and the
    FSM retracts on top of a move it never actually observed finish."""
    hal = _hal_with_timeouts(
        {'stepsToGo', 'currentSteps', 'desiredSteps'},
        stepsToGo=0, desiredSteps=1512, currentSteps=1094)

    assert hal.is_move_done() is False


def test_is_move_done_false_when_only_the_position_reads_time_out():
    """The physically pointed case: the planner really has drained (a genuine
    stepsToGo == 0) but the carriage is only 1094 of 1512 steps through its
    retract, and the two reads that would reveal that both time out. Their
    zeros compare equal, so the unguarded version calls a moving carriage
    stopped."""
    hal = _hal_with_timeouts(
        {'currentSteps', 'desiredSteps'},
        stepsToGo=0, desiredSteps=1512, currentSteps=1094)

    assert hal.is_move_done() is False


def test_the_timed_out_poll_really_did_fabricate():
    """Guards test 1 and 2 against passing for the wrong reason -- if the stub
    stopped fabricating, both would still pass while proving nothing."""
    hal = _hal_with_timeouts(
        {'stepsToGo', 'currentSteps', 'desiredSteps'},
        stepsToGo=0, desiredSteps=1512, currentSteps=1094)
    before = hal.reads_baseline()

    hal.is_move_done()

    assert hal.reads_fabricated_since(before)


def test_is_move_done_still_true_on_a_healthy_flushed_move():
    """The failure mode of an over-eager guard is a retract that never reports
    done and an FSM wedged in `retracting` forever."""
    hal = _healthy_counting_hal(stepsToGo=0, desiredSteps=1512,
                                currentSteps=1512)

    assert hal.is_move_done() is True


def test_a_healthy_is_move_done_counts_nothing():
    hal = _healthy_counting_hal(stepsToGo=0, desiredSteps=1512,
                                currentSteps=1512)
    before = hal.reads_baseline()

    hal.is_move_done()

    assert not hal.reads_fabricated_since(before)


def test_is_move_done_false_for_a_real_in_flight_move_that_read_cleanly():
    """The pre-existing behaviour still holds with the guard in place: this is
    a clean read of a genuinely unfinished move, not a fabricated one."""
    hal = _healthy_counting_hal(stepsToGo=0, desiredSteps=1512,
                                currentSteps=1094)

    assert hal.is_move_done() is False


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


# ── the no-link door: a read that cannot happen must say so ─────────────────
# Two doors produce a fabricated zero. A failed FRAME is counted in
# communication.py and covered by tests/utils/test_read_failure_counter.py.
# This is the other one: a read attempted with NO LINK, which the HAL
# short-circuits. It counted nothing until 2026-08-23, so a disconnect
# fabricated exactly the same zeros while leaving the counter still -- and
# every guard built on that counter passed straight through. The phantom
# "take-up CONFIRMED" that clears a live refusal was reachable that way on any
# disconnect, which on this machine is routine: flashing requires one.
#
# These drive the REAL ElsStopHal. Everything else in this area uses a fake,
# and mutations to the production helper survived the entire suite until this
# section existed.

def _disconnected_hal():
    board = _make_board(connected=False)
    board.connection_manager = _CountingCM()
    return ElsStopHal(board)


def _connected_hal():
    board = _make_board(connected=True)
    board.connection_manager = _CountingCM()
    return ElsStopHal(board)


NO_LINK_READS = [
    "read_takeup_seq", "read_takeup_result", "read_latch_seq",
    "read_phase_offset_steps", "read_phase_offset_seq", "read_cal_seq",
    "read_reference_latched", "read_enable",
]


@pytest.mark.parametrize("method", NO_LINK_READS)
def test_a_read_with_no_link_counts_itself(method):
    """The zero return stays -- callers are written against it -- but it is now
    countable, which is the whole mechanism the action-gating guards rest on."""
    hal = _disconnected_hal()
    before = hal.reads_baseline()

    getattr(hal, method)()

    assert hal.reads_fabricated_since(before), (
        f"{method}() fabricated a value with no link and did not count it; "
        f"every guard built on the counter passes straight through")


@pytest.mark.parametrize("method", NO_LINK_READS)
def test_a_read_with_a_live_link_does_not_count_itself(method):
    """The failure mode of a too-eager counter is a machine that discards every
    poll and silently stops reporting anything."""
    hal = _connected_hal()
    before = hal.reads_baseline()

    getattr(hal, method)()

    assert not hal.reads_fabricated_since(before), (
        f"{method}() counted a healthy read as fabricated")


def test_the_counter_accumulates_across_a_group_of_reads():
    """A poll makes several reads; one count per fabricated read is what lets a
    caller span the whole group with a single before/after comparison."""
    hal = _disconnected_hal()
    before = hal.reads_baseline()

    hal.read_takeup_seq()
    hal.read_takeup_result()
    hal.read_latch_seq()

    assert hal.reads_baseline() == before + 3


def test_a_write_with_no_link_is_not_counted():
    """A write that does not happen fabricates nothing -- it returns no value
    for anyone to act on. Counting it would make every guarded read after a
    write discard itself."""
    hal = _disconnected_hal()
    before = hal.reads_baseline()

    hal.set_enable(True)
    hal.set_stop_position(1234)

    assert hal.reads_baseline() == before
