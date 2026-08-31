"""Emulator-backed measurement of Modbus round-trips per board tick.

WHY THIS IS A TEST AND NOT A ONE-OFF SCRIPT. On 2026-08-23 a lathe session lost
Modbus comms on 6 of 6 cuts, every drop a TIMEOUT at the transition into
`cutting` and none of them a corrupted frame -- i.e. the firmware failing to
answer, not the wiring. The cause was traffic volume: `BaseDevice.__getitem__`
performs a LIVE per-field read (it is not a cache hit), the tick-driven pollers
in `fsms/ui_controller.py` made a handful of those each, and the board ticks at
30 Hz. Cut-start is when the firmware's 100 kHz ISR is busiest -- take-up
initiation, phase correction, diagnostic arming -- so every extra request in
flight is another chance to hit the 0.1 s serial timeout.

The fix was to collapse the traffic (bigger chunks in `BaseDevice.refresh`, one
`elsStop` snapshot per tick serving the pollers). What makes that fix REAL is a
number, and what keeps it real is this file: a poller added later that goes back
to per-field live reads is a regression nothing else in the suite can see. It
measured 5.20 / 6.20 / 14.00 round-trips per tick before and 3.00 / 3.00 / 5.00
after, for the three scenarios below.

ROUND-TRIP, precisely: one request/response exchange on the wire. Every
minimalmodbus `read_*`/`write_*` public method is exactly one, so counting those
counts exchanges. That is the quantity that matters here -- each one is an
independent opportunity for the firmware to fail to answer within the timeout --
and it is NOT the same as bytes on the wire, which the fix deliberately trades
upward.
"""

from fractions import Fraction

import pytest

pytestmark = pytest.mark.system


# The instrument methods the app actually uses. Each is exactly one Modbus
# request/response exchange inside minimalmodbus (they all funnel into
# `_perform_command`), so counting calls counts round-trips. Listed explicitly
# rather than patching `_perform_command` so the report can separate a BLOCK
# read (`read_registers`, i.e. a `refresh()`) from a PER-FIELD read.
_READ_METHODS = ("read_register", "read_registers", "read_long", "read_float",
                 "read_bit", "read_bits")
_WRITE_METHODS = ("write_register", "write_registers", "write_long",
                  "write_float", "write_bit")


class TransportCounter:
    """Counts Modbus round-trips on a live `minimalmodbus.Instrument`.

    Wraps the instrument INSTANCE, not the class, so a counter cannot leak into
    another test's board through a class attribute left patched behind.
    """

    def __init__(self, instrument):
        self._instrument = instrument
        self._originals = {}
        self.counts = {}
        for name in _READ_METHODS + _WRITE_METHODS:
            original = getattr(instrument, name, None)
            if original is None:
                continue          # older/newer minimalmodbus: skip what's absent
            self._originals[name] = original
            setattr(instrument, name, self._wrap(name, original))

    def _wrap(self, name, original):
        def counted(*a, **kw):
            self.counts[name] = self.counts.get(name, 0) + 1
            return original(*a, **kw)
        return counted

    def restore(self):
        for name, original in self._originals.items():
            setattr(self._instrument, name, original)
        self._originals.clear()

    def reset(self):
        self.counts.clear()

    @property
    def reads(self) -> int:
        return sum(v for k, v in self.counts.items() if k in _READ_METHODS)

    @property
    def writes(self) -> int:
        return sum(v for k, v in self.counts.items() if k in _WRITE_METHODS)

    @property
    def total(self) -> int:
        return self.reads + self.writes

    @property
    def block_reads(self) -> int:
        """`refresh()` traffic -- one per chunk of a multi-register block."""
        return self.counts.get("read_registers", 0)

    @property
    def field_reads(self) -> int:
        """Live per-field reads -- the traffic this work exists to remove."""
        return self.reads - self.block_reads

    def report(self, label: str, ticks: int) -> str:
        return (
            f"\n[{label}] {ticks} ticks: "
            f"{self.total / ticks:.2f} round-trips/tick "
            f"(reads {self.reads / ticks:.2f} = "
            f"block {self.block_reads / ticks:.2f} + "
            f"field {self.field_reads / ticks:.2f}, "
            f"writes {self.writes / ticks:.2f})"
            f"\n         raw: {dict(sorted(self.counts.items()))}"
        )


def _instrument(harness):
    return harness.board.connection_manager.device


def _one_tick_per_pump(harness):
    """Make `pump()` produce EXACTLY one board tick.

    `Board.__init__` puts `update` on the Kivy clock at 30 Hz, and `pump()` both
    ticks the clock AND calls `update()` directly -- so a pump costs one tick or
    two depending on how much wall-clock the previous pump's serial I/O burned.
    That is harmless for the behavioural tests and fatal for a measurement: the
    denominator would drift with machine speed. Detaching the scheduled copy
    leaves the explicit call as the only tick, and the clock still runs for
    everything else (the controller marshals property writes through
    `Clock.schedule_once`).
    """
    from kivy.clock import Clock
    Clock.unschedule(harness.board.update)


def _measure(harness, ticks: int, counter: TransportCounter) -> float:
    """Round-trips per board tick over `ticks` pumps, counted from zero."""
    counter.reset()
    for _ in range(ticks):
        harness.pump()
    return counter.total / ticks


def _force_diag_recorder_live(controller, schema=6):
    """Make the diagnostic poller do real work.

    Against release firmware `diagSchema` is 0 and the recorder disables itself
    for the connection, issuing no reads at all -- so a measurement taken with
    it dormant understates the tick cost on any build carrying a probe, which
    is exactly the configuration the lathe was running the night comms dropped.
    Force the state the recorder would reach after finding a probe.
    """
    rec = controller._diag_recorder
    rec._schema = schema
    rec._enabled = True
    rec._baseline_seq = rec._hal.read_diag_seq()
    rec._failures = 0


