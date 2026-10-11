"""The coupling check listens through a thread pass's phase correction.

Decided 2026-10-11 (reflex/fsms/els_ratio.py, WHEN IT LISTENS): the check
starts as soon as the take-up has finished, not once the phase correction
has drained. This drives real thread passes on the emulator, where the second
pass carries a phase correction (the reference latched on the first), and
shows, against the firmware's own stepsToGo:

  * the check is fed, and has started, on polls where the correction is
    still draining (stepsToGo != 0);
  * a pass in the wrong gearbox position is named on that pass, and is not
    called DECOUPLED or RATIO_WRONG on the way;
  * a pass in the right position reaches no verdict at all.

WHAT THIS DOES NOT SHOW. The emulator's drive has no lag: its carriage
follows every step at once. On the lathe the drive lags a correction burst,
and whether that lag ever reads as a wrong ratio on a pass in the right gear
is the hardware check that remains (tests/fsms/test_els_ratio.py pins where
the limit is: a lag held through the whole coarse window).

The check runs in shadow mode here (els_ratio_check off, the default), so a
verdict is recorded but never stops the pass.
"""
import time
from fractions import Fraction

import pytest

from reflex.feeds import THREAD_IN
from reflex.fsms import els_ratio
from reflex.fsms.els_ratio import CouplingRatioCheck

pytestmark = pytest.mark.system

# 120 rpm. At the serve-mode default (30 rpm) a 16 TPI pass commands ~330
# steps/s, the trailing window never gathers MIN_WINDOW_STEPS, and the check
# cannot settle under either listening rule. At 300 rpm the emulator's
# one-step-per-tick servo falls behind and the carriage runs millimetres past
# the stop, which spoils the second pass.
_ENV = {"env": {"EMU_NO_AUTO_RETRACT": "1", "EMU_RPM": "120"}}
_BACKLASH_STEPS = 300
_THREAD_IN_16 = next(f for f in THREAD_IN if f.name == "16")
_TABLE = "A=1, B=2, C=1/2"


class _Spy:
    """Every feed() of every pass: inputs, whether the check had started
    before and after, and the verdict it returned."""

    def __init__(self, monkeypatch):
        self.calls = []
        original = CouplingRatioCheck.feed
        spy = self

        def feed(check, **kw):
            before = check._started
            v = original(check, **kw)
            spy.calls.append({"check": id(check), "started_before": before,
                              "started": check._started, "verdict": v,
                              "t": time.monotonic(), **kw})
            return v

        monkeypatch.setattr(CouplingRatioCheck, "feed", feed)

    def pass_calls(self, n):
        """The calls of the n-th pass (0-based), in order."""
        ids = []
        for c in self.calls:
            if c["check"] not in ids:
                ids.append(c["check"])
        return [c for c in self.calls if c["check"] == ids[n]]


def _set_up(h, gear):
    h.configure(is_threading=True, retract_enabled=True, wizard_enabled=False,
                els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()
    assert h.controller.set_gear_table(_TABLE)
    assert h.controller.select_gear(gear)
    h.pump()
    h.set_feed(_THREAD_IN_16.ratio)
    h.els.els_backlash_steps = _BACKLASH_STEPS
    h.pump()
    z_start = h.z_scaled_position()
    margin = h.safety_margin()
    # Long enough that the check has a full coarse window after the
    # correction: 350 ms and 0.75 mm of travel at the least.
    stop_z = z_start - (margin + 12.0)
    h.set_stop_z(stop_z)
    h.set_retract_z(z_start)
    h.engage()
    h.enable_sync()
    return stop_z


def _cut_to_stop(h):
    h.cut()
    assert h.ui_fsm.state == "in_cycle.cutting"
    assert h.wait_until(lambda: h.ui_fsm.state == "in_cycle.waiting_to_retract",
                        timeout_s=60), f"pass never stopped (ui={h.ui_fsm.state})"


def _retract(h):
    h.trigger_retract()
    assert h.wait_until(lambda: h.ui_fsm.state == "in_cycle.waiting_to_cut",
                        timeout_s=60), f"retract never completed (ui={h.ui_fsm.state})"


def _two_passes(h, gear):
    _set_up(h, gear)
    _cut_to_stop(h)
    assert h.register("elsStop", "referenceLatched") == 1
    _retract(h)
    _cut_to_stop(h)


def _trace(calls):
    """One line per poll, for a failure message."""
    t0 = calls[0]["t"] if calls else 0.0
    rows = []
    for c in calls:
        v = c["verdict"]
        rs = "-" if v.r_since_start is None else f"{v.r_since_start:.3f}"
        rt = "-" if v.r_trailing is None else f"{v.r_trailing:.3f}"
        rows.append(f"{(c['t'] - t0) * 1000:6.0f} ms  tp={int(c['takeup_pending'])} "
                    f"stg={c['steps_to_go']:5d} steps={c['servo_steps']:7d} "
                    f"z={c['z_counts']:7d} started={int(c['started'])} "
                    f"r={rs} trail={rt} {v.kind}")
    return "\n".join(rows)


def _correction_polls(calls):
    """Polls after the take-up on which the phase correction was draining."""
    return [c for c in calls if not c["takeup_pending"] and c["steps_to_go"] != 0]


@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_wrong_gear_is_named_on_a_pass_with_a_phase_correction(harness, monkeypatch):
    """The controller believes C (x1/2); the emulator's carriage moves as in
    A. The check reads the reference ratio and names A.

    C and not B: the take-up is converted to the believed position, so in B it
    would command half the reference lash and be refused before any pass ran.
    In C it commands twice the lash, which the take-up gate accepts."""
    h = harness
    spy = _Spy(monkeypatch)
    _two_passes(h, "C")

    calls = spy.pass_calls(1)
    draining = _correction_polls(calls)
    assert draining, (
        "pass 2 showed no phase correction draining after the take-up, so "
        "this test would measure nothing: steps_to_go per poll "
        f"{[c['steps_to_go'] for c in calls]}")
    # The check STARTED while the correction was still draining: the old rule
    # (wait for stepsToGo == 0) would have started it after.
    start = next(c for c in calls if c["started"])
    assert start["steps_to_go"] != 0, (
        f"the check started at steps_to_go={start['steps_to_go']}, after the "
        "correction drained")

    kinds = [c["verdict"].kind for c in calls]
    terminal = [c["verdict"] for c in calls if c["verdict"].kind in els_ratio.TERMINAL]
    assert terminal, f"no verdict on pass 2: {kinds}"
    assert terminal[0].kind == els_ratio.GEAR_CHANGE and terminal[0].gear == "A", (
        f"pass 2 verdict {terminal[0]}")


@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_right_gear_with_a_phase_correction_reaches_no_verdict(harness, monkeypatch):
    """No false red from listening through the correction, on a drive that
    follows its steps (the emulator)."""
    h = harness
    spy = _Spy(monkeypatch)
    _two_passes(h, "A")

    calls = spy.pass_calls(1)
    assert _correction_polls(calls), "pass 2 showed no phase correction draining"
    start = next(c for c in calls if c["started"])
    assert start["steps_to_go"] != 0
    bad = [c["verdict"] for c in calls if c["verdict"].kind in els_ratio.TERMINAL]
    assert not bad, (f"a verdict on a pass in the right gear: {bad[0]}\n"
                     f"{_trace(calls)}")
    assert calls[-1]["verdict"].kind == els_ratio.OK, calls[-1]["verdict"]
