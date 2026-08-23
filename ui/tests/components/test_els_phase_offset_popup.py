"""Tests for the thread-phase offset entry surface
(reflex/components/home/els_phase_offset_popup.py).

The popup is built for real here — Kivy properties, kv-facing text and all —
with ``apply_class_lang_rules`` patched out, the same way
tests/components/conftest.py builds ElsAdvancedBar and test_custom_popup.py
builds CustomPopup. The kv rule tree pulls in real child widgets and textures
the mock GL backend cannot service; the Python under test is the part that
decides what the operator is told.

WHAT THESE ARE GUARDING
-----------------------
Two failure modes, both of which are silent on the machine:

1. A refusal rendered as success. The FSM answers with a code, and a screen
   that collapsed those codes into one message — or worse, showed the bare
   code — would send the operator to the wrong fix, or to no fix at all.
2. A write that was never acknowledged rendered as silence. The firmware
   consumes phaseOffsetCommand WITHOUT incrementing phaseOffsetSeq when it will
   not honour it, so "nothing changed" is exactly what a successful apply and a
   dropped one both look like. The seq baseline must be captured BEFORE the
   write, and a missing ack must land as a failure.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import reflex.components.home.els_phase_offset_popup as popup_mod
from reflex.components.home.els_phase_offset_popup import (
    APPLIED_TEXT, CLEARED_TEXT, ENTRY_HINT, MESSAGE_CHAR_BUDGET, NO_ACK_TEXT,
    PhaseOffsetPopup, REFUSAL_TEXT, WAITING_TEXT,
)
from reflex.fsms.els_fsm import ElsFsm

# Real-machine servo scale expressed the way the FSM's private converter
# reports it: leadscrew steps per display unit (1 mm at 127/64000 mm/step).
STEPS_PER_MM = 64000 / 127


@pytest.fixture
def fsm():
    """Stand-in for ElsFsm, answering only what the popup is allowed to ask.

    Everything the popup shows has to come through one of these calls — a test
    that had to teach this mock about servo gearing or pitch arithmetic would
    itself be evidence the popup had started doing that arithmetic locally.
    """
    f = MagicMock(name="els_fsm")
    f.phase_offset_display.return_value = (0.0, 0.0)
    f.phase_offset_seq.return_value = 7
    f.phase_offset_steps.return_value = 0
    f.thread_pitch_steps.return_value = 1.5 * STEPS_PER_MM   # 1.5 mm pitch
    # The PUBLIC steps->display conversion. Given as a side_effect rather than
    # a fixed value so a caller that asks for the wrong number of steps gets
    # the wrong distance back, instead of the right one by construction.
    f.steps_to_display.side_effect = lambda steps: float(steps) / STEPS_PER_MM
    f.pitch_display.side_effect = lambda: float(f.thread_pitch_steps()) / STEPS_PER_MM
    f.apply_phase_offset.return_value = ElsFsm.PHASE_OFFSET_OK
    f.clear_phase_offset.return_value = ElsFsm.PHASE_OFFSET_OK
    return f


@pytest.fixture
def popup(running_app, fsm):
    """A real PhaseOffsetPopup wired to `fsm`, built without its kv rules."""
    running_app.els_uic = SimpleNamespace(els_fsm=fsm)
    running_app.formats = SimpleNamespace(
        current_format="MM", position_format="{:+0.3f}")
    with patch.object(PhaseOffsetPopup, "apply_class_lang_rules"):
        return PhaseOffsetPopup()


# ── the running total ────────────────────────────────────────────────
def test_total_shows_both_the_distance_and_the_fraction(popup, fsm):
    """Distance alone leaves modular arithmetic to be done at the machine;
    fraction alone leaves the entry unverifiable against a dial."""
    fsm.phase_offset_display.return_value = (0.75, 0.5)
    popup._refresh_total()
    assert "0.750" in popup.total_text
    assert "mm" in popup.total_text
    assert "1/2" in popup.total_text
    assert "pitch" in popup.total_text


def test_the_fraction_is_named_by_the_SAME_rule_the_status_strip_uses(popup, fsm):
    """The modal and the advanced-bar strip show the same number, so they must
    describe it the same way — an exact division by name, anything else as the
    raw decimal. Two naming rules on one screen is how they come to disagree,
    and the operator has no way to tell which one is lying.
    """
    from reflex.fsms.ui_controller import phase_offset_fraction_text

    for fraction in (0.5, 1.0 / 3.0, 0.25, 0.2755):
        fsm.phase_offset_display.return_value = (0.1, fraction)
        popup._refresh_total()
        assert phase_offset_fraction_text(fraction) in popup.total_text

    # And concretely, so the shared rule cannot quietly become a no-op:
    fsm.phase_offset_display.return_value = (0.1, 1.0 / 3.0)
    popup._refresh_total()
    assert "1/3" in popup.total_text

    fsm.phase_offset_display.return_value = (0.1, 0.2755)
    popup._refresh_total()
    assert "0.276" in popup.total_text  # not a clean division: say so


def test_total_is_reread_from_the_controller_not_accumulated(popup, fsm):
    """The firmware clears the total on the enable edge, so the screen must
    follow it down — a UI-side running count would not."""
    fsm.phase_offset_display.return_value = (0.75, 0.5)
    popup._refresh_total()
    assert "0.750" in popup.total_text

    fsm.phase_offset_display.return_value = (0.0, 0.0)   # job re-engaged
    popup._tick(0.1)
    assert "0.750" not in popup.total_text
    assert "0.000" in popup.total_text


def test_total_survives_an_unreadable_controller(popup, fsm):
    fsm.phase_offset_display.side_effect = AttributeError("no spindle axis")
    popup._refresh_total()
    assert "unavailable" in popup.total_text


# ── refusals ─────────────────────────────────────────────────────────
def _refusal_codes():
    return [v for k, v in vars(ElsFsm).items()
            if k.startswith("PHASE_OFFSET_") and v != ElsFsm.PHASE_OFFSET_OK]


def test_every_refusal_code_has_its_own_message():
    """Guard the guard: a code added to the FSM with no message here would
    otherwise fall through to the unrecognised-code fallback unnoticed."""
    missing = [c for c in _refusal_codes() if c not in REFUSAL_TEXT]
    assert not missing, f"outcome codes with no operator-facing message: {missing}"


def test_refusal_messages_are_all_distinct():
    texts = [REFUSAL_TEXT[c] for c in _refusal_codes()]
    assert len(set(texts)) == len(texts), (
        "two outcome codes render the same message — the operator cannot tell "
        "which fix applies")


@pytest.mark.parametrize("code", _refusal_codes())
def test_a_refusal_never_renders_as_success_or_as_a_bare_code(popup, fsm, code):
    fsm.apply_phase_offset.return_value = code
    popup.apply()
    assert popup.state == "refused"
    assert not popup.busy
    assert popup.message == REFUSAL_TEXT[code]
    # Not a bare code, and not a code with a word bolted on: an outcome token
    # tells the operator nothing about what to do next. (Checked as "is a
    # sentence" rather than "does not contain the token", because words like
    # 'negative' belong in the explanation of why the sign is refused.)
    assert popup.message.strip() != code
    assert len(popup.message.split()) > 8, "refusal message is not an explanation"
    assert popup.message.rstrip().endswith("."), "refusal message is not a sentence"


# ── message length: what fits on the screen ──────────────────────────
# The message label sits in a scroller, so an over-long message no longer
# renders over the buttons -- it goes below the fold instead, silently. On
# 2026-08-23, measured at the machine's real 1024x600, four of the eight
# messages this modal can show were taller than the viewport, and the part
# hidden was in every case the LAST sentence: the one that says what to do.
# AT_PITCH ended on "...so rather than"; NEGATIVE hid "enter the rest of the
# pitch"; NO_ACK -- which exists precisely because a dropped write is otherwise
# silent -- hid "check the ELS stop is still engaged".
#
# The layout half of the fix is in the kv (the popup sizes itself to its
# content, up to the screen) and is measured in pixels by
# previews/preview_walkthrough_shots.py. This is the other half, and it is here
# because a modal that grows to the screen still has a ceiling: the strings
# need one too, and a character budget is the part of that a unit suite can
# hold. A sentence added to any of these fails here first.

def _message_catalogue():
    """Every string this modal's message label can be asked to render."""
    catalogue = [(f"REFUSAL {code}", text) for code, text in REFUSAL_TEXT.items()]
    catalogue += [
        ("NO_ACK", NO_ACK_TEXT),
        ("CLEARED", CLEARED_TEXT),
        ("APPLIED", APPLIED_TEXT),
        ("ENTRY_HINT", ENTRY_HINT),
        ("WAITING", WAITING_TEXT),
    ]
    return catalogue


