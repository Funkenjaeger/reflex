"""DISENGAGE through the ELS command channel (protocolVersion 13).

The operator's disengage writes ONE register (elsStop.command) and
edge-detects elsStop.commandSeq in the tick snapshot; the firmware does the
ordered teardown (docs/design/els-command-channel.md). What these pin:

- the write: one command, and nothing written to elsStop or syncEnable
  between it and its ack;
- the session enters 'disabled' at the WRITE, not the ack;
- the ack: commandSeq (hot, in the tick snapshot) moving off the baseline,
  then commandResult (cold, read live once) OK or OK_CAL_ABORTED, completes
  it and brings the UI's mirrors (servoMode, every axis's syncEnable) into
  line without writing anything;
- not an ack: an unchanged seq, or a seq from a fabricated read; and an edge
  whose result read is fabricated waits for the next tick;
- the timeout: no ack issues DISENGAGE once more (it is idempotent), and a
  second silence tells the operator;
- the refusal: a result in the refusal range is shown, and the UI turns
  sync, the feed and the stop off itself, sync first;
- reconnect: a session with a DISENGAGE unacked issues it again instead of
  consulting the adopt table, and a fresh session takes commandSeq at
  connect as its baseline.

HAL-level checks (the register written, the snapshot keys read) are at the
bottom, against the stub board from test_els_stop_registers.
"""
from unittest.mock import MagicMock, patch

import pytest

from reflex.fsms import els_command
from reflex.fsms.els_stop_registers import ElsStopRegisters
from tests.fsms.test_els_fsm import _build_fsm


# ─── rig ──────────────────────────────────────────────────────────────────

class _Tick:
    """The tick snapshot's command registers, and a read-failure switch."""
    def __init__(self, seq=7, result=0):
        self.seq = seq
        self.result = result


def _rig(seq=7, *, connected=True):
    hal = MagicMock()
    tick = _Tick(seq=seq)
    hal.tick.command_seq.side_effect = lambda: tick.seq
    hal.read_command_result.side_effect = lambda: tick.result
    hal.read_command_seq.side_effect = lambda: tick.seq
    hal.request_disengage.return_value = True
    # Real read-failure accounting: a MagicMock answer is truthy and would
    # make every poll look fabricated.
    hal.failures = 0
    hal.reads_baseline.side_effect = lambda: hal.failures
    hal.reads_fabricated_since.side_effect = lambda b: hal.failures != b
    hal.read_current_mode.return_value = 0
    hal.read_motion_in_flight.return_value = {'moving': False}
    fsm = _build_fsm(hal=hal)
    fsm.board.connected = connected
    fsm.board.servo.adopt_feed_off = MagicMock()
    axes = [MagicMock(syncEnable=True), MagicMock(syncEnable=True)]
    fsm.board.axes = axes
    return fsm, hal, tick, axes


def _engage_then_disengage(fsm, hal):
    fsm.enable()
    hal.reset_mock(return_value=False, side_effect=False)
    fsm.disable()


def _poll(fsm, n=1):
    for _ in range(n):
        fsm._poll_command_ack()


# ─── the write ────────────────────────────────────────────────────────────

def test_disengage_writes_one_command_and_nothing_else():
    fsm, hal, tick, _ = _rig()
    _engage_then_disengage(fsm, hal)
    hal.request_disengage.assert_called_once_with()
    # Nothing to elsStop or syncEnable beside the command: no sync-off, no
    # enable or active write.
    hal.stop_sync.assert_not_called()
    hal.set_enable.assert_not_called()
    hal.set_active.assert_not_called()


def test_the_session_is_disabled_at_the_write_not_the_ack():
    fsm, hal, tick, _ = _rig()
    _engage_then_disengage(fsm, hal)
    assert fsm.state == "disabled"
    assert fsm.disengage_unacked


def test_the_poller_watches_the_tick():
    fsm, hal, tick, _ = _rig()
    _engage_then_disengage(fsm, hal)
    fsm.board.bind.assert_any_call(update_tick=fsm._poll_command_ack)


def test_the_baseline_is_read_live_just_before_the_write():
    fsm, hal, tick, _ = _rig(seq=40)
    _engage_then_disengage(fsm, hal)
    names = [c[0] for c in hal.method_calls]
    assert names.index("read_command_seq") < names.index("request_disengage")
    _poll(fsm)
    assert fsm.disengage_unacked, "seq 40 is the baseline, not an ack"


