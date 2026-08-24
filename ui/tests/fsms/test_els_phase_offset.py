"""Thread-phase offset: unit conversion, accumulation, and the refusals.

The feature exists to WIDEN A THREAD GROOVE PAST THE WIDTH OF THE CUTTER — cut
the groove, shift the controller's idea of phase by a step-over smaller than
the cutter, cut again, until the groove is the width wanted. Nothing is
re-indexed and the datum is never re-established. The firmware half is pinned
by fw/emulator/test/els_phase_offset_command_test.cpp; this file pins the host
half, which owns three things the firmware deliberately does not:

  1. UNIT CONVERSION. The operator types a distance; the register wants
     leadscrew steps. Exact Fractions all the way, rounded ONCE at the end.
  2. ACCUMULATION. The firmware holds one absolute total and replaces it on
     every apply, so the running total is built here: read, add, write back.
  3. THE REFUSALS. Every reason an offset is meaningless or misleading is
     decided here, where there is a screen to explain it on.

WHY THE REFUSALS GET THE MOST COVERAGE. An offset that is silently clamped,
silently ignored, or silently applied to the wrong thing is not a UI
annoyance — it is a wrong thread cut in metal, discovered after the fact. The
firmware's own refusal (consume without ack) is deliberately mute, so if these
checks are wrong the operator watches a number fail to change and is told
nothing.

FIXTURE GEOMETRY, once, so no test has to re-derive it:
    servo ratio  1/1000 mm per step   -> 1000 leadscrew steps per mm
    spindle      3/2 mm per rev       -> 1.5 mm thread pitch
    => one pitch = 1500 steps; half a pitch = 750 steps = 0.75 mm

MUTATION-TESTED 2026-08-22. Each mutation below was applied to the production
source one at a time, the listed failures observed, and the source reverted.
All nine were killed.

    F1 accept exactly one pitch (>= becomes >)     -> 1 failure
    F2 clamp at one pitch instead of refusing      -> 2 failures
    F3 silently absolute a negative entry          -> 1 failure
    F4 send the increment, not the total           -> 3 failures
    F5 drop the in-a-job guard                     -> 2 failures
    F6 drop the threading guard                    -> 1 failure
    F7 round to display units first, then convert  -> 5 failures
    F8 Clear writes nothing                        -> 1 failure
    F9 HAL writes Command before Pending           -> 1 failure

F2 and F9 are the two worth keeping: both leave a UI that looks like it worked.
F2 puts the cut somewhere other than where it was asked for, and F9 opens the
window where the ISR applies half of one number and half of another.
"""
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from reflex.fsms.els_fsm import ElsFsm
from reflex.fsms.els_stop_hal import ElsStopHal

# Reuse the production-shaped mocks rather than growing a second set. The
# comment on _make_els in that module records what a drifted mock already cost
# once; a private copy here would be the same hazard with a longer fuse.
from tests.fsms.test_els_fsm import (
    _make_board, _make_controller, _make_els, _make_servo,
    _make_x_axis, _make_z_axis,
)

PITCH_STEPS = 1500          # 1.5 mm at 1000 steps/mm
STEPS_PER_MM = 1000


def _fsm(*, ratio_num=1, ratio_den=1000, factor=Fraction(1, 1),
         pitch_num=3, pitch_den=2, is_threading=True, connected=True,
         enabled=True, total_steps=0):
    z, _ = _make_z_axis()
    x, _ = _make_x_axis()
    servo = _make_servo(ratio_num=ratio_num, ratio_den=ratio_den)
    board = _make_board(servo=servo)
    board.connected = connected
    board.formats = SimpleNamespace(factor=factor)
    els = _make_els(z_axis=z, x_axis=x,
                    spindle=SimpleNamespace(syncRatioNum=pitch_num,
                                            syncRatioDen=pitch_den))
    hal = MagicMock()
    hal.read_enable.return_value = enabled
    hal.read_phase_offset_steps.return_value = total_steps
    # Healthy reads by default, stated rather than left to MagicMock -- an
    # unstubbed reads_fabricated_since() answers with a truthy Mock, which
    # would make every apply refuse and every test here pass vacuously.
    hal.reads_baseline.return_value = 0
    hal.reads_fabricated_since.return_value = False
    fsm = ElsFsm(els=els, board=board, hal=hal,
                 controller=_make_controller(is_threading=is_threading))
    return fsm, hal


# ─── geometry: the numbers every other case leans on ───────────────────────