@pytest.mark.parametrize("name,text", _message_catalogue(),
                         ids=[n for n, _ in _message_catalogue()])
def test_no_message_outgrows_the_space_the_screen_can_give_it(name, text):
    lines = len(text) / popup_mod.MESSAGE_WRAP_CHARS
    assert len(text) <= MESSAGE_CHAR_BUDGET, (
        f"{name} is {len(text)} chars (~{lines:0.1f} rendered lines) against a "
        f"{MESSAGE_CHAR_BUDGET}-char budget. The popup can only grow to the "
        f"machine's 600 px screen, so past this the tail of the message goes "
        f"below the fold -- and the tail is the instruction.")


ACTION_WORDS = ("enter", "engage", "check", "choose", "reconnect", "press",
                "flash", "clear", "try again")


@pytest.mark.parametrize("code", _refusal_codes())
def test_a_refusal_ends_on_the_thing_to_do(code):
    """The last sentence is the one at risk of falling below the fold, so it is
    the one that has to be worth reading: every refusal here closes on an
    action, not on more explanation. Held separately from the budget because
    the two fail differently -- the budget catches a message that grew, this
    catches an instruction that moved into the middle and left explanation
    trailing after it."""
    text = REFUSAL_TEXT[code]
    tail = [s for s in text.split(". ") if s.strip()][-1].lower()
    assert any(word in tail for word in ACTION_WORDS), (
        f"{code}: the message ends on {tail!r}, which tells the operator "
        f"nothing to do. Put the instruction last -- it is the part the "
        f"screen runs out of room for.")


