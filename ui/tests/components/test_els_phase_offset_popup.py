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
    APPLIED_TEXT, CLEARED_TEXT, ENTRY_HINT, HELP_TEXT, HELP_TITLE,
    INTRO_TEXT, MESSAGE_CHAR_BUDGET, NO_ACK_TEXT,
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
    # Healthy reads by default. Left to MagicMock, reads_fabricated_since()
    # answers with a truthy Mock and every ack poll discards itself.
    f.reads_baseline.return_value = 0
    f.reads_fabricated_since.return_value = False
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


# ── the entry box holds the CURRENT offset (bench 2.2) ───────────────
# Apply SETS the offset rather than adding to it, so the box is not "an amount
# to add" -- it is the value the offset will become. Opening it at 0.000 while
# a widening job is live reads as "the offset is zero", and applying without
# editing would throw the real one away.

def _popup_with_offset(running_app, fsm, distance, fraction=0.0):
    fsm.phase_offset_display.return_value = (distance, fraction)
    running_app.els_uic = SimpleNamespace(els_fsm=fsm)
    running_app.formats = SimpleNamespace(
        current_format="MM", position_format="{:+0.3f}")
    with patch.object(PhaseOffsetPopup, "apply_class_lang_rules"):
        return PhaseOffsetPopup()


def test_the_entry_box_opens_holding_the_current_offset(running_app, fsm):
    p = _popup_with_offset(running_app, fsm, 0.750, 0.5)

    assert p.entry == pytest.approx(0.750)


def test_the_entry_box_opens_at_zero_when_there_is_no_offset(running_app, fsm):
    p = _popup_with_offset(running_app, fsm, 0.0)

    assert p.entry == pytest.approx(0.0)


def test_the_entry_text_follows_the_seeded_value(running_app, fsm):
    """Seeding the property is only half of it -- the operator reads the text."""
    p = _popup_with_offset(running_app, fsm, 0.250)

    assert "0.250" in p.entry_text


def test_the_entry_is_reseeded_after_an_acknowledged_clear(running_app, fsm):
    """After a Clear the offset really is 0 and the box must say so. Leaving
    the old number there means the next Apply silently reinstates what was
    just thrown away."""
    p = _popup_with_offset(running_app, fsm, 0.750)
    assert p.entry == pytest.approx(0.750)

    # The controller now reports no offset, and an ack arrives.
    fsm.phase_offset_display.return_value = (0.0, 0.0)
    p._baseline_seq = 7
    fsm.phase_offset_seq.return_value = 8
    p.state = "waiting"
    p.busy = True
    p._pending = CLEARED_TEXT
    p._tick(0)

    assert p.entry == pytest.approx(0.0)


def test_a_failed_read_leaves_the_entry_alone(running_app, fsm):
    """Blanking it would look like a value. The readout above is the same call
    and reports the fault itself, so this must not be a second notice of one
    failure -- nor a silent zero the operator might Apply."""
    p = _popup_with_offset(running_app, fsm, 0.500)

    fsm.phase_offset_display.side_effect = RuntimeError("link down")
    p._seed_entry_from_current()

    assert p.entry == pytest.approx(0.500)


def test_the_keypad_for_this_field_offers_no_sign_key(running_app, fsm):
    """Bench 3.3: step-overs only go one way, so refusing a minus after it is
    typed spends the operator's attention on a rule the keypad could enforce.
    Keypad cannot be constructed under test, so the contract pinned here is
    the argument -- the behaviour it selects is pinned in test_keypad.py."""
    p = _popup_with_offset(running_app, fsm, 0.0)

    with patch("reflex.components.popups.keypad.Keypad") as Keypad:
        p.edit_entry()

    assert Keypad.call_args.kwargs.get("nonnegative") is True


# ── the running total ────────────────────────────────────────────────
def test_total_shows_both_the_distance_and_the_fraction(popup, fsm):
    """Both numbers, in two properties. The distance is total widening so far —
    the answer to "is the groove wide enough yet" and the thing checkable
    against a dial. The fraction is the aliasing bound, kept because dropping
    it removes the only warning that the total is nearing a full pitch."""
    fsm.phase_offset_display.return_value = (0.75, 0.5)
    popup._refresh_total()
    assert "0.750" in popup.total_text
    assert "mm" in popup.total_text
    assert "1/2" in popup.fraction_text
    assert "pitch" in popup.fraction_text


