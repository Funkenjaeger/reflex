from unittest.mock import patch, MagicMock

import pytest

from tests.dispatchers.conftest import MockFormats, MockOffsetProvider
from reflex.dispatchers.axis import AxisDispatcher
from reflex.dispatchers.board import Board
from reflex.dispatchers.input import InputDispatcher
from reflex.dispatchers.servo import ServoDispatcher


@pytest.fixture
def formats():
    return MockFormats()


@pytest.fixture
def offset_provider():
    return MockOffsetProvider()


@pytest.fixture
def board(formats, offset_provider, tmp_path, monkeypatch):
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / ".config" / "reflex"))
    with patch("reflex.dispatchers.board.ConnectionManager") as MockCM, \
         patch("reflex.dispatchers.board.Clock"):
        mock_cm = MagicMock()
        mock_cm.__getitem__ = MagicMock(return_value=MagicMock())
        MockCM.return_value = mock_cm
        b = Board(formats=formats, offset_provider=offset_provider)
    return b


class TestBoardCreation:
    def test_creates_servo_dispatcher(self, board):
        assert isinstance(board.servo, ServoDispatcher)

    def test_creates_four_input_dispatchers(self, board):
        assert len(board.inputs) == 4
        for inp in board.inputs:
            assert isinstance(inp, InputDispatcher)

    def test_inputs_have_correct_input_indices(self, board):
        for i, inp in enumerate(board.inputs):
            assert inp.inputIndex == i

    def test_creates_four_axis_dispatchers(self, board):
        assert len(board.axes) == 4
        for a in board.axes:
            assert isinstance(a, AxisDispatcher)


class TestGetSpindleAxis:
    def test_returns_none_when_no_spindle(self, board):
        for a in board.axes:
            a.spindleMode = False
        assert board.get_spindle_axis() is None

    def test_returns_spindle_axis(self, board):
        board.axes[2].spindleMode = True
        result = board.get_spindle_axis()
        assert result is board.axes[2]

    def test_returns_none_when_multiple_spindles(self, board):
        board.axes[0].spindleMode = True
        board.axes[1].spindleMode = True
        assert board.get_spindle_axis() is None

    def test_input_spindle_mode_independent_of_axis(self, board):
        """Input owns its spindleMode — setting on axis does not propagate to input."""
        board.inputs[2].spindleMode = True
        assert board.inputs[2].spindleMode is True
        assert board.axes[2].spindleMode is False  # axis spindleMode is independent


class TestLinkHandover:
    """pause_polling/resume_polling: giving the serial port to the flasher.

    The load-bearing assertion is that pausing CLOSES THE PORT rather than
    merely stopping the tick. modbus-flash.py opens the port itself, and a
    minimalmodbus instrument left holding the file descriptor is the
    difference between a flash that works and one that either cannot open the
    device or interleaves frames with polls that never actually stopped.
    """

    @pytest.fixture
    def paused_board(self, formats, offset_provider, tmp_path, monkeypatch):
        """Board with Clock still patched, so pause/resume can be observed."""
        monkeypatch.setenv("REFLEX_CONFIG_DIR",
                           str(tmp_path / ".config" / "reflex"))
        with patch("reflex.dispatchers.board.ConnectionManager") as MockCM, \
             patch("reflex.dispatchers.board.Clock") as MockClock:
            mock_cm = MagicMock()
            mock_cm.__getitem__ = MagicMock(return_value=MagicMock())
            MockCM.return_value = mock_cm
            # A DISTINCT event per call. The default MagicMock hands back the
            # same object every time, which would make
            # test_resume_replaces_the_cancelled_event pass no matter what
            # resume_polling did -- a check unable to see the bug it is for.
            MockClock.schedule_interval.side_effect = \
                lambda *a, **k: MagicMock(name="ClockEvent")
            b = Board(formats=formats, offset_provider=offset_provider)
            yield b, mock_cm, MockClock

    def test_pause_releases_the_serial_port(self, paused_board):
        b, cm, _ = paused_board
        cm.disconnect.reset_mock()
        b.pause_polling()
        cm.disconnect.assert_called_once()

    def test_pause_stops_the_tick_and_says_so(self, paused_board):
        b, _, _ = paused_board
        tick = b.task_update
        b.pause_polling()
        tick.cancel.assert_called_once()
        assert b.link_paused is True
        assert b.connected is False

    def test_pause_clears_the_snapshots_it_can_no_longer_refresh(self, paused_board):
        b, _, _ = paused_board
        b.els_stop_values = {"active": 1}
        b.fast_data_values = {"x": 1}
        b.pause_polling()
        assert b.els_stop_values == {}
        assert b.fast_data_values == {}

    def test_pause_is_idempotent(self, paused_board):
        b, cm, _ = paused_board
        cm.disconnect.reset_mock()
        b.pause_polling()
        b.pause_polling()
        cm.disconnect.assert_called_once()

    def test_resume_reopens_and_reschedules(self, paused_board):
        b, cm, clock = paused_board
        b.pause_polling()
        cm.connect.reset_mock()
        clock.schedule_interval.reset_mock()
        b.resume_polling()
        cm.connect.assert_called_once()
        clock.schedule_interval.assert_called_once()
        assert b.link_paused is False

    def test_resume_replaces_the_cancelled_event(self, paused_board):
        """A cancelled ClockEvent is not re-armed by touching its timeout, and
        update() reads task_update.timeout every tick."""
        b, _, _ = paused_board
        before = b.task_update
        b.pause_polling()
        b.resume_polling()
        assert b.task_update is not before

    def test_resume_without_a_pause_does_nothing(self, paused_board):
        b, cm, clock = paused_board
        cm.connect.reset_mock()
        clock.schedule_interval.reset_mock()
        b.resume_polling()
        cm.connect.assert_not_called()
        clock.schedule_interval.assert_not_called()
