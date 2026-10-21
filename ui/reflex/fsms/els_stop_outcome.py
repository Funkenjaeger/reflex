"""Where each ELS stop landed, measured on the host from the per-tick snapshots.

ONE OBSERVER, THREE READERS. Every stop the firmware fires is measured here
once, and the result feeds:

- the last-stop readout ("Stop landed 2 counts (0.010 mm) short.");
- the outcome watch: a CORRECTED stop that lands past its target by more than
  PAST_TOLERANCE_COUNTS turns the correction off (bias short: past is scrap);
- the guided overshoot measurement (els_overshoot_survey.py), which runs with
  the correction off and records each stop's overshoot against its rate.

WHAT IS MEASURED, from the trigger snapshot the firmware latches in the ISR
(protocolVersion 9+) and the Z scale position once the carriage has settled:

    past      = (settled Z - stopPosition) x stopDirection   counts, + = past
    overshoot = (settled Z - stopTriggerZ) x stopDirection   counts

The firmware fires at stopPosition - stopDirection x offset, so stopDirection
is the direction of travel into the stop (fw elsStop; see
tests/system/test_els_stop_offset.py for the same sign).

WHEN. On a stopTriggerSeq edge, seen on two consecutive polls so a torn
snapshot (the Modbus task copies the block one register at a time) is never
acted on. Then the carriage is SETTLED once Z has read the same for
SETTLE_POLLS consecutive polls; the coast lasts ~12 ms, so on a 30 Hz poll this
is ~100 ms after the trigger. A stop that has not settled within SETTLE_TIMEOUT_S
(the operator retracted at once, or the link stalled) gives no outcome at all.

THE RATE is the stream rate (els_overshoot.ZRateWindow) as it stood on the poll
BEFORE the trigger was first seen, so it is the approach and not the coast: the
same quantity the overshoot table is keyed on.

THE PREMISE. stopTriggerStepsToGo is 0 on every stop recorded on the
development lathe: the firmware has nothing queued when the stop fires, which
is what makes the coast a property of the drive alone. A stop with steps still
queued is reported with `premise_ok` False, and no reader judges it.

PURE: no Kivy, no registers. ElsUiController feeds it once per board tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from reflex.fsms.els_overshoot import ZRateWindow

#: Consecutive equal Z readings that count as settled.
SETTLE_POLLS = 3

#: A stop not settled by then gives no outcome.
SETTLE_TIMEOUT_S = 1.5

#: A corrected stop landing past its target by MORE than this turns the
#: correction off (decided 2026-10-11). One count is inside the measurement's
#: own quantization: the trigger and the settled reading are each a count edge.
PAST_TOLERANCE_COUNTS = 1


@dataclass(frozen=True)
class StopOutcome:
    seq: int
    #: The correction the firmware applied at this trigger (stopTriggerOffset).
    offset: int
    #: |approach rate| in Z counts/s, None if the window had not filled.
    rate: Optional[float]
    stop_position: int
    trigger_z: int
    settled_z: int
    direction: int
    steps_to_go: int

    @property
    def sign(self) -> int:
        return 1 if self.direction >= 0 else -1

    @property
    def past(self) -> int:
        """Counts past stopPosition; negative is short."""
        return (self.settled_z - self.stop_position) * self.sign

    @property
    def overshoot(self) -> int:
        """Counts the carriage travelled after the stop fired."""
        return (self.settled_z - self.trigger_z) * self.sign

    @property
    def premise_ok(self) -> bool:
        return self.steps_to_go == 0

    @property
    def corrected(self) -> bool:
        return self.offset > 0


class StopOutcomeWatch:
    """Fed once per board tick; returns a StopOutcome on the tick a stop
    settles, else None.

    `board` needs `connected`, `els_stop_values` and `fast_data_values`;
    `clock()` is the acquisition timebase.
    """

    def __init__(self, board, clock: Callable[[], float],
                 window: Optional[ZRateWindow] = None):
        self._board = board
        self._clock = clock
        self.window = window or ZRateWindow()
        self._seq: Optional[int] = None          # last ACCEPTED trigger seq
        self._candidate: Optional[int] = None    # a new seq seen once
        self._candidate_rate: Optional[float] = None
        self._prev_rate: Optional[float] = None
        self._pending: Optional[dict] = None     # trigger waiting to settle
        self._last_z: Optional[int] = None
        self._same: int = 0

    def reset(self) -> None:
        """New link: the next seq read is a baseline, not a stop."""
        self.window.clear()
        self._seq = None
        self._candidate = None
        self._pending = None
        self._prev_rate = None

    def poll(self) -> Optional[StopOutcome]:
        board = self._board
        snap = board.els_stop_values
        if not board.connected or not snap:
            return None
        scales = (board.fast_data_values or {}).get("scaleCurrent")
        idx = int(snap.get("scaleIndex", 0))
        if not scales or not 0 <= idx < len(scales):
            return None
        now = self._clock()
        z = int(scales[idx])
        rate_before = self._prev_rate
        self.window.add(now, z)
        r = self.window.rate()
        self._prev_rate = abs(r) if r is not None else None

        seq = int(snap.get("stopTriggerSeq", 0))
        if self._seq is None:
            self._seq = seq                       # baseline at connect
        elif seq != self._seq:
            if self._candidate != seq:
                # First sighting. Hold the approach rate from the poll before.
                self._candidate = seq
                self._candidate_rate = rate_before
            else:
                # Seen twice: one consistent snapshot. Accept it.
                self._seq = seq
                self._candidate = None
                self._pending = {
                    "seq": seq,
                    "offset": int(snap.get("stopTriggerOffset", 0)),
                    "rate": self._candidate_rate,
                    "stop_position": int(snap.get("stopPosition", 0)),
                    "trigger_z": int(snap.get("stopTriggerZ", 0)),
                    "direction": int(snap.get("stopDirection", 1)),
                    "steps_to_go": int(snap.get("stopTriggerStepsToGo", 0)),
                    "t": now,
                }
                self._last_z = z
                self._same = 1
                return None
        else:
            self._candidate = None

        p = self._pending
        if p is None:
            return None
        if z == self._last_z:
            self._same += 1
        else:
            self._last_z = z
            self._same = 1
        if self._same >= SETTLE_POLLS:
            self._pending = None
            return StopOutcome(
                seq=p["seq"], offset=p["offset"], rate=p["rate"],
                stop_position=p["stop_position"], trigger_z=p["trigger_z"],
                settled_z=z, direction=p["direction"],
                steps_to_go=p["steps_to_go"])
        if now - p["t"] > SETTLE_TIMEOUT_S:
            self._pending = None
        return None


# ── What the operator reads ─────────────────────────────────────────────
# Same contract as ELS_TAKEUP_MESSAGES (reflex/utils/devices.py): each text
# stands alone, says what happened and then at most one action, and fits 65
# characters. test_els_stop_outcome pins the length at the widest values.


def _counts(n: int) -> str:
    return f"{n} count" if n == 1 else f"{n} counts"


def distance_text(counts: int, mm_per_count: float, factor: float) -> str:
    """'2 counts (0.010 mm)': counts and display units, so a mis-provisioned
    scale shows as a disagreement between the two. `factor` is display
    units per mm (FormatsDispatcher.factor: 1 for mm, 10/254 for inches)."""
    d = abs(counts) * mm_per_count * factor
    unit, places = ("mm", 3) if abs(factor - 1.0) < 1e-9 else ("in", 4)
    return f"{_counts(abs(counts))} ({d:.{places}f} {unit})"


def landed_text(outcome: StopOutcome, mm_per_count: float, factor: float) -> str:
    """The last-stop readout."""
    past = outcome.past
    if past == 0:
        return "Stop landed on target."
    where = "past" if past > 0 else "short"
    return f"Stop landed {distance_text(past, mm_per_count, factor)} {where}."


def past_off_text(outcome: StopOutcome, mm_per_count: float, factor: float) -> str:
    """The outcome watch tripped."""
    return (f"Stop landed {distance_text(outcome.past, mm_per_count, factor)} "
            f"past. Correction is off.")
