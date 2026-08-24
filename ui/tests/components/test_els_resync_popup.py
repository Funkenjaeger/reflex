"""Tests for the pick-up-existing-thread wizard's operator surface
(reflex/components/home/els_resync_popup.py).

The popup is built for real here — Kivy properties and all — with
``apply_class_lang_rules`` patched out, the same way
tests/components/test_els_phase_offset_popup.py builds PhaseOffsetPopup and
test_custom_popup.py builds CustomPopup. The kv rule tree pulls in real child
widgets and textures the mock GL backend cannot service; the Python under test
is the part that decides how loud the screen is.

WHAT THESE ARE GUARDING
-----------------------
A SEVERITY INVERSION, found 2026-08-23 by rendering the wizard's six states
side by side. Every state's body was hardcoded ``color: app.theme.text``, so:

  * RED_FLAG — a Z-chain custody fault whose own message says "Do not cut" —
    arrived in exactly the same neutral grey as the routine jog instructions
    and as the "reference latched" success message; while
  * the phase-offset modal, two taps away, rendered a mere "engage the ELS stop
    first" in alarm red.

An operator who learns that this screen's red means "you pressed that too
early" has learned the wrong lesson about the one screen that tells them the
machine is lying about where the carriage is. The severity is derived in
Python rather than spelled out in the kv precisely so it can be enumerated and
pinned here: a new state that forgets to say how bad it is fails a test instead
of silently inheriting "routine".

The COLOURS themselves are theme values and can only be checked against real
pixels, which previews/preview_walkthrough_shots.py does (checks tagged D4).
What is checkable without a GL context is the mapping: which severity each
state gets, that the ladder has no two rungs collapsed onto one another, and
that the states an operator can be left sitting on name themselves.
"""
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import reflex.components.home.els_resync_popup as popup_mod
from reflex.components.home.els_resync_popup import (
    FALLBACK_SEVERITY, SEVERITY_FAULT, SEVERITY_INFO, STATE_SEVERITY,
    ThreadResyncPopup,
)

MODULE_DIR = Path(popup_mod.__file__).parent
RESYNC_KV = MODULE_DIR / "els_resync_popup.kv"
RESYNC_PY = MODULE_DIR / "els_resync_popup.py"


@pytest.fixture
def popup(running_app):
    with patch.object(ThreadResyncPopup, "apply_class_lang_rules"):
        return ThreadResyncPopup()


# ── the severity table itself ────────────────────────────────────────
def _states_named_in_source():
    """Every state literal the wizard's own py and kv mention.

    Deliberately scraped rather than listed: a list here would be a second copy
    of the table under test, and the failure this guards against is precisely
    somebody adding a state in one place and not the other. The kv is
    exhaustive on its own — every state needs a button rule — and the py adds
    the ones only it assigns.
    """
    text = RESYNC_KV.read_text(encoding="utf-8") + RESYNC_PY.read_text(encoding="utf-8")
    found = set(re.findall(r'state\s*(?:==|!=|=)\s*"(\w+)"', text))
    for group in re.findall(r'state\s+(?:not\s+in|in)\s+\(([^)]*)\)', text):
        found.update(re.findall(r'"(\w+)"', group))
    return found


def test_every_state_the_wizard_can_reach_has_a_severity():
    """Guard the guard. A state with no entry falls through to the fallback,
    which is deliberately the LOUDEST severity — so the miss would not be
    visible on screen as a missing colour, only as a wizard that shouts on a
    routine step. Caught here instead."""
    named = _states_named_in_source()
    assert named, "no state literals found — has the wizard changed shape?"
    missing = sorted(named - set(STATE_SEVERITY))
    assert not missing, f"states with no severity: {missing}"


def test_the_severity_table_has_no_states_the_wizard_cannot_reach():
    stale = sorted(set(STATE_SEVERITY) - _states_named_in_source())
    assert not stale, f"severity entries for states nothing reaches: {stale}"


# ── the ladder ───────────────────────────────────────────────────────
def test_a_custody_fault_does_not_read_as_a_routine_instruction(popup):
    """The defect, stated as a test: red_flag rendered exactly like jog."""
    popup.state = "jog"
    routine = popup.severity
    popup.state = "red_flag"
    assert popup.severity != routine
    assert popup.severity == SEVERITY_FAULT


