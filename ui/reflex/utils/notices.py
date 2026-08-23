"""Transient operator notices: what gets said, which one wins, and when it stops.

THE GAP THIS FILLS. Until now the app had two ways to tell the operator that
something happened, and both are wrong for a passing event. It could write a log
line -- but at the lathe there is a touchscreen and a bench, not a terminal, so a
log line is a message to whoever reads the file next week. Or it could grow a
feature-specific widget, which is how the take-up refusal and the re-reference
confirm ended up as hand-placed strips in the advanced ELS bar. Neither is
available to "the operator pressed a button and nothing happened", which is the
single most common thing worth saying and the thing that has repeatedly reached
Evan as "it just doesn't do anything".

WHY THIS IS A PLAIN PYTHON OBJECT. It owns a queue, a ranking and a clock, and
none of that needs a Window, a GL context or a Kivy property to be correct. Kept
free of those, the whole policy is testable in a few microseconds against a fake
clock, which is the only way the expiry rules get tested at all -- the
alternative is a test suite that sleeps. The Kivy-facing half (properties the kv
binds to, and the periodic sweep that retires an expired notice) lives in
ElsUiController, which is where every other republished-into-kv value lives.

THE ONE RULE THIS SURFACE INHERITS. From els_advbar.kv, 2026-08-22, Evan:
"having things resize around a temporary warning is distracting." A notice may
COVER, it may never MOVE or RESIZE anything. Nothing in this module can enforce
that -- it is a property of the kv that renders it (statusbar.kv) -- but it is
the reason this module deals in "one message at a time" rather than a stack: a
list that grows is a layout that grows.

DESIGN NOTES worth knowing before changing anything here:

  * A NOTICE'S CLOCK STARTS WHEN IT IS SHOWN, not when it is posted. That is
    what makes "nothing is silently dropped" true: a notice waiting its turn
    cannot time out while it is still invisible, so every notice that enters
    this object either reaches the screen for its full duration or is dropped
    with a log line saying so.
  * DURATION IS CLAMPED, deliberately narrowly. This surface is for seconds.
    Persistent machine state -- a live phase offset, an armed stop -- belongs in
    a strip of its own with a placement argued on its own merits (see the two
    overlays in els_advbar.kv). Clamping at MAX_SECONDS means nobody can quietly
    turn this into a permanent banner by passing a big number.
  * SEVERITY IS A CLOSED SET, and an unknown one is coerced UP rather than
    rejected. A typo'd severity is a programming error, but raising it into
    Kivy's update loop would take the app down at the machine; showing the
    message at the higher severity is the safe side of the error, and the log
    line names the caller's mistake.
"""
import threading
import time
from dataclasses import dataclass

from kivy.logger import Logger

log = Logger.getChild(__name__)

# ── Severities ───────────────────────────────────────────────────────────────
# Two, on purpose. A third ("error"?) is only worth adding when there is a
# rendering that distinguishes it AND a caller that means it; until then it
# would just be a third amber strip and an extra way to pick the wrong one.
NOTICE_INFO = "info"
NOTICE_WARNING = "warning"


@dataclass(frozen=True)
class _Severity:
    """rank orders pre-emption; seconds is the default time on screen."""
    rank: int
    seconds: float


SEVERITIES = {
    # 4 s is about two readings of a short line at arm's length from a lathe.
    NOTICE_INFO:    _Severity(rank=0, seconds=4.0),
    # Longer, because a warning normally asks the operator to go DO something
    # ("turn Sync Enable off first") and they will look away from the screen to
    # do it. Long enough to still be there when they look back.
    NOTICE_WARNING: _Severity(rank=1, seconds=7.0),
}

# An unrecognised severity becomes this one. Coerce UP, never down: over-warning
# is noise, under-warning is a message the operator learns to ignore.
FALLBACK_SEVERITY = NOTICE_WARNING

# A notice shorter than this cannot be read; one longer than this is not
# transient any more and wants a placement argument of its own.
MIN_SECONDS = 1.5
MAX_SECONDS = 30.0

# How many notices may WAIT behind the one on screen. Small on purpose: the
# worst case an operator can face is this many times MAX(seconds) of backlog
# after the event that caused it, and a parade of stale messages is its own
# failure mode. Overflow is logged, never silent.
MAX_QUEUED = 3


