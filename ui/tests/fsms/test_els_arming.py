"""The arming state: what happens between pressing Cut and the feed moving.

Cut used to be `stopped -> cutting`, with six Modbus exchanges and the ACK
accumulation running inline on the Kivy thread, which could not render or take
a touch for the ~120 ms it took. It is now `stopped -> arming -> cutting`: the
writes go out as one batch and the feed is released only when the controller
has acknowledged EVERY one of them.

The cases here are the four ways out of `arming`, because the state is only
worth having if each of them is right:

  * every write acknowledged  -> cutting, and the release happens THEN;
  * any write refused         -> alarm, feed stopped, nothing released;
  * no answer at all          -> alarm on the timeout, same;
  * the operator leaves first -> the late result is ignored.

The verdict covers EVERY write in the batch. Before, only
stopPosition, scaleIndex and enable were checked, so a failed geometry or
backlash write was masked by the later successful enable -- a cut with
half-written thread geometry is not one to release.
"""
from unittest.mock import MagicMock

import pytest

from tests.fsms.test_els_fsm import _build_fsm, _make_controller, _make_z_axis


class _Settled:
    """A Future that is already done, as the synchronous owner's is."""

    def __init__(self, results):
        self._results = results

    def done(self):
        return True

    def result(self, timeout=None):
        if isinstance(self._results, Exception):
            raise self._results
        return self._results


class _Pending:
    """A Future that never resolves: the link went away mid-arming."""

    def __init__(self):
        self.callbacks = []

    def done(self):
        return False

    def add_done_callback(self, fn):
        self.callbacks.append(fn)

    def result(self, timeout=None):
        raise AssertionError("the test should not have waited on this")


def _armed_fsm(results, *, is_threading=True):
    """An FSM at `stopped`, ready to cut, whose arming batch answers `results`."""
    z, z_inp = _make_z_axis()
    hal = MagicMock()
    hal.arm.return_value = (results if hasattr(results, "done")
                            else _Settled(results))
    controller = _make_controller(retract_enabled=True,
                                  is_threading=is_threading)
    fsm = _build_fsm(z=z, hal=hal, controller=controller,
                     els_extra={"els_backlash_steps": 42})
    z.scaledPosition = controller.retract_z   # ready to cut
    fsm.enable()
    hal.reset_mock()
    hal.arm.return_value = (results if hasattr(results, "done")
                            else _Settled(results))
    return fsm, hal


# ── every write acknowledged ─────────────────────────────────────────────────

def test_a_fully_acknowledged_batch_releases_the_cut():
    fsm, hal = _armed_fsm([True] * 6)
    fsm.cut()
    assert fsm.state == "cutting"
    # The release happens in on_enter_cutting, AFTER the acknowledgement --
    # never before, which is the whole ordering this state exists to enforce.
    hal.set_active.assert_called_once_with(False)


def test_the_arming_is_asked_for_once():
    """One domain call, not a batch the FSM assembled: how the writes
    reach the controller is the HAL's business."""
    fsm, hal = _armed_fsm([True] * 6)
    fsm.cut()
    hal.arm.assert_called_once()


# ── any write refused ────────────────────────────────────────────────────────

@pytest.mark.parametrize("refused_index", range(6))
def test_one_refused_write_anywhere_in_the_batch_aborts_the_cut(refused_index):
    """EVERY write, not only the three that used to be checked. A later
    success must not be able to mask an earlier refusal."""
    results = [True] * 6
    results[refused_index] = False
    fsm, hal = _armed_fsm(results)
    fsm.cut()
    assert fsm.state == "alarm", (
        f"a refused write at position {refused_index} did not abort the cut")
    hal.set_active.assert_not_called(), "the cut was released anyway"


def test_an_aborted_arming_stops_the_feed():
    """Not merely "does not release": a feed may ALREADY be running on the
    engage-past-stop path, and aborting has to stop it."""
    fsm, hal = _armed_fsm([True, True, True, True, True, False])
    # The rig's servo carries a plain lambda; make the call observable.
    fsm.board.servo.stop_feed = MagicMock()
    fsm.cut()
    assert fsm.state == "alarm"
    fsm.board.servo.stop_feed.assert_called()


def test_a_batch_that_raises_aborts_the_cut():
    fsm, hal = _armed_fsm(_Settled(RuntimeError("the link went away")))
    fsm.cut()
    assert fsm.state == "alarm"
    hal.set_active.assert_not_called()


def test_no_committed_stop_position_refuses_to_arm():
    fsm, hal = _armed_fsm([True] * 6)
    fsm.controller.stop_z_encoder = None
    fsm.cut()
    assert fsm.state != "cutting", "armed with no stop target"
    hal.arm.assert_not_called()


# ── no answer at all ─────────────────────────────────────────────────────────

def test_a_batch_that_never_answers_times_out_into_alarm():
    """The link going away mid-arming must not leave the UI in `arming` for
    ever with the operator's finger off the button and nothing said."""
    fsm, hal = _armed_fsm(_Pending())
    fsm.cut()
    assert fsm.state == "arming", "should still be waiting"
    hal.set_active.assert_not_called()

    fsm._on_arm_timeout(fsm._arming_seq)      # the Clock event, fired by hand
    assert fsm.state == "alarm"
    hal.set_active.assert_not_called(), "the cut was released after a timeout"


def test_a_timeout_for_a_superseded_attempt_is_ignored():
    fsm, hal = _armed_fsm(_Pending())
    fsm.cut()
    assert fsm.state == "arming"
    stale = fsm._arming_seq - 1
    fsm._on_arm_timeout(stale)
    assert fsm.state == "arming", "a stale timeout moved the FSM"


# ── the operator leaves first ────────────────────────────────────────────────

def test_a_result_arriving_after_the_operator_disengaged_is_ignored():
    """The late-result case the link generation covers for snapshots. A batch
    still in flight when the operator gets out must not drive the FSM
    afterwards -- least of all into `cutting`."""
    pending = _Pending()
    fsm, hal = _armed_fsm(pending)
    fsm.cut()
    assert fsm.state == "arming"

    fsm.disable()
    assert fsm.state == "disabled"

    # The batch finally comes back, fully acknowledged. It must change nothing.
    fsm._arming_done(fsm._arming_seq, _Settled([True] * 6))
    assert fsm.state == "disabled", "a late result re-drove the FSM"
    hal.set_active.assert_not_called()


def test_a_stop_during_arming_lands_in_stopped():
    fsm, hal = _armed_fsm(_Pending())
    fsm.cut()
    assert fsm.state == "arming"
    fsm.stop_active()
    assert fsm.state == "stopped"


def test_arming_is_appended_to_the_state_vocabulary():
    """The flight recorder writes the state as an INDEX into ElsFsm.STATES, so
    a state inserted in the middle re-maps every recording ever made."""
    from reflex.fsms.els_fsm import ElsFsm
    assert ElsFsm.STATES[-1] == "arming"
    assert ElsFsm.STATES[:5] == ['disabled', 'stopped', 'retracting',
                                 'cutting', 'alarm']
