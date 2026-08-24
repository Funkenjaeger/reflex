"""Tests for ThreadResync (the manual reference latch run controller).

Exercised against a fake HAL that models the firmware's ACTUAL ack semantics:
``latchCommand`` is cleared the instant it is consumed, ``latchSeq`` increments
only when the firmware ACCEPTS the latch (enable == 1), and a refused latch
produces NO seq edge at all — the absent ack IS the refusal. A fake that acked
unconditionally would let a controller pass here and hang against real
firmware.

The properties under test are the ones that make the feature safe rather than
merely present:

  - Z drift beyond tolerance must interrupt the procedure, and a re-seat that
    does not return to the baseline must be a terminal RED FLAG — widening or
    skipping that check conceals the exact fault class (Z-chain custody loss)
    that corrupts every other ELS operation;
  - Confirm must be gated on a spindle stillness dwell, and any spindle motion
    must reset the dwell;
  - the latch request must re-verify Z at the moment of the button press
    (TOCTOU), not trust the last poll;
  - the firmware's latched Z must agree with the baseline this controller
    watched — the register readback is the cross-check that host and firmware
    share a coherent view of the axis.

Mutation-tested 2026-08-08 (each mutation applied to els_resync.py, observed,
reverted):
  M1 _z_ok() -> True                         -> 4 failures (drift, re-seat x2, TOCTOU)
  M2 confirm_allowed ignores spindle_still   -> 3 failures (happy path's pre-dwell
                                                gate, dwell-reset, re-seat regate)
  M3 request_latch drops the Z re-check      -> 1 failure  (TOCTOU)
  M4 _poll_latch_ack drops the Z cross-check -> 1 failure  (firmware-disagrees)
"""
from types import SimpleNamespace

from reflex.fsms.els_resync import ResyncState, ThreadResync, z_held
from reflex.utils.devices import ELS_PROTOCOL_VERSION


class Machine:
    """The physical truth the fake HAL and the injected readers both see."""

    def __init__(self, z=5000, spindle=200):
        self.z = z
        self.spindle = spindle


class FakeHal:
    """Models the latch command/ack contract, including its traps."""

    def __init__(self, machine, connected=True,
                 protocol_version=ELS_PROTOCOL_VERSION,
                 enable=True, reference_latched=False,
                 latch_z_skew=0, ack_reference_latched=True):
        self.machine = machine
        self.connected = connected
        self._protocol_version = protocol_version
        self._enable = enable
        self._reference_latched = reference_latched
        # Deliberate capture skew, for the host/firmware-disagreement case.
        self._latch_z_skew = latch_z_skew
        self._ack_reference_latched = ack_reference_latched

        # Fabricated-read accounting, modelled rather than mocked: a stub that
        # answered reads_fabricated_since() with a truthy Mock would make every
        # guarded poll discard itself, and these tests would pass for the wrong
        # reason.
        self.read_failures = 0
        # When set, every read below answers 0 AND counts itself -- exactly
        # what production does behind a failed frame or a dropped link
        # (communication.py returns 0 and increments; ElsStopHal._no_link does
        # the same when there is no link at all). Modelled at the moment of the
        # read rather than as a counter bumped beforehand, because the guard
        # samples its baseline at the top of the poll: a fake that incremented
        # early would leave the counter still DURING the reads and the guard
        # would never trip -- which is exactly how the first version of these
        # tests passed against an unguarded poller.
        self.link_broken = False

        self.latch_command = 0
        self.latch_seq = 4      # non-zero baseline: a machine that has latched before
        self.latched_z = 0
        self.latched_spindle = 0

    def reads_baseline(self):
        return self.read_failures

    def reads_fabricated_since(self, baseline):
        return self.read_failures != baseline

    def break_link(self):
        """From here every read fabricates a zero and counts itself."""
        self.link_broken = True

    def _fabricate(self, real):
        if self.link_broken:
            self.read_failures += 1
            return 0
        return real

    def read_protocol_version(self):
        return self._protocol_version

    def read_enable(self):
        return self._enable

    def read_reference_latched(self):
        return self._reference_latched

    def request_latch(self):
        # Consumed and cleared in one ISR pass. Only an ENABLED job acks;
        # a disabled one clears the command and does nothing else.
        self.latch_command = 0
        if self._enable:
            self.latched_z = self.machine.z + self._latch_z_skew
            self.latched_spindle = self.machine.spindle
            self._reference_latched = self._ack_reference_latched
            self.latch_seq += 1

    def read_latch_seq(self):
        return self._fabricate(self.latch_seq)

    def read_latched_z(self):
        return self._fabricate(self.latched_z)

    def read_latched_spindle(self):
        return self._fabricate(self.latched_spindle)