# ─── the ack ──────────────────────────────────────────────────────────────

def test_an_ok_ack_completes_the_disengage():
    fsm, hal, tick, axes = _rig()
    _engage_then_disengage(fsm, hal)
    tick.seq, tick.result = 8, els_command.CMD_RESULT_OK
    _poll(fsm)
    assert not fsm.disengage_unacked
    fsm.board.servo.adopt_feed_off.assert_called_once_with()
    assert all(a.syncEnable is False for a in axes)
    fsm.board.unbind.assert_any_call(update_tick=fsm._poll_command_ack)
    # The mirrors are observations: nothing was written to make them so.
    hal.stop_sync.assert_not_called()
    hal.set_enable.assert_not_called()


def test_a_calibration_aborted_by_the_disengage_is_a_success():
    fsm, hal, tick, _ = _rig()
    _engage_then_disengage(fsm, hal)
    tick.seq, tick.result = 8, els_command.CMD_RESULT_OK_CAL_ABORTED
    with patch("reflex.fsms.els_fsm.notify_operator") as notify:
        _poll(fsm)
    assert not fsm.disengage_unacked
    notify.assert_not_called()
    hal.stop_sync.assert_not_called()


def test_an_unchanged_seq_is_not_an_ack():
    fsm, hal, tick, _ = _rig()
    _engage_then_disengage(fsm, hal)
    _poll(fsm, 5)
    assert fsm.disengage_unacked
    fsm.board.servo.adopt_feed_off.assert_not_called()


def test_a_fabricated_read_is_not_an_ack():
    """A failed tick refresh answers 0 through _no_link; against a baseline
    of 7 that is a change, and it must not count as the ack."""
    fsm, hal, tick, _ = _rig()
    _engage_then_disengage(fsm, hal)

    def fabricated():
        hal.failures += 1
        return 0
    hal.tick.command_seq.side_effect = fabricated
    _poll(fsm)
    assert fsm.disengage_unacked
    fsm.board.servo.adopt_feed_off.assert_not_called()


def test_an_edge_with_a_fabricated_result_waits_for_the_next_tick():
    fsm, hal, tick, _ = _rig()
    _engage_then_disengage(fsm, hal)
    tick.seq, tick.result = 8, els_command.CMD_RESULT_OK

    def fabricated():
        hal.failures += 1
        return 0
    hal.read_command_result.side_effect = fabricated
    _poll(fsm)
    assert fsm.disengage_unacked, "a result assembled from a failed read is not judged"
    hal.read_command_result.side_effect = lambda: tick.result
    _poll(fsm)
    assert not fsm.disengage_unacked


# ─── the timeout ──────────────────────────────────────────────────────────

def test_a_missing_ack_reissues_once_then_tells_the_operator():
    fsm, hal, tick, _ = _rig()
    with patch("reflex.fsms.els_fsm.notify_operator") as notify:
        _engage_then_disengage(fsm, hal)
        _poll(fsm, els_command.ACK_TIMEOUT_POLLS)
        assert hal.request_disengage.call_count == 2, "issued again: it is idempotent"
        notify.assert_not_called()
        _poll(fsm, els_command.ACK_TIMEOUT_POLLS)
        assert hal.request_disengage.call_count == 2, "no third write"
        notify.assert_called_once()
        assert "did not confirm" in notify.call_args[0][0]
    assert fsm.disengage_unacked, "still unacked: reconnect issues it again"
    fsm.board.unbind.assert_any_call(update_tick=fsm._poll_command_ack)


def test_an_ack_after_the_reissue_still_completes_it():
    fsm, hal, tick, _ = _rig()
    _engage_then_disengage(fsm, hal)
    _poll(fsm, els_command.ACK_TIMEOUT_POLLS)
    tick.seq = 8
    _poll(fsm)
    assert not fsm.disengage_unacked


# ─── the refusal ──────────────────────────────────────────────────────────