def test_thread_pitch_steps_matches_the_geometry_pushed_to_firmware():
    """If this drifts from push_thread_geometry, every refusal bound is wrong
    while still looking principled."""
    fsm, _ = _fsm()
    assert fsm.thread_pitch_steps() == pytest.approx(PITCH_STEPS)


def test_steps_per_display_unit_is_exact_not_floating():
    fsm, _ = _fsm()
    assert fsm._leadscrew_steps_per_display_unit() == Fraction(STEPS_PER_MM)


def test_geometry_is_none_when_the_servo_ratio_is_degenerate():
    """A zero ratio is a machine that has not been set up, not a machine with
    an infinitely fine leadscrew."""
    fsm, _ = _fsm(ratio_num=0)
    assert fsm._leadscrew_steps_per_display_unit() is None


# ─── conversion + accumulation ─────────────────────────────────────────────

def test_apply_converts_distance_to_leadscrew_steps():
    fsm, hal = _fsm()
    assert fsm.apply_phase_offset(0.5) == ElsFsm.PHASE_OFFSET_OK
    hal.request_phase_offset.assert_called_once_with(500)


def test_apply_SETS_the_offset_rather_than_adding_to_it():
    """Evan, 2026-08-23: the entered number IS the offset, measured from the
    latched reference. It is not another helping added to what is there.

    The old behaviour accumulated, which came from the multi-start framing
    where stepping by pitch/N repeatedly was the workflow. For widening a
    groove the field reads as a setting.
    """
    fsm, hal = _fsm(total_steps=500)          # an offset is already applied
    assert fsm.apply_phase_offset(0.2) == ElsFsm.PHASE_OFFSET_OK
    hal.request_phase_offset.assert_called_once_with(200)


def test_applying_the_same_value_twice_is_idempotent():
    """The whole point of the change, and it deletes a hazard: under accumulate,
    an operator unsure the first press landed pressed again and silently stepped
    the groove one increment wider than they had measured for."""
    fsm, hal = _fsm(total_steps=0)
    fsm.apply_phase_offset(0.2)
    fsm.apply_phase_offset(0.2)
    assert [c.args[0] for c in hal.request_phase_offset.call_args_list] == [200, 200]


def test_apply_never_reads_the_current_total():
    """Setting depends only on what was typed, so the read that accumulation
    needed is gone -- and with it the exposure that a fabricated read of that
    total collapsed the offset to a single step-over while reporting success."""
    fsm, hal = _fsm(total_steps=500)
    fsm.apply_phase_offset(0.2)
    hal.read_phase_offset_steps.assert_not_called()


def test_inch_entry_converts_through_the_display_factor():
    """factor is display-units-per-mm: 10/254 for inches. One inch of entry is
    25.4 mm is 25400 steps."""
    fsm, hal = _fsm(factor=Fraction(10, 254), pitch_num=100, pitch_den=1)
    assert fsm.apply_phase_offset(1.0) == ElsFsm.PHASE_OFFSET_OK
    hal.request_phase_offset.assert_called_once_with(25400)


def test_rounding_happens_once_at_the_end():
    """A third of a millimetre is 333.33 steps. Rounded once it is 333; a
    conversion that rounded to an intermediate unit first would land
    elsewhere, and the error would grow with every cumulative entry."""
    fsm, hal = _fsm()
    fsm.apply_phase_offset(1.0 / 3.0)
    (sent,), _ = hal.request_phase_offset.call_args
    assert sent == 333


def test_a_zero_entry_sets_the_offset_to_zero():
    """Under SET, entering nothing means no offset -- the same thing Clear
    does. Not a refusal: a zero is a legitimate setting, not a slip."""
    fsm, hal = _fsm(total_steps=400)
    assert fsm.apply_phase_offset(0.0) == ElsFsm.PHASE_OFFSET_OK
    hal.request_phase_offset.assert_called_once_with(0)


# ─── the refusals ──────────────────────────────────────────────────────────
# Each asserts BOTH the reason code and that NO write went out. A refusal that
# still writes is the worst of both: the operator is told no and the machine
# does it anyway.

def test_refuses_at_exactly_one_pitch():
    """Tested against the ENTRY now, not a running sum: with SET semantics the
    number typed is the offset, so the aliasing bound applies to it directly.

    One pitch of offset is a no-op — the tool re-enters the same groove one
    turn along. Refused rather than clamped, so the cut is never quietly put
    somewhere other than where it was asked for (decision 2026-08-22). This is
    the ALIASING bound and holds whatever the offset is being used for."""
    fsm, hal = _fsm()
    assert fsm.apply_phase_offset(PITCH_STEPS / STEPS_PER_MM) == ElsFsm.PHASE_OFFSET_AT_PITCH
    hal.request_phase_offset.assert_not_called()