def test_red_flag_does_not_look_like_latched(popup):
    """Opposite ends of the ladder — "the machine is lying about the carriage
    position" and "you are good to cut" — shared one colour."""
    popup.state = "latched"
    good = popup.severity
    popup.state = "red_flag"
    assert popup.severity != good


def test_red_flag_outranks_an_ordinary_refusal(popup):
    """Both are danger-coloured, consistently with the phase-offset modal. That
    makes the SEVERITY the only thing separating "the button did not take" from
    "stop and go look at the drivetrain", and the kv hangs the filled banner
    off it — so if these two ever collapse to one value, a machine fault starts
    rendering as a mistimed button press."""
    popup.state = "refused"
    refused = popup.severity
    popup.state = "red_flag"
    assert popup.severity != refused


def test_the_ladder_has_no_two_rungs_collapsed(popup):
    severities = {state: sev for state, (sev, _cap) in STATE_SEVERITY.items()}
    assert severities["jog"] == severities["align"], (
        "the two walkthrough steps are the same kind of screen")
    distinct = {severities[s]
                for s in ("align", "drifted", "latched", "refused", "red_flag")}
    assert len(distinct) == 5, (
        f"two states share a severity and cannot be told apart: {severities}")


# ── captions ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("state", ["drifted", "latched", "refused", "red_flag"])
def test_every_state_the_operator_is_left_on_names_itself(popup, state):
    """Colour is not enough at arm's length, and it is not enough at all for
    the two states that share one. Each terminal or act-now state says in words
    which it is."""
    popup.state = state
    assert popup.severity_caption, f"{state} arrives with no caption"
    assert popup.severity_caption == popup.severity_caption.strip()


def test_the_red_flag_caption_says_not_to_cut(popup):
    popup.state = "red_flag"
    assert "DO NOT CUT" in popup.severity_caption.upper()


@pytest.mark.parametrize("state", ["jog", "align"])
def test_the_walkthrough_steps_are_not_banner_ed(popup, state):
    """A banner on every screen is a banner on none of them."""
    popup.state = state
    assert popup.severity_caption == ""
    assert popup.severity == SEVERITY_INFO


# ── the defaults trap ────────────────────────────────────────────────
def test_the_opening_state_is_classified_without_a_state_change(popup):
    """Kivy does not dispatch on_state for a property's DEFAULT value, so a
    wizard that only classified on change would open holding whatever the
    declared defaults happened to be. They agree today; this is what keeps them
    agreeing if either side is edited."""
    assert popup.state == "jog"
    assert (popup.severity, popup.severity_caption) == STATE_SEVERITY["jog"]


def test_an_unmapped_state_is_coerced_up_not_ignored(popup):
    """Same rule reflex/utils/notices.py applies to an unknown notice severity:
    over-warning is noise, under-warning is a message the operator learns to
    ignore. Raising instead would take the app down at the lathe."""
    popup.state = "some_future_state"
    assert (popup.severity, popup.severity_caption) == FALLBACK_SEVERITY
    assert popup.severity == SEVERITY_FAULT
    assert popup.severity_caption


# ── the live readout ─────────────────────────────────────────────────
class _StubResync:
    """The two numbers the align state puts on screen, and nothing else."""

    def __init__(self, state, delta=0, still=True):
        self._state = state
        self.z_delta_counts = delta
        self.spindle_still = still
        self.stillness_fraction = 1.0 if still else 0.45
        self.tolerance_counts = 3
        self.confirm_allowed = still

    def poll(self):
        return self._state


def test_the_live_readout_is_pre_broken_into_lines(popup):
    """It is rendered a fifth larger than the body prose now, and one line of
    it does not fit this modal's width — so where it breaks would otherwise be
    decided by how long the spindle word happens to be ("still" vs
    "settling 45%"), i.e. the readout would reflow while the operator watched
    it."""
    from reflex.fsms.els_resync import ResyncState

    popup._resync = _StubResync(ResyncState.ALIGNING)
    popup._tick(0.0)
    lines = popup.live_text.splitlines()
    assert len(lines) == 2, f"live readout is not two lines: {popup.live_text!r}"
    assert "Z hold" in lines[0] and "tolerance" in lines[0]
    assert "Spindle" in lines[1]

    popup._resync = _StubResync(ResyncState.DRIFTED, delta=11)
    popup._tick(0.0)
    lines = popup.live_text.splitlines()
    assert len(lines) == 2, f"drift readout is not two lines: {popup.live_text!r}"
    assert "+11" in lines[0]
    assert "Tolerance" in lines[1]