@dataclass
class Notice:
    """One thing to say, plus the bookkeeping that decides when it is said.

    `deadline` is None until the notice is actually on screen -- see the module
    docstring on why the clock starts at show time rather than post time.
    """
    message: str
    severity: str
    rank: int
    seconds: float
    seq: int
    deadline: float | None = None

    def duplicates(self, other: "Notice") -> bool:
        """Same words, same severity -- i.e. the same thing to say.

        Identity for coalescing is the MESSAGE, not the caller, because the
        misuse this defends against is a poller that posts on every tick while a
        condition lasts. Those are all the same sentence, and they should be one
        strip that stays up, not a hundred queued copies.
        """
        return self.message == other.message and self.severity == other.severity


class NoticeCenter:
    """Holds at most one notice on screen and a short queue behind it.

    COLLISION POLICY, in one sentence: a strictly more urgent notice pre-empts
    whatever is showing and the pre-empted one goes back in the queue to be
    shown again; anything else waits its turn, oldest first within a severity.

    Why not the two simpler answers:

      * LAST WINS is the obvious one and it is wrong here, because the notices
        that collide are exactly the ones that matter -- a refusal firing while
        an earlier message is still up. Whichever one is discarded, the operator
        acted on incomplete information.
      * PLAIN FIFO is wrong in the other direction: it makes a warning wait
        behind an informational line. At a machine the urgent thing has to be
        the thing on screen NOW; being second in a queue is indistinguishable
        from being lost.

    So: priority for WHICH, queue for the rest, and the only thing ever
    discarded is a duplicate (deliberately coalesced) or a queue overflow (which
    logs). `time_fn` is injectable so the expiry rules can be tested against a
    fake clock instead of a sleep.

    Thread-safe because it isn't only touched from the main thread: the board
    pollers run on the ConnectionManager thread and are exactly the code most
    likely to want to say something.
    """

    def __init__(self, time_fn=time.monotonic):
        self._now = time_fn
        self._lock = threading.RLock()
        self._showing: Notice | None = None
        self._queue: list[Notice] = []
        self._seq = 0

    # ── inspection ───────────────────────────────────────────────────────────
    @property
    def showing(self) -> Notice | None:
        """The notice that should be on screen right now, or None.

        Does NOT expire anything -- reading is not a tick. Expiry happens in
        poll(), so that the only thing that can change what is on screen is a
        post or a sweep, and a stray read from a kv binding cannot.
        """
        with self._lock:
            return self._showing

    @property
    def queued(self) -> tuple[Notice, ...]:
        """Notices waiting their turn, in no particular order (see _next)."""
        with self._lock:
            return tuple(self._queue)

    # ── the API everything else uses ─────────────────────────────────────────
    def post(self, message: str, severity: str = NOTICE_INFO, *,
             seconds: float | None = None) -> Notice | None:
        """Say something to the operator. Returns the Notice, or None if there
        was nothing to say.

        `seconds` is keyword-only, and that is not decoration: the plausible
        typo here is ``post("...", 5)``, which with a positional third parameter
        would set the SEVERITY to 5 -- silently coerced to a warning by the
        fallback and never noticed. Keyword-only makes that call a TypeError at
        the call site instead.
        """
        text = (message or "").strip()
        if not text:
            # A blank notice is a coloured strip that covers the telemetry
            # gutter for several seconds and says nothing. Refuse it, loudly:
            # it means a caller built a message out of something empty.
            log.warning("notice refused: empty message (severity=%r)", severity)
            return None

        spec = SEVERITIES.get(severity)
        if spec is None:
            log.error("notice severity %r is not one of %s -- showing it as %r",
                      severity, sorted(SEVERITIES), FALLBACK_SEVERITY)
            severity = FALLBACK_SEVERITY
            spec = SEVERITIES[severity]

        if seconds is None:
            seconds = spec.seconds
        else:
            clamped = max(MIN_SECONDS, min(float(seconds), MAX_SECONDS))
            if clamped != seconds:
                log.warning("notice duration %.1fs clamped to %.1fs: %r",
                            float(seconds), clamped, text)
            seconds = clamped

        with self._lock:
            now = self._now()
            # Retire first, so an already-expired notice cannot win the
            # pre-emption comparison below and hold off a live one.
            self._retire_expired(now)

            self._seq += 1
            candidate = Notice(message=text, severity=severity, rank=spec.rank,
                               seconds=seconds, seq=self._seq)

            # 1. Coalesce. A repeat of what is already on screen refreshes its
            #    time rather than queueing behind itself; a repeat of something
            #    already waiting is simply the same message and is dropped.
            #    Neither is a loss, so neither is logged as one.
            if self._showing is not None and self._showing.duplicates(candidate):
                self._showing.deadline = now + self._showing.seconds
                return self._showing
            for waiting in self._queue:
                if waiting.duplicates(candidate):
                    return waiting

            # 2. Nothing on screen: say it now.
            if self._showing is None:
                self._show(candidate, now)
                return candidate

            # 3. Strictly more urgent: take the screen, and put what was there
            #    back in the queue. Its clock does not keep running while it
            #    waits -- _show re-arms the deadline from scratch when it
            #    resumes -- so an interruption costs it nothing. Ties do not
            #    pre-empt: a warning does not interrupt a warning, or a chatty
            #    poller would strobe the strip and nothing would be readable.
            if candidate.rank > self._showing.rank:
                interrupted = self._showing
                self._queue.append(interrupted)
                self._show(candidate, now)
                self._trim()
                return candidate

            # 4. Otherwise it waits.
            self._queue.append(candidate)
            self._trim()
            return candidate

    def poll(self) -> bool:
        """Advance the clock. Returns True iff what should be on screen changed.

        Called from a periodic sweep rather than a one-shot timer per notice, so
        that the number of scheduled callbacks does not depend on how chatty the
        app is, and so a pre-empted notice's revised deadline needs no timer
        surgery.
        """
        with self._lock:
            before = self._showing
            self._retire_expired(self._now())
            return self._showing is not before

    def clear(self) -> bool:
        """Take everything down at once. Returns True iff anything was showing.

        Exists for the app teardown / mode-change case where the notices refer
        to a context that no longer exists. Not a dismiss button: the operator
        has no way to dismiss a notice, on purpose -- a touchscreen at a lathe
        collects accidental taps, and a notice that a stray tap can delete is a
        notice that may never have been read.
        """
        with self._lock:
            was_showing = self._showing is not None
            self._showing = None
            self._queue.clear()
            return was_showing

    # ── internals ────────────────────────────────────────────────────────────
    def _show(self, notice: Notice, now: float):
        """Put a notice on screen and START ITS CLOCK.

        THE DEADLINE IS SET HERE AND NOWHERE ELSE, and that single fact is what
        makes "nothing is silently dropped" true. Set at post time instead, a
        notice waiting behind another would burn its whole duration invisibly
        and be retired the moment it reached the front -- present in the queue,
        never on the screen, and impossible to tell apart from a message that
        was simply never sent. Unconditional, so a notice that was pre-empted
        gets its full time when it resumes rather than the remainder.
        """
        notice.deadline = now + notice.seconds
        self._showing = notice

    def _retire_expired(self, now: float):
        if self._showing is not None and now >= self._showing.deadline:
            self._showing = None
        if self._showing is None and self._queue:
            self._show(self._pop_next(), now)

    def _pop_next(self) -> Notice:
        """Most urgent first; oldest first within a severity.

        Ordering by seq (not by list position) is what makes a pre-empted notice
        come back BEFORE anything posted after it was interrupted -- it keeps
        its original sequence number, so the operator sees the messages in the
        order the machine produced them.
        """
        nxt = min(self._queue, key=lambda n: (-n.rank, n.seq))
        self._queue.remove(nxt)
        return nxt

    def _trim(self):
        """Enforce MAX_QUEUED by dropping the LEAST urgent, OLDEST waiter.

        Least urgent because an overflowing queue means more is happening than
        can be narrated and severity is the whole point of having severity;
        oldest within that because it is the one most likely to describe a
        situation that has already moved on by the time it could be shown.
        Never silent -- the dropped text goes to the log, which is where it
        would have gone before this module existed anyway.

        NOTE THE APPARENT INCONSISTENCY WITH _pop_next, which shows the OLDEST
        first. They are answering different questions and both answers are the
        same principle: display order preserves the order the machine produced
        the events (causality is what makes a sequence readable), while eviction
        discards whatever will be most stale by the time it reaches the screen.
        Evicting the newest instead would mean a burst leaves the operator
        reading the beginning of a story whose ending was thrown away.
        """
        while len(self._queue) > MAX_QUEUED:
            worst = min(self._queue, key=lambda n: (n.rank, n.seq))
            self._queue.remove(worst)
            log.warning("notice dropped (queue full, %d waiting): %r",
                        MAX_QUEUED, worst.message)
