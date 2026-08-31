"""The read-failure counter, tested against PRODUCTION code.

Why this file exists as well as the poller tests: those use a fake connection
manager, so they prove the POLLER reacts correctly to a failure signal but say
nothing about whether the real code ever raises that signal. Mutation-testing
found exactly that hole -- deleting `dm.read_failures += 1` from
communication.py, and making `reads_failed_since` return False unconditionally,
both left the poller tests entirely green.

WHAT THE COUNTER IS FOR. BaseDevice.__getitem__ performs a LIVE Modbus read per
field, and these helpers return 0 when that read raises. Zero is not a neutral
failure value in this register map -- it reads as "not enabled", "no offset",
"sequence reset". On elspi 2026-08-21 a CRC-failed 148-byte frame of zeros made
takeupSeq read 2 -> 0, which the outcome poller took for a sequence edge and
reported as a phantom "CONFIRMED: moved 0 counts, needed 0", clearing a live
take-up refusal. The counter is what lets a caller tell a real zero from a
fabricated one.
"""
from types import SimpleNamespace

import pytest

from reflex.utils.communication import (
    ConnectionManager, read_float, read_long, read_unsigned,
)


class _Boom(Exception):
    """Stands in for minimalmodbus's checksum / no-response / short-frame
    errors, all of which surface as exceptions from the read call."""


def _dm(*, raises: bool):
    """Minimal stand-in for a ConnectionManager as the read helpers use it."""
    def _fail(*_a, **_k):
        raise _Boom("Checksum error in rtu mode")

    device = SimpleNamespace(
        read_float=_fail if raises else (lambda *a, **k: 1.5),
        read_long=_fail if raises else (lambda *a, **k: 7),
        read_register=_fail if raises else (lambda *a, **k: 42),
    )
    return SimpleNamespace(
        device=device,
        connected=True,
        read_failures=0,
        _log_error_once=lambda _m: None,
    )


READERS = [
    pytest.param(read_float, 1.5, id="read_float"),
    pytest.param(read_long, 7, id="read_long"),
    pytest.param(read_unsigned, 42, id="read_unsigned"),
]


@pytest.mark.parametrize("reader,good", READERS)
def test_a_successful_read_does_not_touch_the_counter(reader, good):
    """The counter must not drift on healthy traffic, or every poll would
    discard itself and the machine would silently stop reporting outcomes --
    the failure mode of a too-eager guard."""
    dm = _dm(raises=False)
    assert reader(dm, 0) == good
    assert dm.read_failures == 0


@pytest.mark.parametrize("reader,_good", READERS)
def test_a_failed_read_still_returns_zero_but_counts_itself(reader, _good):
    """The zero return is deliberately preserved -- hundreds of call sites are
    written against it and do not care. What changes is that the failure is now
    COUNTABLE, so the handful of readers where a wrong value is a safety matter
    can tell."""
    dm = _dm(raises=True)
    assert reader(dm, 0) == 0
    assert dm.read_failures == 1
    assert dm.connected is False


@pytest.mark.parametrize("reader,_good", READERS)
def test_the_counter_accumulates_across_reads(reader, _good):
    """A poll makes several reads; one increment per failure is what lets a
    caller span the whole group with a single before/after comparison."""
    dm = _dm(raises=True)
    for _ in range(3):
        reader(dm, 0)
    assert dm.read_failures == 3


# ── the comparator itself ────────────────────────────────────────────────────

def test_reads_failed_since_is_false_when_nothing_failed():
    cm = SimpleNamespace(read_failures=5)
    assert ConnectionManager.reads_failed_since(cm, 5) is False


def test_reads_failed_since_is_true_after_a_failure():
    cm = SimpleNamespace(read_failures=6)
    assert ConnectionManager.reads_failed_since(cm, 5) is True


def test_reads_failed_since_notices_more_than_one_failure():
    """Compares against a baseline rather than testing a flag, so a burst is
    detected as readily as a single failure and nothing has to be reset."""
    cm = SimpleNamespace(read_failures=9)
    assert ConnectionManager.reads_failed_since(cm, 5) is True