def test_the_live_readout_is_cleared_on_every_terminal_state(popup):
    """It is a live watch; leaving the last sample under a terminal message
    would read as a number that is still updating."""
    from reflex.fsms.els_resync import ResyncState

    popup._resync = _StubResync(ResyncState.ALIGNING)
    popup._tick(0.0)
    assert popup.live_text

    popup._resync = _StubResync(ResyncState.RED_FLAG)
    popup._resync.message = "custody fault"
    popup._tick(0.0)
    assert popup.state == "red_flag"
    assert popup.live_text == ""


# ── an existing reference asks, and asking is not refusing ───────────
# begin_alignment() returns False for BOTH "refused" and "please confirm", so
# the popup has to read the controller's state to tell them apart. Getting that
# wrong renders a question as a dead end -- which is the exact failure this
# change set out to remove (2026-08-24 bench feedback 4.5).

class _StubGate:
    """A resync controller that answers begin_alignment and nothing else."""

    def __init__(self, state, message="a message long enough to be a sentence."):
        self.state = state
        self.message = message
        self.force_calls = []

    def begin_alignment(self, force=False):
        self.force_calls.append(force)
        return False

    def cancel(self):
        pass


def test_an_existing_reference_renders_as_a_question(popup):
    from reflex.fsms.els_resync import ResyncState
    popup._resync = _StubGate(ResyncState.CONFIRM_OVERWRITE)

    popup.begin()

    assert popup.state == "confirm_overwrite"


def test_an_existing_reference_does_not_render_as_a_refusal(popup):
    """The distinction the operator acts on: a refusal means the button did
    not take and there is nowhere to go from here; a question has a button."""
    from reflex.fsms.els_resync import ResyncState
    popup._resync = _StubGate(ResyncState.CONFIRM_OVERWRITE)

    popup.begin()

    assert popup.state != "refused"


def test_the_question_carries_the_controllers_own_words(popup):
    from reflex.fsms.els_resync import ResyncState
    popup._resync = _StubGate(
        ResyncState.CONFIRM_OVERWRITE,
        message="Overwriting re-anchors every remaining pass.")

    popup.begin()

    assert popup.body_text == "Overwriting re-anchors every remaining pass."


def test_a_real_refusal_still_renders_as_a_refusal(popup):
    """The failure mode of the new branch is swallowing every refusal into the
    question, which would put an Overwrite button on a disconnected link."""
    from reflex.fsms.els_resync import ResyncState
    popup._resync = _StubGate(ResyncState.REFUSED)

    popup.begin()

    assert popup.state == "refused"


def test_begin_does_not_force(popup):
    """Entering the wizard must never overwrite silently -- force is only ever
    the answer to the question, never the way in."""
    from reflex.fsms.els_resync import ResyncState
    stub = _StubGate(ResyncState.CONFIRM_OVERWRITE)
    popup._resync = stub

    popup.begin()

    assert stub.force_calls == [False]


def test_overwrite_forces(popup):
    from reflex.fsms.els_resync import ResyncState
    stub = _StubGate(ResyncState.CONFIRM_OVERWRITE)
    popup._resync = stub

    popup.overwrite()

    assert stub.force_calls == [True]


def test_the_question_does_not_shout_do_not_cut(popup):
    """It is a caution, not a fault. An unmapped state falls through to the
    fallback severity, which is the loudest one -- so a missing entry here
    would show up as a wizard shouting DO NOT CUT at a routine confirmation,
    and the operator learns to ignore the loudest thing on the screen."""
    popup.state = "confirm_overwrite"

    assert popup.severity != SEVERITY_FAULT
    assert popup.severity_caption != FALLBACK_SEVERITY[1]