def _els(tol=3):
    return SimpleNamespace(els_resync_z_tol_counts=tol)


def _controller(machine=None, hal=None, tol=3):
    machine = machine or Machine()
    hal = hal or FakeHal(machine)
    rc = ThreadResync(hal, _els(tol),
                      read_z_counts=lambda: machine.z,
                      read_spindle_counts=lambda: machine.spindle)
    return rc, machine, hal


def _dwell(rc, polls=None):
    """Poll long enough for the spindle stillness dwell to arm Confirm."""
    for _ in range(polls or rc.SPINDLE_STILL_POLLS):
        rc.poll()


# ── pure policy ──────────────────────────────────────────────────────

def test_z_held_boundaries():
    assert z_held(100, 103, 3)
    assert z_held(100, 97, 3)
    assert not z_held(100, 104, 3)
    assert not z_held(100, 96, 3)


# ── begin_alignment refusals ─────────────────────────────────────────

def test_refuses_when_disconnected():
    machine = Machine()
    rc, _, _ = _controller(machine, FakeHal(machine, connected=False))
    assert not rc.begin_alignment()
    assert rc.state == ResyncState.REFUSED


def test_the_disconnected_refusal_is_a_sentence_with_a_next_step():
    """It was four words — "Not connected to the controller." — while its five
    siblings on this same surface are sentences that say what to do, and the
    phase-offset modal explains the IDENTICAL condition in two. A refusal that
    only names the state leaves the operator at the machine with nothing to act
    on, which is the failure mode every other message here already avoids.
    """
    machine = Machine()
    rc, _, _ = _controller(machine, FakeHal(machine, connected=False))
    rc.begin_alignment()
    assert len(rc.message.split()) > 8, "the refusal is a label, not an explanation"
    assert rc.message.rstrip().endswith("."), "the refusal is not a sentence"
    assert "reconnect" in rc.message.lower(), (
        "the refusal does not name the next step")


def test_every_begin_alignment_refusal_explains_itself():
    """Guard the guard: every refusal this entry point can produce, all through
    the production call, all held to the same shape. A new refusal added as a
    bare label fails here instead of reaching the lathe."""
    cases = {
        "disconnected": dict(connected=False),
        "old firmware": dict(protocol_version=0),
        "no job armed": dict(enable=False),
        # "already referenced" is NOT here any more. It stopped being a
        # refusal on 2026-08-24 and became a question with an Overwrite
        # button -- see test_asks_before_overwriting_an_existing_reference,
        # which holds its message to this same shape.
    }
    for label, kwargs in cases.items():
        machine = Machine()
        rc, _, _ = _controller(machine, FakeHal(machine, **kwargs))
        assert not rc.begin_alignment(), label
        assert rc.state == ResyncState.REFUSED, label
        assert len(rc.message.split()) > 8, f"{label}: refusal is not an explanation"
        assert rc.message.rstrip().endswith("."), f"{label}: refusal is not a sentence"


def test_refuses_old_firmware_by_name():
    """A version-0 readback means firmware predating the latch registers; the
    message must blame the firmware, not the link (the command write would
    land nowhere and the ack could never come)."""
    machine = Machine()
    rc, _, _ = _controller(machine, FakeHal(machine, protocol_version=0))
    assert not rc.begin_alignment()
    assert rc.state == ResyncState.REFUSED
    assert "firmware" in rc.message.lower()


def test_refuses_without_an_armed_job():
    machine = Machine()
    rc, _, _ = _controller(machine, FakeHal(machine, enable=False))
    assert not rc.begin_alignment()
    assert rc.state == ResyncState.REFUSED


# ── an existing reference is a question, not a wall ──────────────────
# Fresh-job-only is HOST policy -- the firmware would happily overwrite. Until
# 2026-08-24 the policy was enforced as a flat refusal telling the operator to
# disengage and re-engage, which from inside this wizard meant leaving two
# modals, crossing to the ELS screen, cycling engage, re-enabling sync and
# navigating back in. The concern is real; the maze was the wrong answer to it.

