"""The pattern screen is a rotary-table feature and a lathe must never offer it.

Hole circles, lines and rectangles lay holes out on a face. A lathe has nothing
to point that at, and until 2026-08-30 the sidebar wand that opens it appeared
on any machine whose `Show Wizard` setting was left at its default True -- which
is every fresh install.

The setting still exists, because a rotary table wants it. What changed is that
the use case now gates it: on a lathe the button is not offered whatever the
setting says, and the setting's own row is collapsed rather than left as a
control that cannot do anything.

Modelled on USE_CASE_MODES, which does exactly this job for the mode selector,
and tested the same way: against the TABLE, so a new use case cannot be added
without deciding this question for it.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

import pytest

from reflex.app import (DEFAULT_USE_CASE, USE_CASE_LABELS, USE_CASE_MODES,
                        USE_CASE_PATTERNS)


def test_a_lathe_never_exposes_the_pattern_screen():
    """THE ASK, stated as a test."""
    assert USE_CASE_PATTERNS["lathe"] is False


def test_a_rotary_table_still_does():
    """The feature is not being removed, only kept off the machines it makes no
    sense on."""
    assert USE_CASE_PATTERNS["rotary_table"] is True
    assert USE_CASE_PATTERNS["all_features"] is True


def test_every_use_case_has_an_answer():
    """A use case missing from the table falls back to the default's answer,
    which is a decision nobody made. Adding a use case should force this
    question the way it already forces the mode question."""
    assert set(USE_CASE_PATTERNS) == set(USE_CASE_MODES), (
        "USE_CASE_PATTERNS and USE_CASE_MODES disagree about which use cases "
        "exist; a use case in one and not the other is answered by a fallback")
    assert set(USE_CASE_PATTERNS) == set(USE_CASE_LABELS)


def test_the_default_use_case_is_in_the_table():
    """The lookup falls back to the default, so the default must be present or
    the fallback raises."""
    assert DEFAULT_USE_CASE in USE_CASE_PATTERNS


@pytest.mark.parametrize("use_case", sorted(USE_CASE_PATTERNS))
def test_the_answer_is_a_real_boolean(use_case):
    """kv reads this straight into `height:` and `disabled:` expressions, where
    a truthy string would silently mean True."""
    assert isinstance(USE_CASE_PATTERNS[use_case], bool)


def test_patterns_are_not_folded_into_the_mode_table():
    """The pattern screen is a SCREEN, not a mode.

    Folding it into USE_CASE_MODES would have made it selectable by the mode
    one-hot in the sidebar, which cycles ELS/DRO -- a different control with
    different semantics.
    """
    from reflex.app import MODE_DRO, MODE_ELS, MODE_INDEX, MODE_JOG

    known = {MODE_INDEX, MODE_ELS, MODE_JOG, MODE_DRO}
    for use_case, modes in USE_CASE_MODES.items():
        assert set(modes) <= known, (
            f"{use_case} lists a mode id that is not one of the four real "
            f"modes: {sorted(set(modes) - known)}")