def test_refuses_beyond_one_pitch():
    """1.5 pitches is indistinguishable from 0.5 at the tool."""
    fsm, hal = _fsm()
    assert fsm.apply_phase_offset(2.0) == ElsFsm.PHASE_OFFSET_AT_PITCH
    hal.request_phase_offset.assert_not_called()


def test_accepts_just_under_one_pitch():
    """The bound is a real edge, not a blanket 'large entries are scary'."""
    fsm, hal = _fsm()
    assert fsm.apply_phase_offset(1.499) == ElsFsm.PHASE_OFFSET_OK
    hal.request_phase_offset.assert_called_once_with(1499)


def test_refuses_a_negative_entry():
    """Advance-only. A negative offset does NOT step back by |offset|: the
    forward bias turns it into a forward jog of pitch-|offset| (els_phase.h
    T5), so a signed control would misrepresent what it does."""
    fsm, hal = _fsm(total_steps=800)
    assert fsm.apply_phase_offset(-0.1) == ElsFsm.PHASE_OFFSET_NEGATIVE
    hal.request_phase_offset.assert_not_called()


def test_refuses_when_turning():
    """Turning is sent threadPitchSteps = 0 — there is no thread phase to
    shift, so the register would be written and never read."""
    fsm, hal = _fsm(is_threading=False)
    assert fsm.apply_phase_offset(0.5) == ElsFsm.PHASE_OFFSET_NO_PITCH
    hal.request_phase_offset.assert_not_called()


def test_refuses_when_the_pitch_is_zero():
    fsm, hal = _fsm(pitch_num=0, pitch_den=1)
    assert fsm.apply_phase_offset(0.5) == ElsFsm.PHASE_OFFSET_NO_PITCH
    hal.request_phase_offset.assert_not_called()


def test_refuses_when_geometry_is_missing():
    fsm, hal = _fsm(ratio_num=0)
    assert fsm.apply_phase_offset(0.5) in (ElsFsm.PHASE_OFFSET_NO_GEOMETRY,
                                           ElsFsm.PHASE_OFFSET_NO_PITCH)
    hal.request_phase_offset.assert_not_called()


def test_refuses_outside_a_job():
    """The firmware consumes the command WITHOUT acking when enable == 0, and
    an absent ack is indistinguishable from a dropped frame. Refusing here is
    what turns silence into a sentence on screen."""
    fsm, hal = _fsm(enabled=False)
    assert fsm.apply_phase_offset(0.5) == ElsFsm.PHASE_OFFSET_NO_JOB
    hal.request_phase_offset.assert_not_called()


def test_refuses_when_offline():
    fsm, hal = _fsm(connected=False)
    assert fsm.apply_phase_offset(0.5) == ElsFsm.PHASE_OFFSET_OFFLINE
    hal.request_phase_offset.assert_not_called()


# ─── clear ─────────────────────────────────────────────────────────────────

def test_clear_is_an_apply_of_zero():
    fsm, hal = _fsm(total_steps=750)
    assert fsm.clear_phase_offset() == ElsFsm.PHASE_OFFSET_OK
    hal.request_phase_offset.assert_called_once_with(0)


def test_clear_outside_a_job_refuses_rather_than_lying():
    """The firmware would swallow it unacked and the stale total would stay on
    screen with no explanation."""
    fsm, hal = _fsm(total_steps=750, enabled=False)
    assert fsm.clear_phase_offset() == ElsFsm.PHASE_OFFSET_NO_JOB
    hal.request_phase_offset.assert_not_called()


# ─── the readout ───────────────────────────────────────────────────────────

def test_display_gives_distance_and_fraction_of_pitch():
    """Both, because they answer different questions: the distance is checkable
    against a dial, the fraction is what says 'start 2 of 2'."""
    fsm, _ = _fsm(total_steps=750)
    distance, fraction = fsm.phase_offset_display()
    assert distance == pytest.approx(0.75)
    assert fraction == pytest.approx(0.5)


def test_display_in_inches_tracks_the_display_factor():
    fsm, _ = _fsm(total_steps=1000, factor=Fraction(10, 254))
    distance, _ = fsm.phase_offset_display()
    assert distance == pytest.approx(1.0 / 25.4)


def test_display_is_zero_without_geometry_rather_than_raising():
    """This is read from a Kivy clock callback; an exception here takes the
    update loop with it."""
    fsm, _ = _fsm(total_steps=750, ratio_num=0)
    assert fsm.phase_offset_display() == (0.0, 0.0)