def test_a_refusal_is_shown_and_the_ui_turns_sync_feed_and_stop_off():
    """The session is already 'disabled' when the refusal arrives, so the UI
    makes the machine inert itself: sync off first, then the feed, then
    enable (dropping enable clears active, and servoEnableTask turns the feed
    back on while any sync is armed with active clear)."""
    fsm, hal, tick, _ = _rig()
    _engage_then_disengage(fsm, hal)
    order = []
    hal.stop_sync.side_effect = lambda: order.append("sync") or True
    hal.set_enable.side_effect = lambda on: order.append(f"enable={on}") or True
    fsm.board.servo.stop_feed = MagicMock(
        side_effect=lambda: order.append("feed") or True)
    tick.seq, tick.result = 8, els_command.CMD_RESULT_ERR_UNKNOWN
    with patch("reflex.fsms.els_fsm.notify_operator") as notify:
        _poll(fsm)
    notify.assert_called_once()
    assert "refused" in notify.call_args[0][0]
    assert order == ["sync", "feed", "enable=False"]
    assert not fsm.disengage_unacked


# ─── reconnect ────────────────────────────────────────────────────────────

def test_reconnect_with_a_disengage_unacked_issues_it_again():
    fsm, hal, tick, _ = _rig()
    _engage_then_disengage(fsm, hal)
    assert fsm.disengage_unacked
    hal.reset_mock(return_value=False, side_effect=False)

    fsm.reconcile_firmware_on_connect()

    hal.request_disengage.assert_called_once_with()
    # Not the adopt table: no mode read, no ordered teardown.
    hal.read_current_mode.assert_not_called()
    hal.stop_sync.assert_not_called()
    hal.set_enable.assert_not_called()


def test_a_disengage_with_no_link_is_issued_at_reconnect():
    fsm, hal, tick, _ = _rig()
    fsm.enable()
    fsm.board.connected = False
    hal.reset_mock(return_value=False, side_effect=False)
    fsm.disable()
    hal.request_disengage.assert_not_called()
    assert fsm.disengage_unacked

    fsm.board.connected = True
    fsm.reconcile_firmware_on_connect()
    hal.request_disengage.assert_called_once_with()


def test_a_fresh_session_takes_commandSeq_at_connect_as_its_baseline():
    fsm, hal, tick, _ = _rig(seq=12)
    fsm.reconcile_firmware_on_connect()
    assert fsm.command_seq_baseline == 12
    # The pre-write live read fails; the connect baseline stands in for it.
    fsm.enable()

    def fabricated():
        hal.failures += 1
        return 0
    hal.read_command_seq.side_effect = fabricated
    fsm.disable()
    _poll(fsm)
    assert fsm.disengage_unacked, "12 is the baseline: no edge yet"
    tick.seq = 13
    _poll(fsm)
    assert not fsm.disengage_unacked


def test_a_connect_with_nothing_unacked_keeps_the_adopt_path():
    fsm, hal, tick, _ = _rig()
    fsm.reconcile_firmware_on_connect()
    hal.request_disengage.assert_not_called()
    hal.read_current_mode.assert_called()



# ─── HAL ──────────────────────────────────────────────────────────────────

def _stub_board(**els):
    from tests.fsms.test_els_stop_registers import _make_board
    board = _make_board(elsStop=dict({'command': 0, 'commandSeq': 0,
                                      'commandResult': 0, 'calBasisSteps': 0},
                                     **els))
    return board


def test_hal_request_disengage_writes_the_command_register():
    board = _stub_board()
    hal = ElsStopRegisters(board)
    assert hal.request_disengage() is True
    assert board._blocks['elsStop']['command'] == els_command.CMD_DISENGAGE


def test_hal_request_disengage_with_no_link_writes_nothing():
    board = _stub_board()
    board.connected = False
    hal = ElsStopRegisters(board)
    assert hal.request_disengage() is False
    assert board._blocks['elsStop']['command'] == 0


def test_hal_reads_commandSeq_live():
    board = _stub_board(commandSeq=31)
    assert ElsStopRegisters(board).read_command_seq() == 31


def test_hal_tick_reads_commandSeq_from_the_snapshot():
    board = _stub_board()
    board.els_stop_values = {'commandSeq': 5}
    assert ElsStopRegisters(board).tick.command_seq() == 5


def test_hal_reads_commandResult_live():
    board = _stub_board(commandResult=256)
    assert ElsStopRegisters(board).read_command_result() == 256


def test_hal_set_cal_basis_writes_the_basis_register():
    board = _stub_board()
    ElsStopRegisters(board).set_cal_basis(368)
    assert board._blocks['elsStop']['calBasisSteps'] == 368


def test_hal_never_writes_calMeasured():
    """calMeasured is firmware-owned again under 13; the facade has no
    writer for it."""
    assert not hasattr(ElsStopRegisters, "set_cal_measured")