def _arm_takeup_edge(controller):
    """Leave the take-up poller one tick away from reporting an outcome.

    It acts on the SECOND poll at which takeupSeq is unchanged (the torn-read
    guard), so both the previous-seq and the pending-seq baselines have to be
    set: seq != prev makes it an edge, pending == seq makes it the second
    sighting. The next poll then reads result, delta and threshold.
    """
    seq = controller._hal.read_takeup_seq()
    controller._prev_takeup_seq = seq - 1
    controller._pending_takeup_seq = seq


def _arm_diag_edge(controller):
    """Leave the diagnostic recorder one tick away from draining a capture.

    Backdating the baseline makes the next diagSeq read an edge, which sends
    the recorder into the whole-block capture read. The emulator carries no
    probe, so it will refuse the payload and go dormant afterwards -- which is
    fine: the cost being measured is the read, and it has already happened by
    the time the schema is checked.
    """
    rec = controller._diag_recorder
    rec._baseline_seq = rec._hal.read_diag_seq() - 1


@pytest.mark.parametrize(
    "emulator_process", [{"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}],
    indirect=True)
def test_round_trips_per_tick_idle_and_cut_start(harness, capsys):
    """Measure (and bound) round-trips per tick, idle and at cut-start."""
    h = harness
    h.configure(is_threading=True, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()
    h.set_feed(Fraction(254, 160))

    _one_tick_per_pump(h)
    counter = TransportCounter(_instrument(h))
    try:
        # ── A. idle connected ─────────────────────────────────────────────
        # Connected, ELS not engaged: the pollers that run unconditionally
        # (elsStop active mirror, take-up outcome, phase offset) plus the
        # board's own fastData refresh. The resting cost of having the app open.
        _force_diag_recorder_live(h.controller)
        idle_per_tick = _measure(h, 30, counter)
        idle_report = counter.report("idle connected", 30)

        # ── B. cut-start, every poller active ─────────────────────────────
        z_start = h.z_scaled_position()
        stop_z = z_start - (h.safety_margin() + 1.0)
        h.set_stop_z(stop_z)
        h.engage()
        assert h.els_fsm.state == "stopped"
        h.enable_sync()
        _force_diag_recorder_live(h.controller)

        h.cut()
        assert h.els_fsm.state == "cutting", f"cut did not start: {h.els_fsm.state}"

        # Measure INSIDE the cut: ElsFsm._on_board_update is bound (it binds on
        # entering `cutting`), so this is the full poller set -- the exact
        # configuration that was timing out on the machine.
        cut_per_tick = _measure(h, 30, counter)
        cut_report = counter.report("cut-start (cutting, all pollers)", 30)
        # The measurement only describes cut-start if the cut was still running
        # throughout. A pass that silently measured a stopped machine (the ELS
        # stop fired early, or the link dropped) would report a comfortable
        # number for a state nobody cares about.
        still_cutting = h.els_fsm.state == "cutting"
        still_connected = h.board.connected

        # ── C. the PEAK tick ──────────────────────────────────────────────
        # Scenario B is the steady cut. What actually loses the link is the one
        # tick where the edge-triggered pollers fire TOO: a take-up outcome to
        # report (three more reads) and a completed diagnostic capture to drain
        # (a whole-block read). Both happen at cut-start, and the average over
        # 30 ticks hides them. Seed both edges and measure exactly one tick.
        _arm_takeup_edge(h.controller)
        _arm_diag_edge(h.controller)
        peak_per_tick = _measure(h, 1, counter)
        peak_report = counter.report("cut-start PEAK tick (both edges due)", 1)
    finally:
        counter.restore()

    assert still_cutting, "cut ended during the measurement window"
    assert still_connected, "link dropped during the measurement window"

    # Printed unconditionally: the number IS the deliverable, and a run that
    # only says "passed" cannot be compared against the next one.
    with capsys.disabled():
        print(idle_report)
        print(cut_report)
        print(peak_report)

    # Measured on this emulator, before -> after the collapse:
    #
    #   idle connected      5.20 -> 3.00   (1 block + 4.20 field -> 3 block)
    #   cut-start, steady   6.20 -> 3.00   (1 block + 5.20 field -> 3 block)
    #   cut-start, PEAK    14.00 -> 5.00   (5 block + 9 field -> 5 block)
    #
    # The three "after" numbers are all block reads: fastData (1) + the elsStop
    # snapshot (2, at 64 registers a request), plus on the peak tick the
    # diagnostic capture that is deliberately still read live (2).
    #
    # BOUNDS, NOT EQUALITIES -- but tight ones. Half a round-trip of slack is
    # enough for the mode-watch sampler's 1-in-5 duty cycle and not enough to
    # hide a single new per-field read per tick, which is the regression worth
    # catching: a poller added later that reaches for device['elsStop'][...]
    # instead of hal.tick lands a full 1.00 above the bound. A change that
    # legitimately adds a block read (another struct getting its own snapshot)
    # will trip these too, and should -- that is a decision worth re-blessing
    # rather than absorbing silently.
    assert idle_per_tick <= 3.5, (
        f"idle traffic regressed to {idle_per_tick:.2f} round-trips/tick"
    )
    assert cut_per_tick <= 3.5, (
        f"cut-start traffic regressed to {cut_per_tick:.2f} round-trips/tick"
    )
    assert peak_per_tick <= 6.0, (
        f"peak-tick traffic regressed to {peak_per_tick:.2f} round-trips"
    )