def test_survives_an_unmapped_spindle_axis():
    """get_spindle_axis() returns None until an axis is mapped, and the pitch
    calculation reaches straight through it. Found by building the operator
    popup against this API: the readout polls on a 10 Hz clock, so this raising
    would not surface as an error message — it would stop the update loop."""
    fsm, _ = _fsm(total_steps=750)
    fsm.els.get_spindle_axis.return_value = None
    assert fsm.thread_pitch_steps() == 0.0
    assert fsm.apply_phase_offset(0.5) == ElsFsm.PHASE_OFFSET_NO_PITCH

    # The DISTANCE half survives, and should: converting steps to millimetres
    # needs the servo gearing and the display factor, neither of which involves
    # the spindle. Only the fraction-of-a-pitch half is unknowable, and it
    # reports 0.0 rather than inventing a denominator.
    distance, fraction = fsm.phase_offset_display()
    assert distance == pytest.approx(0.75)
    assert fraction == 0.0


# ─── the public conversion, so callers never rebuild the arithmetic ────────

def test_steps_to_display_inverts_the_entry_conversion():
    """A caller asking for 'half a pitch as a distance' must land on the same
    number that, typed back in, produces those steps. A second copy of this
    arithmetic elsewhere is free to drift from the one the firmware is fed."""
    fsm, _ = _fsm()
    assert fsm.steps_to_display(750) == pytest.approx(0.75)


def test_pitch_display_matches_the_refusal_bound():
    """The pitch as a DISTANCE and the at-one-pitch refusal must agree about
    what a pitch is.

    It had a production caller until 2026-08-23 — the fill-from-a-fraction
    buttons, removed with the multi-start framing — and this test named it.
    Kept because the agreement is the property, not the caller: anything that
    ever names the bound on screen has to name the same one the FSM enforces,
    and a half-pitch entry has to be accepted by that enforcement."""
    fsm, _ = _fsm()
    assert fsm.pitch_display() == pytest.approx(1.5)
    half = fsm.pitch_display() / 2
    assert fsm.apply_phase_offset(half) == ElsFsm.PHASE_OFFSET_OK


def test_steps_to_display_is_zero_without_geometry():
    fsm, _ = _fsm(ratio_num=0)
    assert fsm.steps_to_display(750) == 0.0
    assert fsm.pitch_display() == 0.0


# ─── HAL: the write ORDER is the lock-free property ────────────────────────
# Lives here rather than with the other HAL tests because it is only
# meaningful alongside the reason it exists. The 32-bit Pending crosses a
# 16-bit register bus as two writes; the ISR reads it ONLY under a nonzero
# Command. Write Command first and there is a window where the ISR applies
# half of one number and half of another — a phase offset of garbage, applied
# to a live thread, with a perfectly normal-looking ack.

class _RecordingBlock(dict):
    def __init__(self, initial, log):
        super().__init__(initial)
        self._log = log

    def __setitem__(self, key, value):
        self._log.append((key, value))
        super().__setitem__(key, value)


def _recording_hal():
    writes = []
    block = _RecordingBlock({'phaseOffsetCommand': 0, 'phaseOffsetSeq': 0,
                             'phaseOffsetPending': 0, 'phaseOffsetSteps': 0},
                            writes)
    board = MagicMock()
    board.connected = True
    board.device.__getitem__.side_effect = lambda key: block
    return ElsStopHal(board), writes


def test_pending_is_written_before_the_command():
    hal, writes = _recording_hal()
    hal.request_phase_offset(1234)
    keys = [k for k, _ in writes]
    assert keys.index('phaseOffsetPending') < keys.index('phaseOffsetCommand'), (
        "Command must be written LAST — it is what makes the pair atomic")
    assert dict(writes)['phaseOffsetPending'] == 1234
    assert dict(writes)['phaseOffsetCommand'] == 1


def test_request_writes_nothing_while_disconnected():
    hal, writes = _recording_hal()
    hal._board.connected = False
    hal.request_phase_offset(1234)
    assert writes == []


# ─── fabricated reads ──────────────────────────────────────────────────────
# There used to be four tests here, guarding a read of the running total that
# apply_phase_offset made in order to accumulate onto it. A failed frame
# returned 0, so the new total collapsed to just the entry -- every earlier
# step-over discarded, under a green "Applied".
#
# SET semantics removed the read, and with it the exposure: the value written
# depends only on what the operator typed. test_apply_never_reads_the_current
# _total above pins that the read is gone, which is a stronger guarantee than
# guarding it was. The equivalent hazard on the ACK path is still real and is
# covered in tests/fsms/test_els_resync.py and the popup tests.