def test_asks_before_overwriting_an_existing_reference():
    machine = Machine()
    rc, _, _ = _controller(machine, FakeHal(machine, reference_latched=True))

    assert not rc.begin_alignment()
    assert rc.state == ResyncState.CONFIRM_OVERWRITE
    assert rc.state != ResyncState.REFUSED


def test_the_overwrite_question_says_what_overwriting_costs():
    """Held to the same shape as every refusal on this surface: a sentence
    that names the consequence, not a label. The operator is being asked to
    authorise something that silently re-anchors passes already cut."""
    machine = Machine()
    rc, _, _ = _controller(machine, FakeHal(machine, reference_latched=True))

    rc.begin_alignment()

    assert len(rc.message.split()) > 8, "the question is a label, not an explanation"
    assert rc.message.rstrip().endswith("."), "the question is not a sentence"
    assert "remaining pass" in rc.message.lower(), (
        "the question does not say what overwriting costs")


def test_nothing_is_latched_merely_by_asking(machine=None):
    """The question must not be a side effect. Answering it is."""
    machine = Machine()
    rc, _, hal = _controller(machine, FakeHal(machine, reference_latched=True))

    rc.begin_alignment()

    assert rc.state == ResyncState.CONFIRM_OVERWRITE
    assert hal.latch_command == 0
    assert hal.latch_seq == 4        # the untouched baseline


def test_forcing_proceeds_past_an_existing_reference():
    """The answer coming back. force=True is reachable only from the
    Overwrite button, which only exists in the CONFIRM_OVERWRITE state."""
    machine = Machine()
    rc, _, _ = _controller(machine, FakeHal(machine, reference_latched=True))

    assert rc.begin_alignment(force=True)
    assert rc.state == ResyncState.ALIGNING


def test_forcing_does_not_bypass_the_other_gates():
    """force answers ONE question. A disconnected controller, old firmware or
    an unarmed job must still refuse -- otherwise the Overwrite button becomes
    a way to skip every check on the way in."""
    for label, kwargs in (
        ("disconnected", dict(connected=False)),
        ("old firmware", dict(protocol_version=0)),
        ("no job armed", dict(enable=False)),
    ):
        machine = Machine()
        rc, _, _ = _controller(
            machine, FakeHal(machine, reference_latched=True, **kwargs))

        assert not rc.begin_alignment(force=True), label
        assert rc.state == ResyncState.REFUSED, label


# ── happy path ───────────────────────────────────────────────────────

def test_full_procedure_latches():
    rc, machine, hal = _controller()
    assert rc.begin_alignment()
    assert rc.state == ResyncState.ALIGNING
    assert not rc.confirm_allowed          # dwell not yet accumulated

    _dwell(rc)
    assert rc.confirm_allowed

    assert rc.request_latch()
    assert rc.state == ResyncState.LATCH_REQUESTED
    rc.poll()                              # ack lands on the next poll
    assert rc.state == ResyncState.LATCHED
    assert rc.latched_z == machine.z
    assert rc.latched_spindle == machine.spindle
    assert hal.latch_command == 0          # never left pending


def test_spindle_motion_resets_the_dwell():
    rc, machine, _ = _controller()
    rc.begin_alignment()
    _dwell(rc, rc.SPINDLE_STILL_POLLS - 1)
    machine.spindle += 10                  # operator's hand still on the wheel
    rc.poll()
    assert not rc.confirm_allowed          # motion reset the dwell to zero
    _dwell(rc, rc.SPINDLE_STILL_POLLS - 1)
    assert not rc.confirm_allowed
    rc.poll()
    assert rc.confirm_allowed


# ── drift and re-seat ────────────────────────────────────────────────

def test_drift_interrupts_alignment():
    rc, machine, _ = _controller(tol=3)
    rc.begin_alignment()
    _dwell(rc)
    machine.z += 4                         # one past tolerance
    assert rc.poll() == ResyncState.DRIFTED
    assert not rc.confirm_allowed
    assert not rc.request_latch()


def test_reseat_within_tolerance_resumes_and_regates():
    rc, machine, _ = _controller(tol=3)
    rc.begin_alignment()
    _dwell(rc)
    machine.z += 10
    rc.poll()
    machine.z -= 10                        # operator nudged back to the flank
    assert rc.reseat_check()
    assert rc.state == ResyncState.ALIGNING
    # The stillness dwell must re-accumulate after an interruption — a stale
    # dwell from before the drift must not leave Confirm already armed.
    assert not rc.confirm_allowed


