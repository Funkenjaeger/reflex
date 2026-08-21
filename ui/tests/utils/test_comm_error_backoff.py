"""_log_error_once: progressive backoff on a persisting identical error.

The flat 10-second repeat turned the 42-minute post-flash limbo of
2026-08-17 into 244 identical checksum-error lines (each one also a Sentry
envelope). The contract now: first occurrence logs immediately; while the
SAME message persists the repeat interval doubles (10 s -> 20 -> ... -> 10 min
cap) and each repeat line carries the count it suppressed; any different
message (or the connected setter) resets the backoff so a NEW problem is
never delayed.
"""
import reflex.utils.communication as comm


class FakeLog:
    def __init__(self):
        self.lines = []

    def error(self, msg):
        self.lines.append(msg)


def make_dm():
    dm = object.__new__(comm.ConnectionManager)
    dm._last_error_message = None
    dm._last_error_time = 0.0
    dm._error_repeat_interval = comm._ERROR_REPEAT_INTERVAL_S
    dm._error_suppressed = 0
    return dm


def drive(dm, monkeypatch, events):
    """events: iterable of (monotonic_time, message)."""
    for t, msg in events:
        monkeypatch.setattr(comm.time, "monotonic", lambda t=t: t)
        dm._log_error_once(msg)


def test_42_minutes_of_the_same_error_logs_10_lines_not_244(monkeypatch):
    fake = FakeLog()
    monkeypatch.setattr(comm, "log", fake)
    dm = make_dm()
    # One failed transaction per second for 42 minutes, all identical.
    drive(dm, monkeypatch, ((float(t), "Checksum error") for t in range(2520)))
    # Backoff logs at t=0,10,30,70,150,310,630,1230,1830,2430.
    assert len(fake.lines) == 10
    assert fake.lines[0] == "Checksum error"
    assert all("suppressed" in l for l in fake.lines[1:])


def test_suppressed_counts_account_for_every_swallowed_repeat(monkeypatch):
    fake = FakeLog()
    monkeypatch.setattr(comm, "log", fake)
    dm = make_dm()
    n = 2520
    drive(dm, monkeypatch, ((float(t), "Checksum error") for t in range(n)))
    import re
    suppressed = sum(int(m.group(1)) for l in fake.lines[1:]
                     for m in [re.search(r"(\d+) identical repeats", l)] if m)
    # every event is either a logged line or counted as suppressed by a
    # later line -- except the tail still accumulating when we stopped
    assert len(fake.lines) + suppressed + dm._error_suppressed == n


def test_a_different_message_logs_immediately_and_resets_the_backoff(monkeypatch):
    fake = FakeLog()
    monkeypatch.setattr(comm, "log", fake)
    dm = make_dm()
    drive(dm, monkeypatch, [(0.0, "err A"), (5.0, "err A"), (6.0, "err B")])
    # A logged at t=0; A@5 suppressed (interval 10); B logs at once.
    assert fake.lines == ["err A", "err B"]
    assert dm._error_repeat_interval == comm._ERROR_REPEAT_INTERVAL_S
