"""ElsDiagRecorder: the firmware diagnostic scratchpad reader.

The properties under test are the ones that make this safe to ship enabled in
the tree while being inert on the machine, plus the ones that stop it recording
a number whose meaning it does not actually know.
"""

import json
from unittest.mock import MagicMock

import pytest

from reflex.fsms.els_diag import ElsDiagRecorder, MAX_FAILURES
from reflex.utils.devices import (ELS_DIAG_SCHEMA_TAKEUP_SETTLE,
                                  ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2)


def make_capture(seq=1, schema=ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2):
    return {
        "schema": schema,
        "seq": seq,
        "bucket_ticks": 100,
        "bucket_count": 50,
        "settle_ticks": 412,
        "net_counts": 29,
        "trace": [0] * 48 + [3, 1],
    }


@pytest.fixture
def board():
    b = MagicMock()
    b.connected = True
    b.fast_data_values = {"executionInterval": 1000}
    return b


@pytest.fixture
def hal():
    return MagicMock()


def rec(hal, board, tmp_path):
    return ElsDiagRecorder(hal, board, capture_dir=tmp_path)


class TestDormancy:
    """Against a release build this must cost nothing at all -- not 'a little'."""

    def test_schema_zero_goes_dormant(self, hal, board, tmp_path):
        hal.read_diag_schema.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()
        assert r.enabled is False

    def test_dormant_recorder_issues_no_further_reads(self, hal, board, tmp_path):
        """The whole cost argument rests on this. If a dormant recorder still
        polled diagSeq every tick it would be a permanent Modbus tax on every
        production machine for a feature that is switched off."""
        hal.read_diag_schema.return_value = 0
        r = rec(hal, board, tmp_path)
        for _ in range(50):
            r.poll()
        assert hal.read_diag_schema.call_count == 1
        hal.read_diag_seq.assert_not_called()
        hal.read_diag_capture.assert_not_called()

    def test_unknown_schema_is_refused_not_guessed(self, hal, board, tmp_path):
        """Firmware carrying a probe written after this UI. Recording its numbers
        under a shape they do not have is worse than recording nothing."""
        hal.read_diag_schema.return_value = 99
        r = rec(hal, board, tmp_path)
        r.poll()
        assert r.enabled is False
        hal.read_diag_capture.assert_not_called()

    def test_disconnected_board_does_not_interrogate(self, hal, board, tmp_path):
        board.connected = False
        r = rec(hal, board, tmp_path)
        r.poll()
        hal.read_diag_schema.assert_not_called()


