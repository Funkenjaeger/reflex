"""The 2026-08-08 hardware defect, driven end to end over the real Modbus link.

WHAT HAPPENED ON THE MACHINE
----------------------------
Half-nut open, operator presses Cut. The firmware's backlash take-up
confirmation gate correctly WITHHELD the pass -- and then the operator nudged
the carriage by hand and the gate released it. A hand-pushed carriage was
accepted as proof the half-nut was engaged, which is precisely the failure the
whole take-up-confirmation feature exists to prevent.

The gate asked "did Z move?" when the question it had to answer is "did Z move
BECAUSE WE DROVE IT?". reflex-fw commit b3b78e3 bounded how long it would keep
listening (~250 ms, ELS_TAKEUP_CONFIRM_WINDOW_TICKS) which shrank the exposure
without changing its shape: any Z motion from any source inside that window
still counted. Motion attribution (reflex-fw Core/Inc/els_slip.h) is what
changed the shape -- only Z counts arriving while the servo was driving, or
within a settle horizon of a real pulse, are evidence.

WHY THIS TEST EXISTS ALONGSIDE THE FIRMWARE'S OWN
-------------------------------------------------
reflex-fw's els_takeup_confirm_test.cpp already pins this at the ISR level, and
els_slip_test.cpp pins the horizon's timing properties. Neither goes through
Modbus, the HAL, or the FSM stack -- so neither can catch the take-up being
configured wrong on the way down (backlashSteps never written, thread geometry
mismatched, the gate reading a threshold the host never pushed). This test
drives the REAL reflex-ui controller against the REAL firmware and asks the
same question the operator did.

It is also the first system test to read the take-up registers at all.

WHAT MAKES IT A TEST RATHER THAN A TAUTOLOGY
--------------------------------------------
A machine that refuses EVERYTHING passes any "it refused" assertion. Three
things guard against that reading:

  * ``test_coupled_takeup_still_confirms`` runs the identical setup with the
    half-nut CLOSED and requires the take-up to CONFIRM. If attribution ever
    over-refuses, that test goes red, not this one.
  * Inside the refusal test, ``lastTakeupZDelta`` is asserted ~0 BEFORE the
    nudge and large AFTER it. The firmware demonstrably SAW the carriage move;
    it declined to treat it as evidence. Without this, a run where the nudge
    silently failed to reach the physics would pass for entirely the wrong
    reason.
  * ``takeupPending`` is asserted STILL 1 at the moment the motion is observed.
    That is the host-visible proxy for "the confirmation window had not closed
    yet" (the firmware's own elsStopTakeupLatched is ISR-private). Without it
    the test would pass on the pre-existing window timeout -- the behaviour
    b3b78e3 already shipped -- and would prove nothing about attribution.

TIMING BUDGET (emulator ticks are NOT hardware ticks)
-----------------------------------------------------
The emulator ISR runs at isr_rate_hz = 10 kHz (reflex-fw
emulator/config/lathe.toml), 10x slower than the ~100 kHz the firmware's tick
constants assume. So in emulator WALL-CLOCK:

    ELS_SLIP_SETTLE_TICKS       1000 ticks  ->  ~100 ms   (nudge must land AFTER)
    ELS_TAKEUP_CONFIRM_WINDOW  25000 ticks  ->  ~2.5 s    (nudge must land BEFORE)

_NUDGE_DWELL_S sits between them with margin on both sides. A run that misses
the window fails on the ``takeupPending``/``lastTakeupZDelta`` assertions with
an explicit message rather than passing quietly.

CROSS-REPO PAIRING -- READ THIS BEFORE BELIEVING A CI FAILURE
------------------------------------------------------------
This test requires firmware that has the closed-loop take-up confirmation gate
AND motion attribution (reflex-fw Core/Inc/els_slip.h, branch
feat/els-slip-attribution off integration).

ci.yml pairs reflex-fw by MATCHING BRANCH NAME (falling back to integration,
then dev-staging, then dev), and prints the ref it chose to the job summary.
Check that line first -- it is the whole diagnosis.

Until 2026-08-10 the ref was hardcoded to `dev-staging`, which predates the
whole feature: b98b398 (closed-loop cal + take-up confirmation), b62722c
(derived threshold) and b3b78e3 (bounded window) are all absent, 7+ commits
behind integration. Reproduced locally via REFLEX_FW_DIR: paired that way BOTH
tests here fail, including the coupled positive control, and both fail EARLY
with:

    the take-up gate never published an outcome: takeupSeq still 0,
    takeupPending=0, takeupResult=0, backlashSteps=300

That is the pairing signature: backlashSteps reached the firmware, and the
firmware simply has no gate to answer with. It is a statement about the FIRMWARE
BEING OLD, not a regression in this repo -- and note it is NOT the
"A HAND-PUSHED CARRIAGE RELEASED THE TAKE-UP GATE" message, which would mean
something genuinely alarming. Check the reflex-fw SHA in the pytest header
before drawing any conclusion. Bumping the pairing is a deliberate act, per the
rule in AGENTS.md.

(ci.yml's `system-tests` job is continue-on-error, and the semantic-release
workflow is a separate file gated on main/dev, so a red here releases nothing
and blocks nothing.)

EMULATOR GREEN IS NOT A HARDWARE RESULT: no servo dynamics beyond the model, no
metal, and ELS_SLIP_SETTLE_TICKS is UNCOMMISSIONED (reflex-fw todo.md) -- it
cannot even be measured here, because the emulator's lash model moves the
carriage instantaneously with the pulse. The elspi verification pass is Evan's.
"""