def test_reseat_missing_baseline_is_a_terminal_red_flag():
    rc, machine, _ = _controller(tol=3)
    rc.begin_alignment()
    _dwell(rc)
    machine.z += 10
    rc.poll()
    machine.z -= 5                         # "re-seated" 5 counts off — custody lost
    assert not rc.reseat_check()
    assert rc.state == ResyncState.RED_FLAG
    assert "custody" in rc.message.lower() or "scale" in rc.message.lower()
    # No retry path: a repeat press must not un-flag it.
    assert not rc.reseat_check()
    assert rc.state == ResyncState.RED_FLAG


def test_request_latch_recheck_catches_drift_at_the_button_press():
    """TOCTOU: drift arriving between the last poll and the press must refuse,
    not latch a position the operator is no longer looking at."""
    rc, machine, _ = _controller(tol=3)
    rc.begin_alignment()
    _dwell(rc)
    assert rc.confirm_allowed
    machine.z += 4                         # drifts AFTER the last poll
    assert not rc.request_latch()
    assert rc.state == ResyncState.DRIFTED


# ── latch ack edge cases ─────────────────────────────────────────────

def test_missing_ack_times_out_as_refusal():
    """enable dropped between begin and confirm: the firmware consumes the
    command with NO seq edge, so the only signal is the timeout."""
    rc, machine, hal = _controller()
    rc.begin_alignment()
    _dwell(rc)
    hal._enable = False                    # job disengaged under the wizard
    assert rc.request_latch()
    for _ in range(rc.LATCH_TIMEOUT_POLLS):
        rc.poll()
    assert rc.state == ResyncState.REFUSED


def test_firmware_disagreeing_with_baseline_is_a_red_flag():
    """The ack arrives but the firmware's latched Z is not the Z this
    controller was watching — host and firmware do not share a coherent view
    of the axis, which is the same custody fault class as a bad re-seat."""
    machine = Machine()
    rc, _, _ = _controller(machine, FakeHal(machine, latch_z_skew=7), tol=3)
    rc.begin_alignment()
    _dwell(rc)
    assert rc.request_latch()
    rc.poll()
    assert rc.state == ResyncState.RED_FLAG


def test_ack_without_reference_is_refused():
    machine = Machine()
    rc, _, _ = _controller(machine, FakeHal(machine, ack_reference_latched=False))
    rc.begin_alignment()
    _dwell(rc)
    assert rc.request_latch()
    rc.poll()
    assert rc.state == ResyncState.REFUSED


# ─── a fabricated seq is not an ack ───────────────────────────────────────
# The command/ack contract rests on "the absent ack IS the refusal", which only
# holds if a seq the controller never sent cannot impersonate one. A failed
# frame or a dropped link returns 0 for latchSeq, and 0 differs from any
# nonzero baseline -- so without the guard the wizard announces "Thread
# reference latched" for a latch the firmware refused.

def test_a_fabricated_seq_is_not_taken_for_a_latch_ack():
    rc, machine, hal = _controller()
    rc.begin_alignment()
    _dwell(rc)
    rc.request_latch()
    assert rc.state == ResyncState.LATCH_REQUESTED

    hal.break_link()              # every read from here answers with fiction
    rc.poll()

    assert rc.state == ResyncState.LATCH_REQUESTED, (
        "a fabricated seq was taken for the controller's acknowledgement")
    assert "latched" not in rc.message.lower()


def test_a_fabricated_seq_does_not_raise_a_false_red_flag():
    """The other way it lands: the cross-check reads latchedZ through the same
    door, so fabricated zeros compared against the watched baseline accuse a
    healthy Z scale of losing custody. Sending the operator to inspect wiring
    because of one dropped frame is its own kind of damage."""
    rc, machine, hal = _controller()
    rc.begin_alignment()
    _dwell(rc)
    rc.request_latch()

    hal.break_link()
    rc.poll()

    assert rc.state != ResyncState.RED_FLAG


def test_a_link_that_never_recovers_still_times_out():
    """Discarding polls must not become an infinite wait."""
    rc, machine, hal = _controller()
    rc.begin_alignment()
    _dwell(rc)
    rc.request_latch()

    for _ in range(ThreadResync.LATCH_TIMEOUT_POLLS + 2):
        hal.break_link()
        rc.poll()

    assert rc.state == ResyncState.REFUSED
    assert "nothing was latched" in rc.message.lower()
