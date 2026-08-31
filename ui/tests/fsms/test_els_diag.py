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


def make_capture(seq=1, schema=ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2, end_reason=1):
    return {
        "schema": schema,
        "seq": seq,
        "capture_ticks": 59,
        "end_reason": end_reason,
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
    h = MagicMock()
    # ONE REGISTER, TWO ACCESS PATHS, deliberately. The steady-state tick reads
    # diagSeq from the board's once-per-tick elsStop snapshot
    # (`hal.tick.diag_seq`); `_choose_baseline` still reads it live
    # (`hal.read_diag_seq`), because it runs once per connection before any
    # snapshot is guaranteed to exist. See ElsDiagRecorder.poll.
    #
    # Tying them together here means `hal.read_diag_seq.return_value = N` keeps
    # meaning "the firmware says N" whichever path asks, so every test below
    # still says what it meant. Leaving `tick.diag_seq` unstubbed would be
    # actively misleading: a bare MagicMock returns a fresh object every call,
    # which never equals the baseline -- i.e. a permanent phantom capture edge.
    h.tick.diag_seq.side_effect = lambda: h.read_diag_seq()
    return h


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
        # Both paths, because the tick now reads through the snapshot: a
        # dormant recorder that had merely moved its polling to `tick.diag_seq`
        # would still be paying for the snapshot's existence and would slip
        # past a check that only watched the live reader.
        hal.tick.diag_seq.assert_not_called()
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

    def test_power_cycle_resets_the_counter_without_inventing_a_capture(
            self, hal, board, tmp_path):
        """Regression, 2026-08-16, introduced by the fix above. This board needs
        a power cycle after every flash, which zeroes diagSeq -- so the file held
        seq 13 while the firmware said 0. Baselining from the file then made 0
        look new and recorded the empty block, twice."""
        (tmp_path / "takeup_settle.jsonl").write_text(
            json.dumps({"seq": 13, "schema": ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2}) + "\n")

        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2
        hal.read_diag_seq.return_value = 0            # firmware rebooted
        r = rec(hal, board, tmp_path)
        r.poll()
        r.poll()

        assert r.captures_written == 0
        hal.read_diag_capture.assert_not_called()

    def test_after_a_reset_the_next_real_capture_is_still_recorded(
            self, hal, board, tmp_path):
        """Re-baselining must not make the recorder deaf for the rest of the
        session -- the whole point is to keep recording after a power cycle."""
        (tmp_path / "takeup_settle.jsonl").write_text(
            json.dumps({"seq": 13, "schema": ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2}) + "\n")

        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2
        hal.read_diag_seq.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()                                       # re-baseline at 0

        hal.read_diag_seq.return_value = 1
        hal.read_diag_capture.return_value = make_capture(seq=1)
        r.poll()
        assert r.captures_written == 1

    def test_an_incomplete_block_is_not_recorded_as_a_measurement(
            self, hal, board, tmp_path):
        """end_reason == 0 means no capture finished. A row of zeros written from
        an empty block is indistinguishable from a real measurement of a carriage
        that never moved -- which is a thing that genuinely happens with the
        half-nut open, so it must not be faked."""
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2
        hal.read_diag_seq.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()

        hal.read_diag_seq.return_value = 1
        hal.read_diag_capture.return_value = make_capture(seq=1, end_reason=0)
        r.poll()

        assert r.captures_written == 0
        assert not (tmp_path / "takeup_settle.jsonl").exists()

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


class TestModeWatchSchema:
    """Schemas 4 and 5: diagSeq counts MODE TRANSITIONS, and end_reason means
    'a latch suppression happened' rather than 'a capture completed'. The
    membership rules below are what keep those redefinitions from silently
    dropping or inventing records."""

    def _active(self, hal, board, tmp_path, seq=0):
        from reflex.utils.devices import ELS_DIAG_SCHEMA_MODE_WATCH
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_MODE_WATCH
        hal.read_diag_seq.return_value = seq
        r = rec(hal, board, tmp_path)
        r.poll()
        return r

    def test_schema_4_is_recognised(self, hal, board, tmp_path):
        """Retired firmware-side, still accepted here: the lathe runs a
        schema-4 build until its next flash + power cycle, and dropping it
        would silently lose that machine's transition log."""
        from reflex.utils.devices import ELS_DIAG_SCHEMA_MODE_WATCH
        r = self._active(hal, board, tmp_path)
        assert r.enabled is True
        assert r.schema == ELS_DIAG_SCHEMA_MODE_WATCH

    def test_schema_5_is_recognised_and_records_transitions(self, hal, board,
                                                            tmp_path):
        """mode-watch-v2 (effective-only counting) must be accepted end to
        end — recognised at interrogation AND past the end_reason membership
        rule, which applies to it identically (0 = healthy steady state)."""
        from reflex.utils.devices import ELS_DIAG_SCHEMA_MODE_WATCH_V2
        hal.read_diag_schema.return_value = ELS_DIAG_SCHEMA_MODE_WATCH_V2
        hal.read_diag_seq.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()
        assert r.enabled is True
        assert r.schema == ELS_DIAG_SCHEMA_MODE_WATCH_V2
        hal.read_diag_seq.return_value = 1
        hal.read_diag_capture.return_value = make_capture(
            seq=1, schema=ELS_DIAG_SCHEMA_MODE_WATCH_V2, end_reason=0)
        r.poll()
        assert r.captures_written == 1

    def test_schema_property_is_none_while_dormant(self, hal, board, tmp_path):
        hal.read_diag_schema.return_value = 0
        r = rec(hal, board, tmp_path)
        r.poll()
        assert r.schema is None

    def test_transition_records_despite_end_reason_zero(self, hal, board, tmp_path):
        """THE membership rule. For schema 4 end_reason == 0 is the healthy
        steady state (zero suppressions). Were schema 4 ever added to
        SCHEMAS_WITH_END_REASON, every mode transition on a healthy machine
        would be silently dropped — this is the test that makes that edit
        cost a red."""
        from reflex.utils.devices import ELS_DIAG_SCHEMA_MODE_WATCH
        r = self._active(hal, board, tmp_path, seq=0)
        hal.read_diag_seq.return_value = 1
        hal.read_diag_capture.return_value = make_capture(
            seq=1, schema=ELS_DIAG_SCHEMA_MODE_WATCH, end_reason=0)
        r.poll()
        assert r.captures_written == 1

    def test_suppression_count_rides_along(self, hal, board, tmp_path):
        from reflex.utils.devices import ELS_DIAG_SCHEMA_MODE_WATCH
        r = self._active(hal, board, tmp_path, seq=0)
        hal.read_diag_seq.return_value = 1
        capture = make_capture(seq=1, schema=ELS_DIAG_SCHEMA_MODE_WATCH,
                               end_reason=1)
        capture["net_counts"] = 2          # two suppressions: the finding
        hal.read_diag_capture.return_value = capture
        r.poll()
        assert r.captures_written == 1
        recorded = json.loads(
            (tmp_path / "takeup_settle.jsonl").read_text().splitlines()[-1])
        assert recorded["net_counts"] == 2
