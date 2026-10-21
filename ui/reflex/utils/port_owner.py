"""The single owner of the Modbus port -- AND THE HARDWARE SEAM.

WHICH LAYER IS THE ABSTRACTION. Every exchange the application makes goes
through here, by construction: BaseDevice item access, refresh, read_span
and the board tick all funnel into submit(), and
tests/dispatchers/test_board_wire_trace.py pins that call for call. The
invariants live here and nowhere else -- one owner of the instrument, FIFO
order, a batch that is never split, a per-write acknowledgement, and the
write epoch that keeps a stale snapshot from being applied over a newer
command.

fsms/els_stop_registers.py is NOT that layer: it is a domain facade over the
elsStop block. PortOwner is the hardware seam, and the other dispatchers
(servo, axis, input) write through it directly by design; their writes are
not moved behind typed per-device facades. The facade cannot carry an
invariant, so nothing should be built on the assumption that it does, and
transport does not go back into it.

WHAT IT OWNS. The ConnectionManager, and through it the minimalmodbus
Instrument (opening, closing, every exchange), the connection verdict
(`connected`) and the error-log backoff (`_log_error_once`). minimalmodbus is
not thread-safe, and every Modbus exchange the app makes used to run on the
Kivy main thread, so a slow or timed-out reply froze the UI for the length of
the timeout. Funnelling every exchange through one object is what allows them
to move to a thread of their own later without a second thread ever touching
the Instrument.

THE SHAPE, and why each part exists:

  * submit(command) -> Future. One command is a single read, a single write,
    a span read, or a Batch: an ordered list executed back to back, never
    split, with a per-item result. `call(command)` is submit-and-wait, the
    blocking proxy that BaseDevice's item access and refresh() go through.
  * run_cycle(): one iteration of the owner's loop on the calling thread.
    Drain the command queue in FIFO order, then run the board-tick scan (one
    request: Global.read_tick) and return its Snapshot. This is the synchronous
    seam: it is what a port thread would call in a loop, and what Board.update
    and the tests call directly.
  * pause() / resume(): the serial-port handover to the firmware flasher.

HOW IT RUNS. In the app, on its OWN THREAD: Board.start_io_thread() calls
start(), which switches the owner out of synchronous mode and runs run_cycle in
a loop on a daemon thread named "modbus-owner". submit() from the Kivy thread
then ENQUEUES, the owner thread drains, and call() blocks on the future -- the
blocking proxy. Snapshots go back through the publish callback, which is
Clock.schedule_once, the same marshalling @mainthread uses.

IN THE TESTS, ON THE CALLING THREAD, SYNCHRONOUSLY. A Board's owner is
built with `synchronous=True`, so submit() executes the command before it
returns and hands back a future that is already done; the queue stays empty
and run_cycle only scans. That keeps the exact order the code had when every
exchange was a direct call: a write issued between two ticks reaches the wire
between those two tick reads, and a live read returns the value from an
exchange made at the moment of the read. Deferring a command to the next
run_cycle is not an option on one thread: every caller waits for its result,
and with no other thread to drain the queue the wait would never end. The
recording in tests/dispatchers/test_board_wire_trace.py pins the resulting
traffic call for call.

`synchronous=False` is the queued mode: submit() from any thread other than
the one inside run_cycle enqueues, and run_cycle drains. start() turns it on;
the unit tests also drive it by calling run_cycle by hand, which is how its
ordering guarantees are checked without a thread.

WHY THE SEAM SURVIVES. Board.update() still exists and still runs a whole cycle
on the calling thread, so the system harness's pump() (Clock.tick() then
board.update()) stays deterministic and every test that calls board.update()
keeps working. The thread is opt-in, taken only by the running app.

PACING. While Board's Kivy Clock event drives run_cycle, the event is the
pacer -- its timeout carries the retry backoff, as it always has -- so Board
calls run_cycle(force_scan=True). The owner still records when its next scan
is due (`scan_due`) from the same constants, which is the cadence a loop that
paces itself follows: SCAN_PERIOD_S after a good tick, 1.0 s after a failed
tick read, 2.0 s when the port would not open.

PAUSING A RUNNING THREAD. pause() cannot close the port under the owner's
feet, so on a thread it ASKS: it sets a request, wakes the loop, and waits
(bounded) for the loop to finish whatever exchange is in flight, close the port
and acknowledge. It returns False if that acknowledgement does not arrive,
because a port that might still be open must not be handed to the flasher.
Synchronously there is no one to ask and it closes inline.

THE WRITE EPOCH increments every time a Write reaches the wire, and every
Snapshot carries the epoch its scan ran under. Board drops a snapshot whose
epoch is behind the owner's at apply, because such a snapshot was ACQUIRED
BEFORE a write that has since landed: it describes the machine as it was
before the UI changed it. Without this a scan already in flight when a cut's
arming burst went out could be applied after it and show the pre-arming
registers -- which the ELS stop detector reads as the stop having fired, and
the cut is abandoned on the tick it started. The generation below is the same
idea for the port handover; this is the idea for writes.

THE LINK GENERATION increments on pause(). Every Snapshot carries the
generation it was taken under, and Board drops one from an older generation at
apply, so a tick read that was already under way when the port was handed over
can never repopulate the dicts pause_polling() cleared. Synchronously that
cannot happen; it is checked anyway so the check exists before it is needed.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Optional


# The board tick's period, and the two retry intervals of its failure paths.
SCAN_PERIOD_S = 1.0 / 30
RETRY_AFTER_FAILED_READ_S = 1.0
RETRY_AFTER_NO_DEVICE_S = 2.0

# How long pause() waits for the owner thread to finish its exchange and close
# the port. One exchange is about 30 ms; a timed-out one is bounded by
# minimalmodbus's own read timeout, so this is several times the worst case.
PAUSE_ACK_TIMEOUT_S = 0.5
# How long stop() waits for the loop to leave. Same reasoning.
STOP_JOIN_TIMEOUT_S = 2.0
# How long the blocking proxy waits for the owner to answer. A wait with no
# bound is a UI that can hang for ever if the owner thread dies or wedges, and
# that is a worse failure than a refused write: the operator gets a frozen
# screen instead of an alarm. Generous against the real worst case -- one
# timed-out exchange plus the one in front of it.
CALL_TIMEOUT_S = 5.0


# ── commands ─────────────────────────────────────────────────────────────────
# Each executes against the ConnectionManager it is given, and only ever on the
# owner's side. Read and Write wrap the helpers in reflex/utils/communication.py
# unchanged: those helpers catch the exchange's exception, set `connected`,
# count read failures and log, exactly as when they were called directly.

@dataclass(frozen=True)
class Read:
    """One field read: `function(cm, address)`, a communication.read_* helper.
    The result is the value (0 on a failed exchange, as the helpers return)."""
    function: Callable
    address: int

    def execute(self, cm):
        return self.function(cm, self.address)


@dataclass(frozen=True)
class Write:
    """One field write: `function(cm, address, value, name)`, a
    communication.write_* helper. The result is the helper's acknowledgement:
    True when the controller answered, False when the exchange failed."""
    function: Callable
    address: int
    value: Any
    name: str = ""

    def execute(self, cm):
        return self.function(cm, self.address, self.value, self.name)


@dataclass(frozen=True)
class ReadSpan:
    """One FC3 request of `count` registers from `address`. The result is the
    register list; a failed exchange RAISES, as minimalmodbus does, and that
    exception is what the caller's wait re-raises."""
    address: int
    count: int

    def execute(self, cm):
        return cm.device.read_registers(
            registeraddress=self.address,
            number_of_registers=self.count,
        )