def test_the_distance_leads_and_the_fraction_is_subordinate(popup, fsm):
    """The fraction is NOT a width, and the screen must not present it as a
    peer of the one that is. It lives in its own property so the kv can render
    it smaller and dimmer; a single combined line — which is what this was
    until the multi-start framing was corrected — puts a limit gauge at the
    same weight as the measurement and invites the operator to work to it."""
    fsm.phase_offset_display.return_value = (0.100, 1.0 / 15.0)
    popup._refresh_total()
    assert "0.100" in popup.total_text
    # The headline carries the distance and NOTHING about pitch fractions.
    assert "pitch" not in popup.total_text
    assert "0.100" not in popup.fraction_text
    # And the bound is named where the fraction is, so the number has a scale.
    assert "pitch" in popup.fraction_text


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
        assert phase_offset_fraction_text(fraction) in popup.fraction_text

    # And concretely, so the shared rule cannot quietly become a no-op:
    fsm.phase_offset_display.return_value = (0.1, 1.0 / 3.0)
    popup._refresh_total()
    assert "1/3" in popup.fraction_text

    fsm.phase_offset_display.return_value = (0.1, 0.2755)
    popup._refresh_total()
    assert "0.276" in popup.fraction_text  # not a clean division: say so


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
    # And the bound line is BLANKED rather than left showing the last fraction
    # it managed to read, which would qualify a total that is not on screen.
    assert popup.fraction_text == ""


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


# ── the base/help split (bench 2026-08-24) ───────────────────────────
# "Way too many words about the whole offset." The base strings say what to
# DO; the why lives behind the HelpButton in the button row. Both halves are
# pinned: a base that grows back defeats the split, and a help that loses the
# moved rationale makes the trim a deletion.

def test_the_base_text_stays_stripped_to_the_doing():
    """Two short sentences for the intro, one for the hint. The ceiling is
    deliberately tight — the whole point of the split is that the always-on
    text CANNOT grow back to scrolling; new explanation goes in HELP_TEXT."""
    assert len(INTRO_TEXT) <= 160, (
        f"INTRO_TEXT is {len(INTRO_TEXT)} chars — explanation belongs in "
        f"HELP_TEXT, behind the help button")
    assert len(ENTRY_HINT) <= 80, (
        f"ENTRY_HINT is {len(ENTRY_HINT)} chars — explanation belongs in "
        f"HELP_TEXT, behind the help button")


def test_the_base_text_still_carries_the_set_not_add_semantics():
    """The one thing the base may not lose in the trim: Apply SETS the whole
    offset. An operator who reads only the base and treats the box as an
    increment applies the wrong number with no refusal to catch it."""
    assert "WHOLE" in INTRO_TEXT
    assert "not an amount to add" in ENTRY_HINT


def test_the_help_keeps_what_the_trim_removed():
    """The rationale is moved, not deleted: the set-not-add why, the
    no-motion-until-re-entry behaviour, what a minus would really do, and the
    aliasing bound the dim fraction line renders."""
    assert "does not add" in HELP_TEXT
    assert "re-enters the thread" in HELP_TEXT
    assert "minus" in HELP_TEXT
    assert "pitch" in HELP_TEXT
    assert HELP_TITLE


def test_the_modal_hands_the_help_button_its_words(popup):
    """The kv binds the HelpButton to these two properties; a popup whose
    properties drifted from the module constants would open empty help over
    a stripped base — the worst of both halves."""
    assert popup.help_title == HELP_TITLE
    assert popup.help_text == HELP_TEXT


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


# ── the entry ──────────────────────────────────────────
# THE FILL-FROM-A-FRACTION BUTTONS ARE GONE, and with them the seven tests that
# covered them. They offered 1/2, 1/3 and 1/4 of a pitch — multi-start
# step-overs, from the framing this feature was built under and which was
# corrected 2026-08-23 to what it is actually for: widening a groove past the
# width of the cutter. There is no equivalent number the software can compute,
# because nothing on this machine knows how wide the cutter is, so the row was
# removed rather than refilled with invented presets.
#
# What those tests really guarded is kept below in the form that still applies:
# that what the screen SHOWS is what the FSM is SENT. The rest — "a fill does
# not apply", "a fill without a pitch says so" — protected a control that no
# longer exists, and the refusal paths they reached are covered for every code
# by test_a_refusal_never_renders_as_success_or_as_a_bare_code.

def test_the_displayed_number_is_the_number_that_gets_applied(popup, fsm):
    """The entry field's job is to be checkable against a dial before it is
    committed, so what is on the button and what goes to the FSM must not
    diverge. Asserted at a value the display format carries exactly, so the
    only way this fails is the popup altering the entry on its way out."""
    popup.entry = 0.075
    displayed = popup.entry_text
    popup.apply()
    sent = fsm.apply_phase_offset.call_args[0][0]
    assert float(displayed) == pytest.approx(sent)


def test_the_entry_is_not_rounded_behind_the_operators_back(popup, fsm):
    """A keypad value finer than the display format still reaches the FSM
    whole: it converts to leadscrew steps with exact Fractions and rounds ONCE
    at the end, so pre-rounding here would spend that precision twice. The
    BUTTON is allowed to render the format's three digits; the write is not."""
    popup.entry = 0.0625
    assert popup.entry_text == "0.062" or popup.entry_text == "0.063"
    popup.apply()
    assert fsm.apply_phase_offset.call_args[0][0] == pytest.approx(0.0625)


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