class TestCapture:
    def test_baselines_against_existing_seq(self, hal, board, tmp_path):
        """A capture the firmware completed before the UI connected is history,
        not news -- replaying it would date-stamp an old measurement as new."""
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE
        hal.read_diag_seq.return_value = 7
        r = rec(hal, board, tmp_path)
        r.poll()          # interrogate + baseline
        r.poll()          # seq still 7
        assert r.captures_written == 0
        hal.read_diag_capture.assert_not_called()

    def test_new_seq_writes_a_capture(self, hal, board, tmp_path):
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE
        hal.read_diag_seq.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()

        hal.read_diag_seq.return_value = 1
        hal.read_diag_capture.return_value = make_capture(seq=1)
        r.poll()

        assert r.captures_written == 1
        lines = (tmp_path / "takeup_settle.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        got = json.loads(lines[0])
        assert got["settle_ticks"] == 412
        assert got["net_counts"] == 29
        assert len(got["trace"]) == 50
        assert got["seq"] == 1

    def test_records_the_isr_interval_so_ticks_are_convertible(
            self, hal, board, tmp_path):
        """reflex-fw states three different ISR rates across four files. The
        capture therefore has to carry its own measured time base; without this
        a trace in 'ticks' cannot be honestly converted to seconds at all."""
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE
        hal.read_diag_seq.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()

        hal.read_diag_seq.return_value = 1
        hal.read_diag_capture.return_value = make_capture(seq=1)
        r.poll()

        got = json.loads((tmp_path / "takeup_settle.jsonl").read_text().strip())
        assert got["execution_interval_cycles"] == 1000
        assert got["bucket_ticks"] == 100

    def test_successive_captures_append(self, hal, board, tmp_path):
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE
        hal.read_diag_seq.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()

        for n in (1, 2, 3):
            hal.read_diag_seq.return_value = n
            hal.read_diag_capture.return_value = make_capture(seq=n)
            r.poll()

        lines = (tmp_path / "takeup_settle.jsonl").read_text().strip().split("\n")
        assert len(lines) == 3
        assert [json.loads(x)["seq"] for x in lines] == [1, 2, 3]

    def test_payload_schema_is_rechecked_not_trusted(self, hal, board, tmp_path):
        """The board can be reflashed under a live connection. The schema in the
        payload outranks the one learned at connect."""
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE
        hal.read_diag_seq.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()

        hal.read_diag_seq.return_value = 1
        hal.read_diag_capture.return_value = make_capture(seq=1, schema=42)
        r.poll()

        assert r.captures_written == 0
        assert r.enabled is False
        assert not (tmp_path / "takeup_settle.jsonl").exists()


class TestFailureHandling:
    def test_read_failure_never_propagates(self, hal, board, tmp_path):
        """This is bound to the board update tick. An exception escaping here
        would take the machine control surface down over a diagnostic."""
        hal.read_diag_schema.side_effect = RuntimeError("bus fault")
        r = rec(hal, board, tmp_path)
        r.poll()   # must not raise

    def test_gives_up_after_repeated_failures(self, hal, board, tmp_path):
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE
        hal.read_diag_seq.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()

        hal.read_diag_seq.side_effect = RuntimeError("bus fault")
        for _ in range(MAX_FAILURES):
            r.poll()

        assert r.enabled is False

    def test_recovers_from_a_transient_failure(self, hal, board, tmp_path):
        """One bad read must not permanently disable an otherwise healthy probe."""
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE
        hal.read_diag_seq.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()

        hal.read_diag_seq.side_effect = RuntimeError("blip")
        r.poll()
        assert r.enabled is True

        hal.read_diag_seq.side_effect = None
        hal.read_diag_seq.return_value = 1
        hal.read_diag_capture.return_value = make_capture(seq=1)
        r.poll()
        assert r.captures_written == 1


class TestBaseline:
    """What counts as 'already seen', which is where a capture got lost."""

    def test_reconnect_does_not_lose_a_capture_completed_during_the_gap(
            self, hal, board, tmp_path):
        """Regression, 2026-08-16. The board reconnected mid-test, the recorder
        re-interrogated and baselined against the FIRMWARE's seq, and capture 12
        went straight from the firmware to nowhere -- the file jumps 11 to 13."""
        (tmp_path / "takeup_settle.jsonl").write_text(
            json.dumps({"seq": 11, "schema": ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2}) + "\n")

        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2
        hal.read_diag_seq.return_value = 12          # completed while disconnected
        hal.read_diag_capture.return_value = make_capture(seq=12)

        r = rec(hal, board, tmp_path)
        r.poll()          # interrogate: must baseline from the FILE (11), not 12
        r.poll()          # so seq 12 is still new and gets recorded

        assert r.captures_written == 1
        seqs = [json.loads(l)["seq"]
                for l in (tmp_path / "takeup_settle.jsonl").read_text().splitlines() if l.strip()]
        assert seqs == [11, 12]

    def test_fresh_start_does_not_backdate_a_preexisting_capture(
            self, hal, board, tmp_path):
        """With no file, the block may hold a capture from before this UI ever
        ran. Recording it would stamp an old measurement with today's time."""
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2
        hal.read_diag_seq.return_value = 7
        r = rec(hal, board, tmp_path)
        r.poll()
        r.poll()
        assert r.captures_written == 0

    def test_a_torn_line_does_not_lose_the_whole_file(self, hal, board, tmp_path):
        """A half-written last line (power cut mid-append) must not make the
        recorder forget everything it had already recorded."""
        p = tmp_path / "takeup_settle.jsonl"
        p.write_text(
            json.dumps({"seq": 4, "schema": ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2}) + "\n"
            + '{"seq": 5, "sch')                      # torn
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2
        hal.read_diag_seq.return_value = 4
        r = rec(hal, board, tmp_path)
        r.poll()
        assert r.enabled is True
        assert r._baseline_seq == 4


class TestReset:
    def test_reset_reinterrogates_after_a_reflash(self, hal, board, tmp_path):
        """Dormant against release firmware, then the operator flashes a diag
        build and reconnects. Without re-interrogation the recorder would stay
        dormant for the whole measurement session."""
        hal.read_diag_schema.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()
        assert r.enabled is False

        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE
        hal.read_diag_seq.return_value = 0
        r.reset()
        r.poll()
        assert r.enabled is True