import time
from fractions import Fraction

import pytest

from reflex.feeds import THREAD_IN
from reflex.utils.devices import ELS_CAL_OK, ELS_TAKEUP_ERR_UNCONFIRMED

pytestmark = pytest.mark.system

# EMU_NO_AUTO_RETRACT: no simulated hand-retract on ELS stop -- this test owns
# the carriage's motion, and a surprise retract IS uncommanded Z motion, which
# is the exact quantity under measurement.
_ENV = {"env": {"EMU_NO_AUTO_RETRACT": "1"}}

# Reference-machine geometry (reflex-fw emulator/config/lathe.toml): 400
# counts/mm Z, 4000 ppr spindle, 800 steps/rev on a 8 TPI leadscrew
# => exactly 127/32000 mm per servo step (0.00396875).
_MM_PER_STEP = Fraction(127, 32000)
_Z_COUNTS_PER_MM = 400

# EMU_SCENARIO=serve forces z_backlash_mm = 0.6 (reflex-fw main.cpp) regardless
# of the TOML. The take-up MUST be commanded past it or the carriage
# legitimately never moves -- the regime els_backlash_cal.h warns is
# indistinguishable from an open half-nut, and one in which this test could not
# tell its two cases apart.
_SERVE_LASH_MM = 0.6
_BACKLASH_STEPS = 300
_TAKEUP_MM = float(_BACKLASH_STEPS * _MM_PER_STEP)
assert _TAKEUP_MM > _SERVE_LASH_MM * 1.5, (
    f"take-up {_TAKEUP_MM:.3f} mm does not clear the serve-mode lash "
    f"{_SERVE_LASH_MM} mm with margin -- the coupled control cannot confirm"
)
# Carriage motion a healthy take-up must produce, in Z counts.
_EXPECTED_COUPLED_COUNTS = (_TAKEUP_MM - _SERVE_LASH_MM) * _Z_COUNTS_PER_MM  # ~236

# How far the hand nudge must travel before it counts as "the motion arrived".
# 200 counts = 0.5 mm at 400 counts/mm -- a hundred times the default
# calMotionThreshCounts of 2 (reflex/dispatchers/els.py), so a refusal can never
# be blamed on the shove being too small for the gate to see. At the emulator's
# jog profile (10 mm/s, 50 mm/s^2) that is ~0.2 s of travel, comfortably inside
# the window remaining after the settle dwell.
_NUDGE_SEEN_COUNTS = 200