@dataclass(frozen=True)
class Batch:
    """Commands executed back to back with nothing in between, never split.
    The result is the list of per-item results. An item that raises ends the
    batch there, and the exception is the batch's: the items after it are not
    executed, which is what the same calls made in a row always did."""
    items: tuple

    def execute(self, cm):
        return [item.execute(cm) for item in self.items]


# ── the tick's result ────────────────────────────────────────────────────────

def _writes(command) -> bool:
    """True if this command puts anything on the wire that changes the
    controller: a Write, or a Batch holding one at any depth."""
    if isinstance(command, Write):
        return True
    if isinstance(command, Batch):
        return any(_writes(item) for item in command.items)
    return False


@dataclass(frozen=True)
class Snapshot:
    """One board-tick scan, applied on the main thread by Board._apply_snapshot.

    seq          increments per scan.
    generation   the link generation the scan ran under.
    write_epoch  the write epoch the scan ran under. Behind the owner's at
                 apply means a write landed after this scan was taken, so it
                 describes a machine the UI has already changed.
    t_acq        time.monotonic() just after the tick read returned, which is
                 when its last reply byte arrived; None when nothing was read.
    port_open    False when there was no port to read (it would not open).
    ok_fast      the tick read succeeded; fast_data is its fastData dict.
    ok_hot       the hot group decoded; els_stop is its dict. Always False
                 when ok_fast is, and els_stop is then {}.
    fast_data    None when ok_fast is False: the board keeps the last good one.
    retry_after  the backoff a failed scan asks for, None after a good one.
    """
    seq: int
    generation: int
    write_epoch: int
    t_acq: float | None
    port_open: bool
    ok_fast: bool
    ok_hot: bool
    fast_data: dict | None
    els_stop: dict
    retry_after: float | None