def test_the_no_ack_message_keeps_its_instruction(popup, fsm):
    """NO_ACK exists because a dropped write is otherwise indistinguishable
    from a successful one. It reached the screen with "check the ELS stop is
    still engaged" below the fold, i.e. the one message whose whole purpose is
    to break a silence was itself half-silent."""
    fsm.phase_offset_seq.return_value = 7          # never acks
    popup.entry = 0.75
    popup.apply()
    for _ in range(PhaseOffsetPopup.ACK_TIMEOUT_POLLS):
        popup._tick(0.1)
    assert popup.message == NO_ACK_TEXT
    assert "ELS stop is still engaged" in popup.message
    assert len(NO_ACK_TEXT) <= MESSAGE_CHAR_BUDGET


def test_an_unknown_outcome_code_still_reads_as_english(popup, fsm):
    """This screen can be older than the FSM it drives. An unrecognised code
    must still say plainly that nothing was applied."""
    fsm.apply_phase_offset.return_value = "some_future_code"
    popup.apply()
    assert popup.state == "refused"
    assert "Nothing was" in popup.message or "nothing was" in popup.message
    assert popup.message.strip() != "some_future_code"


def test_a_negative_entry_is_passed_to_the_fsm_not_corrected_locally(popup, fsm):
    """Advance-only is the FSM's rule to enforce. The popup must not quietly
    take an absolute value — that would apply a phase shift the operator did
    not ask for instead of explaining why the sign does not mean what it looks
    like it means."""
    fsm.apply_phase_offset.return_value = ElsFsm.PHASE_OFFSET_NEGATIVE
    popup.entry = -0.5
    popup.apply()
    fsm.apply_phase_offset.assert_called_once_with(-0.5)
    assert popup.state == "refused"


# ── ack edge detection ───────────────────────────────────────────────
def test_apply_waits_for_the_ack_before_claiming_anything(popup):
    popup.entry = 0.75
    popup.apply()
    assert popup.state == "waiting"
    assert popup.busy, "controls must be locked while an ack is in flight"


def test_the_seq_baseline_is_captured_before_the_write(popup, fsm):
    """The FSM call IS the write. A baseline read afterwards would already
    contain the increment being waited for, turning every apply — including one
    the firmware silently dropped — into an instant false success."""
    seq = {"value": 7}
    fsm.phase_offset_seq.side_effect = lambda: seq["value"]

    def _write(_distance):
        seq["value"] += 1               # firmware acks during the call
        return ElsFsm.PHASE_OFFSET_OK

    fsm.apply_phase_offset.side_effect = _write

    popup.entry = 0.75
    popup.apply()
    popup._tick(0.1)
    assert popup.state == "applied", (
        "the ack was missed — the baseline was read after the write, so the "
        "increment it was supposed to detect had already happened")


def test_a_write_that_is_never_acked_surfaces_as_a_failure(popup, fsm):
    """Not as silence: an unacked write leaves the old total on screen, which
    is indistinguishable from a successful no-op."""
    fsm.phase_offset_seq.return_value = 7          # never moves
    popup.entry = 0.75
    popup.apply()
    for _ in range(PhaseOffsetPopup.ACK_TIMEOUT_POLLS):
        popup._tick(0.1)
    assert popup.state == "refused"
    assert popup.message == NO_ACK_TEXT
    assert not popup.busy


def test_the_ack_wait_does_not_give_up_early(popup, fsm):
    fsm.phase_offset_seq.return_value = 7
    popup.entry = 0.75
    popup.apply()
    for _ in range(PhaseOffsetPopup.ACK_TIMEOUT_POLLS - 1):
        popup._tick(0.1)
    assert popup.state == "waiting"


def test_the_total_is_refreshed_when_the_ack_lands(popup, fsm):
    popup.entry = 0.75
    popup.apply()
    fsm.phase_offset_display.return_value = (0.75, 0.5)
    fsm.phase_offset_seq.return_value = 8          # ack
    popup._tick(0.1)
    assert popup.state == "applied"
    assert "0.750" in popup.total_text