# Well past the ~100 ms settle horizon, well inside the ~2.5 s window.
#
# Sized against the emulator running SLOW, which is the direction that hurts.
# This dwell is WALL-CLOCK; the horizon it has to clear is measured in ISR
# TICKS. A loaded machine that drops the emulator to a quarter of its nominal
# 10 kHz turns a 0.4 s dwell into ~1000 ticks -- exactly the horizon, i.e. a
# coin flip. At 1.0 s the same quarter-speed run still clears it 2.5x over.
#
# The other side does not tighten in the same way: running slow also stretches
# the ~2.5 s window in wall-clock terms, so the budget the dwell spends only
# matters at full speed, where 1.0 s of 2.5 s leaves ample room for the ~0.2 s
# the nudge needs.
_NUDGE_DWELL_S = 1.0

_THREAD_IN_16 = next(f for f in THREAD_IN if f.name == "16")
assert _THREAD_IN_16.ratio == Fraction(254, 160), (
    f"reflex/feeds.py THREAD_IN '16' changed: {_THREAD_IN_16.ratio} != 254/160"
)


def _set_up_job(h, *, is_threading=True):
    """Configure, commission, set stop/retract, engage and enable sync.

    Threading and turning share everything but ``is_threading``. Since
    2026-08-21 the firmware take-up runs in BOTH modes and on the first pass,
    so the backlash is configured regardless of mode (until then turning wrote
    backlashSteps = 0 and every turning pass ran ungated).

    Leaves the machine armed and idle (``active = 1``, ``enable = 1``), Cut not
    yet pressed, ``referenceLatched == 0``. Returns stop_z.
    """
    h.configure(is_threading=is_threading, retract_enabled=True,
                wizard_enabled=False, els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()          # reference machine defaults
    h.set_feed(_THREAD_IN_16.ratio)
    h.els.els_backlash_steps = _BACKLASH_STEPS
    h.pump()

    z_start = h.z_scaled_position()
    margin = h.safety_margin()
    assert margin > 0
    stop_z = z_start - (margin + 1.0)   # els_forward=True cuts -Z
    h.set_stop_z(stop_z)
    h.set_retract_z(z_start)

    h.engage()
    h.enable_sync()
    assert h.register("elsStop", "referenceLatched") == 0
    return stop_z


def _arm_and_run_first_pass(h):
    """Set up, cut one full pass to the ELS stop, and retract.

    Since 2026-08-21 THE TAKE-UP RUNS ON THE FIRST CUT TOO: the resume path
    initiates it on every pass, and only the phase correction waits for a
    latched reference (reflex-fw Ramps.c). So pass 1 here is itself a gated,
    confirmed take-up against a coupled drivetrain, and it is asserted as
    such — a first pass that quietly skipped the gate is the pre-2026-08-21
    behaviour this helper used to document.

    The pass-2 scenarios in this module still need a completed pass in front
    of them, because they are about a take-up against a LATCHED reference —
    which is also exactly the situation the 2026-08-08 defect happened in: the
    operator had finished a pass, retracted, and pressed Cut for the next one
    with the half-nut still open.

    (The manual reference latch would shortcut this, but it lives on
    reflex-fw's feat/els-thread-resync branch; this test targets `integration`
    plus the attribution work and must not depend on it.)

    Leaves the machine in ``in_cycle.waiting_to_cut`` with the reference
    latched, ready for the pass under test.
    """
    stop_z = _set_up_job(h, is_threading=True)
    seq0 = h.register("elsStop", "takeupSeq")

    # ── Pass 1: cut to the stop. This is what latches the reference. ────────
    h.cut()
    assert h.ui_fsm.state == "in_cycle.cutting"
    stopped = h.wait_until(
        lambda: h.ui_fsm.state == "in_cycle.waiting_to_retract", timeout_s=30
    )
    assert stopped, (
        f"pass 1 never reached the ELS stop (ui={h.ui_fsm.state}, "
        f"z={h.z_scaled_position():.3f}, stop_z={stop_z:.3f})"
    )
    assert h.register("elsStop", "referenceLatched") == 1, (
        "the stop trigger did not latch a reference, so no take-up can be "
        "initiated on the next resume and this test would measure nothing"
    )
    # Pass 1 was itself gated (2026-08-21): one take-up outcome, CONFIRMED,
    # before the cut was released against a coupled drivetrain.
    assert h.register("elsStop", "takeupSeq") == seq0 + 1, (
        "pass 1 ran without a take-up -- the first-pass gate is not gating"
    )
    assert h.register("elsStop", "takeupResult") == 0, (
        "pass 1's take-up was refused on a COUPLED drivetrain"
    )

    # ── Retract, so the next Cut is a genuine resume from the shoulder ──────
    h.trigger_retract()
    retracted = h.wait_until(
        lambda: h.ui_fsm.state == "in_cycle.waiting_to_cut", timeout_s=30
    )
    assert retracted, (
        f"the retract never completed (ui={h.ui_fsm.state}, "
        f"z={h.z_scaled_position():.3f})"
    )


def _await_takeup_verdict(h, seq0, timeout_s=5.0):
    """Wait for the gate to publish a verdict for THIS take-up.

    Keyed on takeupSeq, which increments once per OUTCOME (firmware Ramps.h),
    because neither of the obvious alternatives is observable from the host:

      * ``takeupPending == 0`` is also true BEFORE the take-up initiates, so
        waiting on it returns instantly and reads the previous pass's result.
        (It did exactly that on this test's first run: a green-looking
        "not refused" against a take-up that had not started.)
      * ``takeupPending == 1`` can be missed entirely -- at the emulator's
        10 kHz ISR a 300-step take-up is ~30 ms, shorter than one poll's
        Modbus round trip, so a CONFIRMED pass may never be sampled pending.

    A monotonic counter cannot be raced by either.
    """
    reached = h.wait_until(
        lambda: h.register("elsStop", "takeupSeq") != seq0, timeout_s=timeout_s
    )
    assert reached, (
        "the take-up gate never published an outcome: "
        f"takeupSeq still {seq0}, "
        f"takeupPending={h.register('elsStop', 'takeupPending')}, "
        f"takeupResult={h.register('elsStop', 'takeupResult')}, "
        f"backlashSteps={h.register('elsStop', 'backlashSteps')}"
    )


@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_hand_nudge_inside_the_window_does_not_confirm_the_takeup(harness):
    """Half-nut OPEN + Cut + hand nudge: the 2026-08-08 defect must not recur."""
    h = harness

    _arm_and_run_first_pass(h)

    # Open the half-nut between passes -- the operator's actual mistake.
    #
    # It has to happen HERE, not at boot: the first pass and its retract need a
    # coupled drivetrain. EMU_SCENARIO=serve force-engages the nut for any
    # scenario value (reflex-fw main.cpp), so before this command existed the
    # uncoupled-mid-cycle state was simply unreachable from a system test --
    # which is why the defect had no coverage. Explicit state, not a toggle:
    # see LathePhysics::setHalfNutEngaged.
    h.emu_cmd("halfnut open")
    h.pump()

    z_before = h.carriage_position_counts()
    seq0 = h.register("elsStop", "takeupSeq")

    h.cut()
    assert h.els_fsm.state == "cutting"
    assert h.register("elsStop", "backlashSteps") == _BACKLASH_STEPS, (
        "backlashSteps never reached the firmware -- no take-up would run at all"
    )

    _await_takeup_verdict(h, seq0)

    # The gate withheld, and is still listening.
    assert h.register("elsStop", "takeupResult") == ELS_TAKEUP_ERR_UNCONFIRMED, (
        "an uncoupled take-up was not refused at all -- the gate is not gating"
    )
    assert h.register("elsStop", "takeupPending") == 1, (
        "the confirmation window closed before the test could act; the nudge "
        "below would then be testing the b3b78e3 window timeout, not attribution"
    )

    # Baseline: nothing has moved the carriage yet. This is the control for the
    # post-nudge assertion -- it makes "the firmware saw 400 counts" meaningful
    # rather than a number that was always there.
    assert abs(h.register("elsStop", "lastTakeupZDelta")) < _NUDGE_SEEN_COUNTS, (
        "the carriage moved before the nudge was issued -- the half-nut is not "
        "open, or something else is driving Z"
    )

    # Dwell past the settle horizon, so the shove cannot be mistaken for the
    # drivetrain's own inertia. Pump throughout: the link must stay live.
    deadline = time.monotonic() + _NUDGE_DWELL_S
    while time.monotonic() < deadline:
        h.pump()
        time.sleep(0.02)
    assert h.register("elsStop", "takeupPending") == 1, (
        f"the window closed during the {_NUDGE_DWELL_S}s settle dwell -- the "
        "emulator is running slower than the timing budget in this module's "
        "docstring assumes"
    )

    # THE NUDGE: the operator's hand on the handwheel, with the nut open.
    #
    # Re-issued every poll rather than once. LathePhysics stops a jog after
    # JOG_KEY_TIMEOUT (0.15 s) of silence -- it models an arrow key being HELD,
    # so a single command is a 0.15 s tap, and the distance that covers depends
    # on the accel ramp. Re-sending is what "the operator is still pushing"
    # looks like, and it makes the travel bounded by the loop, not by a timeout.
    #
    # The machine's state is captured at the FIRST poll where the motion is
    # visible: taking it later would let the window close between observing the
    # motion and reading the verdict, which is the one race that could turn this
    # test into a check of the b3b78e3 timeout instead.
    observed = {}
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        h.emu_cmd("z jog 1")
        h.pump()
        moved = abs(h.carriage_position_counts() - z_before)
        if moved >= _NUDGE_SEEN_COUNTS:
            observed = {
                "moved_counts": moved,
                "takeupPending": h.register("elsStop", "takeupPending"),
                "takeupResult": h.register("elsStop", "takeupResult"),
                "lastTakeupZDelta": h.register("elsStop", "lastTakeupZDelta"),
            }
            break
        time.sleep(0.02)
    h.emu_cmd("z jog 0")

    assert observed, (
        f"the nudge never reached the physics: carriage moved "
        f"{abs(h.carriage_position_counts() - z_before)} counts, wanted "
        f">= {_NUDGE_SEEN_COUNTS}. Without motion this test proves nothing."
    )

    # ── The assertion this whole file exists for ────────────────────────────
    assert observed["takeupPending"] == 1, (
        "the window had already closed when the nudge landed, so the refusal "
        "below is the pre-existing timeout rather than attribution "
        f"(observed {observed})"
    )
    assert abs(observed["lastTakeupZDelta"]) >= _NUDGE_SEEN_COUNTS, (
        "the firmware did not observe the carriage motion at all -- assert the "
        f"motion was SEEN before trusting that it was refused (observed {observed})"
    )
    assert observed["takeupResult"] == ELS_TAKEUP_ERR_UNCONFIRMED, (
        "A HAND-PUSHED CARRIAGE RELEASED THE TAKE-UP GATE. This is the "
        f"2026-08-08 hardware defect (observed {observed})"
    )

    # ── And it stays refused: the pass aborts to stopped, reference intact ──
    closed = h.wait_until(
        lambda: h.register("elsStop", "takeupPending") == 0, timeout_s=6.0
    )
    assert closed, "the take-up never stopped holding the machine"
    assert h.register("elsStop", "takeupResult") == ELS_TAKEUP_ERR_UNCONFIRMED, (
        "the gate confirmed after the window closed"
    )
    assert h.register("elsStop", "active") == 1, "not returned to stopped-at-shoulder"
    assert h.register("elsStop", "referenceLatched") == 1, (
        "the thread phase reference was lost -- a retry must be free"
    )


@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_coupled_takeup_still_confirms(harness):
    """POSITIVE CONTROL. Identical setup, half-nut CLOSED (serve-mode default).

    Without this, every assertion in the refusal test above is satisfied by a
    machine that refuses unconditionally -- which is what an over-tight settle
    horizon, or an attribution bug that credits nothing, would produce.
    """
    h = harness

    _arm_and_run_first_pass(h)

    z_before = h.carriage_position_counts()
    seq0 = h.register("elsStop", "takeupSeq")

    h.cut()
    _await_takeup_verdict(h, seq0)

    assert h.register("elsStop", "takeupResult") == ELS_CAL_OK, (
        "a COUPLED take-up was refused -- attribution is discarding motion the "
        "servo genuinely caused. Suspect ELS_SLIP_SETTLE_TICKS being shorter "
        "than the live pulse pacing (reflex-fw els_slip.h)"
    )
    assert h.register("elsStop", "takeupSeq") >= 1, "no take-up outcome was published"

    # The carriage really did travel the commanded distance past the lash --
    # otherwise "confirmed" could be a gate that waves everything through.
    moved = abs(h.carriage_position_counts() - z_before)
    assert moved >= _EXPECTED_COUPLED_COUNTS * 0.5, (
        f"the take-up confirmed but the carriage only moved {moved} counts "
        f"(expected ~{_EXPECTED_COUPLED_COUNTS:.0f}) -- the gate is not "
        "measuring what it claims to"
    )


@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
@pytest.mark.parametrize("is_threading", [True, False], ids=["threading", "turning"])
def test_first_pass_with_half_nut_open_is_refused(harness, is_threading):
    """THE DATUM PASS IS GATED (2026-08-21). Half-nut open, first Cut of a job:
    the take-up must run, be refused, abort back to armed-idle, and latch
    NOTHING -- before this change pass one ran ungated in both modes, so an
    open nut gave a turning spindle and nothing happening with no message, and
    a partially engaged nut latched a reference every later pass then
    confirmed cleanly against. Turning never had the gate at all.
    """
    h = harness
    _set_up_job(h, is_threading=is_threading)

    h.emu_cmd("halfnut open")
    h.pump()
    seq0 = h.register("elsStop", "takeupSeq")

    h.cut()
    assert h.els_fsm.state == "cutting"
    assert h.register("elsStop", "backlashSteps") == _BACKLASH_STEPS, (
        "backlashSteps never reached the firmware -- no take-up would run at all "
        "(in turning this is the 2026-08-21 UI change: it used to write 0)"
    )
    if not is_threading:
        assert h.register("elsStop", "threadPitchSteps") == 0.0, (
            "turning must not carry a pitch, or the firmware would phase-correct"
        )
        assert h.register("elsStop", "zCountsPerPitch") != 0.0, (
            "turning must carry the SIGNED Z polarity for the take-up direction"
        )

    _await_takeup_verdict(h, seq0)
    assert h.register("elsStop", "takeupResult") == ELS_TAKEUP_ERR_UNCONFIRMED, (
        "an uncoupled FIRST pass was not refused -- the datum pass is ungated"
    )

    closed = h.wait_until(
        lambda: h.register("elsStop", "takeupPending") == 0, timeout_s=6.0
    )
    assert closed, "the take-up never stopped holding the machine"
    assert h.register("elsStop", "active") == 1, "not returned to armed-idle"
    assert h.register("elsStop", "referenceLatched") == 0, (
        "a reference was latched from a drivetrain that was never coupled -- "
        "the exact failure that is invisible on every later pass"
    )
    assert h.els_fsm.state == "stopped"


@pytest.mark.parametrize("emulator_process", [_ENV], indirect=True)
def test_turning_first_pass_takes_up_and_confirms(harness):
    """POSITIVE CONTROL for the turning half: nut closed, first turning Cut
    takes up, confirms, releases the feed, and the pass reaches the stop with
    no phase correction having run (there is no pitch to correct to)."""
    h = harness
    stop_z = _set_up_job(h, is_threading=False)
    seq0 = h.register("elsStop", "takeupSeq")

    h.cut()
    assert h.els_fsm.state == "cutting"
    _await_takeup_verdict(h, seq0)
    assert h.register("elsStop", "takeupResult") == 0, (
        "a coupled turning take-up was refused -- the gate refuses unconditionally"
    )
    stopped = h.wait_until(lambda: h.els_fsm.state == "stopped", timeout_s=30)
    assert stopped, (
        f"the turning pass never reached the stop (z={h.z_scaled_position():.3f}, "
        f"stop_z={stop_z:.3f})"
    )
    assert h.register("elsStop", "lastIdealAdvance") == 0.0, (
        "phase correction ran on a turning pass"
    )
