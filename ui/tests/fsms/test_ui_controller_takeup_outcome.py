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
    # Stubbed on `tick`, not on the live readers: this poller is tick-driven,
    # so since 2026-08-23 all four of its reads come from the board's
    # once-per-tick elsStop snapshot rather than four separate Modbus
    # exchanges. The live readers still exist for on-demand callers and are
    # deliberately NOT what this poller uses; stubbing those instead would
    # leave `tick` a bare MagicMock handing the poller a fresh object per call,
    # never equal to the previous one, so the seen-it-twice guard would never
    # settle and nothing below would test what it says it tests.
    c._hal.tick.last_takeup_z_delta.return_value = 0
    c._hal.tick.takeup_thresh_counts.return_value = 2
    return c


def _polls(ctrl, snapshots):
    """Feed the poller a sequence of (seq, result) snapshots, one per poll."""
    for seq, result in snapshots:
        ctrl._hal.tick.takeup_seq.return_value = seq
        ctrl._hal.tick.takeup_result.return_value = result
        ctrl._poll_takeup_outcome()


def test_torn_snapshot_does_not_clear_the_refusal(ctrl, caplog):
    caplog.set_level(logging.INFO)
    # Poll 1: the torn read -- seq advanced, result still the initiation value.
    # Poll 2: the consistent read.
    _polls(ctrl, [(1, 0), (1, ELS_TAKEUP_ERR_UNCONFIRMED)])

    assert ctrl.takeup_warning == takeup_failure_text(ELS_TAKEUP_ERR_UNCONFIRMED, 0)
    assert ctrl._prev_takeup_seq == 1
    assert "CONFIRMED" not in caplog.text
    assert caplog.text.count("REFUSED") == 1


def test_the_refused_log_line_carries_the_counts_the_screen_no_longer_shows(
        ctrl, caplog):
    """THE OTHER END OF THE 2026-08-29 RELOCATION.

    The operator-facing warning used to append "Moved 0 counts, needed 2" and
    no longer does: raw Z-scale counts are a unit this UI exposes nowhere else,
    and the width they cost is what keeps the translucent notice strip from
    landing on top of the status chips. Evan's call, and he called it a
    relocation of audience rather than a loss of information -- which is only
    true while this log line still carries both numbers.

    So the claim gets a guard at the end it moved TO, not just at the end it
    moved FROM (tests/fsms/test_els_cal.py). This is also the line the elspi
    2026-08-21 phantom-CONFIRMED investigation was actually read from.
    """
    caplog.set_level(logging.INFO)
    ctrl._hal.tick.last_takeup_z_delta.return_value = 1
    ctrl._hal.tick.takeup_thresh_counts.return_value = 15
    _polls(ctrl, [(1, ELS_TAKEUP_ERR_UNCONFIRMED),
                  (1, ELS_TAKEUP_ERR_UNCONFIRMED)])

    assert "REFUSED" in caplog.text
    assert "moved 1 counts, needed 15" in caplog.text, (
        "the counts must survive in the log -- dropping them from the screen "
        "was only defensible because a diagnostician can still read them "
        f"here.\n  {caplog.text}")
    # ...and they are still absent from what the operator sees.
    assert "15" not in ctrl.takeup_warning and "count" not in ctrl.takeup_warning.lower()


def test_a_stable_confirmation_is_reported_exactly_once(ctrl, caplog):
    caplog.set_level(logging.INFO)
    ctrl._hal.tick.last_takeup_z_delta.return_value = 44
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


# ── a failed read must not be consumed as the value zero ─────────────────────
# elspi 2026-08-21: a CRC-failed 148-byte frame of ZEROS made takeupSeq read
# 2 -> 0. The poller took that for a sequence edge, read zeros for result,
# moved and needed through the same failing path, logged
# "CONFIRMED: moved 0 counts, needed 0" and CLEARED a live take-up refusal --
# the operator-facing half of a safety gate, silently switched off by a bad
# frame.
#
# The two-poll torn-read guard does NOT cover this and was never meant to: it
# defends against a frame that is internally inconsistent, whereas a zero frame
# is perfectly self-consistent. It is only wrong.

