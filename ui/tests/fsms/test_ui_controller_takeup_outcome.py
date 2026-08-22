"""The take-up outcome poller must survive a torn Modbus snapshot.

The firmware writes takeupResult and takeupSeq in one ISR pass, but the Modbus
task copies the register block one 16-bit register at a time (reflex-fw
Modbus.c process_FC3) and the 100 kHz ISR can land between the two. Result is
the lower address, so a torn read is (stale result, new seq).

Observed on elspi 2026-08-21: a REFUSED first pass was logged as
"ELS takeup #1 CONFIRMED: moved 0 counts, needed 2 (headroom -2)" -- a
confirmation with less motion than required, which the gate cannot produce --
and takeup_warning was cleared instead of shown. The operator saw the machine
refuse with no message.

The poller now acts on a seq edge only once it has seen the same seq on two
consecutive polls, i.e. one consistent snapshot ~100 ms later.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

import logging
from unittest.mock import MagicMock

import pytest

from reflex.utils.devices import ELS_TAKEUP_ERR_UNCONFIRMED, takeup_failure_text
from tests.fsms.test_ui_controller import _make_collaborators, _make_z_axis, _make_x_axis, _pump
from reflex.fsms.ui_controller import ElsUiController


@pytest.fixture
def ctrl():
    board, els = _make_collaborators(z_axis=_make_z_axis(), x_axis=_make_x_axis())
    c = ElsUiController(els=els, board=board)
    _pump()
    c._hal = MagicMock(name="hal")
    c._hal.read_last_takeup_z_delta.return_value = 0
    c._hal.read_takeup_thresh_counts.return_value = 2
    return c


def _polls(ctrl, snapshots):
    """Feed the poller a sequence of (seq, result) snapshots, one per poll."""
    for seq, result in snapshots:
        ctrl._hal.read_takeup_seq.return_value = seq
        ctrl._hal.read_takeup_result.return_value = result
        ctrl._poll_takeup_outcome()


def test_torn_snapshot_does_not_clear_the_refusal(ctrl, caplog):
    caplog.set_level(logging.INFO)
    # Poll 1: the torn read -- seq advanced, result still the initiation value.
    # Poll 2: the consistent read.
    _polls(ctrl, [(1, 0), (1, ELS_TAKEUP_ERR_UNCONFIRMED)])

    assert ctrl.takeup_warning == takeup_failure_text(ELS_TAKEUP_ERR_UNCONFIRMED, 0, 2)
    assert ctrl._prev_takeup_seq == 1
    assert "CONFIRMED" not in caplog.text
    assert caplog.text.count("REFUSED") == 1


def test_a_stable_confirmation_is_reported_exactly_once(ctrl, caplog):
    caplog.set_level(logging.INFO)
    ctrl._hal.read_last_takeup_z_delta.return_value = 44
    ctrl.takeup_warning = "stale warning from an earlier pass"

    _polls(ctrl, [(1, 0), (1, 0), (1, 0)])

    assert ctrl.takeup_warning == ""
    assert caplog.text.count("CONFIRMED") == 1
    assert ctrl._prev_takeup_seq == 1


def test_nothing_is_acted_on_from_a_single_poll(ctrl, caplog):
    caplog.set_level(logging.INFO)
    ctrl.takeup_warning = "left over"
    _polls(ctrl, [(1, 0)])
    # One poll is not evidence. Nothing logged, nothing cleared, baseline unmoved.
    assert ctrl.takeup_warning == "left over"
    assert caplog.text == ""
    assert ctrl._prev_takeup_seq == 0


def test_a_second_edge_before_confirmation_waits_for_the_newer_seq(ctrl, caplog):
    caplog.set_level(logging.INFO)
    _polls(ctrl, [(1, ELS_TAKEUP_ERR_UNCONFIRMED), (2, 0), (2, 0)])
    # seq 1 was never seen twice, so it is never reported; seq 2 is.
    assert "REFUSED" not in caplog.text
    assert caplog.text.count("CONFIRMED") == 1
    assert ctrl._prev_takeup_seq == 2
    assert ctrl.takeup_warning == ""