def test_clear_goes_through_the_same_ack_path(popup, fsm):
    popup.clear()
    fsm.clear_phase_offset.assert_called_once()
    assert popup.state == "waiting"
    fsm.phase_offset_seq.return_value = 8
    popup._tick(0.1)
    assert popup.state == "applied"
    assert popup.message == popup_mod.CLEARED_TEXT


def test_clear_is_refused_with_its_own_reason(popup, fsm):
    fsm.clear_phase_offset.return_value = ElsFsm.PHASE_OFFSET_NO_JOB
    popup.clear()
    assert popup.state == "refused"
    assert popup.message == REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_NO_JOB]


def test_a_second_command_cannot_start_while_one_is_in_flight(popup, fsm):
    popup.entry = 0.75
    popup.apply()
    popup.apply()
    assert fsm.apply_phase_offset.call_count == 1


# ── fraction fill ────────────────────────────────────────────────────
def test_fraction_fill_uses_the_fsm_pitch_and_converter(popup, fsm):
    """1/2 of a 1.5 mm pitch is 0.75 mm, and both halves of that sum have to
    come from the FSM — the popup owning either one is how a fill button and
    the running total end up disagreeing."""
    popup.fill_fraction(2)
    fsm.thread_pitch_steps.assert_called()
    assert popup.entry == pytest.approx(0.75)


@pytest.mark.parametrize("denominator,expected", [(2, 0.75), (3, 0.5), (4, 0.375)])
def test_fraction_fill_covers_the_common_multi_starts(popup, denominator, expected):
    popup.fill_fraction(denominator)
    assert popup.entry == pytest.approx(expected)


def test_fraction_fill_does_not_apply(popup, fsm):
    """A button that both computed and committed would make a mis-tap a phase
    change on a job in progress."""
    popup.fill_fraction(2)
    fsm.apply_phase_offset.assert_not_called()
    assert popup.state == "entry"


def test_the_filled_number_is_the_number_that_gets_applied(popup, fsm):
    """The entry field's job is to be checkable against a dial before it is
    committed, so what is displayed and what is sent must not diverge."""
    popup.fill_fraction(3)
    displayed = popup.entry_text
    popup.apply()
    sent = fsm.apply_phase_offset.call_args[0][0]
    assert float(displayed) == pytest.approx(sent)


def test_fraction_fill_without_a_pitch_says_so(popup, fsm):
    fsm.thread_pitch_steps.return_value = 0.0
    popup.fill_fraction(2)
    assert popup.state == "refused"
    assert popup.message == REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_NO_PITCH]
    assert popup.entry == 0.0


def test_fraction_fill_without_geometry_says_so(popup, fsm):
    # The FSM reports missing geometry as a 0.0 conversion rather than by
    # raising -- it is read from a clock callback, where an exception would
    # stop the update loop instead of showing anything.
    fsm.steps_to_display.side_effect = None
    fsm.steps_to_display.return_value = 0.0
    popup.fill_fraction(2)
    assert popup.state == "refused"
    assert popup.message == REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_NO_GEOMETRY]


def test_an_unmapped_spindle_axis_reports_missing_geometry(popup, fsm):
    """thread_pitch_steps() reaches through els.get_spindle_axis(), which is
    None until an axis is mapped.

    It USED to raise AttributeError there, which this popup caught. Since
    2026-08-22 the FSM catches it and returns 0.0 instead — the readout is
    polled from a Kivy clock callback, so a raise would stop the update loop
    rather than surface as a message. This pins the popup against the current
    contract: no pitch, stated as no pitch.
    """
    fsm.thread_pitch_steps.return_value = 0.0
    popup.fill_fraction(2)
    assert popup.state == "refused"
    assert popup.message == REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_NO_PITCH]


def test_the_keypad_writes_back_into_the_entry_property(popup):
    """The keypad is opened against `entry` by name; a rename that missed this
    call site would leave a keypad that accepts a number and drops it."""
    with patch("reflex.components.popups.keypad.Keypad") as keypad_cls:
        popup.edit_entry()
    keypad_cls.return_value.show.assert_called_once_with(popup, "entry")


# ── units ────────────────────────────────────────────────────────────
def test_the_entry_is_labelled_in_the_active_display_unit(running_app, fsm):
    running_app.els_uic = SimpleNamespace(els_fsm=fsm)
    running_app.formats = SimpleNamespace(
        current_format="IN", position_format="{:+0.4f}")
    with patch.object(PhaseOffsetPopup, "apply_class_lang_rules"):
        p = PhaseOffsetPopup()
    assert p.unit_label == "in"
    assert "in" in p.total_text