def test_a_failed_read_is_not_taken_for_a_sequence_reset(ctrl, caplog):
    """The elspi defect, reproduced: seq 2 -> 0 because the read FAILED."""
    caplog.set_level(logging.INFO)
    cm = ctrl._board.connection_manager

    # A real outcome first, so there is a live refusal on screen to destroy.
    _polls(ctrl, [(2, ELS_TAKEUP_ERR_UNCONFIRMED), (2, ELS_TAKEUP_ERR_UNCONFIRMED)])
    warning_before = ctrl.takeup_warning
    assert warning_before, "fixture precondition: a refusal is showing"

    # Now the bad frame: every read in the poll returns 0 AND fails.
    caplog.clear()

    def _zero_and_fail(*_a, **_k):
        cm.fail_read()
        return 0

    ctrl._hal.tick.takeup_seq.side_effect = _zero_and_fail
    ctrl._hal.tick.takeup_result.side_effect = _zero_and_fail
    # POLL IT TWICE. A single bad frame is already absorbed by the two-poll
    # torn-read guard, so one poll proves nothing about this one -- the first
    # version of this test did exactly that and passed with the read-failure
    # guard removed. The real incident had RX starvation lasting longer than a
    # poll interval, so the zero frame REPEATED and was self-consistent across
    # both polls, which is precisely what the two-poll guard cannot catch.
    ctrl._poll_takeup_outcome()
    ctrl._poll_takeup_outcome()

    assert ctrl.takeup_warning == warning_before, (
        "a failed read cleared a live take-up refusal")
    assert "CONFIRMED" not in caplog.text, "phantom confirmation from a zero frame"
    assert ctrl._prev_takeup_seq == 2, (
        "the failed poll must not rewrite the last known sequence")


def test_a_read_that_fails_midway_through_the_outcome_is_discarded(ctrl, caplog):
    """The nastier half: seq reads fine, then a read fails while collecting the
    outcome. Reporting then announces an outcome built from zeros."""
    caplog.set_level(logging.INFO)
    cm = ctrl._board.connection_manager

    _polls(ctrl, [(3, ELS_TAKEUP_ERR_UNCONFIRMED), (3, ELS_TAKEUP_ERR_UNCONFIRMED)])
    caplog.clear()

    # Two clean polls establish the edge for seq 4, then result fails.
    ctrl._hal.tick.takeup_seq.side_effect = None
    ctrl._hal.tick.takeup_seq.return_value = 4
    ctrl._hal.tick.takeup_result.side_effect = None
    ctrl._hal.tick.takeup_result.return_value = 0
    ctrl._poll_takeup_outcome()          # first sighting of the edge

    def _zero_and_fail(*_a, **_k):
        cm.fail_read()
        return 0

    ctrl._hal.tick.takeup_result.side_effect = _zero_and_fail
    ctrl._poll_takeup_outcome()          # commit poll, but the result read fails

    assert "CONFIRMED" not in caplog.text, (
        "reported an outcome assembled from a failed read")
    assert ctrl._prev_takeup_seq == 3, (
        "seq must roll back so the real outcome is still reported once the "
        "link recovers")

    # Link recovers: the same outcome must still arrive.
    ctrl._hal.tick.takeup_result.side_effect = None
    ctrl._hal.tick.takeup_result.return_value = 0
    _polls(ctrl, [(4, 0), (4, 0)])
    assert "CONFIRMED" in caplog.text, "the real outcome was lost, not deferred"
    assert ctrl._prev_takeup_seq == 4


def test_only_the_sequence_read_failing_is_still_caught(ctrl, caplog):
    """The baseline is sampled BEFORE the seq read, not after, and this is the
    case that proves it matters.

    A frame can fail for the seq read and the very next read succeed. If the
    baseline were taken after the seq read, that failure would be invisible and
    a fabricated seq of 0 would be interpreted against a perfectly good result
    -- reporting a real outcome under a sequence number that never existed.
    """
    caplog.set_level(logging.INFO)
    cm = ctrl._board.connection_manager

    _polls(ctrl, [(5, ELS_TAKEUP_ERR_UNCONFIRMED), (5, ELS_TAKEUP_ERR_UNCONFIRMED)])
    warning_before = ctrl.takeup_warning
    caplog.clear()

    def _seq_zero_and_fail(*_a, **_k):
        cm.fail_read()
        return 0

    ctrl._hal.tick.takeup_seq.side_effect = _seq_zero_and_fail
    # The outcome reads are CLEAN and would look like a confirmation.
    ctrl._hal.tick.takeup_result.side_effect = None
    ctrl._hal.tick.takeup_result.return_value = 0
    ctrl._poll_takeup_outcome()
    ctrl._poll_takeup_outcome()

    assert "CONFIRMED" not in caplog.text, (
        "a fabricated sequence number was interpreted against a clean result")
    assert ctrl.takeup_warning == warning_before
    assert ctrl._prev_takeup_seq == 5


def test_a_clean_poll_still_reports_normally(ctrl, caplog):
    """The guard must not make the poller inert -- the failure mode of a
    too-eager 'discard the poll' check is a machine that never reports."""
    caplog.set_level(logging.INFO)
    _polls(ctrl, [(1, 0), (1, 0)])
    assert "CONFIRMED" in caplog.text
    assert ctrl.takeup_warning == ""
    assert ctrl._board.connection_manager.read_failures == 0