class PortOwner:
    def __init__(self, connection_manager, *, synchronous: bool = True):
        self._cm = connection_manager
        self._synchronous = synchronous
        self._queue: deque[tuple[Any, Future]] = deque()
        # The thread inside run_cycle, if any. A command submitted FROM it is
        # executed inline: the tick scan's own read_tick -> read_span is one,
        # and enqueueing it would wait on a queue only that thread drains.
        self._cycle_thread: int | None = None
        # The Global device whose read_tick() is the scan. Set by Board.
        self.scan_device = None
        self.generation = 0
        # Bumped by every Write that reaches the wire. See the module docstring.
        self.write_epoch = 0
        self.paused = False
        self._seq = 0
        self.next_scan_at = 0.0
        # ── the thread, when there is one ────────────────────────────────
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        # Set when a command is queued or a pause is asked for, so the loop
        # stops waiting for its next scan and attends to it at once.
        self._wake = threading.Event()
        self._pause_requested = threading.Event()
        self._pause_acked = threading.Event()
        self._publish: Optional[Callable[["Snapshot"], None]] = None
        # Counted, not logged per occurrence: a loop iteration that raised.
        self.loop_exceptions = 0

    @classmethod
    def of(cls, connection_manager) -> "PortOwner":
        """The one owner of `connection_manager`'s port, created on first use.

        Kept on the ConnectionManager so that every BaseDevice built on it
        (they hold the ConnectionManager as `dm`) and the Board's tick reach
        the SAME owner, and so the same queue. Test doubles standing in for a
        ConnectionManager get one the same way.
        """
        owner = getattr(connection_manager, "_port_owner", None)
        if not isinstance(owner, cls):
            owner = cls(connection_manager)
            connection_manager._port_owner = owner
        return owner

    @property
    def connection_manager(self):
        return self._cm

    # ── commands ─────────────────────────────────────────────────────────

    def submit(self, command) -> Future:
        future: Future = Future()
        if self._synchronous or threading.get_ident() == self._cycle_thread:
            self._execute(command, future)
        else:
            self._queue.append((command, future))
            # deque.append is atomic under the GIL, so no lock is needed to
            # hand the command over; the wake only shortens the loop's wait.
            self._wake.set()
        return future

    def call(self, command, timeout_s: float = CALL_TIMEOUT_S):
        """Submit and wait for the result: the blocking proxy. Re-raises the
        command's exception, if it raised, and raises TimeoutError if the owner
        does not answer within `timeout_s` (see CALL_TIMEOUT_S).

        The timeout can only fire when the owner thread is dead or wedged -- in
        synchronous mode the result is already there, and a healthy loop answers
        within an exchange. The watchdog in Board names that state; this stops
        the caller hanging on it.
        """
        return self.submit(command).result(timeout=timeout_s)

    def _execute(self, command, future: Future) -> None:
        if not future.set_running_or_notify_cancel():
            return
        # Decided BEFORE the attempt, so the bump below cannot be skipped by an
        # exception on a path that has already touched the wire.
        changes_the_controller = _writes(command)
        try:
            result = command.execute(self._cm)
        except Exception as e:
            future.set_exception(e)
        else:
            future.set_result(result)
        finally:
            # Bumped even when the command raised: a write that failed may
            # still have reached the controller, so a snapshot older than it
            # is no more trustworthy than one older than a write that worked.
            if changes_the_controller:
                self.write_epoch += 1

    # ── the loop body ────────────────────────────────────────────────────

    def scan_due(self, now: float | None = None) -> bool:
        return (time.monotonic() if now is None else now) >= self.next_scan_at

    def run_cycle(self, *, force_scan: bool = False) -> Snapshot | None:
        """Drain the queue in FIFO order, then scan if the scan is due (or
        `force_scan`). Returns the scan's Snapshot, or None when no scan ran.
        While paused, commands still execute (against a closed port they fail
        exactly as a direct call would) but nothing scans."""
        self._cycle_thread = threading.get_ident()
        try:
            # Asked for by another thread between two exchanges, never during
            # one: this is the only place the port closes while a loop runs.
            if self._pause_requested.is_set() and not self.paused:
                self._close_for_pause()
            while self._queue:
                command, future = self._queue.popleft()
                self._execute(command, future)
            if self.paused:
                return None
            if not (force_scan or self.scan_due()):
                return None
            return self._scan()
        finally:
            self._cycle_thread = None

    def _scan(self) -> Snapshot:
        """The board tick's exchange and its failure policy.

        No port: try to open one; if it still is not there, the scan fails
        with the 2.0 s backoff. A failed tick read logs, closes the port so the
        next scan re-opens it, and fails with the 1.0 s backoff; fastData is
        not replaced (the board keeps the last good one) and the elsStop
        snapshot is empty. A good read decodes the hot group (see _decode_hot)
        and only then declares the link up, so a decode error is logged before
        the "Communication established" line it would otherwise reset.
        """
        cm = self._cm
        started = time.monotonic()
        if cm.device is None:
            cm.connect()

        if cm.device is None:
            return self._publish_snapshot(started, t_acq=None, port_open=False,
                                 fast_data=None, els_stop={}, ok_hot=False,
                                 retry_after=RETRY_AFTER_NO_DEVICE_S)

        # ONE request for the whole tick: fastData and the elsStop hot group
        # are adjacent registers, read as one span (Global.read_tick). Its
        # failure takes the path a failed fastData read always took. A single
        # exchange cannot half-fail; the hot group's DECODE still can, and has
        # its own policy (_decode_hot).
        try:
            fast_data, hot_raw = self.scan_device.read_tick()
        except Exception as e:
            cm._log_error_once(str(e))
            cm.disconnect()
            return self._publish_snapshot(started, t_acq=None, port_open=True,
                                 fast_data=None, els_stop={}, ok_hot=False,
                                 retry_after=RETRY_AFTER_FAILED_READ_S)
        t_acq = time.monotonic()

        els_stop, ok_hot = self._decode_hot(hot_raw)
        cm.connected = True
        return self._publish_snapshot(started, t_acq=t_acq, port_open=True,
                             fast_data=fast_data, els_stop=els_stop,
                             ok_hot=ok_hot, retry_after=None)

    def _decode_hot(self, hot_raw) -> tuple[dict, bool]:
        """Decode this tick's elsStop hot group, for the pollers to share.

        The same arrangement `fast_data_values` has always had, extended to the
        block the ELS pollers read. It exists because `BaseDevice.__getitem__`
        is a LIVE per-field read, not a cache lookup: every
        ``device['elsStop']['active']`` in a tick-driven poller was its own
        Modbus exchange, and there are enough of those pollers that a board tick
        cost five to fourteen exchanges depending on which edges were due. At
        30 Hz, with the firmware's 100 kHz ISR saturated at cut-start, that is
        what lost comms on six of six cuts on 2026-08-23 -- every drop a
        timeout, none a corrupted frame. The snapshot rides the tick's one
        request (Global.read_tick) no matter how many pollers read it, and it
        costs MORE bytes than the reads it replaces. That trade is the point:
        bytes are not what times out, exchanges are.

        A SNAPSHOT THAT DID NOT HAPPEN MUST NOT LOOK LIKE ONE THAT DID, which
        is why failure yields an EMPTY dict rather than the last good one.
        Every reader goes through ElsStopRegisters's snapshot accessor, which treats
        an empty dict exactly as it treats a read attempted with no link --
        returning the fallback AND bumping ConnectionManager.read_failures --
        so `reads_fabricated_since()` still tells the acknowledgement pollers to
        discard the poll. Keeping the stale values would serve a plausible
        number with the counter unmoved, which is precisely the fabricated-zero
        hole that counter was added to close.

        Deliberately NOT a disconnect path. The tick read owns the connection
        verdict; a hot group that fails to decode is one bad tick for the ELS
        pollers, and tearing the link down over it would take the DRO with it
        for no reason.

        HOT GROUP ONLY, and that is the point: it is what lets the tick stay
        one request. Every field any tick-driven reader touches is in this
        group by construction -- tools/audit_tick_readers.py enumerates the
        readers of Board.els_stop_values and FAILS if a `cold` field appears
        in one, so a future reader reaching for a cold field breaks the audit
        rather than silently adding a request per tick.
        """
        try:
            return self.scan_device['elsStop'].decode_hot(hot_raw), True
        except Exception as e:
            self._cm._log_error_once(str(e))
            return {}, False

    def _publish_snapshot(self, started, *, t_acq, port_open, fast_data,
                          els_stop, ok_hot, retry_after) -> Snapshot:
        self._seq += 1
        self.next_scan_at = started + (retry_after or SCAN_PERIOD_S)
        return Snapshot(
            seq=self._seq,
            generation=self.generation,
            write_epoch=self.write_epoch,
            t_acq=t_acq,
            port_open=port_open,
            ok_fast=fast_data is not None,
            ok_hot=ok_hot,
            fast_data=fast_data,
            els_stop=els_stop,
            retry_after=retry_after,
        )

    # ── the flasher handover ─────────────────────────────────────────────

    def pause(self, timeout_s: float = PAUSE_ACK_TIMEOUT_S) -> bool:
        """Stop scanning and close the port. Idempotent. True when the port is
        known to be closed.

        WITHOUT A THREAD the caller IS the cycle, so no exchange can be in
        flight and the close happens inline, as it always did.

        WITH A THREAD the close must happen between two exchanges, so this asks
        the loop to do it and waits for the acknowledgement. It returns False if
        the loop does not acknowledge in time -- the port may still be open, and
        the caller must not hand it to the firmware flasher on that answer.
        """
        if self.paused:
            return True
        if not self.running:
            self._close_for_pause()
            return True
        self._pause_acked.clear()
        self._pause_requested.set()
        self._wake.set()
        return self._pause_acked.wait(timeout_s)

    def resume(self) -> None:
        """Re-open the port and allow scanning again. Idempotent."""
        if not self.paused:
            return
        self._pause_requested.clear()
        self._pause_acked.clear()
        self.paused = False
        self._cm.connect()
        self.next_scan_at = 0.0
        self._wake.set()

    def _close_for_pause(self) -> None:
        """Do the pause, on whichever thread owns the cycle. See pause()."""
        self.paused = True
        self.generation += 1
        self._cm.disconnect()
        self._pause_acked.set()

    # ── the thread ───────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, publish: Callable[["Snapshot"], None]) -> None:
        """Run the loop on a daemon thread; `publish` receives each Snapshot.

        `publish` IS CALLED ON THE OWNER THREAD and must marshal to the main
        thread itself -- Board hands in a Clock.schedule_once. Starting also
        leaves synchronous mode, so from now on a submit() from the Kivy thread
        is queued for this loop rather than executed in place.
        """
        if self.running:
            return
        self._publish = publish
        self._synchronous = False
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._loop, name="modbus-owner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the loop to leave and wait briefly for it. Idempotent.

        Daemon threads do not keep the process alive, so this is for an orderly
        shutdown and for tests, not for correctness at exit.
        """
        self._stopping.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(STOP_JOIN_TIMEOUT_S)
        self._synchronous = True
        self._publish = None

    def _loop(self) -> None:
        """One cycle, then wait until the next scan is due or something wakes us.

        NOTHING IS ALLOWED OUT OF THIS LOOP. An exception here would end the
        thread and the link would go quiet with the UI still believing in it, so
        every iteration is wrapped: the error is reported, a link-fault snapshot
        is published so the pollers see the link down exactly as they do for a
        failed read, and the loop carries on at the failed-read backoff.
        """
        while not self._stopping.is_set():
            try:
                snapshot = self.run_cycle()
                if snapshot is not None:
                    self._emit(snapshot)
            except Exception as e:                       # noqa: BLE001
                self.loop_exceptions += 1
                self._report_loop_error(e)
                try:
                    self._emit(self._fault_snapshot())
                except Exception:                        # noqa: BLE001
                    pass
                self.next_scan_at = time.monotonic() + RETRY_AFTER_FAILED_READ_S
            self._wake.wait(max(0.0, self.next_scan_at - time.monotonic()))
            self._wake.clear()

    def _emit(self, snapshot) -> None:
        publish = self._publish
        if publish is not None:
            publish(snapshot)

    def _fault_snapshot(self) -> "Snapshot":
        """What the pollers see when the loop itself failed: the same shape a
        failed tick read produces, so no reader needs a new case."""
        return self._publish_snapshot(
            time.monotonic(), t_acq=None, port_open=False, fast_data=None,
            els_stop={}, ok_hot=False, retry_after=RETRY_AFTER_FAILED_READ_S)

    def _report_loop_error(self, exc: BaseException) -> None:
        """Log once per distinct message, and tell Sentry if it is configured.

        Deliberately quiet on repeats: a link that is down fails every cycle,
        and 30 identical tracebacks a second would bury the first one.
        """
        try:
            self._cm._log_error_once(f"port owner loop: {exc}")
        except Exception:                                # noqa: BLE001
            pass
        try:
            import sentry_sdk
        except Exception:                                # noqa: BLE001
            return
        try:
            sentry_sdk.capture_exception(exc)
        except Exception:                                # noqa: BLE001
            pass
